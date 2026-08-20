"""Strict, reproducible method reports stratified by public-dataset metadata."""

from __future__ import annotations

import csv
import io
import json
import math
import os
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

from .hashing import sha256_file, sha256_json
from .models import RunManifest, validate_id

OPTIONAL_QUALITY_METRICS = (
    "ocr_character_recall",
    "face_count_preservation",
    "person_count_preservation",
    "product_count_preservation",
    "logo_count_preservation",
    "structure_line_similarity",
)
RESOURCE_FIELDS = (
    "generation_wall_seconds",
    "generation_cpu_seconds",
    "generation_peak_rss_bytes",
    "evaluation_wall_seconds",
    "evaluation_cpu_seconds",
    "evaluation_rss_delta_bytes",
)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError(f"required artifact is missing: {path.name}") from error
    if not isinstance(payload, dict):
        raise ValueError(f"expected a JSON object: {path.name}")
    return payload


def _finite_number(value: Any, field: str, *, minimum: float | None = None) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite number or null")
    result = float(value)
    if not math.isfinite(result) or (minimum is not None and result < minimum):
        raise ValueError(f"{field} must be finite and >= {minimum}")
    return result


def _load_source_strata(source_manifest: Path) -> dict[str, tuple[str, str]]:
    try:
        handle = source_manifest.open("r", encoding="utf-8-sig", newline="")
    except FileNotFoundError as error:
        raise ValueError("dataset source_manifest.csv does not exist") from error
    with handle:
        reader = csv.DictReader(handle)
        required = {"source_id", "scene_category", "difficulty_tier"}
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise ValueError(
                "source manifest requires source_id, scene_category, and difficulty_tier"
            )
        strata: dict[str, tuple[str, str]] = {}
        for line_number, row in enumerate(reader, start=2):
            source_id = (row.get("source_id") or "").strip()
            scene = (row.get("scene_category") or "").strip()
            difficulty = (row.get("difficulty_tier") or "").strip()
            if not source_id or not scene or not difficulty:
                raise ValueError(f"source manifest line {line_number} has blank stratum metadata")
            if source_id in strata:
                raise ValueError(f"duplicate source_id in source manifest: {source_id}")
            strata[source_id] = (scene, difficulty)
    if not strata:
        raise ValueError("source manifest is empty")
    return strata


def _load_run_artifacts(
    run_dir: Path,
    evaluation_id: str,
    source_strata: dict[str, tuple[str, str]],
) -> tuple[
    RunManifest,
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, tuple[str, str]],
]:
    manifest = RunManifest.model_validate(_read_json(run_dir / "run.json"))
    if manifest.status != "COMPLETED":
        raise ValueError(f"Generation Run is not COMPLETED: {manifest.status}")
    if len(manifest.task_ids) != len(set(manifest.task_ids)):
        raise ValueError("Generation Run contains duplicate task_ids")
    declared_methods = tuple(manifest.methods)
    if not declared_methods or len(declared_methods) != len(set(declared_methods)):
        raise ValueError("Generation Run must declare a non-empty unique method profile")

    task_strata: dict[str, tuple[str, str]] = {}
    for task_id in manifest.task_ids:
        task = _read_json(run_dir / "tasks" / f"{task_id}.json")
        if str(task.get("task_id")) != task_id:
            raise ValueError(f"task artifact identity mismatch: {task_id}")
        source = task.get("source")
        if not isinstance(source, dict) or not source.get("source_id"):
            raise ValueError(f"task {task_id} has no source identity")
        source_id = str(source["source_id"])
        if source_id not in source_strata:
            raise ValueError(f"task source missing from source manifest: {source_id}")
        task_strata[task_id] = source_strata[source_id]

    candidates: dict[str, dict[str, Any]] = {}
    task_methods: dict[str, list[str]] = defaultdict(list)
    for path in sorted((run_dir / "candidates").glob("*/*/candidate.json")):
        candidate = _read_json(path)
        candidate_id = str(candidate.get("candidate_id", ""))
        task_id = str(candidate.get("task_id", ""))
        method_id = str(candidate.get("method_id", ""))
        if not candidate_id or candidate_id in candidates:
            raise ValueError(f"duplicate or blank candidate_id: {candidate_id!r}")
        if task_id not in task_strata:
            raise ValueError(f"candidate references an unknown task: {task_id}")
        candidates[candidate_id] = candidate
        task_methods[task_id].append(method_id)

    expected_candidate_ids = set(manifest.candidate_ids)
    if len(manifest.candidate_ids) != len(expected_candidate_ids):
        raise ValueError("Generation Run contains duplicate candidate_ids")
    if set(candidates) != expected_candidate_ids:
        raise ValueError("candidate artifacts do not exactly match the Run denominator")
    for task_id in manifest.task_ids:
        methods = task_methods.get(task_id, [])
        if len(methods) != len(declared_methods) or set(methods) != set(declared_methods):
            raise ValueError(f"task {task_id} does not exactly match the declared method profile")

    evaluation = _read_json(run_dir / "evaluations" / evaluation_id / "evaluation.json")
    if evaluation.get("evaluation_id") != evaluation_id:
        raise ValueError("evaluation artifact identity mismatch")
    if evaluation.get("source_run_id") not in {None, manifest.run_id}:
        raise ValueError("evaluation belongs to a different Generation Run")
    if set(evaluation.get("task_ids", ())) != set(manifest.task_ids):
        raise ValueError("evaluation task denominator does not match the Generation Run")
    if set(evaluation.get("candidate_ids", ())) != expected_candidate_ids:
        raise ValueError("evaluation candidate denominator does not match the Generation Run")

    metrics: dict[str, dict[str, Any]] = {}
    metrics_dir = run_dir / "evaluations" / evaluation_id / "metrics"
    for path in sorted(metrics_dir.glob("*.json")):
        bundle = _read_json(path)
        candidate_id = str(bundle.get("candidate_id", ""))
        metric_values = bundle.get("metrics")
        if not candidate_id or candidate_id in metrics:
            raise ValueError(f"duplicate or blank evaluation candidate_id: {candidate_id!r}")
        if not isinstance(metric_values, dict):
            raise ValueError(f"candidate {candidate_id} has no metrics object")
        metrics[candidate_id] = metric_values
    if set(metrics) != expected_candidate_ids:
        raise ValueError("metric artifacts do not exactly match the candidate denominator")
    return manifest, candidates, metrics, task_strata


