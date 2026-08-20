"""Materialize the user-supplied movie visual dataset into the engine contract."""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import yaml
from PIL import Image, ImageOps

from .datasets import FolderCsvDatasetAdapter

EXPECTED_CATEGORIES = {
    "movie_poster": 15,
    "film_still": 15,
    "video_cover": 15,
    "person": 15,
}
TARGET_ID = "square-1536"
DATASET_ID = "movie-visual-60-v1"
EGRESS_AUTHORIZATION = "user_explicit_2026-08-18_movie_visual60_seedream"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def materialize_movie_visual60(source_root: Path, output_root: Path) -> dict[str, Any]:
    """Copy 60 local research inputs and freeze a deterministic 20/40 split."""

    source_root = source_root.resolve()
    output_root = output_root.resolve()
    manifest_path = source_root / "manifest.csv"
    if not manifest_path.is_file():
        raise FileNotFoundError(manifest_path)
    rows = _read_csv(manifest_path)
    if len(rows) != 60:
        raise ValueError(f"movie visual manifest must contain exactly 60 rows, got {len(rows)}")
    counts = Counter(row["category"] for row in rows)
    if dict(counts) != EXPECTED_CATEGORIES:
        raise ValueError(f"category denominator mismatch: {dict(counts)}")
    ids = [row["id"] for row in rows]
    if len(set(ids)) != len(ids):
        raise ValueError("source IDs must be unique")

    by_category: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_category[row["category"]].append(row)
    calibration_ids = {
        row["id"]
        for category in sorted(by_category)
        for row in sorted(by_category[category], key=lambda item: item["id"])[:5]
    }

    image_root = output_root / "images"
    image_root.mkdir(parents=True, exist_ok=True)
    source_rows: list[dict[str, Any]] = []
    task_rows: list[dict[str, Any]] = []
    provenance_rows: list[dict[str, Any]] = []
    egress_rows: list[dict[str, Any]] = []
    seen_hashes: set[str] = set()
    for row in sorted(rows, key=lambda item: item["id"]):
        source_path = (source_root / row["local_file"]).resolve()
        try:
            source_path.relative_to(source_root)
        except ValueError as error:
            raise ValueError(f"source path escapes dataset root: {row['id']}") from error
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
        digest = _sha256(source_path)
        if digest != row["sha256"]:
            raise ValueError(f"source hash mismatch: {row['id']}")
        if digest in seen_hashes:
            raise ValueError(f"duplicate source pixels: {row['id']}")
        seen_hashes.add(digest)
        with Image.open(source_path) as opened:
            normalized = ImageOps.exif_transpose(opened)
            width, height = normalized.size
            normalized.verify()
        if (width, height) != (int(row["verified_width"]), int(row["verified_height"])):
            raise ValueError(f"source dimensions mismatch: {row['id']}")
        suffix = source_path.suffix.lower()
        if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
            raise ValueError(f"unsupported image extension: {source_path.name}")
        filename = f"{row['id']}{suffix}"
        destination = image_root / filename
        if destination.exists() and _sha256(destination) != digest:
            raise FileExistsError(f"refusing to overwrite different pixels: {destination}")
        if not destination.exists():
            shutil.copyfile(source_path, destination)
        split = "calibration" if row["id"] in calibration_ids else "validation"
        low_resolution = min(width, height) < 1024
        source_rows.append(
            {
                "source_id": row["id"],
                "image_path": f"images/{filename}",
                "width": width,
                "height": height,
                "sha256": digest,
                "split": split,
                "scene_profile": "balanced",
                "enabled": "true",
                "source_kind": "user_authorized_local_real",
                "license_status": "local_research_not_publicly_redistributable",
                "scene_category": row["category"],
                "fixture_type": "",
                "test_purpose": "",
            }
        )
        task_rows.append(
            {
                "task_id": f"{row['id']}__{TARGET_ID}",
                "source_id": row["id"],
                "target_id": TARGET_ID,
                "enabled": "true",
            }
        )
        provenance_rows.append(
            {
                **row,
                "split": split,
                "source_low_resolution": str(low_resolution).lower(),
                "materialized_file": f"images/{filename}",
                "materialized_sha256": digest,
                "redistribution_status": "not_redistributable_local_only",
            }
        )
        egress_rows.append(
            {
                "source_id": row["id"],
                "provider": "seedream_api",
                "api_egress_allowed": "true",
                "authorization_basis": EGRESS_AUTHORIZATION,
                "redistribution_status": "not_redistributable_local_only",
                "public_license_claimed": "false",
                "maximum_outputs_per_task": 1,
            }
        )

    descriptor = {
        "schema_version": "1.0",
        "dataset_id": DATASET_ID,
        "version": "1.0.0",
        "description": "Local movie visuals; user-authorized SeedDream experiment only.",
        "expected_source_count": 60,
        "expected_scene_counts": EXPECTED_CATEGORIES,
        "evaluation_canvas": "1536x1536",
        "generation_originals_may_be_retained_at_2k": True,
        "silent_upsampling_forbidden": True,
    }
    (output_root / "dataset.yaml").write_text(
        yaml.safe_dump(descriptor, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    _write_csv(
        output_root / "sources.csv",
        source_rows,
        [
            "source_id",
            "image_path",
            "width",
            "height",
            "sha256",
            "split",
            "scene_profile",
            "enabled",
            "source_kind",
            "license_status",
            "scene_category",
            "fixture_type",
            "test_purpose",
        ],
    )
    _write_csv(
        output_root / "targets.csv",
        [{"target_id": TARGET_ID, "width": 1536, "height": 1536, "format": "png"}],
        ["target_id", "width", "height", "format"],
    )
    _write_csv(
        output_root / "tasks.csv",
        task_rows,
        ["task_id", "source_id", "target_id", "enabled"],
    )
    _write_csv(
        output_root / "provenance.csv",
        provenance_rows,
        list(provenance_rows[0]),
    )
    _write_csv(
        output_root / "seedream_egress_authorization.csv",
        egress_rows,
        list(egress_rows[0]),
    )
    validation = FolderCsvDatasetAdapter().validate(output_root)
    if not validation.valid:
        raise ValueError(f"materialized dataset is invalid: {validation.errors}")
    summary = {
        "dataset_id": DATASET_ID,
        "source_count": 60,
        "task_count": len(validation.tasks),
        "split_counts": dict(Counter(task.source.split for task in validation.tasks)),
        "scene_counts": dict(Counter(task.source.scene_category for task in validation.tasks)),
        "source_low_resolution_count": sum(
            row["source_low_resolution"] == "true" for row in provenance_rows
        ),
        "api_egress_authorized_count": len(egress_rows),
        "dataset_fingerprint": validation.dataset_fingerprint,
    }
    (output_root / "materialization_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


__all__ = ["EGRESS_AUTHORIZATION", "materialize_movie_visual60"]
