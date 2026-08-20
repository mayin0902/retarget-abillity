from __future__ import annotations

import csv
from pathlib import Path

from retarget_agent.datasets import FolderCsvDatasetAdapter
from retarget_agent.fixtures import materialize_fixture_dataset


def _rewrite_cell(path: Path, row_index: int, field: str, value: str) -> None:
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
        fieldnames = list(rows[0])
    rows[row_index][field] = value
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_programmatic_smoke_dataset_has_24_tasks(tmp_path: Path) -> None:
    root = materialize_fixture_dataset(tmp_path / "fixture")
    first = FolderCsvDatasetAdapter().validate(root)
    second = FolderCsvDatasetAdapter().validate(root)
    assert first.valid, first.errors
    assert len(first.tasks) == 24
    assert len({task.source.source_id for task in first.tasks}) == 12
    assert first.dataset_fingerprint == second.dataset_fingerprint


def test_hash_mismatch_stops_affected_source(tmp_path: Path) -> None:
    root = materialize_fixture_dataset(tmp_path / "fixture")
    _rewrite_cell(root / "sources.csv", 0, "sha256", "0" * 64)
    result = FolderCsvDatasetAdapter().validate(root)
    assert not result.valid
    assert any("sha256 mismatch" in error for error in result.errors)


def test_path_escape_is_rejected(tmp_path: Path) -> None:
    root = materialize_fixture_dataset(tmp_path / "fixture")
    _rewrite_cell(root / "sources.csv", 0, "image_path", "../outside.png")
    result = FolderCsvDatasetAdapter().validate(root)
    assert not result.valid
    assert any("dataset root" in error for error in result.errors)
