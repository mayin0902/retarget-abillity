"""Strict complete-denominator aggregation across benchmark rounds."""

from __future__ import annotations

import csv
import io
import json
import math
import os
from pathlib import Path
from typing import Any

from .hashing import sha256_file, sha256_json
from .models import validate_id

ROW_METADATA_FIELDS = {
    "arm_id",
    "arm_type",
    "complete",
    "required_task_count",
    "completed_task_count",
    "model_version",
    "route_mode",
}


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError(f"{label} does not exist: {path.name}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _finite_number(value: Any, field: str) -> float | int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite number or null")
    if not math.isfinite(float(value)):
        raise ValueError(f"{field} must be finite")
    return value


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _round_specs(spec: dict[str, Any]) -> list[dict[str, Any]]:
    rounds = spec.get("rounds")
    if not isinstance(rounds, list) or not rounds:
        raise ValueError("spec.rounds must be a non-empty array")
    names: set[str] = set()
    normalized: list[dict[str, Any]] = []
    canonical_set: set[str] | None = None
    for index, item in enumerate(rounds):
        if not isinstance(item, dict):
            raise ValueError(f"rounds[{index}] must be an object")
        name = item.get("name")
        report = item.get("benchmark_report")
        arm_map = item.get("arm_map")
        if not isinstance(name, str) or not name.strip() or name in names:
            raise ValueError("round names must be non-empty and unique")
        if not isinstance(report, str) or not report.strip():
            raise ValueError(f"round {name} has no benchmark_report")
        if not isinstance(arm_map, dict) or not arm_map:
            raise ValueError(f"round {name} has no arm_map")
        if any(
            not isinstance(key, str) or not key or not isinstance(value, str) or not value
            for key, value in arm_map.items()
        ):
            raise ValueError(f"round {name} arm_map requires non-empty string IDs")
        if len(set(arm_map.values())) != len(arm_map):
            raise ValueError(f"round {name} maps multiple canonical arms to one source arm")
        current_set = set(arm_map)
        if canonical_set is None:
            canonical_set = current_set
        elif current_set != canonical_set:
            raise ValueError("canonical arm sets are not identical across rounds")
        names.add(name)
        normalized.append({"name": name, "benchmark_report": report, "arm_map": arm_map})
    return normalized


def _report_arms(report: dict[str, Any], round_name: str) -> tuple[int, dict[str, dict[str, Any]]]:
    if report.get("all_arms_complete") is not True:
        raise ValueError(f"round {round_name} benchmark is not all-arms complete")
    task_count = _positive_int(report.get("task_count"), f"round {round_name} task_count")
    rows = report.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"round {round_name} benchmark has no rows")
    by_arm: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("arm_id"), str):
            raise ValueError(f"round {round_name} has an invalid arm row")
        arm_id = row["arm_id"]
        if arm_id in by_arm:
            raise ValueError(f"round {round_name} has duplicate arm_id: {arm_id}")
        by_arm[arm_id] = row
    return task_count, by_arm


