from __future__ import annotations

import csv
from pathlib import Path

import pytest
import yaml

from retarget_agent.cn60 import load_cn60_plan


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    candidates = tmp_path / "candidates.csv"
    with candidates.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "candidate_id",
                "official_source_name",
                "official_page_url",
                "asset_url",
                "sha256",
                "local_path",
            ),
        )
        writer.writeheader()
        for index in range(60):
            writer.writerow(
                {
                    "candidate_id": f"candidate-{index:02d}",
                    "official_source_name": "official",
                    "official_page_url": "https://official.example/page",
                    "asset_url": f"https://official.example/{index}.jpg",
                    "sha256": f"{index:064x}",
                    "local_path": f"assets/{index}.jpg",
                }
            )
    sources = []
    for index in range(60):
        category = "poster" if index < 30 else "portrait"
        split = "calibration" if index < 20 else "validation"
        sources.append(
            {
                "source_id": f"source-{index:02d}",
                "candidate_id": f"candidate-{index:02d}",
                "scene_category": category,
                "split": split,
                "review_reason": "reviewed",
            }
        )
    selection = tmp_path / "selection.yaml"
    selection.write_text(
        yaml.safe_dump(
            {
                "dataset_id": "dataset-v1",
                "target": {
                    "target_id": "square-1536",
                    "width": 1536,
                    "height": 1536,
                    "format": "png",
                },
                "rights_defaults": {
                    "redistribution_status": "not_redistributable_local_only",
                    "api_egress_allowed": False,
                },
                "expected_scene_counts": {"poster": 30, "portrait": 30},
                "expected_split_counts": {"calibration": 20, "validation": 40},
                "sources": sources,
            }
        ),
        encoding="utf-8",
    )
    return selection, candidates


def test_load_cn60_plan_freezes_exact_denominator(tmp_path: Path) -> None:
    selection, candidates = _fixture(tmp_path)
    plan = load_cn60_plan(selection, candidates)
    assert len(plan.sources) == 60
    assert plan.target_width == plan.target_height == 1536
    assert plan.sources[0].split == "calibration"


def test_load_cn60_plan_rejects_api_egress(tmp_path: Path) -> None:
    selection, candidates = _fixture(tmp_path)
    payload = yaml.safe_load(selection.read_text(encoding="utf-8"))
    payload["rights_defaults"]["api_egress_allowed"] = True
    selection.write_text(yaml.safe_dump(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="third-party egress"):
        load_cn60_plan(selection, candidates)
