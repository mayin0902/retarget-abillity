from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from retarget_agent.models import CandidateRecord, RunManifest, TaskSpec
from retarget_agent.storage import LocalArtifactStore


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _grade_rank(value: str) -> int:
    return {"D": 0, "C": 1, "B": 2, "A": 3}.get(value, 4)


def _review_reason(review: dict[str, Any]) -> str:
    payload = review["invocation"]["review"]
    summary = str(payload.get("summary", "")).strip()
    dimensions = []
    for name in (
        "subject",
        "face_body",
        "text",
        "product_logo",
        "structure",
        "composition",
    ):
        item = payload[name]
        if item.get("applicable") and item.get("grade") in {"B", "C", "D"}:
            codes = ", ".join(item.get("reason_codes", []))
            dimensions.append(f"{name}={item['grade']}({codes})")
    return summary + (f"；维度证据：{'; '.join(dimensions)}" if dimensions else "")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--evaluation-id", required=True)
    parser.add_argument("--overview-agent-run-id", required=True)
    parser.add_argument("--strict-run-id", required=True)
    parser.add_argument("--plan-id", required=True)
    parser.add_argument("--seedream-review-id", required=True)
    parser.add_argument("--benchmark-id", required=True)
    parser.add_argument("--route-pass-report-id")
    parser.add_argument("--route-comparison-dir", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--per-category", type=int, default=3)
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    output = args.output_dir.resolve()
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    store = LocalArtifactStore(run_dir)
    run = RunManifest.model_validate(store.read_json("run.json"))
    strict_root = run_dir / "strict-reviews" / args.strict_run_id
    plan = _json(
        run_dir / "external-generation" / "plans" / args.plan_id / "plan.json"
    )
    plan_by_task = {item["task_id"]: item for item in plan["entries"]}

    choices: dict[str, list[tuple[TaskSpec, dict[str, Any]]]] = defaultdict(list)
    for task_id in run.task_ids:
        task = TaskSpec.model_validate(store.read_json(f"tasks/{task_id}.json"))
        decision = _json(strict_root / "decisions" / f"{task_id}.json")
        choices[task.source.scene_category].append((task, decision))
    selected: list[tuple[TaskSpec, dict[str, Any]]] = []
    for category in sorted(choices):
        ordered = sorted(
            choices[category],
            key=lambda item: (
                not plan_by_task[item[0].task_id]["requested_by"],
                _grade_rank(item[1]["selected_grade"]),
                item[0].task_id,
            ),
        )
        selected.extend(ordered[: args.per_category])

    index_rows: list[dict[str, Any]] = []
    for task, decision in selected:
        task_id = task.task_id
        folder = output / "representatives" / task_id
        source_ref = store.read_json(f"sources/{task.source.source_id}.json")
        source_path = store.path(source_ref["relative_path"])
        _copy(source_path, folder / f"00_source{source_path.suffix.lower()}")
        _copy(
            run_dir / "visualizations" / f"{task_id}.png",
            folder / "01_technical_overview.png",
        )
        unbiased = (
            run_dir
            / "agent-inputs"
            / "movie60-unbiased-v1"
            / f"{task_id}.png"
        )
        if unbiased.is_file():
            _copy(unbiased, folder / "02_unbiased_agent_overview.png")
        candidates = [
            CandidateRecord.model_validate_json(path.read_text(encoding="utf-8"))
            for path in (run_dir / "candidates" / task_id).glob("*/candidate.json")
        ]
        score_rows = []
        for index, candidate in enumerate(
            sorted(candidates, key=lambda item: item.method_id), start=1
        ):
            metric_path = (
                run_dir
                / "evaluations"
                / args.evaluation_id
                / "metrics"
                / f"{candidate.candidate_id}.json"
            )
            metric = _json(metric_path)["metrics"]
            if candidate.output is not None:
                _copy(
                    store.path(candidate.output.relative_path),
                    folder / "candidates" / f"{index:02d}_{candidate.method_id}.png",
                )
            _copy(metric_path, folder / "metrics" / f"{candidate.method_id}.json")
            score_rows.append(
                {
                    "method_id": candidate.method_id,
                    "generation_status": candidate.generation_status.value,
                    "quality_score": metric.get("quality_score"),
                    "proxy_grade": metric.get("proxy_grade"),
                }
            )
        _copy(
            strict_root / "decisions" / f"{task_id}.json",
            folder / "strict" / "decision.json",
        )
        review_rows = []
        for candidate_id in decision["reviewed_candidate_ids"]:
            review = strict_root / "reviews" / f"{candidate_id}.json"
            _copy(review, folder / "strict" / f"{candidate_id}-review.json")
            review_payload = _json(review)
            machine_review = review_payload["invocation"]["review"]
            review_rows.append(
                {
                    "candidate_id": candidate_id,
                    "rank_before_pairwise": review_payload["rank_before_pairwise"],
                    "method_id": review_payload["evidence"]["method_id"],
                    "grade": machine_review["overall_grade"],
                    "directly_usable": machine_review["directly_usable"],
                    "reason": _review_reason(review_payload),
                }
            )
        for sheet in (strict_root / "sheets" / task_id).glob("*.png"):
            _copy(sheet, folder / "strict" / sheet.name)
        seedream_result = run_dir / "external-generation" / "results" / f"{task_id}.json"
        if seedream_result.is_file():
            result = _json(seedream_result)
            _copy(seedream_result, folder / "seedream" / "result.json")
            if result["status"] == "success":
                _copy(
                    run_dir / result["provider_native_path"],
                    folder / "seedream" / "native" / Path(result["provider_native_path"]).name,
                )
                _copy(
                    run_dir / result["evaluation_path"],
                    folder / "seedream" / "evaluation-1536.png",
                )
                seedream_review = (
                    run_dir
                    / "external-generation"
                    / "strict-reviews"
                    / args.seedream_review_id
                    / "reviews"
                    / f"{task_id}.json"
                )
                if seedream_review.is_file():
                    _copy(seedream_review, folder / "seedream" / "strict-review.json")
        scorecard = {
            "task_id": task_id,
            "scene_category": task.source.scene_category,
            "split": task.source.split,
            "strict_decision": decision,
            "aigc_plan": plan_by_task[task_id],
            "traditional_candidates": sorted(
                score_rows,
                key=lambda item: -float(item["quality_score"] or -1),
            ),
        }
        (folder / "scorecard.json").write_text(
            json.dumps(scorecard, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        task_lines = [
            f"# {task_id}",
            "",
            f"- 场景：`{task.source.scene_category}`；Split：`{task.source.split}`",
            f"- 严审选择：`{decision['selected_candidate_id']}`",
            f"- 严审等级：**{decision['selected_grade']}**；直接可用："
            f"`{decision['selected_directly_usable']}`",
            f"- 请求 AIGC：`{decision['request_external_aigc']}`；原因："
            f"`{', '.join(decision['aigc_trigger_reasons']) or '无'}`",
            "",
            "## 七候选自动分数排名",
            "",
            "| 排名 | 方法 | Quality | Proxy等级 | 生成状态 |",
            "| ---: | --- | ---: | --- | --- |",
        ]
        for rank, row in enumerate(scorecard["traditional_candidates"], start=1):
            task_lines.append(
                f"| {rank} | {row['method_id']} | {float(row['quality_score']):.2f} | "
                f"{row['proxy_grade']} | {row['generation_status']} |"
            )
        task_lines.extend(
            [
                "",
                "## Top1/Top2 高清复核",
                "",
                "| 原排名 | 方法 | 等级 | 直接可用 | 评分原因 |",
                "| ---: | --- | --- | --- | --- |",
            ]
        )
        for row in sorted(review_rows, key=lambda item: item["rank_before_pairwise"]):
            reason = row["reason"].replace("|", "\\|").replace("\n", " ")
            task_lines.append(
                f"| {row['rank_before_pairwise']} | {row['method_id']} | "
                f"{row['grade']} | {row['directly_usable']} | {reason} |"
            )
        task_lines.extend(
            [
                "",
                "说明：`01_technical_overview.png` 是传统技术总览；"
                "`02_unbiased_agent_overview.png` 是无 Top1 标记的 Agent 输入；"
                "`strict/` 中是源图与候选的高清整图/关键区域对照及六维原始 JSON。",
            ]
        )
        (folder / "README.md").write_text(
            "\n".join(task_lines) + "\n", encoding="utf-8"
        )
        index_rows.append(
            {
                "task_id": task_id,
                "scene_category": task.source.scene_category,
                "split": task.source.split,
                "strict_grade": decision["selected_grade"],
                "strict_selected": decision["selected_candidate_id"],
                "aigc_requested": decision["request_external_aigc"],
            }
        )

    report_source = run_dir / "benchmarks" / args.benchmark_id / "report.json"
    _copy(report_source, output / "reports" / "four-arm-report.json")
    if args.route_pass_report_id:
        route_report = run_dir / "benchmarks" / args.route_pass_report_id
        if not route_report.is_dir():
            raise FileNotFoundError(route_report)
        shutil.copytree(route_report, output / "reports" / "route-pass-report")
    if args.route_comparison_dir:
        comparison = args.route_comparison_dir.resolve()
        if not comparison.is_dir():
            raise FileNotFoundError(comparison)
        shutil.copytree(comparison, output / "route-comparisons")
    for source, name in (
        (run_dir / "evaluations" / args.evaluation_id / "summary.json", "automatic-summary.json"),
        (
            run_dir / "agent-runs" / args.overview_agent_run_id / "summary.json",
            "overview-agent-summary.json",
        ),
        (strict_root / "summary.json", "strict-top2-summary.json"),
        (
            run_dir / "external-generation" / "evaluation" / "summary.json",
            "seedream-evaluation-summary.json",
        ),
    ):
        if source.is_file():
            _copy(source, output / "reports" / name)
    repository_root = Path(__file__).resolve().parents[1]
    for source, name in (
        (
            repository_root / "docs" / "reports" / "MOVIE60_STRICT_END_TO_END_REPORT.md",
            "MOVIE60_STRICT_END_TO_END_REPORT.md",
        ),
        (
            repository_root / "docs" / "reports" / "MOVIE60_CODEX_CALIBRATION_AUDIT.md",
            "MOVIE60_CODEX_CALIBRATION_AUDIT.md",
        ),
        (
            repository_root
            / "docs"
            / "experiments"
            / "MOVIE_VISUAL60_STRICT_PROTOCOL.md",
            "MOVIE_VISUAL60_STRICT_PROTOCOL.md",
        ),
    ):
        if source.is_file():
            _copy(source, output / "reports" / name)
    resource_root = run_dir / "resource-observations"
    if resource_root.is_dir():
        for source in resource_root.glob("qwen4-*-gu30.*"):
            _copy(source, output / "resources" / source.name)
    benchmark = _json(report_source)
    lines = [
        "# Movie Visual 60 严格机审交付包",
        "",
        f"Run：`{run.run_id}`",
        "",
        "## 四路线摘要",
        "",
        "| 路线 | 完整分母 | Quality均值 | Proxy A率 | Proxy成功率 | 采用SeedDream |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for arm, values in benchmark["arms"].items():
        lines.append(
            f"| {arm} | {values['task_count']} | {values['quality_score_mean']:.2f} | "
            f"{values['proxy_a_rate']:.1%} | {values['proxy_success_rate']:.1%} | "
            f"{values['selected_seedream_count']} |"
        )
    lines.extend(
        [
            "",
            "## 高清严格口径与AIGC子集",
            "",
            "- Agent本身A/B：23/60（38.3%）；严格门禁采用1张AIGC后：24/60（40.0%）；",
            "- 付费计划20张：8张回图，高清复核1B/7C；计划全分母严格A/B为1/20（5.0%）；",
            "- 实际账单未回传；成功与提交后不确定共17次，保守估算5.10–10.20元。",
            "",
            "## 代表任务",
            "",
            "每个目录包含原图、传统技术总览、无偏Agent总览、七候选独立PNG、自动指标、",
            "Top-2高清对照、逐维严格评分与一份可直接阅读的README，",
            "以及存在时的SeedDream原生2K、1536评测图和严格复核。",
            "`route-comparisons/tasks/`另按20个付费计划Task分目录，明确拆出原图、",
            "Rule选择、Agent选择、AIGC结果、四栏拼图和评分理由JSON。",
            "",
            "| Task | 类别 | Split | 严格等级 | AIGC触发 |",
            "| --- | --- | --- | ---: | ---: |",
        ]
    )
    for row in index_rows:
        lines.append(
            f"| [{row['task_id']}](representatives/{row['task_id']}/) | "
            f"{row['scene_category']} | {row['split']} | {row['strict_grade']} | "
            f"{row['aigc_requested']} |"
        )
    lines.extend(
        [
            "",
            "## 口径",
            "",
            "- Proxy是自动证据，不是正式人工等级；",
            "- 严格等级来自Qwen总览后Top-1/Top-2高清复核；",
            "- Calibration的Codex检查不冒充业务人工标签；",
            "- SeedDream实际账单未知，报告使用每次0.30–0.60元区间；",
            "- 所有原始和派生图片仅用于本地研究，不随Git分发。",
        ]
    )
    (output / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (output / "representative-index.json").write_text(
        json.dumps(index_rows, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    archive = shutil.make_archive(str(output), "zip", root_dir=output.parent, base_dir=output.name)
    print(
        json.dumps(
            {
                "output_dir": str(output),
                "archive": archive,
                "representative_count": len(index_rows),
                "categories": dict(Counter(row["scene_category"] for row in index_rows)),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
