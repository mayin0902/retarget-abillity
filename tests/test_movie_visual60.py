from __future__ import annotations

import csv
import hashlib
from pathlib import Path

from PIL import Image

from retarget_agent.movie_visual60 import EXPECTED_CATEGORIES, materialize_movie_visual60


def test_materialize_movie_visual60_freezes_balanced_20_40_split(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    rows = []
    index = 0
    for category, count in EXPECTED_CATEGORIES.items():
        folder = source / category
        folder.mkdir()
        for offset in range(count):
            source_id = f"{category}-{offset:02d}"
            path = folder / f"{source_id}.png"
            Image.new("RGB", (1100 + offset, 900 + offset), (index, 20, 40)).save(path)
            payload = path.read_bytes()
            rows.append(
                {
                    "id": source_id,
                    "category": category,
                    "title": source_id,
                    "description": "fixture",
                    "platform_context": "fixture",
                    "source_site": "fixture",
                    "source_page_url": "https://example.test/page",
                    "reference_page_url": "https://example.test/reference",
                    "asset_url": "https://example.test/image.png",
                    "local_file": path.relative_to(source).as_posix(),
                    "expected_width": 1100 + offset,
                    "expected_height": 900 + offset,
                    "retrieval_date": "2026-08-18",
                    "rights_note": "local test",
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "bytes": len(payload),
                    "mime_type": "image/png",
                    "verified_width": 1100 + offset,
                    "verified_height": 900 + offset,
                    "image_format": "PNG",
                }
            )
            index += 1
    with (source / "manifest.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    result = materialize_movie_visual60(source, tmp_path / "output")

    assert result["source_count"] == 60
    assert result["task_count"] == 60
    assert result["split_counts"] == {"calibration": 20, "validation": 40}
    assert result["api_egress_authorized_count"] == 60