def _field_groups(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    weighted: set[str] = set()
    totals: set[str] = set()
    counts: set[str] = set()
    maxima: set[str] = set()
    percentiles: set[str] = set()
    for row in rows:
        for field in row:
            if field in ROW_METADATA_FIELDS:
                continue
            if field.endswith("_p50") or field.endswith("_p95"):
                percentiles.add(field)
            elif field.endswith("_mean") or field.endswith("_rate"):
                weighted.add(field)
            elif field.endswith("_total"):
                totals.add(field)
            elif field.endswith("_count"):
                counts.add(field)
            elif field.endswith("_max"):
                maxima.add(field)
    return {
        "weighted": sorted(weighted),
        "totals": sorted(totals),
        "counts": sorted(counts),
        "maxima": sorted(maxima),
        "percentiles": sorted(percentiles),
    }


def _weighted(values: list[tuple[Any, int]], field: str) -> float | None:
    checked = [(_finite_number(value, field), weight) for value, weight in values]
    if any(value is None for value, _ in checked):
        return None
    if field.endswith("_rate") and any(not 0 <= float(value) <= 1 for value, _ in checked):
        raise ValueError(f"{field} rates must be between zero and one")
    denominator = sum(weight for _, weight in checked)
    return sum(float(value) * weight for value, weight in checked) / denominator


def _sum_or_unknown(values: list[Any], field: str, *, integer: bool) -> float | int | None:
    checked = [_finite_number(value, field) for value in values]
    if any(value is None for value in checked):
        return None
    if any(float(value) < 0 for value in checked):
        raise ValueError(f"{field} cannot be negative")
    if integer and any(not isinstance(value, int) or isinstance(value, bool) for value in checked):
        raise ValueError(f"{field} must contain integer counts")
    return sum(checked)  # type: ignore[arg-type]


def _max_or_unknown(values: list[Any], field: str) -> float | int | None:
    checked = [_finite_number(value, field) for value in values]
    if any(value is None for value in checked):
        return None
    if any(float(value) < 0 for value in checked):
        raise ValueError(f"{field} cannot be negative")
    return max(checked)  # type: ignore[type-var]


def _atomic_text(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _csv_text(rows: list[dict[str, Any]]) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def aggregate_benchmark_rounds(
    spec_path: Path,
    output_dir: Path,
    report_id: str,
) -> dict[str, Any]:
    """Aggregate aligned complete arms while preserving unknown values."""

    validate_id(report_id)
    spec_path = spec_path.resolve()
    spec = _read_json(spec_path, "round aggregation spec")
    round_specs = _round_specs(spec)
    resolved_reports: set[Path] = set()
    benchmark_ids: set[str] = set()
    run_ids: set[str] = set()
    loaded: list[dict[str, Any]] = []
    selected_rows: list[dict[str, Any]] = []
    for round_spec in round_specs:
        raw_path = Path(round_spec["benchmark_report"])
        report_path = raw_path if raw_path.is_absolute() else spec_path.parent / raw_path
        report_path = report_path.resolve()
        if report_path in resolved_reports:
            raise ValueError("source benchmark report paths must be unique")
        report = _read_json(report_path, f"benchmark report for {round_spec['name']}")
        benchmark_id = report.get("benchmark_id")
        run_id = report.get("run_id")
        if not isinstance(benchmark_id, str) or not benchmark_id or benchmark_id in benchmark_ids:
            raise ValueError("source benchmark IDs must be non-empty and unique")
        if not isinstance(run_id, str) or not run_id or run_id in run_ids:
            raise ValueError("source run IDs must be non-empty and unique")
        task_count, arms = _report_arms(report, round_spec["name"])
        canonical_rows: dict[str, dict[str, Any]] = {}
        for canonical_id, source_arm_id in round_spec["arm_map"].items():
            if source_arm_id not in arms:
                raise ValueError(
                    f"round {round_spec['name']} mapped arm does not exist: {source_arm_id}"
                )
            arm = arms[source_arm_id]
            if arm.get("complete") is not True:
                raise ValueError(f"mapped arm is partial: {source_arm_id}")
            if arm.get("required_task_count") != task_count:
                raise ValueError(f"mapped arm task denominator mismatch: {source_arm_id}")
            if arm.get("completed_task_count") != task_count:
                raise ValueError(f"mapped arm completed denominator mismatch: {source_arm_id}")
            canonical_rows[canonical_id] = arm
            selected_rows.append(arm)
        resolved_reports.add(report_path)
        benchmark_ids.add(benchmark_id)
        run_ids.add(run_id)
        loaded.append(
            {
                "name": round_spec["name"],
                "benchmark_id": benchmark_id,
                "run_id": run_id,
                "task_count": task_count,
                "report_filename": report_path.name,
                "report_sha256": sha256_file(report_path),
                "arm_map": dict(round_spec["arm_map"]),
                "rows": canonical_rows,
            }
        )

    groups = _field_groups(selected_rows)
    canonical_ids = sorted(round_specs[0]["arm_map"])
    total_tasks = sum(item["task_count"] for item in loaded)
    rows: list[dict[str, Any]] = []
    for canonical_id in canonical_ids:
        parts = [(item["rows"][canonical_id], item["task_count"]) for item in loaded]
        model_versions = {row.get("model_version") for row, _ in parts}
        if len(model_versions) != 1:
            raise ValueError(f"model_version mismatch for canonical arm: {canonical_id}")
        arm_types = {row.get("arm_type") for row, _ in parts}
        if len(arm_types) != 1:
            raise ValueError(f"arm_type mismatch for canonical arm: {canonical_id}")
        route_modes = {row.get("route_mode") for row, _ in parts}
        row: dict[str, Any] = {
            "arm_id": canonical_id,
            "arm_type": next(iter(arm_types)),
            "complete": True,
            "required_task_count": total_tasks,
            "completed_task_count": total_tasks,
            "model_version": next(iter(model_versions)),
            "route_mode": next(iter(route_modes)) if len(route_modes) == 1 else None,
            "source_arm_ids": "|".join(
                f"{item['name']}={item['arm_map'][canonical_id]}" for item in loaded
            ),
        }
        for field in groups["weighted"]:
            row[field] = _weighted(
                [(source.get(field), task_count) for source, task_count in parts], field
            )
        for field in groups["totals"]:
            row[field] = _sum_or_unknown(
                [source.get(field) for source, _ in parts], field, integer=False
            )
        for field in groups["counts"]:
            row[field] = _sum_or_unknown(
                [source.get(field) for source, _ in parts], field, integer=True
            )
        for field in groups["maxima"]:
            row[field] = _max_or_unknown([source.get(field) for source, _ in parts], field)
        for field in groups["percentiles"]:
            row[field] = None
        call_count = row.get("agent_call_count")
        schema_valid_count = row.get("agent_schema_valid_count")
        cache_hit_count = row.get("agent_cache_hit_count")
        if isinstance(call_count, int) and call_count > 0:
            if isinstance(schema_valid_count, int):
                row["agent_schema_valid_rate"] = schema_valid_count / call_count
            if isinstance(cache_hit_count, int):
                row["agent_cache_hit_rate"] = cache_hit_count / call_count
        rows.append(row)

    sources = [{key: value for key, value in item.items() if key != "rows"} for item in loaded]
    report = {
        "schema_version": "1.0",
        "report_type": "strict_cross_round_complete_denominator_aggregation",
        "report_id": report_id,
        "spec_filename": spec_path.name,
        "spec_sha256": sha256_file(spec_path),
        "round_count": len(loaded),
        "task_count": total_tasks,
        "canonical_arm_ids": canonical_ids,
        "all_arms_complete": True,
        "sources": sources,
        "rows": rows,
        "rows_hash": sha256_json(rows),
        "notes": [
            "Means and rates are weighted by each source round's complete task_count.",
            "Agent schema-valid and cache-hit rates are recomputed from summed exact counts.",
            "Counts and totals are summed; any unknown source value makes the aggregate null.",
            "Maxima require every source round and use the maximum rather than a sum.",
            "p50 and p95 are null because quantiles cannot be reconstructed from round quantiles.",
            "Proxy metrics remain uncalibrated automatic evidence, not human ground truth.",
        ],
    }
    report_text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    csv_text = _csv_text(rows)
    destination = output_dir.resolve() / report_id
    try:
        destination.mkdir(parents=True, exist_ok=False)
    except FileExistsError as error:
        raise FileExistsError(f"round aggregation report ID already exists: {report_id}") from error
    _atomic_text(destination / "report.json", report_text)
    _atomic_text(destination / "arms.csv", csv_text)
    return report