def _candidate_observation(candidate: dict[str, Any], metrics: dict[str, Any]) -> dict[str, Any]:
    grade = str(metrics.get("proxy_grade", ""))
    if grade not in {"proxy_a", "proxy_b", "proxy_c", "proxy_d", "unknown"}:
        raise ValueError(f"invalid proxy_grade for {candidate['candidate_id']}: {grade!r}")
    result: dict[str, Any] = {
        "quality_score": _finite_number(metrics.get("quality_score"), "quality_score", minimum=0),
        "proxy_grade": grade,
    }
    for name in OPTIONAL_QUALITY_METRICS:
        result[name] = _finite_number(metrics.get(name), name, minimum=0)

    performance = candidate.get("performance")
    if performance is not None and not isinstance(performance, dict):
        raise ValueError(f"candidate {candidate['candidate_id']} has invalid performance")
    performance = performance or {}
    result.update(
        {
            "generation_wall_seconds": _finite_number(
                performance.get("wall_seconds"), "generation wall_seconds", minimum=0
            ),
            "generation_cpu_seconds": _finite_number(
                performance.get("cpu_seconds"), "generation cpu_seconds", minimum=0
            ),
            "generation_peak_rss_bytes": _finite_number(
                performance.get("peak_rss_bytes"), "generation peak_rss_bytes", minimum=0
            ),
            "evaluation_wall_seconds": _finite_number(
                metrics.get("evaluation_wall_seconds"), "evaluation wall_seconds", minimum=0
            ),
            "evaluation_cpu_seconds": _finite_number(
                metrics.get("evaluation_cpu_seconds"), "evaluation cpu_seconds", minimum=0
            ),
            "evaluation_rss_delta_bytes": _finite_number(
                metrics.get("evaluation_rss_delta_bytes"), "evaluation rss_delta_bytes"
            ),
        }
    )
    return result


def _optional_mean(values: list[Any], name: str) -> tuple[float | None, int]:
    observed = [float(item[name]) for item in values if item[name] is not None]
    return (mean(observed) if observed else None, len(observed))


def _complete_resource_aggregate(
    values: list[dict[str, Any]], name: str, aggregation: str
) -> tuple[float | int | None, int]:
    observed = [item[name] for item in values if item[name] is not None]
    if len(observed) != len(values):
        return None, len(observed)
    if aggregation == "sum":
        return sum(observed), len(observed)
    if aggregation == "mean":
        return mean(observed), len(observed)
    if aggregation == "max":
        return max(observed), len(observed)
    raise AssertionError(f"unknown aggregation: {aggregation}")


