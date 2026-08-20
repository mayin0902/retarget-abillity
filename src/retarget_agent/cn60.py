"""Audited local-only materialization for the Chinese-scene CN60 benchmark."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import requests
import yaml
from PIL import Image, ImageDraw, ImageOps

from .datasets import FolderCsvDatasetAdapter
from .hashing import sha256_file

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"
)


@dataclass(frozen=True)
class Cn60SourcePlan:
    source_id: str
    candidate_id: str
    scene_category: str
    split: str
    review_reason: str
    official_source_name: str
    official_page_url: str
    asset_url: str
    source_raw_sha256: str
    discovery_cache_path: Path


@dataclass(frozen=True)
class Cn60Plan:
    dataset_id: str
    target_id: str
    target_width: int
    target_height: int
    target_format: str
    sources: tuple[Cn60SourcePlan, ...]
    expected_scene_counts: dict[str, int]
    expected_split_counts: dict[str, int]


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def load_cn60_plan(selection_path: Path, candidate_csv: Path) -> Cn60Plan:
    raw = yaml.safe_load(selection_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("sources"), list):
        raise ValueError("selection must contain a sources list")
    candidate_rows = _read_csv(candidate_csv)
    by_id = {row.get("candidate_id", ""): row for row in candidate_rows}
    if "" in by_id or len(by_id) != len(candidate_rows):
        raise ValueError("candidate pool contains blank or duplicate candidate IDs")

    rights = raw.get("rights_defaults") or {}
    if rights.get("redistribution_status") != "not_redistributable_local_only":
        raise ValueError("CN60 commercial sources must remain local-only")
    if rights.get("api_egress_allowed") is not False:
        raise ValueError("CN60 commercial sources must fail closed for third-party egress")

    plans: list[Cn60SourcePlan] = []
    for item in raw["sources"]:
        if not isinstance(item, dict):
            raise ValueError("each selected source must be a mapping")
        candidate_id = str(item.get("candidate_id", ""))
        candidate = by_id.get(candidate_id)
        if candidate is None:
            raise ValueError(f"selected candidate is absent from discovery pool: {candidate_id}")
        plans.append(
            Cn60SourcePlan(
                source_id=str(item.get("source_id", "")),
                candidate_id=candidate_id,
                scene_category=str(item.get("scene_category", "")),
                split=str(item.get("split", "")),
                review_reason=str(item.get("review_reason", "")),
                official_source_name=candidate["official_source_name"],
                official_page_url=candidate["official_page_url"],
                asset_url=candidate["asset_url"],
                source_raw_sha256=candidate["sha256"],
                discovery_cache_path=(candidate_csv.parent / candidate["local_path"]).resolve(),
            )
        )

    source_ids = [item.source_id for item in plans]
    candidate_ids = [item.candidate_id for item in plans]
    if len(plans) != 60 or len(set(source_ids)) != 60 or len(set(candidate_ids)) != 60:
        raise ValueError("CN60 selection must contain exactly 60 unique sources and candidates")
    if any(not item.review_reason for item in plans):
        raise ValueError("every selected source requires a review reason")
    expected_scene_counts = {
        str(key): int(value) for key, value in raw["expected_scene_counts"].items()
    }
    expected_split_counts = {
        str(key): int(value) for key, value in raw["expected_split_counts"].items()
    }
    if Counter(item.scene_category for item in plans) != Counter(expected_scene_counts):
        raise ValueError("selected scene counts do not match the frozen policy")
    if Counter(item.split for item in plans) != Counter(expected_split_counts):
        raise ValueError("selected split counts do not match the frozen policy")

    target = raw.get("target") or {}
    width, height = int(target.get("width", 0)), int(target.get("height", 0))
    if (width, height) != (1536, 1536):
        raise ValueError("CN60 v1 is frozen to a 1536x1536 target")
    return Cn60Plan(
        dataset_id=str(raw.get("dataset_id", "")),
        target_id=str(target.get("target_id", "")),
        target_width=width,
        target_height=height,
        target_format=str(target.get("format", "png")),
        sources=tuple(plans),
        expected_scene_counts=expected_scene_counts,
        expected_split_counts=expected_split_counts,
    )


def _download_and_normalize(
    plan: Cn60SourcePlan,
    prefer_discovery_cache: bool = False,
) -> tuple[Cn60SourcePlan, bytes, int, int, str, str]:
    materialization_mode = "fresh_official_download"
    if prefer_discovery_cache:
        if not plan.discovery_cache_path.is_file():
            raise FileNotFoundError(f"discovery cache is missing: {plan.source_id}")
        source_bytes = plan.discovery_cache_path.read_bytes()
        materialization_mode = "verified_discovery_cache_requested"
    else:
        try:
            response = requests.get(
                plan.asset_url,
                headers={"User-Agent": USER_AGENT, "Referer": plan.official_page_url},
                timeout=30,
            )
            response.raise_for_status()
            source_bytes = response.content
            if len(source_bytes) > 30 * 1024 * 1024:
                raise ValueError(f"source exceeds the 30 MiB safety limit: {plan.source_id}")
            actual_raw_hash = hashlib.sha256(source_bytes).hexdigest()
            if actual_raw_hash != plan.source_raw_sha256:
                raise ValueError(f"official source bytes changed: {plan.source_id}")
        except requests.RequestException:
            if not plan.discovery_cache_path.is_file():
                raise
            source_bytes = plan.discovery_cache_path.read_bytes()
            materialization_mode = "verified_discovery_cache_after_network_failure"
    cache_or_raw_hash = hashlib.sha256(source_bytes).hexdigest()
    with Image.open(io.BytesIO(source_bytes)) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
        width, height = image.size
        output = io.BytesIO()
        image.save(output, format="PNG", compress_level=6)
    return plan, output.getvalue(), width, height, materialization_mode, cache_or_raw_hash


def _write_immutable(path: Path, content: bytes) -> None:
    if path.exists():
        if path.read_bytes() != content:
            raise FileExistsError(f"refusing to overwrite different artifact: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(content)
    temporary.replace(path)


def _csv_bytes(rows: list[dict[str, Any]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def _overview(records: list[dict[str, Any]], dataset_root: Path) -> None:
    columns, cell_width, cell_height = 5, 300, 300
    rows = (len(records) + columns - 1) // columns
    canvas = Image.new("RGB", (columns * cell_width, rows * cell_height), "white")
    draw = ImageDraw.Draw(canvas)
    for index, record in enumerate(records):
        x, y = (index % columns) * cell_width, (index // columns) * cell_height
        with Image.open(dataset_root / "images" / record["local_filename"]) as opened:
            image = opened.convert("RGB")
            image.thumbnail((cell_width - 16, cell_height - 58), Image.Resampling.LANCZOS)
        canvas.paste(image, (x + (cell_width - image.width) // 2, y + 48))
        draw.text((x + 8, y + 7), record["source_id"], fill="#171717")
        draw.text((x + 8, y + 25), record["scene_category"], fill="#5c6269")
        draw.rectangle((x, y, x + cell_width - 1, y + cell_height - 1), outline="#dde1e5")
    target = dataset_root / "source_overview.jpg"
    output = io.BytesIO()
    canvas.save(output, format="JPEG", quality=90)
    _write_immutable(target, output.getvalue())


def materialize_cn60(
    selection_path: Path,
    candidate_csv: Path,
    output_root: Path,
    audit_manifest_path: Path,
    *,
    access_date: date | None = None,
    workers: int = 8,
    prefer_discovery_cache: bool = False,
) -> dict[str, Any]:
    """Download, verify, normalize and freeze the exact local-only CN60 denominator."""

    plan = load_cn60_plan(selection_path.resolve(), candidate_csv.resolve())
    output_root = output_root.resolve()
    audit_manifest_path = audit_manifest_path.resolve()
    access_date = access_date or date.today()

    existing_audit = (
        {row["source_id"]: row for row in _read_csv(audit_manifest_path)}
        if audit_manifest_path.is_file()
        else {}
    )
    normalized: dict[str, tuple[bytes, int, int, str, str]] = {}
    missing_sources: list[Cn60SourcePlan] = []
    for source in plan.sources:
        existing_path = output_root / "images" / f"{source.source_id}.png"
        audit = existing_audit.get(source.source_id)
        if not existing_path.is_file() or audit is None:
            missing_sources.append(source)
            continue
        content = existing_path.read_bytes()
        if hashlib.sha256(content).hexdigest() != audit.get("materialized_sha256"):
            raise ValueError(f"existing materialized image hash mismatch: {source.source_id}")
        with Image.open(io.BytesIO(content)) as opened:
            width, height = opened.size
        normalized[source.source_id] = (
            content,
            width,
            height,
            audit["materialization_mode"],
            audit["materialization_input_sha256"],
        )
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                _download_and_normalize, source, prefer_discovery_cache
            ): source.source_id
            for source in missing_sources
        }
        for future in as_completed(futures):
            source, content, width, height, mode, input_hash = future.result()
            normalized[source.source_id] = (content, width, height, mode, input_hash)

    audit_rows: list[dict[str, Any]] = []
    source_rows: list[dict[str, Any]] = []
    source_audit_rows: list[dict[str, Any]] = []
    task_rows: list[dict[str, Any]] = []
    for source in plan.sources:
        content, width, height, materialization_mode, input_hash = normalized[source.source_id]
        filename = f"{source.source_id}.png"
        image_path = output_root / "images" / filename
        _write_immutable(image_path, content)
        materialized_hash = hashlib.sha256(content).hexdigest()
        source_rows.append(
            {
                "source_id": source.source_id,
                "image_path": f"images/{filename}",
                "width": width,
                "height": height,
                "sha256": materialized_hash,
                "split": source.split,
                "scene_profile": "balanced",
                "enabled": "true",
                "source_kind": "real_official_web_image",
                "license_status": "official_site_local_evaluation_only",
                "scene_category": source.scene_category,
                "fixture_type": "",
                "test_purpose": "",
            }
        )
        source_audit_rows.append(
            {
                "source_id": source.source_id,
                "official_file_title": source.candidate_id,
                "source_url": source.asset_url,
                "official_source": source.official_page_url,
                "license": "Official-site display; local evaluation only",
                "license_url": source.official_page_url,
                "access_date": access_date.isoformat(),
                "sha256": materialized_hash,
                "scene_category": source.scene_category,
                "local_filename": filename,
                "redistribution_status": "not_redistributable_local_only",
                "author": source.official_source_name,
                "attribution": f"Local evaluation source: {source.official_source_name}",
                "rights_notes": (
                    "Public display on the official website does not grant redistribution or "
                    "third-party model egress rights. Pixels stay in ignored local_data."
                ),
                "api_egress_allowed": "false",
                "local_algorithm_smoke_only": "true",
                "expected_width": width,
                "expected_height": height,
            }
        )
        audit_rows.append(
            {
                "source_id": source.source_id,
                "candidate_id": source.candidate_id,
                "source_url": source.asset_url,
                "official_page_url": source.official_page_url,
                "official_source_name": source.official_source_name,
                "access_date": access_date.isoformat(),
                "source_raw_sha256": source.source_raw_sha256,
                "materialization_input_sha256": input_hash,
                "materialization_mode": materialization_mode,
                "materialized_sha256": materialized_hash,
                "width": width,
                "height": height,
                "scene_category": source.scene_category,
                "split": source.split,
                "local_filename": filename,
                "license_status": "official_site_local_evaluation_only",
                "redistribution_status": "not_redistributable_local_only",
                "api_egress_allowed": "false",
                "review_status": "approved_for_local_prototype",
                "review_reason": source.review_reason,
            }
        )
        task_rows.append(
            {
                "task_id": f"{source.source_id}__{plan.target_id}",
                "source_id": source.source_id,
                "target_id": plan.target_id,
                "enabled": "true",
            }
        )

    descriptor = {
        "schema_version": "1.0",
        "dataset_id": plan.dataset_id,
        "version": "1.0.0",
        "description": "60 audited recent Chinese-scene images for local 1:1 prototyping.",
        "sources_file": "sources.csv",
        "targets_file": "targets.csv",
        "tasks_file": "tasks.csv",
        "source_audit_file": "source_audit.csv",
        "expected_source_count": 60,
        "expected_scene_counts": plan.expected_scene_counts,
        "evaluation_canvas": "1536x1536",
        "generation_originals_may_be_retained_at_2k": True,
        "silent_upsampling_forbidden": False,
    }
    target_rows = [
        {
            "target_id": plan.target_id,
            "width": plan.target_width,
            "height": plan.target_height,
            "format": plan.target_format,
        }
    ]
    _write_immutable(
        output_root / "dataset.yaml",
        yaml.safe_dump(descriptor, allow_unicode=True, sort_keys=False).encode("utf-8"),
    )
    _write_immutable(output_root / "sources.csv", _csv_bytes(source_rows))
    _write_immutable(output_root / "targets.csv", _csv_bytes(target_rows))
    _write_immutable(output_root / "tasks.csv", _csv_bytes(task_rows))
    _write_immutable(output_root / "source_audit.csv", _csv_bytes(source_audit_rows))
    _write_immutable(audit_manifest_path, _csv_bytes(audit_rows))
    _overview(audit_rows, output_root)

    validation = FolderCsvDatasetAdapter().validate(output_root)
    if not validation.valid:
        raise ValueError(
            "materialized CN60 failed dataset validation: " + "; ".join(validation.errors)
        )
    return {
        "dataset_id": plan.dataset_id,
        "dataset_root": str(output_root),
        "source_count": len(plan.sources),
        "task_count": len(validation.tasks),
        "scene_counts": dict(Counter(item.scene_category for item in plan.sources)),
        "split_counts": dict(Counter(item.split for item in plan.sources)),
        "dataset_fingerprint": validation.dataset_fingerprint,
        "source_manifest_sha256": sha256_file(audit_manifest_path),
        "api_egress_allowed_count": 0,
        "redistributable_count": 0,
    }


def summary_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
