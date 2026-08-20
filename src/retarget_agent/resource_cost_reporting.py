"""Resource and cost sidecars for completed benchmark reports."""

from __future__ import annotations

import csv
import io
import json
import math
import os
import re
from collections.abc import Iterable
from decimal import Decimal
from pathlib import Path
from typing import Any

from .hashing import sha256_file, sha256_json
from .models import validate_id

DEFAULT_GPU_HOURLY_RATES_CNY = (1.0, 2.0, 5.0, 10.0)
BENCHMARK_PASSTHROUGH_FIELDS = (
    "direct_cost_cny_total",
    "agent_call_count",
    "agent_call_rate",
    "agent_latency_seconds_mean",
    "agent_latency_seconds_p50",
    "agent_latency_seconds_p95",
    "schema_valid_rate",
)


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError(f"{label} does not exist") from error
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def _number(
    value: Any,
    field: str,
    *,
    minimum: float | None = None,
) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite number or null")
    result = float(value)
    if not math.isfinite(result) or (minimum is not None and result < minimum):
        raise ValueError(f"{field} must be finite and >= {minimum}")
    return result


def _normalize_model_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _model_aliases(observation: dict[str, Any]) -> tuple[set[str], set[str]]:
    workload = observation.get("workload")
    if not isinstance(workload, dict):
        workload = {}
    names = {
        str(value)
        for value in (
            observation.get("served_model_name"),
            observation.get("model_key"),
            observation.get("model_id"),
            workload.get("served_model_name"),
            workload.get("model_key"),
            workload.get("model_id"),
        )
        if value
    }
    full = {_normalize_model_name(name.rsplit("/", 1)[-1]) for name in names}
    families: set[str] = set()
    for alias in full:
        family = re.sub(r"[0-9]+p[0-9]+b(?:instruct)?$", "", alias)
        family = re.sub(r"[0-9]+b(?:instruct)?$", "", family)
        if len(family) >= 7:
            families.add(family)
    return full, families


