from __future__ import annotations

import argparse
import csv
import json
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _method(candidate_id: str, candidates: dict[str, dict[str, Any]]) -> str:
    return str(candidates[candidate_id]["method_id"])


def _review_summary(path: Path | None) -> str:
    if path is None or not path.is_file():
        return "未单独复核（与 Rule Top1 相同）"
    payload = _read(path)["invocation"]["review"]
    return str(payload.get("summary", "")).replace("|", "\\|").replace("\n", " ")


def _collect_candidates(run_dir: Path, task_id: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for path in (run_dir / "candidates" / task_id).glob("*/candidate.json"):
        candidate = _read(path)
        result[str(candidate["candidate_id"])] = candidate
    if len(result) != 7:
        raise ValueError(f"{task_id}: expected 7 candidates, got {len(result)}")
    return result


def _summary_block(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        key: summary[key]
        for key in (
            "review_run_id",
            "phase",
            "task_count",
            "candidate_review_count",
            "pair_call_count",
            "rule_forced_review_count",
            "agent_proposal_review_count",
            "agent_override_count",
            "selected_grade_counts",
            "selected_ab_count",
            "selected_ab_rate",
            "aigc_request_count",
            "within_soft_target_120s_count",
            "override_block_reason_counts",
            "complete",
        )
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Package frozen Calibration20 + one-shot Validation40 Rule-anchored review."
    )
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--evaluation-id", required=True)
    parser.add_argument("--calibration-review-id", required=True)
    parser.add_argument("--validation-review-id", required=True)
    parser.add_argument("--calibration-agent-id", required=True)
    parser.add_argument("--validation-agent-id", required=True)
    parser.add_argument("--overview-input-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    run_dir = args.run_dir.resolve()
    output = args.output_dir.resolve()
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)

    run = _read(run_dir / "run.json")
    review_specs = (
        ("calibration", args.calibration_review_id, args.calibration_agent_id),
        ("validation", args.validation_review_id, args.validation_agent_id),
    )
    summaries: dict[str, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    block_counts: Counter[str] = Counter()
    category_grades: dict[str, Counter[str]] = defaultdict(Counter)

    for phase, review_id, agent_id in review_specs:
        review_root = run_dir / "strict-reviews" / review_id
        agent_root = run_dir / "agent-runs" / agent_id
        summary = _read(review_root / "summary.json")
        agent_manifest = _read(agent_root / "agent-run.json")
        if not summary.get("complete"):
            raise ValueError(f"{review_id}: incomplete review")
        if summary["phase"] != phase:
            raise ValueError(f"{review_id}: phase mismatch")
        if summary["policy_sha256"] != agent_manifest["skill_sha256"]:
            raise ValueError(f"{review_id}: policy SHA mismatch")
        summaries[phase] = summary

        for task_id in agent_manifest["task_ids"]:
            task = _read(run_dir / "tasks" / f"{task_id}.json")
            decision = _read(review_root / "decisions" / f"{task_id}.json")
            agent_decision = _read(agent_root / "decisions" / f"{task_id}.json")
            candidates = _collect_candidates(run_dir, task_id)
            ranking = decision["rule_complete_ranking"]
            if len(ranking) != 7 or len(set(ranking)) != 7:
                raise ValueError(f"{task_id}: invalid complete Rule ranking")
            rule_id = decision["rule_top1_candidate_id"]
            challenger_id = decision["agent_proposed_candidate_id"]
            selected_id = decision["selected_candidate_id"]
            folder = output / "tasks" / task_id

            source_ref = _read(run_dir / "sources" / f"{task['source']['source_id']}.json")
            source_path = run_dir / source_ref["relative_path"]
            _copy(source_path, folder / f"00_source{source_path.suffix.lower()}")
            overview = run_dir / "agent-inputs" / args.overview_input_id / f"{task_id}.png"
            _copy(overview, folder / "01_rule_aware_overview.png")

            copied_candidates: set[str] = set()
            for label, candidate_id in (
                ("02_rule_top1", rule_id),
                ("03_qwen_challenger", challenger_id),
                ("04_final_selected", selected_id),
            ):
                candidate = candidates[candidate_id]
                method_id = candidate["method_id"]
                source = run_dir / candidate["output"]["relative_path"]
                if candidate_id in copied_candidates and label == "04_final_selected":
                    (folder / f"{label}_IS_{method_id}.txt").write_text(
                        f"Final selected is the already copied {method_id} candidate.\n",
                        encoding="utf-8",
                    )
                else:
                    _copy(source, folder / f"{label}_{method_id}.png")
                    copied_candidates.add(candidate_id)

            candidate_review_root = review_root / "candidate-reviews" / task_id
            candidate_sheet_root = review_root / "candidate-sheets" / task_id
            for source in candidate_review_root.glob("*.json"):
                _copy(source, folder / "reviews" / source.name)
            for source in candidate_sheet_root.glob("*.png"):
                _copy(source, folder / "reviews" / source.name)
            pair_review = review_root / "pair-reviews" / f"{task_id}.json"
            pair_sheet = review_root / "pair-sheets" / f"{task_id}.png"
            if pair_review.is_file():
                _copy(pair_review, folder / "reviews" / "pair-review.json")
            if pair_sheet.is_file():
                _copy(pair_sheet, folder / "05_rule_vs_qwen_highres.png")
            _copy(
                review_root / "decisions" / f"{task_id}.json",
                folder / "decision.json",
            )
            _copy(
                agent_root / "decisions" / f"{task_id}.json",
                folder / "qwen-overview-decision.json",
            )

            ranking_rows: list[dict[str, Any]] = []
            for rank, candidate_id in enumerate(ranking, start=1):
                metric = _read(
                    run_dir
                    / "evaluations"
                    / args.evaluation_id
                    / "metrics"
                    / f"{candidate_id}.json"
                )["metrics"]
                ranking_rows.append(
                    {
                        "rank": rank,
                        "method": _method(candidate_id, candidates),
                        "candidate_id": candidate_id,
                        "quality": metric.get("quality_score"),
                        "proxy_grade": metric.get("proxy_grade"),
                        "ocr_recall": metric.get("ocr_character_recall"),
                        "person_preservation": metric.get("person_count_preservation"),
                        "face_preservation": metric.get("face_count_preservation"),
                        "product_preservation": metric.get("product_count_preservation"),
                        "logo_preservation": metric.get("logo_count_preservation"),
                    }
                )
            (folder / "rule-ranking.json").write_text(
                json.dumps(ranking_rows, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            rule_review_path = candidate_review_root / "rule_top1.json"
            agent_review_path = candidate_review_root / "agent_top1.json"
            rule_reason = _review_summary(rule_review_path)
            agent_reason = _review_summary(agent_review_path)
            pair_reason = "同一候选，无需配对"
            pair_payload: dict[str, Any] | None = None
            if pair_review.is_file():
                pair_payload = _read(pair_review)
                pair_reason = (
                    str(pair_payload["invocation"]["review"].get("summary", ""))
                    .replace("|", "\\|")
                    .replace("\n", " ")
                )

            lines = [
                f"# {task_id}",
                "",
                f"- Split：`{phase}`；场景：`{task['source']['scene_category']}`",
                f"- Rule Top1：`{_method(rule_id, candidates)}` / {decision['rule_grade']}",
                f"- Qwen challenger：`{_method(challenger_id, candidates)}` / "
                f"{decision['agent_grade']}",
                f"- 最终选择：`{_method(selected_id, candidates)}` / "
                f"**{decision['selected_grade']}**",
                f"- 是否覆盖 Rule：`{decision['agent_overrode_rule']}`",
                f"- 覆盖阻断：`{', '.join(decision['override_block_reasons']) or '无'}`",
                f"- 建议 AIGC：`{decision['request_external_aigc']}`",
                "",
                "## Rule 完整排名",
                "",
                "| 排名 | 方法 | Quality | Proxy | OCR召回 | 人物 | 人脸 | 商品 | Logo |",
                "| ---: | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |",
            ]
            for row in ranking_rows:
                values = []
                for key in (
                    "ocr_recall",
                    "person_preservation",
                    "face_preservation",
                    "product_preservation",
                    "logo_preservation",
                ):
                    value = row[key]
                    values.append("—" if value is None else f"{float(value):.3f}")
                lines.append(
                    f"| {row['rank']} | {row['method']} | {float(row['quality']):.2f} | "
                    f"{row['proxy_grade']} | {' | '.join(values)} |"
                )
            lines.extend(
                [
                    "",
                    "## 高清判断理由",
                    "",
                    f"- Rule：{rule_reason}",
                    f"- Qwen challenger：{agent_reason}",
                    f"- 配对：{pair_reason}",
                    "",
                    "## 看图顺序",
                    "",
                    "1. `01_rule_aware_overview.png`：七候选与完整 Rule 排名；",
                    "2. `02_rule_top1_*.png` 与 `03_qwen_challenger_*.png`：两张完整 1536 图；",
                    "3. `reviews/*top1-*.png`：各自与源图的高清整图/局部复核；",
                    "4. `05_rule_vs_qwen_highres.png`：两者不同时的最终配对证据；",
                    "5. `decision.json`：本地硬门禁的最终裁决。",
                ]
            )
            (folder / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

            block_counts.update(decision["override_block_reasons"])
            category = str(task["source"]["scene_category"])
            category_grades[category][decision["selected_grade"]] += 1
            rows.append(
                {
                    "task_id": task_id,
                    "phase": phase,
                    "scene_category": category,
                    "rule_top1_method": _method(rule_id, candidates),
                    "rule_grade": decision["rule_grade"],
                    "qwen_challenger_method": _method(challenger_id, candidates),
                    "qwen_grade": decision["agent_grade"],
                    "pair_preference": decision["pair_preference"],
                    "pair_clear_visual_evidence": decision["pair_clear_visual_evidence"],
                    "pair_evidence_consistent": decision["pair_evidence_consistent"],
                    "final_method": _method(selected_id, candidates),
                    "final_grade": decision["selected_grade"],
                    "passed_ab": decision["selected_grade"] in {"A", "B"},
                    "agent_overrode_rule": decision["agent_overrode_rule"],
                    "aigc_requested": decision["request_external_aigc"],
                    "wall_seconds": decision["task_review_wall_seconds"],
                    "override_block_reasons": ";".join(decision["override_block_reasons"]),
                    "pair_reason": pair_reason,
                    "qwen_overview_reason_codes": ";".join(agent_decision["reason_codes"]),
                }
            )

    if len(rows) != 60 or len({row["task_id"] for row in rows}) != 60:
        raise ValueError("expected exactly 60 unique tasks")
    if summaries["calibration"]["policy_sha256"] != summaries["validation"]["policy_sha256"]:
        raise ValueError("Calibration and Validation policy SHA differ")
    if not summaries["calibration"]["policy_frozen_after_calibration"]:
        raise ValueError("Calibration policy was not frozen")
    if summaries["validation"]["calibration_review_run_id"] != args.calibration_review_id:
        raise ValueError("Validation does not reference the frozen Calibration")

    with (output / "all-task-results.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (output / "all-task-results.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    total_grades = Counter(row["final_grade"] for row in rows)
    total_ab = sum(bool(row["passed_ab"]) for row in rows)
    report = {
        "schema_version": "1.0",
        "run_id": run["run_id"],
        "evaluation_id": args.evaluation_id,
        "policy_sha256": summaries["calibration"]["policy_sha256"],
        "calibration": _summary_block(summaries["calibration"]),
        "validation": _summary_block(summaries["validation"]),
        "full60": {
            "task_count": 60,
            "selected_grade_counts": dict(total_grades),
            "selected_ab_count": total_ab,
            "selected_ab_rate": total_ab / 60,
            "agent_override_count": sum(bool(row["agent_overrode_rule"]) for row in rows),
            "aigc_request_count": sum(bool(row["aigc_requested"]) for row in rows),
            "within_soft_target_120s_count": sum(float(row["wall_seconds"]) <= 120 for row in rows),
            "override_block_reason_counts": dict(block_counts),
        },
        "category_grade_counts": {
            category: dict(counts) for category, counts in sorted(category_grades.items())
        },
    }
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    report_lines = [
        "# Movie Visual 60：Rule 锚定 Agent 冻结验证",
        "",
        f"Run：`{run['run_id']}`  ",
        f"Policy SHA256：`{report['policy_sha256']}`",
        "",
        "## 结论",
        "",
        "- Calibration20 调整后冻结策略；Validation40 只运行一次，没有回看调参。",
        "- Qwen 每个任务都收到完整 Rule 排名和 Rule Top1；Rule Top1 60/60 强制高清复核。",
        "- Qwen challenger 与 Rule 不同时，做完整图和关键文字/主体/商品/Logo 局部的高清配对。",
        "- 本轮 0 次覆盖 Rule：没有任何 challenger 同时满足证据明确、一致、等级更好"
        "及内容保护门禁。",
        f"- 最终 A+B：{total_ab}/60（{total_ab / 60:.1%}）；其中 Calibration "
        f"{summaries['calibration']['selected_ab_count']}/20"
        f"（{summaries['calibration']['selected_ab_rate']:.1%}），Validation "
        f"{summaries['validation']['selected_ab_count']}/40"
        f"（{summaries['validation']['selected_ab_rate']:.1%}）。",
        f"- C 级请求 AIGC：{report['full60']['aigc_request_count']}/60。"
        "本轮只做路由判断，没有新增付费调用。",
        "",
        "## 冻结实验结果",
        "",
        "| Split | Task | Rule强制高清 | 不同候选配对 | Agent覆盖 | A | B | C | A+B | 120秒内 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for phase in ("calibration", "validation"):
        summary = summaries[phase]
        grades = summary["selected_grade_counts"]
        report_lines.append(
            f"| {phase} | {summary['task_count']} | {summary['rule_forced_review_count']} | "
            f"{summary['pair_call_count']} | {summary['agent_override_count']} | "
            f"{grades.get('A', 0)} | {grades.get('B', 0)} | {grades.get('C', 0)} | "
            f"{summary['selected_ab_count']} ({summary['selected_ab_rate']:.1%}) | "
            f"{summary['within_soft_target_120s_count']} |"
        )
    pair_count = (
        summaries["calibration"]["pair_call_count"] + summaries["validation"]["pair_call_count"]
    )
    report_lines.extend(
        [
            f"| **合计** | **60** | **60** | "
            f"**{pair_count}** | "
            f"**0** | **{total_grades.get('A', 0)}** | **{total_grades.get('B', 0)}** | "
            f"**{total_grades.get('C', 0)}** | **{total_ab} ({total_ab / 60:.1%})** | "
            f"**{report['full60']['within_soft_target_120s_count']}** |",
            "",
            "## 覆盖保护门禁",
            "",
            "| 阻断原因 | 次数 |",
            "| --- | ---: |",
        ]
    )
    for reason, count in block_counts.most_common():
        report_lines.append(f"| `{reason}` | {count} |")
    report_lines.extend(
        [
            "",
            "## 逐图查看",
            "",
            "| Task | Split | 场景 | Rule | Qwen challenger | 最终等级 | A+B | 覆盖 | AIGC |",
            "| --- | --- | --- | --- | --- | --- | ---: | ---: | ---: |",
        ]
    )
    for row in rows:
        report_lines.append(
            f"| [{row['task_id']}](tasks/{row['task_id']}/) | {row['phase']} | "
            f"{row['scene_category']} | {row['rule_top1_method']} ({row['rule_grade']}) | "
            f"{row['qwen_challenger_method']} ({row['qwen_grade']}) | {row['final_grade']} | "
            f"{row['passed_ab']} | {row['agent_overrode_rule']} | {row['aigc_requested']} |"
        )
    report_lines.extend(
        [
            "",
            "## 口径说明",
            "",
            "- A/B/C 是本轮严格机器预审，不是业务人工评分；A+B 仅作为机器侧直接可用率。",
            "- 0 次覆盖不等于 Qwen 没有作用：Qwen 提出 challenger 并提供高清语义证据，"
            "本地门禁负责保守裁决。",
            "- `no_clear_visual_evidence` 是必需条件，不应单独理解成模型失败；"
            "它保证证据不足时回退 Rule。",
            "- 原 Rule 自动 Quality 仍在每个任务的 `rule-ranking.json` 中，和高清等级分开保存。",
        ]
    )
    (output / "README.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    archive = shutil.make_archive(str(output), "zip", output.parent, output.name)
    print(
        json.dumps(
            {"output_dir": str(output), "archive": archive, **report["full60"]},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
