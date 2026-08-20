from __future__ import annotations

import argparse
import csv
import json
import shutil
import textwrap
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps

from retarget_agent.aigc_experiment import load_aigc_human_calibration_feedback
from retarget_agent.models import TaskSpec
from retarget_agent.storage import LocalArtifactStore

PANEL = 560
HEADER = 252
FOOTER = 68
BACKGROUND = "#f2f3f5"
INK = "#111820"
MUTED = "#59636e"
PASS = "#16805b"
FAIL = "#c33c32"
ACCENT = "#c7000b"


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        Path("C:/Windows/Fonts/msyhbd.ttc") if bold else Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/arialbd.ttf") if bold else Path("C:/Windows/Fonts/arial.ttf"),
    )
    for path in candidates:
        if path.is_file():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def _method(candidate_id: str | None) -> str:
    if not candidate_id:
        return "none"
    parts = candidate_id.split("--")
    return parts[1] if len(parts) >= 3 else candidate_id


def _metric_path(run_dir: Path, evaluation_id: str, candidate_id: str) -> Path:
    return run_dir / "evaluations" / evaluation_id / "metrics" / f"{candidate_id}.json"


def _metrics(run_dir: Path, evaluation_id: str, candidate_id: str) -> dict[str, Any]:
    return _json(_metric_path(run_dir, evaluation_id, candidate_id))["metrics"]


def _candidate_path(run_dir: Path, task_id: str, candidate_id: str) -> Path:
    return run_dir / "candidates" / task_id / _method(candidate_id) / "candidate.png"


def _strict_review(strict_root: Path, task_id: str, candidate_id: str) -> dict[str, Any] | None:
    path = strict_root / "reviews" / f"{candidate_id}.json"
    return _json(path) if path.is_file() else None


def _strict_codes(review: dict[str, Any] | None) -> set[str]:
    if review is None:
        return set()
    payload = review["invocation"]["review"]
    codes: set[str] = set()
    for name in ("subject", "face_body", "text", "product_logo", "structure", "composition"):
        codes.update(payload[name].get("reason_codes", []))
    return codes


def _review_reason(review: dict[str, Any] | None, metrics: dict[str, Any]) -> str:
    if review is not None:
        payload = review["invocation"]["review"]
        labels = {
            "subject": "主体",
            "face_body": "人物",
            "text": "文字",
            "product_logo": "商品/Logo",
            "structure": "结构",
            "composition": "构图",
        }
        downgraded: list[str] = []
        for name, label in labels.items():
            dimension = payload[name]
            if dimension.get("applicable") and dimension.get("grade") in {"B", "C", "D"}:
                codes = ",".join(dimension.get("reason_codes", [])) or "可见质量问题"
                downgraded.append(f"{label}{dimension['grade']}:{codes}")
        if downgraded:
            return "高清复核：" + "；".join(downgraded)
        return "高清复核：未见明显不可用缺陷"
    parts: list[str] = []
    for label, key in (
        ("OCR召回", "ocr_character_recall"),
        ("结构相似", "structure_line_similarity"),
        ("拉伸d", "direct_warp_d_stretch"),
    ):
        value = metrics.get(key)
        if value is not None:
            parts.append(f"{label}={float(value):.2f}")
    regressions = str(metrics.get("critical_regressions", "")).replace("|", ",")
    if regressions:
        parts.append("回归=" + regressions)
    return "自动指标：" + ("；".join(parts) if parts else "无可用细分理由")


def _tradeoff(metrics: dict[str, Any], review: dict[str, Any] | None) -> str:
    regressions = set(filter(None, str(metrics.get("critical_regressions", "")).split("|")))
    codes = regressions | _strict_codes(review)
    count_keys = (
        "person_count_preservation",
        "face_count_preservation",
        "product_count_preservation",
        "logo_count_preservation",
    )
    count_loss = any(
        metrics.get(key) is not None and float(metrics[key]) < 0.9 for key in count_keys
    )
    text_loss = (
        metrics.get("ocr_character_recall") is not None
        and float(metrics["ocr_character_recall"]) < 0.7
    ) or bool(
        {
            "critical_text_missing",
            "critical_text_damage",
            "text_damage",
            "missing_content",
            "logo_count_preservation",
        }
        & codes
    )
    content_loss = count_loss or text_loss
    geometry_codes = {
        "global_stretch",
        "severe_global_stretch",
        "local_deformation",
        "face_body_deformation",
        "structure_deformation",
        "composition_damage",
        "mesh_distortion",
        "seam_artifact",
    }
    geometry_risk = bool(geometry_codes & codes)
    stretch = metrics.get("direct_warp_d_stretch")
    if stretch is not None and float(stretch) >= 0.15:
        geometry_risk = True
    if metrics.get("structure_line_similarity") is not None:
        geometry_risk |= float(metrics["structure_line_similarity"]) < 0.65
    if content_loss and geometry_risk:
        return "内容缺失 + 拉伸/局部形变"
    if content_loss:
        return "视觉较自然，但有内容缺失"
    if geometry_risk:
        return "内容较完整，但有拉伸/形变风险"
    return "内容完整且视觉较自然"


