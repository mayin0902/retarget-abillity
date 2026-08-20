from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _fit(path: Path, size: tuple[int, int]) -> Image.Image:
    with Image.open(path) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
    contained = ImageOps.contain(image, size, Image.Resampling.LANCZOS)
    panel = Image.new("RGB", size, (18, 18, 18))
    panel.paste(contained, ((size[0] - contained.width) // 2, (size[1] - contained.height) // 2))
    return panel


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in (Path("C:/Windows/Fonts/msyh.ttc"), Path("C:/Windows/Fonts/arial.ttf")):
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--evaluation-id", required=True)
    parser.add_argument("--strict-run-id", required=True)
    parser.add_argument("--plan-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    run = args.run_dir.resolve()
    output = args.output_dir.resolve()
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    plan = _json(run / "external-generation" / "plans" / args.plan_id / "plan.json")
    rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    candidates: dict[str, dict[str, Any]] = {}
    for path in (run / "candidates").glob("*/*/candidate.json"):
        candidate = _json(path)
        candidates[candidate["candidate_id"]] = candidate
    for entry in plan["entries"]:
        if entry["split"] != "calibration":
            continue
        task_id = entry["task_id"]
        task = _json(run / "tasks" / f"{task_id}.json")
        source = _json(run / "sources" / f"{task['source']['source_id']}.json")
        strict = _json(
            run / "strict-reviews" / args.strict_run_id / "decisions" / f"{task_id}.json"
        )
        rule_id = entry["rule_selected_candidate_id"]
        qwen_id = strict["selected_candidate_id"]
        rule_metric = _json(
            run / "evaluations" / args.evaluation_id / "metrics" / f"{rule_id}.json"
        )["metrics"]
        qwen_metric = _json(
            run / "evaluations" / args.evaluation_id / "metrics" / f"{qwen_id}.json"
        )["metrics"]
        rows[entry["scene_category"]].append(
            {
                "task_id": task_id,
                "source": run / source["relative_path"],
                "rule": run / candidates[rule_id]["output"]["relative_path"],
                "qwen": run / candidates[qwen_id]["output"]["relative_path"],
                "rule_method": candidates[rule_id]["method_id"],
                "qwen_method": candidates[qwen_id]["method_id"],
                "rule_quality": rule_metric["quality_score"],
                "qwen_quality": qwen_metric["quality_score"],
                "qwen_strict_grade": strict["selected_grade"],
            }
        )
    panel_size = (640, 420)
    header = 58
    task_font = _font(24)
    label_font = _font(19)
    index: list[dict[str, Any]] = []
    for category, items in sorted(rows.items()):
        canvas = Image.new(
            "RGB",
            (panel_size[0] * 3, len(items) * (panel_size[1] + header)),
            (245, 245, 245),
        )
        draw = ImageDraw.Draw(canvas)
        for row_index, item in enumerate(sorted(items, key=lambda value: value["task_id"])):
            y = row_index * (panel_size[1] + header)
            draw.text((12, y + 6), item["task_id"], fill=(20, 20, 20), font=task_font)
            labels = (
                "SOURCE（保持原比例）",
                f"RULE {item['rule_method']}  Q={item['rule_quality']:.2f}",
                f"QWEN {item['qwen_method']}  Q={item['qwen_quality']:.2f}  "
                f"严格={item['qwen_strict_grade']}",
            )
            paths = (item["source"], item["rule"], item["qwen"])
            for column, (label, path) in enumerate(zip(labels, paths, strict=True)):
                x = column * panel_size[0]
                draw.text((x + 12, y + 33), label, fill=(50, 50, 50), font=label_font)
                canvas.paste(_fit(path, panel_size), (x, y + header))
            index.append(
                {
                    key: value
                    for key, value in item.items()
                    if key not in {"source", "rule", "qwen"}
                }
            )
        canvas.save(output / f"{category}.jpg", format="JPEG", quality=94, subsampling=0)
    (output / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"task_count": len(index), "categories": sorted(rows)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
