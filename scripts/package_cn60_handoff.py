"""Build a local-only CN60 handoff bundle with representative image evidence."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

import yaml
from PIL import Image, ImageDraw, ImageFont

DEFAULT_TASKS = (
    "cn60-poster-01__square-1536",
    "cn60-poster-02__square-1536",
    "cn60-poster-03__square-1536",
    "cn60-poster-04__square-1536",
    "cn60-portrait-01__square-1536",
    "cn60-portrait-02__square-1536",
    "cn60-multi-01__square-1536",
    "cn60-multi-02__square-1536",
    "cn60-product-01__square-1536",
    "cn60-product-02__square-1536",
    "cn60-complex-01__square-1536",
    "cn60-complex-02__square-1536",
)
METHODS = ("direct_warp", "crop", "seam_full", "mesh_full", "seam_scale")
COLORS = {
    "text": "#d6293e",
    "face": "#007d68",
    "person": "#1273de",
    "product": "#e26b0a",
    "logo_candidate": "#8437b5",
}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _font(size: int) -> ImageFont.ImageFont:
    for path in (
        Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path(r"C:\Windows\Fonts\arial.ttf"),
    ):
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def _fit(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    copy = image.copy()
    copy.thumbnail(size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", size, "#eef0f2")
    x = (size[0] - copy.width) // 2
    y = (size[1] - copy.height) // 2
    canvas.paste(copy.convert("RGB"), (x, y))
    return canvas


def _draw_overlay(
    source: Path,
    analysis: dict[str, Any],
    output: Path,
    *,
    semantics: set[str] | None = None,
    show_labels: bool = True,
) -> Counter[str]:
    image = Image.open(source).convert("RGB")
    scale = min(1.0, 1600 / max(image.size))
    if scale < 1.0:
        image = image.resize(
            (round(image.width * scale), round(image.height * scale)), Image.Resampling.LANCZOS
        )
    draw = ImageDraw.Draw(image)
    font = _font(max(14, round(18 * scale)))
    counts: Counter[str] = Counter()
    for region in analysis.get("regions", []):
        semantic = str(
            region.get("attributes", {}).get("semantic_type", region.get("label", "other"))
        )
        if semantics is not None and semantic not in semantics:
            continue
        counts[semantic] += 1
        rect = region["rect"]
        box = tuple(round(float(rect[key]) * scale) for key in ("x1", "y1", "x2", "y2"))
        color = COLORS.get(semantic, "#59636e")
        draw.rectangle(box, outline=color, width=max(2, round(3 * scale)))
        if show_labels:
            recognized = str(region.get("attributes", {}).get("recognized_text", ""))[:16]
            name = recognized or str(region.get("label", semantic))
            label = f"{semantic}:{name} {float(region.get('confidence', 0)):.2f}"
            text_box = draw.textbbox((box[0], box[1]), label, font=font)
            draw.rectangle(text_box, fill=color)
            draw.text((box[0], box[1]), label, fill="white", font=font)
    image.save(output, format="PNG", optimize=True)
    return counts


def _metric_for(evaluation_dir: Path, task_id: str, method: str) -> dict[str, Any]:
    matches = list((evaluation_dir / "metrics").glob(f"{task_id}--{method}--*.json"))
    if len(matches) != 1:
        raise RuntimeError(f"expected one metric for {task_id}/{method}, found {len(matches)}")
    return _load_json(matches[0])


def _number(value: Any) -> str:
    return "—" if value is None else f"{float(value):.3f}"


def _candidate_for(run_dir: Path, task_id: str, method: str) -> tuple[Path, dict[str, Any]]:
    root = run_dir / "candidates" / task_id / method
    return root / "candidate.png", _load_json(root / "candidate.json")


def _build_overview(
    source: Path,
    candidates: list[tuple[str, Path, float]],
    output: Path,
) -> None:
    tile = (460, 460)
    header = 62
    canvas = Image.new("RGB", (tile[0] * 3, (tile[1] + header) * 2), "white")
    draw = ImageDraw.Draw(canvas)
    title_font = _font(24)
    score_font = _font(20)
    entries = [("source", source, None), *candidates]
    ranked = {
        method: index + 1
        for index, (method, _, _) in enumerate(sorted(candidates, key=lambda x: x[2], reverse=True))
    }
    for index, (name, path, score) in enumerate(entries):
        column, row = index % 3, index // 3
        x, y = column * tile[0], row * (tile[1] + header)
        canvas.paste(_fit(Image.open(path), tile), (x, y + header))
        draw.text((x + 14, y + 10), name, fill="#171717", font=title_font)
        if score is not None:
            draw.text(
                (x + 210, y + 14),
                f"#{ranked[name]}  Quality {score:.2f}",
                fill="#b4232c",
                font=score_font,
            )
    canvas.save(output, format="JPEG", quality=94, subsampling=0)


def package(
    repo: Path,
    run_dir: Path,
    evaluation_id: str,
    output: Path,
    *,
    agent_run_id: str | None = None,
    benchmark_id: str | None = None,
) -> None:
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"refusing to overwrite non-empty output: {output}")
    output.mkdir(parents=True, exist_ok=True)
    evaluation_dir = run_dir / "evaluations" / evaluation_id
    selection = yaml.safe_load(
        (repo / "datasets" / "retarget_cn60_v1" / "selection.yaml").read_text(encoding="utf-8")
    )
    selections = {row["source_id"]: row for row in selection["sources"]}
    manifest: dict[str, Any] = {
        "bundle_id": output.name,
        "local_only": True,
        "redistribution_allowed": False,
        "run_id": _load_json(run_dir / "run.json")["run_id"],
        "evaluation_id": evaluation_id,
        "evaluation_scope": "FAST_PROXY_NO_CANDIDATE_DETECTOR_RERUN"
        if "fast" in evaluation_id
        else "FULL_CANDIDATE_DETECTOR_RERUN",
        "agent_run_id": agent_run_id,
        "benchmark_id": benchmark_id,
        "tasks": [],
    }
    agent_dir = run_dir / "agent-runs" / agent_run_id if agent_run_id else None
    representatives = output / "representatives"
    representatives.mkdir()
    for ordinal, task_id in enumerate(DEFAULT_TASKS, start=1):
        task = _load_json(run_dir / "tasks" / f"{task_id}.json")
        source_id = task["source"]["source_id"]
        source_path = run_dir / "sources" / f"{source_id}.png"
        task_out = representatives / f"{ordinal:02d}_{source_id}"
        task_out.mkdir()
        shutil.copy2(source_path, task_out / "source.png")
        analysis_root = run_dir / "analysis" / task_id
        analysis = _load_json(analysis_root / "analysis.json")
        shutil.copy2(analysis_root / "importance.png", task_out / "importance.png")
        shutil.copy2(analysis_root / "tolerance.png", task_out / "tolerance.png")
        region_counts = _draw_overlay(
            source_path,
            analysis,
            task_out / "protection_overlay.png",
            show_labels=False,
        )
        _draw_overlay(
            source_path,
            analysis,
            task_out / "ocr_overlay.png",
            semantics={"text"},
        )
        _draw_overlay(
            source_path,
            analysis,
            task_out / "people_overlay.png",
            semantics={"face", "person"},
        )
        _draw_overlay(
            source_path,
            analysis,
            task_out / "product_logo_overlay.png",
            semantics={"product", "logo_candidate"},
        )
        candidate_rows: list[dict[str, Any]] = []
        overview_rows: list[tuple[str, Path, float]] = []
        for method in METHODS:
            candidate_path, candidate = _candidate_for(run_dir, task_id, method)
            metric = _metric_for(evaluation_dir, task_id, method)
            output_name = f"candidate_{method}.png"
            shutil.copy2(candidate_path, task_out / output_name)
            metrics = metric["metrics"]
            row = {
                "candidate_id": candidate["candidate_id"],
                "method_id": method,
                "filename": output_name,
                "generation_status": candidate["generation_status"],
                "generation_wall_seconds": candidate["performance"]["wall_seconds"],
                "quality_score": metrics["quality_score"],
                "proxy_grade": metrics["proxy_grade"],
                "proxy_business_success": metrics["proxy_business_success"],
                "content_fidelity_score": metrics["content_fidelity_score"],
                "structure_line_similarity": metrics["structure_line_similarity"],
                "transform_safety_score": metrics["transform_safety_score"],
                "warnings": candidate.get("warnings", []),
            }
            candidate_rows.append(row)
            overview_rows.append((method, task_out / output_name, float(metrics["quality_score"])))
        candidate_rows.sort(key=lambda row: row["quality_score"], reverse=True)
        (task_out / "metrics.json").write_text(
            json.dumps(candidate_rows, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _build_overview(source_path, overview_rows, task_out / "overview_ranked.jpg")
        selected = selections[source_id]
        lines = [
            f"# {source_id}",
            "",
            f"- 场景：`{selected['scene_category']}`",
            f"- 切分：`{selected['split']}`",
            f"- 选图目的：{selected['review_reason']}",
            f"- 保护检测：{dict(region_counts)}",
            "- 评分性质：未做人评校准的机器代理分；只用于排序检查，不等于 A/B/C/D。",
            "",
            "| 排名 | 方法 | Quality | 状态 | 耗时(s) | 评分原因摘要 |",
            "| ---: | --- | ---: | --- | ---: | --- |",
        ]
        for rank, row in enumerate(candidate_rows, start=1):
            reason = (
                f"内容 {_number(row['content_fidelity_score'])}；结构 "
                f"{_number(row['structure_line_similarity'])}；变换安全 "
                f"{_number(row['transform_safety_score'])}"
            )
            if row["warnings"]:
                reason += "；风险：" + " / ".join(row["warnings"])
            lines.append(
                f"| {rank} | {row['method_id']} | {row['quality_score']:.2f} | "
                f"{row['generation_status']} | {row['generation_wall_seconds']:.3f} | {reason} |"
            )
        agent_summary: dict[str, Any] | None = None
        if agent_dir is not None:
            decision = _load_json(agent_dir / "decisions" / f"{task_id}.json")
            shutil.copy2(
                agent_dir / "decisions" / f"{task_id}.json",
                task_out / "agent_decision.json",
            )
            selected_row = next(
                row
                for row in candidate_rows
                if row["candidate_id"] == decision["selected_candidate_id"]
            )
            agent_summary = {
                "selected_method": selected_row["method_id"],
                "schema_valid": decision["agent_schema_valid"],
                "changed_rule_top1": decision["changed_top1"],
                "reason_codes": decision["reason_codes"],
            }
            lines.extend(
                [
                    "",
                    "## 大模型预审（非人工金标准）",
                    "",
                    f"- 选择：`{agent_summary['selected_method']}`",
                    f"- Schema 有效：`{agent_summary['schema_valid']}`",
                    f"- 是否覆盖规则 Top-1：`{agent_summary['changed_rule_top1']}`",
                    "- 原因码：" + "、".join(agent_summary["reason_codes"]),
                ]
            )
        (task_out / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        manifest["tasks"].append(
            {
                "task_id": task_id,
                "source_id": source_id,
                "scene_category": selected["scene_category"],
                "split": selected["split"],
                "directory": str(task_out.relative_to(output)),
                "region_counts": dict(region_counts),
                "machine_ranking": [row["method_id"] for row in candidate_rows],
                "agent_pre_review": agent_summary,
            }
        )
    docs_out = output / "documentation"
    docs_out.mkdir()
    for relative in (
        "README.md",
        "docs/ARCHITECTURE.md",
        "docs/ALGORITHMS.md",
        "docs/SCORING.md",
        "docs/DATASET_CN60.md",
        "docs/REVIEW_GUIDE.md",
        "docs/EXPERIMENT_PROTOCOL.md",
        "docs/HANDOFF.md",
        "docs/CURRENT_STATE.md",
        "docs/ROADMAP.md",
        "docs/reports/CN60_PROTOTYPE_REPORT.md",
    ):
        source = repo / relative
        if source.is_file():
            shutil.copy2(source, docs_out / source.name)
    ui_qa = repo / "local_data" / "ui-qa"
    for screenshot_name in (
        "review-ui-top.png",
        "review-ui-candidates.png",
        "review-ui-top-final.png",
        "review-ui-candidates-final.png",
        "review-ui-cards-final.png",
    ):
        screenshot = ui_qa / screenshot_name
        if screenshot.exists():
            shutil.copy2(screenshot, docs_out / screenshot_name)
    evidence_out = output / "machine_evidence"
    evidence_out.mkdir()
    for source, target_name in (
        (evaluation_dir / "evaluation.json", "evaluation.json"),
        (evaluation_dir / "summary.json", "evaluation-summary.json"),
        (
            run_dir / "agent-runs" / "cn60-rule-only-v1p2-20260818" / "summary.json",
            "rule-summary.json",
        ),
    ):
        if source.exists():
            shutil.copy2(source, evidence_out / target_name)
    if agent_dir is not None:
        for source_name in ("agent-run.json", "summary.json"):
            source = agent_dir / source_name
            if source.exists():
                shutil.copy2(source, evidence_out / f"agent-{source_name}")
    if benchmark_id is not None:
        benchmark = run_dir / "benchmarks" / benchmark_id / "report.json"
        if benchmark.exists():
            shutil.copy2(benchmark, evidence_out / "benchmark-report.json")
    subprocess.run(
        [
            "git",
            "archive",
            "--format=zip",
            f"--output={output / 'retarget-engine-source.zip'}",
            "HEAD",
        ],
        cwd=repo,
        check=True,
    )
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output / "README.md").write_text(
        "# Retarget Engine CN60 本地交接包\n\n"
        "此包含受限的本地评测像素，**不得上传 GitHub、Release、HF Dataset 或第三方 API**。\n\n"
        "- `representatives/`：12 个场景文件夹；每个含原图、五个独立候选、"
        "排名总览、OCR/人脸人物/商品Logo保护图和评分原因。\n"
        "- `documentation/`：架构、算法、数据、评审、实验和交接文档。\n"
        "- `machine_evidence/`：最终 Evaluation、规则、Agent 与完整分母 Benchmark JSON。\n"
        "- `retarget-engine-source.zip`：当前 Git HEAD 的可共享源码快照，不含本地像素和 Run。\n"
        "- `manifest.json`：包级分母、场景、机器排名和权利声明。\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--evaluation-id", required=True)
    parser.add_argument("--agent-run-id")
    parser.add_argument("--benchmark-id")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    package(
        args.repo.resolve(),
        args.run_dir.resolve(),
        args.evaluation_id,
        args.output.resolve(),
        agent_run_id=args.agent_run_id,
        benchmark_id=args.benchmark_id,
    )
    print(args.output.resolve())


if __name__ == "__main__":
    main()
