"""Build local review sheets for the CN60 discovery pool."""

from __future__ import annotations

import argparse
import csv
import math
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps


def build(candidate_csv: Path, output_root: Path, per_sheet: int = 25) -> int:
    with candidate_csv.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row["page_id"]].append(row)
    overview_root = output_root / "overviews"
    overview_root.mkdir(parents=True, exist_ok=True)
    columns = 5
    cell_width, cell_height = 300, 300
    for page_id, page_rows in sorted(grouped.items()):
        for offset in range(0, len(page_rows), per_sheet):
            items = page_rows[offset : offset + per_sheet]
            sheet_rows = math.ceil(len(items) / columns)
            canvas = Image.new("RGB", (columns * cell_width, sheet_rows * cell_height), "white")
            draw = ImageDraw.Draw(canvas)
            for index, row in enumerate(items):
                x = (index % columns) * cell_width
                y = (index // columns) * cell_height
                path = output_root / row["local_path"]
                with Image.open(path) as opened:
                    image = ImageOps.exif_transpose(opened).convert("RGB")
                    image.thumbnail((cell_width - 16, cell_height - 58), Image.Resampling.LANCZOS)
                canvas.paste(image, (x + (cell_width - image.width) // 2, y + 48))
                draw.text((x + 8, y + 7), row["candidate_id"], fill="#171717")
                draw.text(
                    (x + 8, y + 25),
                    f"{row['width']}x{row['height']}  {row['scene_hint']}",
                    fill="#5c6269",
                )
                draw.rectangle((x, y, x + cell_width - 1, y + cell_height - 1), outline="#dde1e5")
            sheet = offset // per_sheet + 1
            canvas.save(overview_root / f"{page_id}-{sheet:02d}.jpg", quality=90)
    print(f"overviews={len(list(overview_root.glob('*.jpg')))} rows={len(rows)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--candidate-csv",
        type=Path,
        default=Path("local_data/datasets/retarget_cn60_discovery/candidates.csv"),
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("local_data/datasets/retarget_cn60_discovery"),
    )
    args = parser.parse_args()
    return build(args.candidate_csv.resolve(), args.output_root.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