def _raw_observations(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(payload.get("observations"), list):
        values = payload["observations"]
    elif isinstance(payload.get("observation"), dict):
        values = [payload["observation"]]
    elif isinstance(payload.get("models"), list):
        values = []
        for index, model in enumerate(payload["models"]):
            if not isinstance(model, dict):
                raise ValueError(f"models[{index}] must be an object")
            resource = model.get("resource_observation")
            if not isinstance(resource, dict):
                raise ValueError(f"models[{index}].resource_observation must be an object")
            agent_runs = model.get("agent_runs")
            if not isinstance(agent_runs, list) or any(
                not isinstance(run, dict)
                or not isinstance(run.get("agent_run_id"), str)
                or not run["agent_run_id"]
                for run in agent_runs
            ):
                raise ValueError(
                    f"models[{index}].agent_runs must contain non-empty agent_run_id values"
                )
            observation = dict(resource)
            observation.update(
                {
                    key: model[key]
                    for key in (
                        "model_id",
                        "revision",
                        "served_model_name",
                        "gpu_index",
                    )
                    if key in model
                }
            )
            observation["agent_run_ids"] = [run["agent_run_id"] for run in agent_runs]
            observation["capture_scope"] = model.get("sampler_window_scope")
            aliases = {
                "memory_peak_mib": "peak_memory_mib",
                "energy_active_wh": "active_energy_wh",
                "energy_total_window_wh": "total_energy_wh",
                "power_peak_watts": "peak_power_watts",
                "power_active_mean_watts": "active_mean_power_watts",
            }
            for source, target in aliases.items():
                if source in observation and target not in observation:
                    observation[target] = observation[source]
            values.append(observation)
    elif any(key in payload for key in ("workload", "activity", "power", "memory")):
        values = [payload]
    else:
        raise ValueError("resource observation JSON has no observation records")
    if not values or any(not isinstance(value, dict) for value in values):
        raise ValueError("resource observations must be a non-empty list of objects")
    return values


def _select_observations(arm_id: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    observations = _raw_observations(payload)
    agent_run_id = arm_id.removeprefix("route:")
    run_id_matches: list[dict[str, Any]] = []
    for item in observations:
        workload = _nested(item, "workload")
        raw_ids = item.get("agent_run_ids", workload.get("agent_run_ids", []))
        if raw_ids is None:
            raw_ids = []
        if not isinstance(raw_ids, list) or any(not isinstance(value, str) for value in raw_ids):
            raise ValueError("agent_run_ids must be a list of strings")
        if agent_run_id in raw_ids:
            run_id_matches.append(item)
    if len(run_id_matches) == 1:
        return run_id_matches
    if len(run_id_matches) > 1:
        raise ValueError(f"duplicate agent_run_ids mapping for arm: {arm_id}")

    exact = [
        item
        for item in observations
        if item.get("arm_id") == arm_id
        or (isinstance(item.get("workload"), dict) and item["workload"].get("arm_id") == arm_id)
    ]
    if exact:
        return exact

    normalized_arm = _normalize_model_name(arm_id)
    full_matches = [
        item
        for item in observations
        if any(alias and alias in normalized_arm for alias in _model_aliases(item)[0])
    ]
    if len(full_matches) == 1:
        return full_matches
    family_matches = [
        item
        for item in observations
        if any(alias in normalized_arm for alias in _model_aliases(item)[1])
    ]
    if len(family_matches) == 1:
        return family_matches
    if len(observations) == 1:
        return observations
    raise ValueError(f"resource observations are ambiguous for arm: {arm_id}")


def _nested(observation: dict[str, Any], section: str) -> dict[str, Any]:
    value = observation.get(section)
    return value if isinstance(value, dict) else {}


def _capture_scope(observation: dict[str, Any]) -> str | None:
    workload = _nested(observation, "workload")
    value = observation.get("capture_scope", workload.get("capture_scope"))
    return str(value).strip() if value is not None and str(value).strip() else None


def _mapped_agent_run_id(arm_id: str, observation: dict[str, Any]) -> str:
    requested = arm_id.removeprefix("route:")
    workload = _nested(observation, "workload")
    raw_ids = observation.get("agent_run_ids", workload.get("agent_run_ids", ()))
    if isinstance(raw_ids, list) and requested in raw_ids:
        return requested
    return requested


def _scope_marks_retry_run(
    capture_scope: str | None, mapped_run_id: str, observation: dict[str, Any]
) -> bool:
    normalized = (capture_scope or "").lower()
    if "retry" not in normalized and "retries" not in normalized:
        return False
    if "retry" in mapped_run_id.lower():
        return True
    retry_versions = set(
        re.findall(r"\b(v[0-9]+)\s+retr(?:y|ies)\b", normalized)
        + re.findall(r"\bretr(?:y|ies)[^;,.]{0,32}\b(v[0-9]+)\b", normalized)
    )
    run_versions = set(re.findall(r"(?:^|[-_])(v[0-9]+)(?:$|[-_])", mapped_run_id.lower()))
    if retry_versions:
        return bool(retry_versions & run_versions)
    workload = _nested(observation, "workload")
    raw_ids = observation.get("agent_run_ids", workload.get("agent_run_ids", ()))
    return isinstance(raw_ids, list) and len(raw_ids) == 1


def _energy_coverage(observation: dict[str, Any], capture_scope: str | None, arm_id: str) -> str:
    workload = _nested(observation, "workload")
    explicit = observation.get("energy_coverage", workload.get("energy_coverage"))
    if explicit is not None and explicit not in {"complete", "explicit_partial", "unknown"}:
        raise ValueError(f"invalid energy_coverage: {explicit!r}")
    normalized = (capture_scope or "").lower()
    scope_is_incomplete = any(
        marker in normalized
        for marker in (
            "excludes the original complete",
            "excludes original complete",
            "does not cover the complete",
            "does not cover the full",
            "not cover the complete",
            "not cover the full",
            "diagnostics only",
            "validation only",
            "startup only",
            "subset",
            "partial",
            "incomplete",
        )
    )
    mapped_run_id = _mapped_agent_run_id(arm_id, observation)
    if (
        explicit == "explicit_partial"
        or scope_is_incomplete
        or _scope_marks_retry_run(capture_scope, mapped_run_id, observation)
    ):
        return "explicit_partial"
    if explicit == "complete":
        return "complete"
    if normalized.startswith("complete ") or "complete 12-image replay" in normalized:
        return "complete"
    return "unknown"


def _model_metadata(
    observation: dict[str, Any],
) -> tuple[str | None, str | None, str | None, str | None]:
    workload = _nested(observation, "workload")
    served = observation.get("served_model_name", workload.get("served_model_name"))
    model_key = observation.get("model_key", workload.get("model_key"))
    model = observation.get("model_id", workload.get("model_id"))
    revision = observation.get("revision", workload.get("revision"))
    return (
        str(served) if served is not None else None,
        str(model_key) if model_key is not None else None,
        str(model) if model is not None else None,
        str(revision) if revision is not None else None,
    )


def _normalized_observation(
    payload: dict[str, Any], observation: dict[str, Any], index: int, arm_id: str
) -> dict[str, Any]:
    environment = payload.get("environment")
    if not isinstance(environment, dict):
        environment = {}
    activity = _nested(observation, "activity")
    power = _nested(observation, "power")
    memory = _nested(observation, "memory")
    served, model_key, model, revision = _model_metadata(observation)
    scope = _capture_scope(observation)
    active_seconds = observation.get(
        "active_seconds",
        observation.get(
            "active_seconds_left_rectangle", activity.get("active_seconds_left_rectangle")
        ),
    )
    active_energy = observation.get(
        "active_energy_wh",
        observation.get(
            "energy_wh_active_left_rectangle", power.get("energy_wh_active_left_rectangle")
        ),
    )
    total_energy = observation.get(
        "total_energy_wh",
        observation.get("energy_wh_total_trapezoidal", power.get("energy_wh_total_trapezoidal")),
    )
    active_mean_power = observation.get(
        "active_mean_power_watts",
        observation.get(
            "power_watts_active_mean_time_weighted",
            power.get(
                "active_mean_watts_time_weighted_left_rectangle",
                power.get("active_mean_watts_sample"),
            ),
        ),
    )
    return {
        "resource_observation_id": payload.get("observation_id"),
        "observation_index": index,
        "gpu_index": observation.get("gpu_index"),
        "gpu_model": environment.get("gpu_model", observation.get("gpu_model")),
        "served_model_name": served,
        "model_key": model_key,
        "model_id": model,
        "revision": revision,
        "capture_scope": scope,
        "energy_coverage": _energy_coverage(observation, scope, arm_id),
        "peak_memory_mib": _number(
            observation.get(
                "peak_memory_mib",
                observation.get("memory_used_mib_peak", memory.get("peak_used_mib")),
            ),
            "peak_memory_mib",
            minimum=0,
        ),
        "active_seconds": _number(active_seconds, "active_seconds", minimum=0),
        "active_energy_wh": _number(active_energy, "active_energy_wh", minimum=0),
        "total_energy_wh": _number(total_energy, "total_energy_wh", minimum=0),
        "peak_power_watts": _number(
            observation.get(
                "peak_power_watts",
                observation.get("power_watts_peak", power.get("peak_watts")),
            ),
            "peak_power_watts",
            minimum=0,
        ),
        "active_mean_power_watts": _number(active_mean_power, "active_mean_power_watts", minimum=0),
    }


def _pricing_rates(values: Iterable[float] | None) -> tuple[float, ...]:
    supplied = DEFAULT_GPU_HOURLY_RATES_CNY if values is None else tuple(values)
    if not supplied:
        raise ValueError("at least one GPU hourly rate is required")
    normalized: set[float] = set()
    for value in supplied:
        rate = _number(value, "gpu_hourly_rate_cny", minimum=0)
        if rate is None:
            raise ValueError("GPU hourly rate cannot be null")
        normalized.add(rate)
    return tuple(sorted(normalized))


def _benchmark_arms(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if report.get("all_arms_complete") is not True:
        raise ValueError("benchmark report is not complete")
    rows = report.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ValueError("benchmark report has no arm rows")
    arms: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or not row.get("arm_id"):
            raise ValueError("benchmark report contains an invalid arm row")
        arm_id = str(row["arm_id"])
        if arm_id in arms:
            raise ValueError(f"duplicate arm_id in benchmark report: {arm_id}")
        if row.get("complete") is not True:
            raise ValueError(f"benchmark arm is incomplete: {arm_id}")
        arms[arm_id] = row
    return arms


def _passthrough(arm: dict[str, Any]) -> dict[str, Any]:
    values = {field: arm.get(field) for field in BENCHMARK_PASSTHROUGH_FIELDS}
    if values["schema_valid_rate"] is None:
        values["schema_valid_rate"] = arm.get("agent_schema_valid_rate")
    return values


def _priced_row(
    arm_id: str,
    arm: dict[str, Any],
    observation: dict[str, Any],
    rate: float,
) -> dict[str, Any]:
    task_count_value = arm.get("required_task_count")
    if not isinstance(task_count_value, int) or isinstance(task_count_value, bool):
        raise ValueError(f"arm {arm_id} has invalid required_task_count")
    if task_count_value <= 0:
        raise ValueError(f"arm {arm_id} has no required tasks")
    active_seconds = observation["active_seconds"]
    cost: float | None = None
    per_task: float | None = None
    if active_seconds is not None:
        cost_decimal = Decimal(str(active_seconds)) / Decimal(3600) * Decimal(str(rate))
        cost = float(cost_decimal)
        per_task = float(cost_decimal / Decimal(task_count_value))
    return {
        "arm_id": arm_id,
        "arm_type": arm.get("arm_type"),
        "required_task_count": task_count_value,
        **_passthrough(arm),
        **observation,
        "gpu_hourly_rate_cny": rate,
        "observed_active_gpu_cost_cny": cost,
        "observed_active_gpu_cost_cny_per_task": per_task,
    }


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


def build_resource_cost_report(
    benchmark_report: Path,
    arm_observations: dict[str, Path],
    report_id: str,
    gpu_hourly_rates_cny: Iterable[float] | None = None,
) -> dict[str, Any]:
    """Attach observed GPU resource and scenario costs to complete benchmark arms."""

    validate_id(report_id)
    benchmark_report = benchmark_report.resolve()
    benchmark = _read_json(benchmark_report, "benchmark report")
    arms = _benchmark_arms(benchmark)
    if not arm_observations:
        raise ValueError("at least one arm resource observation is required")
    unknown_arms = sorted(set(arm_observations) - set(arms))
    if unknown_arms:
        raise ValueError(f"arm does not exist in benchmark report: {unknown_arms[0]}")
    rates = _pricing_rates(gpu_hourly_rates_cny)

    rows: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    for arm_id in sorted(arm_observations):
        path = arm_observations[arm_id].resolve()
        payload = _read_json(path, f"resource observation for {arm_id}")
        selected = _select_observations(arm_id, payload)
        sources.append(
            {
                "arm_id": arm_id,
                "filename": path.name,
                "sha256": sha256_file(path),
                "selected_observation_count": len(selected),
            }
        )
        for index, observation in enumerate(selected):
            normalized = _normalized_observation(payload, observation, index, arm_id)
            rows.extend(_priced_row(arm_id, arms[arm_id], normalized, rate) for rate in rates)

    report = {
        "schema_version": "1.0",
        "report_type": "benchmark_resource_cost_sidecar",
        "resource_cost_report_id": report_id,
        "source_benchmark_id": benchmark.get("benchmark_id"),
        "source_run_id": benchmark.get("run_id"),
        "source_benchmark_filename": benchmark_report.name,
        "source_benchmark_sha256": sha256_file(benchmark_report),
        "gpu_hourly_rates_cny": list(rates),
        "observation_sources": sources,
        "rows": rows,
        "rows_hash": sha256_json(rows),
        "notes": [
            "GPU costs price observed active GPU-seconds; they are not provider charges.",
            "explicit_partial costs cover only the declared capture scope.",
            "Missing measurements and missing benchmark passthrough values remain null.",
        ],
    }
    report_text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    csv_text = _csv_text(rows)
    output_dir = benchmark_report.parent.parent / report_id
    try:
        output_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError as error:
        raise FileExistsError(f"resource cost report ID already exists: {report_id}") from error
    _atomic_text(output_dir / "resource-costs.json", report_text)
    _atomic_text(output_dir / "resource-costs.csv", csv_text)
    return report