def _grade_from_proxy(proxy: str | None) -> str:
    return {"proxy_a": "A", "proxy_b": "B", "proxy_c": "C"}.get(str(proxy), "N/A")


def _fit(source: Path, size: tuple[int, int]) -> Image.Image:
    with Image.open(source) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
        image.thumbnail(size, Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", size, "#15181c")
        canvas.paste(image, ((size[0] - image.width) // 2, (size[1] - image.height) // 2))
    return canvas


def _panel(
    *,
    role: str,
    image_path: Path | None,
    method: str,
    score: float | None,
    grade: str,
    status: str,
    tradeoff: str,
    reason: str,
    selected_note: str,
) -> Image.Image:
    canvas = Image.new("RGB", (PANEL, HEADER + PANEL + FOOTER), BACKGROUND)
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 0, PANEL, 10), fill=ACCENT if role != "SOURCE" else "#252b31")
    draw.text((24, 24), role, font=_font(30, bold=True), fill=INK)
    score_text = "—" if score is None else f"{score:.2f}"
    passed = grade in {"A", "B"}
    meta = f"方法：{method}   Quality：{score_text}   等级：{grade}"
    draw.text((24, 68), meta, font=_font(20, bold=True), fill=PASS if passed else FAIL)
    draw.text((24, 102), f"状态：{status}｜{selected_note}", font=_font(18), fill=MUTED)
    detail = f"权衡：{tradeoff}｜{reason}"
    for index, line in enumerate(textwrap.wrap(detail, width=28)[:5]):
        draw.text((24, 132 + index * 22), line, font=_font(16), fill=INK)
    if image_path is not None and image_path.is_file():
        canvas.paste(_fit(image_path, (PANEL, PANEL)), (0, HEADER))
    else:
        draw.rectangle((0, HEADER, PANEL, HEADER + PANEL), fill="#d9dde1")
        draw.text(
            (PANEL // 2 - 74, HEADER + PANEL // 2 - 14),
            "无成功图片",
            font=_font(24),
            fill=FAIL,
        )
    draw.text(
        (20, HEADER + PANEL + 20),
        "A/B=通过" if passed else ("C/D=未通过" if grade in {"C", "D"} else "无等级"),
        font=_font(22, bold=True),
        fill=PASS if passed else FAIL,
    )
    return canvas


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--evaluation-id", required=True)
    parser.add_argument("--agent-run-id", required=True)
    parser.add_argument("--strict-run-id", required=True)
    parser.add_argument("--plan-id", required=True)
    parser.add_argument("--seedream-review-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    output = args.output_dir.resolve()
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    store = LocalArtifactStore(run_dir)
    plan = _json(run_dir / "external-generation" / "plans" / args.plan_id / "plan.json")
    selected = sorted(
        (item for item in plan["entries"] if item["selected_for_paid_generation"]),
        key=lambda item: int(item["paid_priority"]),
    )
    strict_root = run_dir / "strict-reviews" / args.strict_run_id
    agent_decisions = run_dir / "agent-runs" / args.agent_run_id / "decisions"
    seedream_metrics_root = run_dir / "external-generation" / "evaluation" / "metrics"
    seedream_reviews_root = (
        run_dir / "external-generation" / "strict-reviews" / args.seedream_review_id / "reviews"
    )
    rows: list[dict[str, Any]] = []
    for item in selected:
        task_id = str(item["task_id"])
        task = TaskSpec.model_validate(store.read_json(f"tasks/{task_id}.json"))
        source_ref = store.read_json(f"sources/{task.source.source_id}.json")
        source_path = store.path(source_ref["relative_path"])
        rule_id = str(item["rule_selected_candidate_id"])
        rule_metrics = _metrics(run_dir, args.evaluation_id, rule_id)
        rule_review = _strict_review(strict_root, task_id, rule_id)
        agent_decision = _json(agent_decisions / f"{task_id}.json")
        agent_id = str(agent_decision["selected_candidate_id"])
        agent_metrics = _metrics(run_dir, args.evaluation_id, agent_id)
        strict_decision = _json(strict_root / "decisions" / f"{task_id}.json")
        agent_review = _strict_review(strict_root, task_id, agent_id)
        result_path = run_dir / "external-generation" / "results" / f"{task_id}.json"
        result = _json(result_path) if result_path.is_file() else None
        seedream_metric_path = seedream_metrics_root / f"{task_id}--seedream--v1.json"
        seedream_metric = (
            _json(seedream_metric_path)["metrics"] if seedream_metric_path.is_file() else {}
        )
        seedream_review_path = seedream_reviews_root / f"{task_id}.json"
        seedream_review = _json(seedream_review_path) if seedream_review_path.is_file() else None
        candidate_sha256 = (
            result.get("normalization", {}).get("evaluation_sha256") if result else None
        )
        human_feedback = load_aigc_human_calibration_feedback(
            run_dir,
            task_id,
            f"{task_id}--seedream--v1",
            candidate_sha256,
        )
        seedream_grade = (
            human_feedback["human_grade"]
            if human_feedback is not None
            else seedream_review["invocation"]["review"]["overall_grade"]
            if seedream_review is not None
            else _grade_from_proxy(seedream_metric.get("proxy_grade"))
        )
        seedream_grade_source = (
            "human_calibration"
            if human_feedback is not None
            else "qwen4_high_resolution_strict_review"
            if seedream_review is not None
            else "automatic_proxy"
        )
        seedream_image = (
            run_dir / result["evaluation_path"]
            if result is not None and result.get("status") == "success"
            else None
        )
        panels = [
            _panel(
                role="SOURCE｜原图",
                image_path=source_path,
                method="reference",
                score=None,
                grade="N/A",
                status="REFERENCE",
                tradeoff="原始内容与构图基准",
                reason="不参与评分",
                selected_note="不参与候选排名",
            ),
            _panel(
                role="RULE 选择",
                image_path=_candidate_path(run_dir, task_id, rule_id),
                method=_method(rule_id),
                score=float(rule_metrics["quality_score"]),
                grade=_grade_from_proxy(rule_metrics.get("proxy_grade")),
                status=("评分来源：自动 Proxy｜" + str(rule_metrics.get("proxy_grade", "unknown"))),
                tradeoff=_tradeoff(rule_metrics, rule_review),
                reason=_review_reason(rule_review, rule_metrics),
                selected_note="传统规则 Top1",
            ),
            _panel(
                role="AGENT 选择",
                image_path=_candidate_path(run_dir, task_id, agent_id),
                method=_method(agent_id),
                score=float(agent_metrics["quality_score"]),
                grade=str(strict_decision["selected_grade"]),
                status=(
                    "评分来源：高清严格复核｜自动 Proxy="
                    + str(agent_metrics.get("proxy_grade", "unknown"))
                ),
                tradeoff=_tradeoff(agent_metrics, agent_review),
                reason=_review_reason(agent_review, agent_metrics),
                selected_note="与 Rule 同选" if agent_id == rule_id else "Qwen 改选",
            ),
            _panel(
                role="AIGC",
                image_path=seedream_image,
                method="seedream" if seedream_image else "none",
                score=(
                    float(seedream_metric["quality_score"])
                    if seedream_metric.get("quality_score") is not None
                    else None
                ),
                grade=seedream_grade,
                status=(
                    "评分来源："
                    + (
                        "人工Calibration"
                        if human_feedback is not None
                        else "高清严格复核"
                        if seedream_review
                        else "自动 Proxy"
                    )
                    + "｜生成状态="
                    + (str(result.get("status")) if result else "not_run")
                ),
                tradeoff=(
                    "人工确认可直接使用，无可见缺陷"
                    if human_feedback is not None
                    else _tradeoff(seedream_metric, seedream_review)
                    if seedream_metric
                    else (
                        "已生成，待机器评分"
                        if result and result.get("status") == "success"
                        else "生成失败："
                        + (str(result.get("error_code", "尚未运行")) if result else "尚未运行")
                    )
                ),
                reason=(
                    str(human_feedback["note"])
                    if human_feedback is not None
                    else _review_reason(seedream_review, seedream_metric)
                    if seedream_metric
                    else "无成功输出，无法视觉评分"
                ),
                selected_note="生成时延不计入路线比较",
            ),
        ]
        collage = Image.new("RGB", (PANEL * 4, HEADER + PANEL + FOOTER), BACKGROUND)
        for index, panel in enumerate(panels):
            collage.paste(panel, (index * PANEL, 0))
        collage_path = output / "collages" / f"{task_id}.jpg"
        collage_path.parent.mkdir(parents=True, exist_ok=True)
        collage.save(collage_path, quality=94, subsampling=0)
        row = {
            "task_id": task_id,
            "scene_category": task.source.scene_category,
            "split": task.source.split,
            "rule_method": _method(rule_id),
            "rule_quality": rule_metrics["quality_score"],
            "rule_grade": _grade_from_proxy(rule_metrics.get("proxy_grade")),
            "rule_grade_source": "automatic_proxy",
            "rule_tradeoff": _tradeoff(rule_metrics, rule_review),
            "rule_reason": _review_reason(rule_review, rule_metrics),
            "agent_method": _method(agent_id),
            "agent_quality": agent_metrics["quality_score"],
            "agent_grade": strict_decision["selected_grade"],
            "agent_grade_source": "qwen4_high_resolution_strict_review",
            "agent_tradeoff": _tradeoff(agent_metrics, agent_review),
            "agent_reason": _review_reason(agent_review, agent_metrics),
            "same_selection": rule_id == agent_id,
            "aigc_status": result.get("status") if result else "not_run",
            "aigc_quality": seedream_metric.get("quality_score"),
            "aigc_grade": seedream_grade,
            "aigc_grade_source": seedream_grade_source,
            "aigc_tradeoff": (
                "人工确认可直接使用，无可见缺陷"
                if human_feedback is not None
                else _tradeoff(seedream_metric, seedream_review)
                if seedream_metric
                else "无成功图"
            ),
            "aigc_reason": (
                str(human_feedback["note"])
                if human_feedback is not None
                else _review_reason(seedream_review, seedream_metric)
                if seedream_metric
                else "无成功输出，无法视觉评分"
            ),
            "collage": collage_path.relative_to(output).as_posix(),
        }
        task_dir = output / "tasks" / task_id
        task_dir.mkdir(parents=True)
        shutil.copy2(source_path, task_dir / f"00_source{source_path.suffix.lower()}")
        rule_path = _candidate_path(run_dir, task_id, rule_id)
        agent_path = _candidate_path(run_dir, task_id, agent_id)
        shutil.copy2(rule_path, task_dir / f"01_rule_{_method(rule_id)}.png")
        shutil.copy2(agent_path, task_dir / f"02_agent_{_method(agent_id)}.png")
        if seedream_image is not None:
            shutil.copy2(seedream_image, task_dir / "03_aigc_seedream.png")
        shutil.copy2(collage_path, task_dir / "collage.jpg")
        (task_dir / "scoring.json").write_text(
            json.dumps(row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        rows.append(row)
    with (output / "results.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (output / "results.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        "# Movie Visual 60｜Rule–Agent–AIGC 对比",
        "",
        "A/B 视为通过；C/D 视为未通过。Rule 等级为冻结自动 Proxy，Agent 为 Top2 高清严格等级；",
        "AIGC 等级优先级为显式人工Calibration > 高清机器预审 > 自动Proxy。生成时延不计入路线效率。",
        "OCR、主体/人脸/商品/Logo 数量和结构相似度只是辅助证据；与高清完整图冲突时不得单独降级。",
        "",
        "| Task | Rule | Agent | AIGC | 对比图 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row['task_id']} | {row['rule_method']} / {row['rule_grade']} / "
            f"{float(row['rule_quality']):.2f} | {row['agent_method']} / "
            f"{row['agent_grade']} / {float(row['agent_quality']):.2f} | "
            f"{row['aigc_status']} / {row['aigc_grade']} | "
            f"[查看]({row['collage']}) |"
        )
    (output / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"task_count": len(rows), "output_dir": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
