"""Build 14 auditable representative folders and contact sheets for AIGC30."""

from __future__ import annotations

import csv
import json
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parents[1]
AIGC = ROOT / "runs/aigc30-seedream5-v3-20260812"
BENCH = AIGC / "benchmarks/aigc30-api-only-v1"
OUTPUT = ROOT / "local_data/deliverables/retarget-agent-aigc30-20260812-v7"
RUNS = {
    "pilot60": ROOT / "runs/square-public-v2-pilot60-20260812",
    "heldout240": ROOT / "runs/square-public-v2-heldout240-20260812",
}
EVALUATIONS = {
    "pilot60": "auto-proxy-v1p1-pilot60-20260812",
    "heldout240": "auto-proxy-v1p1-heldout240-20260812",
}
Q4 = {
    "pilot60": "agent-qwen3vl4b-conditional-pilot-v1",
    "heldout240": "agent-qwen3vl4b-conditional-heldout-v1",
}
METHODS = ("direct_warp", "crop", "seam", "mesh")


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _fit(path: Path, size: tuple[int, int]) -> Image.Image:
    with Image.open(path) as opened:
        return ImageOps.contain(ImageOps.exif_transpose(opened).convert("RGB"), size)


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in (Path("C:/Windows/Fonts/msyh.ttc"), Path("C:/Windows/Fonts/arial.ttf")):
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


def _metric_rows(run: Path, evaluation: str, task_id: str) -> dict[str, dict[str, Any]]:
    rows = {}
    for path in (run / "evaluations" / evaluation / "metrics").glob(f"{task_id}--*.json"):
        payload = _json(path)
        rows[payload["candidate_id"].split("--")[-2]] = payload["metrics"]
    return rows


