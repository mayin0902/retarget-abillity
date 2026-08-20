"""Strict complete-denominator benchmark aggregation over frozen artifacts."""

from __future__ import annotations

import csv
import io
import json
import os
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median
from typing import Any

from .hashing import sha256_json
from .models import RunManifest, validate_id
from .storage import LocalArtifactStore


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))]


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _integer_total_or_unknown(values: list[Any], field: str) -> int | None:
    """Sum non-negative integer observations without treating missing data as zero."""

    if any(value is None for value in values):
        return None
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values):
        raise ValueError(f"{field} must contain non-negative integers or null")
    return sum(values)


def _method_rows(
    run_dir: Path,
    manifest: RunManifest,
    metrics: dict[str, dict[str, Any]],
    candidates: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    del run_dir
    grouped: dict[str, list[str]] = defaultdict(list)
    for candidate_id, candidate in candidates.items():
        grouped[str(candidate["method_id"])].append(candidate_id)
    rows: list[dict[str, Any]] = []
    required = len(manifest.task_ids)
    for method_id in manifest.methods:
        ids = grouped.get(method_id, [])
        task_ids = {str(candidates[candidate_id]["task_id"]) for candidate_id in ids}
        complete = len(ids) == required and task_ids == set(manifest.task_ids)
        if not complete:
            raise ValueError(f"method {method_id} is incomplete: {len(ids)}/{required} candidates")
        scores = [float(metrics[item]["quality_score"]) for item in ids]
        grades = [str(metrics[item]["proxy_grade"]) for item in ids]
        walls = [
            float(candidates[item]["performance"]["wall_seconds"])
            for item in ids
            if candidates[item].get("performance", {}).get("wall_seconds") is not None
        ]
        cpus = [
            float(candidates[item]["performance"]["cpu_seconds"])
            for item in ids
            if candidates[item].get("performance", {}).get("cpu_seconds") is not None
        ]
        peaks = [
            int(candidates[item]["performance"]["peak_rss_bytes"])
            for item in ids
            if candidates[item].get("performance", {}).get("peak_rss_bytes") is not None
        ]
        rows.append(
            {
                "arm_id": f"method:{method_id}",
                "arm_type": "traditional_method",
                "required_task_count": required,
                "completed_task_count": len(task_ids),
                "complete": True,
                "quality_score_mean": mean(scores),
                "proxy_a_rate": grades.count("proxy_a") / required,
                "proxy_success_rate": sum(grade in {"proxy_a", "proxy_b"} for grade in grades)
                / required,
                "end_to_end_wall_seconds_p50": median(walls) if walls else None,
                "end_to_end_wall_seconds_p95": _p95(walls),
                "wall_seconds_total": sum(walls) if len(walls) == required else None,
                "cpu_seconds_total": sum(cpus) if len(cpus) == required else None,
                "peak_rss_bytes_max": max(peaks) if peaks else None,
                "agent_call_count": 0,
                "agent_schema_valid_count": 0,
                "agent_cache_hit_count": 0,
                "agent_attempt_count": 0,
                "agent_input_tokens_total": 0,
                "agent_output_tokens_total": 0,
                "agent_tokens_total": 0,
                "agent_token_observation_count": 0,
                "agent_observed_tokens_total": 0,
                "agent_call_rate": 0.0,
                "agent_schema_valid_rate": None,
                "agent_cache_hit_rate": None,
                "agent_failure_count": 0,
                "agent_latency_seconds_mean": None,
                "agent_latency_seconds_p95": None,
                "agent_top1_change_rate": None,
                "route_mode": None,
                "model_version": None,
                "external_generation_count": 0,
                "direct_cost_cny_total": 0.0,
                "proxy_routing_regret_mean": None,
                "proxy_delta_vs_deterministic_mean": None,
            }
        )
    return rows


def _route_row(
    run_dir: Path,
    route_id: str,
    evaluation_id: str,
    manifest: RunManifest,
    metrics: dict[str, dict[str, Any]],
    candidates: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    route_dir = run_dir / "agent-runs" / route_id
    route_manifest = json.loads((route_dir / "agent-run.json").read_text(encoding="utf-8"))
    if route_manifest.get("evaluation_id") != evaluation_id:
        raise ValueError(
            f"route {route_id} evaluation mismatch: "
            f"{route_manifest.get('evaluation_id')} != {evaluation_id}"
        )
    decision_files = sorted((route_dir / "decisions").glob("*.json"))
    decisions = [json.loads(path.read_text(encoding="utf-8")) for path in decision_files]
    by_task = {str(item["task_id"]): item for item in decisions}
    required_tasks = set(manifest.task_ids)
    if len(decisions) != len(required_tasks) or set(by_task) != required_tasks:
        raise ValueError(
            f"route {route_id} is incomplete: {len(by_task)}/{len(required_tasks)} decisions"
        )
    selected_ids = [by_task[task]["selected_candidate_id"] for task in manifest.task_ids]
    if any(candidate_id not in candidates for candidate_id in selected_ids):
        raise ValueError(f"route {route_id} references missing traditional candidates")
    scores = [float(metrics[str(item)]["quality_score"]) for item in selected_ids]
    grades = [str(metrics[str(item)]["proxy_grade"]) for item in selected_ids]
    method_walls = [
        float(candidates[str(item)]["performance"]["wall_seconds"]) for item in selected_ids
    ]
    calls: dict[str, dict[str, Any]] = {}
    for path in (route_dir / "calls").glob("*.json"):
        call = json.loads(path.read_text(encoding="utf-8"))
        calls[str(call["agent_call_id"])] = call
    end_to_end: list[float] = []
    regrets: list[float] = []
    deltas_vs_deterministic: list[float] = []
    oracle_by_task: dict[str, float] = {}
    for candidate_id, candidate in candidates.items():
        task_id = str(candidate.get("task_id") or candidate["performance"]["task_id"])
        oracle_by_task[task_id] = max(
            oracle_by_task.get(task_id, float("-inf")),
            float(metrics[candidate_id]["quality_score"]),
        )
    for task_id, selected_id, method_wall in zip(
        manifest.task_ids, selected_ids, method_walls, strict=True
    ):
        decision = by_task[task_id]
        call = calls.get(str(decision.get("agent_call_id")))
        end_to_end.append(method_wall + (float(call["latency_seconds"]) if call else 0.0))
        selected_score = float(metrics[str(selected_id)]["quality_score"])
        regrets.append(oracle_by_task[task_id] - selected_score)
        deterministic_id = decision.get("deterministic_candidate_id")
        if deterministic_id in metrics:
            deltas_vs_deterministic.append(
                selected_score - float(metrics[str(deterministic_id)]["quality_score"])
            )
    estimates = [call.get("estimated_cost") for call in calls.values()]
    direct_cost = (
        sum(float(value) for value in estimates)
        if estimates and all(value is not None for value in estimates)
        else (0.0 if not calls else None)
    )
    action_counts = Counter(str(item["route_action"]) for item in decisions)
    required = len(required_tasks)
    call_values = list(calls.values())
    call_latencies = [float(call["latency_seconds"]) for call in call_values]
    successful_calls = sum(bool(call.get("success")) for call in call_values)
    attempt_count = _integer_total_or_unknown(
        [call.get("attempt_count") for call in call_values], "attempt_count"
    )
    input_tokens = _integer_total_or_unknown(
        [call.get("input_tokens") for call in call_values], "input_tokens"
    )
    output_tokens = _integer_total_or_unknown(
        [call.get("output_tokens") for call in call_values], "output_tokens"
    )
    token_total = (
        input_tokens + output_tokens
        if input_tokens is not None and output_tokens is not None
        else None
    )
    observed_tokens: list[int] = []
    for call in call_values:
        call_total = call.get("tokens")
        if call_total is None:
            call_input = call.get("input_tokens")
            call_output = call.get("output_tokens")
            if (
                isinstance(call_input, int)
                and not isinstance(call_input, bool)
                and isinstance(call_output, int)
                and not isinstance(call_output, bool)
            ):
                call_total = call_input + call_output
        if call_total is not None:
            checked = _integer_total_or_unknown([call_total], "tokens")
            assert checked is not None
            observed_tokens.append(checked)
    return {
        "arm_id": f"route:{route_id}",
        "arm_type": "agent_or_routing_policy",
        "required_task_count": required,
        "completed_task_count": required,
        "complete": True,
        "quality_score_mean": mean(scores),
        "proxy_a_rate": grades.count("proxy_a") / required,
        "proxy_success_rate": sum(grade in {"proxy_a", "proxy_b"} for grade in grades) / required,
        "end_to_end_wall_seconds_p50": median(end_to_end),
        "end_to_end_wall_seconds_p95": _p95(end_to_end),
        "wall_seconds_total": sum(end_to_end),
        "cpu_seconds_total": None,
        "peak_rss_bytes_max": None,
        "agent_call_count": len(calls),
        "agent_schema_valid_count": successful_calls,
        "agent_cache_hit_count": sum(bool(call.get("cache_hit")) for call in call_values),
        "agent_attempt_count": attempt_count,
        "agent_input_tokens_total": input_tokens,
        "agent_output_tokens_total": output_tokens,
        "agent_tokens_total": token_total,
        "agent_token_observation_count": len(observed_tokens),
        "agent_observed_tokens_total": sum(observed_tokens),
        "agent_call_rate": len(calls) / required,
        "agent_schema_valid_rate": successful_calls / len(calls) if calls else None,
        "agent_cache_hit_rate": (
            sum(bool(call.get("cache_hit")) for call in call_values) / len(calls) if calls else None
        ),
        "agent_failure_count": len(calls) - successful_calls,
        "agent_latency_seconds_mean": mean(call_latencies) if call_latencies else None,
        "agent_latency_seconds_p95": _p95(call_latencies),
        "agent_top1_change_rate": (
            sum(bool(item.get("changed_top1")) for item in decisions) / required
        ),
        "route_mode": route_manifest.get("mode"),
        "model_version": route_manifest.get("model_version"),
        "external_generation_count": action_counts["CALL_EXTERNAL_AIGC"],
        "direct_cost_cny_total": direct_cost,
        "proxy_routing_regret_mean": mean(regrets) if regrets else None,
        "proxy_delta_vs_deterministic_mean": (
            mean(deltas_vs_deterministic) if deltas_vs_deterministic else None
        ),
    }


def _generation_selector_row(
    run_dir: Path,
    manifest: RunManifest,
    metrics: dict[str, dict[str, Any]],
    candidates: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    decision_dir = run_dir / "decisions"
    files = sorted(decision_dir.glob("*.json"))
    if not files:
        return None
    decisions = [json.loads(path.read_text(encoding="utf-8")) for path in files]
    by_task = {str(item["task_id"]): item for item in decisions}
    required_tasks = set(manifest.task_ids)
    if len(decisions) != len(required_tasks) or set(by_task) != required_tasks:
        raise ValueError(f"Generation selector is incomplete: {len(by_task)}/{len(required_tasks)}")
    selected_ids = [str(by_task[task]["best_candidate_id"]) for task in manifest.task_ids]
    if any(candidate_id not in candidates for candidate_id in selected_ids):
        raise ValueError("Generation selector references missing candidates")
    scores = [float(metrics[item]["quality_score"]) for item in selected_ids]
    grades = [str(metrics[item]["proxy_grade"]) for item in selected_ids]
    walls = [float(candidates[item]["performance"]["wall_seconds"]) for item in selected_ids]
    oracle_by_task: dict[str, float] = {}
    for candidate_id, candidate in candidates.items():
        task_id = str(candidate["task_id"])
        oracle_by_task[task_id] = max(
            oracle_by_task.get(task_id, float("-inf")),
            float(metrics[candidate_id]["quality_score"]),
        )
    regrets = [
        oracle_by_task[task_id] - float(metrics[selected_id]["quality_score"])
        for task_id, selected_id in zip(manifest.task_ids, selected_ids, strict=True)
    ]
    required = len(required_tasks)
    return {
        "arm_id": "route:no-agent-generation-selector",
        "arm_type": "no_agent_selector",
        "required_task_count": required,
        "completed_task_count": required,
        "complete": True,
        "quality_score_mean": mean(scores),
        "proxy_a_rate": grades.count("proxy_a") / required,
        "proxy_success_rate": sum(grade in {"proxy_a", "proxy_b"} for grade in grades) / required,
        "end_to_end_wall_seconds_p50": median(walls),
        "end_to_end_wall_seconds_p95": _p95(walls),
        "wall_seconds_total": sum(walls),
        "cpu_seconds_total": None,
        "peak_rss_bytes_max": None,
        "agent_call_count": 0,
        "agent_schema_valid_count": 0,
        "agent_cache_hit_count": 0,
        "agent_attempt_count": 0,
        "agent_input_tokens_total": 0,
        "agent_output_tokens_total": 0,
        "agent_tokens_total": 0,
        "agent_token_observation_count": 0,
        "agent_observed_tokens_total": 0,
        "agent_call_rate": 0.0,
        "agent_schema_valid_rate": None,
        "agent_cache_hit_rate": None,
        "agent_failure_count": 0,
        "agent_latency_seconds_mean": None,
        "agent_latency_seconds_p95": None,
        "agent_top1_change_rate": None,
        "route_mode": None,
        "model_version": None,
        "external_generation_count": 0,
        "direct_cost_cny_total": 0.0,
        "proxy_routing_regret_mean": mean(regrets),
        "proxy_delta_vs_deterministic_mean": None,
    }


def _proxy_oracle_row(
    manifest: RunManifest,
    metrics: dict[str, dict[str, Any]],
    candidates: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Return a post-hoc metric argmax upper bound, not a deployable online policy."""

    by_task: dict[str, list[str]] = defaultdict(list)
    for candidate_id, candidate in candidates.items():
        by_task[str(candidate["task_id"])].append(candidate_id)
    selected_ids = [
        max(
            by_task[task_id],
            key=lambda candidate_id: (
                float(metrics[candidate_id]["quality_score"]),
                candidate_id,
            ),
        )
        for task_id in manifest.task_ids
    ]
    scores = [float(metrics[candidate_id]["quality_score"]) for candidate_id in selected_ids]
    grades = [str(metrics[candidate_id]["proxy_grade"]) for candidate_id in selected_ids]
    walls = [
        float(candidates[candidate_id]["performance"]["wall_seconds"])
        for candidate_id in selected_ids
    ]
    required = len(manifest.task_ids)
    return {
        "arm_id": "upper-bound:posthoc-proxy-argmax",
        "arm_type": "posthoc_proxy_upper_bound",
        "required_task_count": required,
        "completed_task_count": required,
        "complete": True,
        "quality_score_mean": mean(scores),
        "proxy_a_rate": grades.count("proxy_a") / required,
        "proxy_success_rate": sum(grade in {"proxy_a", "proxy_b"} for grade in grades) / required,
        "end_to_end_wall_seconds_p50": median(walls),
        "end_to_end_wall_seconds_p95": _p95(walls),
        "wall_seconds_total": sum(walls),
        "cpu_seconds_total": None,
        "peak_rss_bytes_max": None,
        "agent_call_count": 0,
        "agent_schema_valid_count": 0,
        "agent_cache_hit_count": 0,
        "agent_attempt_count": 0,
        "agent_input_tokens_total": 0,
        "agent_output_tokens_total": 0,
        "agent_tokens_total": 0,
        "agent_token_observation_count": 0,
        "agent_observed_tokens_total": 0,
        "agent_call_rate": 0.0,
        "agent_schema_valid_rate": None,
        "agent_cache_hit_rate": None,
        "agent_failure_count": 0,
        "agent_latency_seconds_mean": None,
        "agent_latency_seconds_p95": None,
        "agent_top1_change_rate": None,
        "route_mode": "posthoc_only",
        "model_version": None,
        "external_generation_count": 0,
        "direct_cost_cny_total": 0.0,
        "proxy_routing_regret_mean": 0.0,
        "proxy_delta_vs_deterministic_mean": None,
    }


def build_benchmark_report(
    run_dir: Path,
    evaluation_id: str,
    benchmark_id: str,
    route_ids: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Aggregate only complete method and route arms; reject partial denominators."""

    validate_id(benchmark_id)
    run_dir = run_dir.resolve()
    store = LocalArtifactStore(run_dir)
    output_base = f"benchmarks/{benchmark_id}"
    if store.path(f"{output_base}/report.json").exists():
        raise FileExistsError(f"benchmark_id already exists: {benchmark_id}")
    manifest = RunManifest.model_validate(store.read_json("run.json"))
    evaluation = store.read_json(f"evaluations/{evaluation_id}/evaluation.json")
    if set(evaluation["task_ids"]) != set(manifest.task_ids):
        raise ValueError("evaluation task denominator does not match the Generation Run")
    metrics: dict[str, dict[str, Any]] = {}
    for path in (run_dir / "evaluations" / evaluation_id / "metrics").glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        metrics[str(payload["candidate_id"])] = payload["metrics"]
    candidates: dict[str, dict[str, Any]] = {}
    for path in (run_dir / "candidates").glob("*/*/candidate.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        candidates[str(payload["candidate_id"])] = payload
    expected_candidates = set(manifest.candidate_ids)
    if set(metrics) != expected_candidates or set(candidates) != expected_candidates:
        raise ValueError("candidate or metric denominator is incomplete")
    rows = _method_rows(run_dir, manifest, metrics, candidates)
    rows.append(_proxy_oracle_row(manifest, metrics, candidates))
    selector_row = _generation_selector_row(run_dir, manifest, metrics, candidates)
    if selector_row is not None:
        rows.append(selector_row)
    rows.extend(
        _route_row(run_dir, route_id, evaluation_id, manifest, metrics, candidates)
        for route_id in route_ids
    )
    report = {
        "schema_version": "1.0",
        "benchmark_id": benchmark_id,
        "run_id": manifest.run_id,
        "evaluation_id": evaluation_id,
        "task_count": len(manifest.task_ids),
        "candidate_count": len(manifest.candidate_ids),
        "all_arms_complete": all(row["complete"] for row in rows),
        "calibration_status": "uncalibrated_automatic_proxy_no_human_ground_truth",
        "route_ids": list(route_ids),
        "rows": rows,
        "report_hash": sha256_json(rows),
        "notes": [
            "Partial arms are rejected rather than ranked.",
            "Proxy quality and regret are automatic evidence, not human ground truth.",
            "The post-hoc proxy argmax row is an evaluation upper bound, not an online policy.",
            "Unknown infrastructure or provider costs remain null rather than zero.",
        ],
    }
    store.write_json(f"{output_base}/report.json", report)
    fieldnames = list(rows[0]) if rows else []
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    _atomic_text(store.path(f"{output_base}/arms.csv"), buffer.getvalue())
    return report
