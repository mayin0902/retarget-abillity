"""Folder/CSV dataset adapter and reproducibility validation."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import yaml
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import ValidationError

from .hashing import sha256_file, sha256_json
from .models import DatasetDescriptor, SourceAuditRecord, SourceRecord, TargetSpec, TaskSpec
from .protocols import DatasetValidationResult


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _as_bool(value: str | bool | None, default: bool = True) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"invalid boolean value: {value!r}")


def _is_below(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


class FolderCsvDatasetAdapter:
    adapter_id = "folder_csv"
    adapter_version = "1.0.0"

    def validate(self, dataset_root: Path) -> DatasetValidationResult:
        root = dataset_root.resolve()
        errors: list[str] = []
        warnings: list[str] = []

        descriptor_path = root / "dataset.yaml"
        if not descriptor_path.is_file():
            return DatasetValidationResult(
                dataset_id="invalid-dataset",
                dataset_fingerprint="",
                tasks=[],
                errors=["missing dataset.yaml"],
            )

        try:
            raw = yaml.safe_load(descriptor_path.read_text(encoding="utf-8"))
            descriptor = DatasetDescriptor.model_validate(raw)
        except (OSError, yaml.YAMLError, ValidationError, TypeError) as error:
            return DatasetValidationResult(
                dataset_id="invalid-dataset",
                dataset_fingerprint="",
                tasks=[],
                errors=[f"invalid dataset.yaml: {error}"],
            )

        sources_path = root / descriptor.sources_file
        targets_path = root / descriptor.targets_file
        tasks_path = root / descriptor.tasks_file
        for name, path in (
            ("sources", sources_path),
            ("targets", targets_path),
            ("tasks", tasks_path),
        ):
            if not path.is_file():
                errors.append(f"missing {name} file: {path.name}")
        if errors:
            return DatasetValidationResult(
                dataset_id=descriptor.dataset_id,
                dataset_fingerprint="",
                tasks=[],
                errors=errors,
            )

        sources: dict[str, SourceRecord] = {}
        image_real_paths: dict[Path, str] = {}
        image_hashes: dict[str, str] = {}
        try:
            source_rows = _read_csv(sources_path)
        except (OSError, csv.Error) as error:
            source_rows = []
            errors.append(f"cannot read sources.csv: {error}")

        for row_number, row in enumerate(source_rows, start=2):
            try:
                source = SourceRecord(
                    source_id=row.get("source_id", ""),
                    image_path=row.get("image_path", ""),
                    width=int(row.get("width", "0")),
                    height=int(row.get("height", "0")),
                    sha256=row.get("sha256", ""),
                    split=row.get("split", "smoke"),
                    scene_profile=row.get("scene_profile", "balanced"),
                    enabled=_as_bool(row.get("enabled")),
                    source_kind=row.get("source_kind", "unknown"),
                    license_status=row.get("license_status", "unknown"),
                    scene_category=row.get("scene_category", "unknown"),
                    fixture_type=row.get("fixture_type") or None,
                    test_purpose=row.get("test_purpose") or None,
                )
            except (ValueError, ValidationError) as error:
                errors.append(f"sources.csv:{row_number}: {error}")
                continue
            if source.source_id in sources:
                errors.append(f"sources.csv:{row_number}: duplicate source_id {source.source_id}")
                continue

            image_path = (root / Path(source.image_path)).resolve()
            if not _is_below(image_path, root):
                errors.append(f"source {source.source_id}: image path escapes dataset root")
                continue
            if image_path in image_real_paths:
                duplicate_source = image_real_paths[image_path]
                errors.append(
                    f"source {source.source_id}: duplicate image path with {duplicate_source}"
                )
                continue
            if not image_path.is_file():
                errors.append(f"source {source.source_id}: missing image {source.image_path}")
                continue
            try:
                actual_hash = sha256_file(image_path)
                with Image.open(image_path) as opened:
                    opened.verify()
                with Image.open(image_path) as opened:
                    normalized = ImageOps.exif_transpose(opened)
                    actual_size = normalized.size
            except (OSError, UnidentifiedImageError) as error:
                errors.append(f"source {source.source_id}: image is not decodable: {error}")
                continue
            if actual_hash != source.sha256:
                errors.append(f"source {source.source_id}: sha256 mismatch")
                continue
            if actual_size != (source.width, source.height):
                errors.append(
                    f"source {source.source_id}: dimensions {actual_size} do not match "
                    f"manifest {(source.width, source.height)}"
                )
                continue
            if actual_hash in image_hashes:
                duplicate_source = image_hashes[actual_hash]
                errors.append(
                    f"source {source.source_id}: duplicate image content with {duplicate_source}"
                )
                continue
            image_real_paths[image_path] = source.source_id
            image_hashes[actual_hash] = source.source_id
            sources[source.source_id] = source

        targets: dict[str, TargetSpec] = {}
        try:
            target_rows = _read_csv(targets_path)
        except (OSError, csv.Error) as error:
            target_rows = []
            errors.append(f"cannot read targets.csv: {error}")
        for row_number, row in enumerate(target_rows, start=2):
            try:
                target = TargetSpec(
                    target_id=row.get("target_id", ""),
                    width=int(row.get("width", "0")),
                    height=int(row.get("height", "0")),
                    format=row.get("format", "png"),
                )
            except (ValueError, ValidationError) as error:
                errors.append(f"targets.csv:{row_number}: {error}")
                continue
            if target.target_id in targets:
                errors.append(f"targets.csv:{row_number}: duplicate target_id {target.target_id}")
                continue
            targets[target.target_id] = target

        parsed_tasks: list[TaskSpec] = []
        seen_task_ids: set[str] = set()
        source_splits: dict[str, set[str]] = {}
        try:
            task_rows = _read_csv(tasks_path)
        except (OSError, csv.Error) as error:
            task_rows = []
            errors.append(f"cannot read tasks.csv: {error}")
        for row_number, row in enumerate(task_rows, start=2):
            task_id = row.get("task_id", "")
            source_id = row.get("source_id", "")
            target_id = row.get("target_id", "")
            try:
                enabled = _as_bool(row.get("enabled"))
            except ValueError as error:
                errors.append(f"tasks.csv:{row_number}: {error}")
                continue
            if not enabled:
                continue
            if task_id in seen_task_ids:
                errors.append(f"tasks.csv:{row_number}: duplicate task_id {task_id}")
                continue
            if source_id not in sources:
                errors.append(f"tasks.csv:{row_number}: unknown source_id {source_id}")
                continue
            if target_id not in targets:
                errors.append(f"tasks.csv:{row_number}: unknown target_id {target_id}")
                continue
            try:
                task = TaskSpec(
                    dataset_id=descriptor.dataset_id,
                    task_id=task_id,
                    source=sources[source_id],
                    target=targets[target_id],
                )
            except ValidationError as error:
                errors.append(f"tasks.csv:{row_number}: {error}")
                continue
            seen_task_ids.add(task_id)
            source_splits.setdefault(source_id, set()).add(task.source.split)
            parsed_tasks.append(task)

        for source_id, splits in source_splits.items():
            if len(splits) > 1:
                errors.append(f"source {source_id}: tasks leak across splits {sorted(splits)}")

        unused_sources = sorted(set(sources) - set(source_splits))
        if unused_sources:
            warnings.append(f"enabled sources without tasks: {unused_sources}")

        if descriptor.expected_source_count is not None:
            enabled_source_count = sum(source.enabled for source in sources.values())
            if enabled_source_count != descriptor.expected_source_count:
                errors.append(
                    f"expected {descriptor.expected_source_count} enabled sources, "
                    f"found {enabled_source_count}"
                )
        if descriptor.expected_scene_counts:
            actual_scene_counts: dict[str, int] = {}
            for source in sources.values():
                if source.enabled:
                    actual_scene_counts[source.scene_category] = (
                        actual_scene_counts.get(source.scene_category, 0) + 1
                    )
            if actual_scene_counts != descriptor.expected_scene_counts:
                errors.append(
                    "scene category counts do not match descriptor: "
                    f"expected={descriptor.expected_scene_counts}, actual={actual_scene_counts}"
                )

        fingerprint_payload: dict[str, Any] = {
            "descriptor": descriptor.model_dump(mode="json"),
            "sources": [sources[key].model_dump(mode="json") for key in sorted(sources)],
            "targets": [targets[key].model_dump(mode="json") for key in sorted(targets)],
            "tasks": [
                task.model_dump(mode="json")
                for task in sorted(parsed_tasks, key=lambda item: item.task_id)
            ],
        }
        annotations_path = root / "annotations" / "regions.csv"
        if annotations_path.is_file():
            fingerprint_payload["annotations_sha256"] = sha256_file(annotations_path)
        if descriptor.source_audit_file:
            audit_path = root / descriptor.source_audit_file
            if not audit_path.is_file():
                errors.append(f"missing source audit file: {descriptor.source_audit_file}")
            else:
                fingerprint_payload["source_audit_sha256"] = sha256_file(audit_path)
                try:
                    audit_rows = [
                        SourceAuditRecord.model_validate(row) for row in _read_csv(audit_path)
                    ]
                except (OSError, csv.Error, ValidationError) as error:
                    errors.append(f"invalid source audit: {error}")
                else:
                    audit_by_id = {row.source_id: row for row in audit_rows}
                    if len(audit_by_id) != len(audit_rows):
                        errors.append("source audit contains duplicate source_id values")
                    if set(audit_by_id) != set(sources):
                        errors.append("source audit source_id set does not match sources.csv")
                    for source_id, source in sources.items():
                        audit = audit_by_id.get(source_id)
                        if audit is None:
                            continue
                        if audit.sha256 != source.sha256:
                            errors.append(f"source audit sha256 mismatch for {source_id}")
                        if f"images/{audit.local_filename}" != source.image_path:
                            errors.append(f"source audit filename mismatch for {source_id}")
                        if audit.scene_category != source.scene_category:
                            errors.append(f"source audit scene category mismatch for {source_id}")
        fingerprint = sha256_json(fingerprint_payload) if not errors else ""
        return DatasetValidationResult(
            dataset_id=descriptor.dataset_id,
            dataset_fingerprint=fingerprint,
            tasks=sorted(parsed_tasks, key=lambda task: task.task_id),
            errors=errors,
            warnings=warnings,
        )


def read_region_rows(dataset_root: Path) -> list[dict[str, str]]:
    path = dataset_root / "annotations" / "regions.csv"
    return _read_csv(path) if path.is_file() else []