def _aggregate_row(
    method_id: str,
    scene_category: str | None,
    difficulty_tier: str | None,
    values: list[dict[str, Any]],
) -> dict[str, Any]:
    count = len(values)
    row: dict[str, Any] = {
        "stratum_level": "global_method" if scene_category is None else "scene_difficulty",
        "method_id": method_id,
        "scene_category": scene_category,
        "difficulty_tier": difficulty_tier,
        "count": count,
        "quality_score_mean": None,
        "quality_score_observed_count": 0,
        "proxy_a_rate": sum(item["proxy_grade"] == "proxy_a" for item in values) / count,
        "proxy_success_rate": sum(item["proxy_grade"] in {"proxy_a", "proxy_b"} for item in values)
        / count,
    }
    row["quality_score_mean"], row["quality_score_observed_count"] = _optional_mean(
        values, "quality_score"
    )
    for metric in OPTIONAL_QUALITY_METRICS:
        row[f"{metric}_mean"], row[f"{metric}_observed_count"] = _optional_mean(values, metric)

    for field in RESOURCE_FIELDS:
        if field.endswith("peak_rss_bytes") or field.endswith("rss_delta_bytes"):
            aggregate = "max"
            suffix = "max"
        else:
            aggregate = "mean"
            suffix = "mean"
        row[f"{field}_{suffix}"], row[f"{field}_observed_count"] = _complete_resource_aggregate(
            values, field, aggregate
        )
        if field.endswith(("wall_seconds", "cpu_seconds")):
            row[f"{field}_total"], _ = _complete_resource_aggregate(values, field, "sum")
    return row


def _csv_text(rows: list[dict[str, Any]]) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def _atomic_text(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def build_stratified_benchmark_report(
    run_dir: Path,
    evaluation_id: str,
    source_manifest: Path,
    benchmark_id: str,
) -> dict[str, Any]:
    """Build a strict declared-profile report without accepting partial denominators."""

    validate_id(evaluation_id)
    validate_id(benchmark_id)
    run_dir = run_dir.resolve()
    source_manifest = source_manifest.resolve()
    source_strata = _load_source_strata(source_manifest)
    manifest, candidates, metrics, task_strata = _load_run_artifacts(
        run_dir, evaluation_id, source_strata
    )

    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    global_grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate_id in manifest.candidate_ids:
        candidate = candidates[candidate_id]
        method_id = str(candidate["method_id"])
        scene, difficulty = task_strata[str(candidate["task_id"])]
        observation = _candidate_observation(candidate, metrics[candidate_id])
        grouped[(method_id, scene, difficulty)].append(observation)
        global_grouped[method_id].append(observation)

    declared_methods = tuple(manifest.methods)
    rows = [
        _aggregate_row(method, None, None, global_grouped[method]) for method in declared_methods
    ]
    rows.extend(
        _aggregate_row(method, scene, difficulty, grouped[(method, scene, difficulty)])
        for method, scene, difficulty in sorted(
            grouped,
            key=lambda item: (declared_methods.index(item[0]), item[1], item[2]),
        )
    )
    expected_count = len(manifest.task_ids)
    if any(row["count"] != expected_count for row in rows[: len(declared_methods)]):
        raise ValueError("global method denominator is incomplete")

    report = {
        "schema_version": "1.0",
        "report_type": "strict_method_scene_difficulty_strata",
        "benchmark_id": benchmark_id,
        "run_id": manifest.run_id,
        "evaluation_id": evaluation_id,
        "dataset_id": manifest.dataset_id,
        "source_manifest_filename": source_manifest.name,
        "source_manifest_sha256": sha256_file(source_manifest),
        "task_count": expected_count,
        "candidate_count": len(manifest.candidate_ids),
        "methods": list(declared_methods),
        "all_method_denominators_complete": True,
        "calibration_status": "uncalibrated_automatic_proxy_no_human_ground_truth",
        "rows": rows,
        "rows_hash": sha256_json(rows),
        "notes": [
            "Every task is required to have exactly one candidate for each standard method.",
            "Proxy grades and quality metrics are automatic evidence, not human labels.",
            "Optional semantic metrics average observed values and expose observed_count.",
            "Resource aggregates remain null unless every candidate in the row is observed.",
        ],
    }
    report_text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    csv_text = _csv_text(rows)

    output_dir = run_dir / "benchmarks" / benchmark_id
    try:
        output_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError as error:
        raise FileExistsError(f"benchmark_id already exists: {benchmark_id}") from error
    _atomic_text(output_dir / "stratified-report.json", report_text)
    _atomic_text(output_dir / "strata.csv", csv_text)
    return report