def _copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _comparison(cells: list[tuple[str, Path]], output: Path) -> None:
    tile = 360
    label = 54
    canvas = Image.new("RGB", (tile * 4, (tile + label) * 2), "#F4F6FA")
    draw = ImageDraw.Draw(canvas)
    font = _font(20)
    for index, (title, path) in enumerate(cells):
        x = (index % 4) * tile
        y = (index // 4) * (tile + label)
        image = _fit(path, (tile - 12, tile - 12))
        canvas.paste(image, (x + (tile - image.width) // 2, y + (tile - image.height) // 2))
        draw.text((x + 10, y + tile + 8), title, fill="#172033", font=font)
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output, format="PNG", optimize=True)


def main() -> None:
    if OUTPUT.exists():
        raise FileExistsError(OUTPUT)
    with (BENCH / "tasks.csv").open("r", encoding="utf-8-sig", newline="") as handle:
        task_rows = list(csv.DictReader(handle))
    with (ROOT / "datasets/retarget_square_public_v2/aigc30_source_audit.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as handle:
        audit = {row["task_id"]: row for row in csv.DictReader(handle)}
    by_scene: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in task_rows:
        by_scene[row["scene_category"]].append(row)
    selected: list[dict[str, str]] = []
    for _scene, rows in by_scene.items():
        successes = [row for row in rows if row["aigc_status"] == "success"]
        failures = [row for row in rows if row["aigc_status"] != "success"]
        successes.sort(key=lambda row: float(row["aigc_quality_score"]), reverse=True)
        selected.append(successes[0])
        selected.append(failures[0] if failures else successes[-1])

    with (ROOT / "docs/reviews/aigc30-codex-visual-review.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as handle:
        reviews = {row["task_id"]: row for row in csv.DictReader(handle)}

    overview_cells: list[tuple[str, Path]] = []
    index_rows: list[dict[str, Any]] = []
    for index, row in enumerate(selected, 1):
        task_id = row["task_id"]
        source_id = task_id.split("__", 1)[0]
        split = (
            "pilot60" if (RUNS["pilot60"] / "tasks" / f"{task_id}.json").is_file() else "heldout240"
        )
        run = RUNS[split]
        task = _json(run / "tasks" / f"{task_id}.json")
        source_ref = _json(run / "sources" / f"{source_id}.json")
        metrics = _metric_rows(run, EVALUATIONS[split], task_id)
        ranked = sorted(METHODS, key=lambda method: metrics[method]["quality_score"], reverse=True)
        decision = _json(run / "agent-runs" / Q4[split] / "decisions" / f"{task_id}.json")
        q4_method = decision["selected_candidate_id"].split("--")[-2]
        result = _json(AIGC / "results" / f"{task_id}.json")
        aigc_metric = _json(
            AIGC
            / "evaluations/auto-proxy-v1p1-aigc30-20260812/metrics"
            / f"{task_id}--seedream5.json"
        )["metrics"]
        folder = OUTPUT / "representatives" / f"{index:02d}_{row['scene_category']}_{source_id}"
        source_out = folder / "00_source.png"
        _copy(run / source_ref["relative_path"], source_out)
        cells: list[tuple[str, Path]] = [("Source", source_out)]
        for rank, method in enumerate(ranked, 1):
            candidate = _json(run / "candidates" / task_id / method / "candidate.json")
            target = folder / (
                f"{rank:02d}_rank{rank}_{method}_score-{metrics[method]['quality_score']:.2f}.png"
            )
            _copy(run / candidate["output"]["relative_path"], target)
            cells.append((f"#{rank} {method} {metrics[method]['quality_score']:.1f}", target))
        q4_candidate = _json(run / "candidates" / task_id / q4_method / "candidate.json")
        q4_out = folder / (
            f"05_qwen4-selected_{q4_method}_score-{metrics[q4_method]['quality_score']:.2f}.png"
        )
        _copy(run / q4_candidate["output"]["relative_path"], q4_out)
        cells.append((f"Qwen4 {q4_method} {metrics[q4_method]['quality_score']:.1f}", q4_out))
        if result["status"] == "success":
            _copy(
                AIGC / result["provider_output_path"],
                folder / "06a_seedream5-full-2048.jpg",
            )
            aigc_out = folder / f"06_seedream5_score-{aigc_metric['quality_score']:.2f}.png"
            _copy(AIGC / result["evaluation_image_path"], aigc_out)
            cells.append((f"SeedDream5 {aigc_metric['quality_score']:.1f}", aigc_out))
            reason = (
                f"SeedDream succeeded. Proxy {aigc_metric['proxy_grade']}; quality "
                f"{aigc_metric['quality_score']:.2f}; OCR recall "
                f"{aigc_metric.get('ocr_character_recall')}; structure "
                f"{aigc_metric.get('structure_line_similarity')}."
            )
        else:
            placeholder = Image.new("RGB", (1024, 1024), "#F8E8E8")
            draw = ImageDraw.Draw(placeholder)
            draw.text(
                (70, 430),
                f"SeedDream5 failed\n{result['error_code']}\nNo retry",
                fill="#9B1C1C",
                font=_font(40),
            )
            aigc_out = folder / f"06_seedream5_failed-{result['error_code']}.png"
            placeholder.save(aigc_out, format="PNG")
            cells.append((f"SeedDream5 FAIL {result['error_code']}", aigc_out))
            reason = (
                f"SeedDream failed with {result['error_code']}; it remains a failure in the "
                "30-task denominator and was not retried. The hybrid falls back to the "
                f"Qwen4-selected {q4_method} candidate."
            )
        _comparison(cells, folder / "07_comparison.png")
        metadata = {
            "task": task,
            "audit": audit[task_id],
            "traditional_ranking": [
                {
                    "rank": rank,
                    "method": method,
                    "metrics": metrics[method],
                }
                for rank, method in enumerate(ranked, 1)
            ],
            "qwen4_decision": decision,
            "seedream_result": result,
            "seedream_metrics": aigc_metric,
            "automatic_reason": reason,
            "codex_visual_review": reviews[task_id],
        }
        (folder / "metrics-and-provenance.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (folder / "README.md").write_text(
            f"# {task_id}\n\n"
            f"- Scene: `{row['scene_category']}`\n"
            f"- Difficulty: `{row['difficulty_tier']}`\n"
            f"- License: `{audit[task_id]['license']}`\n"
            f"- Attribution: {audit[task_id]['attribution']}\n"
            f"- Qwen4 action: `{row['qwen4_route_action']}`\n"
            f"- Hybrid source: `{row['hybrid_source']}`\n\n"
            f"## Automatic scoring reason\n\n{reason}\n\n"
            "## Codex visual review\n\n"
            f"- Score: **{reviews[task_id]['codex_score_100']}/100**\n"
            f"- Verdict: `{reviews[task_id]['verdict']}`\n"
            f"- Reason: {reviews[task_id]['reason']}\n\n"
            "Scores are uncalibrated automatic proxy evidence. See the technical report for "
            "metric definitions and limitations.\n",
            encoding="utf-8",
        )
        overview_cells.append(
            (f"{index:02d} {row['scene_category']}", folder / "07_comparison.png")
        )
        index_rows.append(
            {
                "index": index,
                "task_id": task_id,
                "scene_category": row["scene_category"],
                "difficulty_tier": row["difficulty_tier"],
                "aigc_status": result["status"],
                "aigc_quality_score": aigc_metric.get("quality_score"),
                "qwen4_method": q4_method,
                "qwen4_quality_score": metrics[q4_method]["quality_score"],
                "reason": reason,
                "codex_score_100": reviews[task_id]["codex_score_100"],
                "codex_verdict": reviews[task_id]["verdict"],
                "codex_reason": reviews[task_id]["reason"],
            }
        )

    for sheet_index in range(0, len(overview_cells), 2):
        pair = overview_cells[sheet_index : sheet_index + 2]
        canvas = Image.new("RGB", (1440, 1670 * len(pair)), "white")
        for offset, (title, path) in enumerate(pair):
            image = _fit(path, (1440, 1620))
            canvas.paste(image, (0, offset * 1670 + 50))
            ImageDraw.Draw(canvas).text(
                (20, offset * 1670 + 10), title, fill="#111827", font=_font(24)
            )
        (OUTPUT / "overviews").mkdir(parents=True, exist_ok=True)
        canvas.save(
            OUTPUT / "overviews" / f"scene-overview-{sheet_index // 2 + 1:02d}.jpg",
            format="JPEG",
            quality=88,
        )
    with (OUTPUT / "representative-index.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(index_rows[0]))
        writer.writeheader()
        writer.writerows(index_rows)
    print(json.dumps({"output": str(OUTPUT), "representatives": len(selected)}, indent=2))


if __name__ == "__main__":
    main()
