"""Aggregate exact AIGC30 quality, latency and API-only cost comparisons."""

from __future__ import annotations

import csv
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path
from statistics import mean
from typing import Any

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
AIGC_RUN = ROOT / "runs/aigc30-seedream5-v3-20260812"
AIGC_EVALUATION = AIGC_RUN / "evaluations/auto-proxy-v1p1-aigc30-20260812"
RUNS = {
    "pilot60": {
        "root": ROOT / "runs/square-public-v2-pilot60-20260812",
        "evaluation": "auto-proxy-v1p1-pilot60-20260812",
        "rules": "rules-router-v1",
        "qwen4": "agent-qwen3vl4b-conditional-pilot-v1",
        "qwen8": "agent-qwen3vl8b-conditional-pilot-v1",
        "smol": "agent-smolvlm2-conditional-pilot-v2",
    },
    "heldout240": {
        "root": ROOT / "runs/square-public-v2-heldout240-20260812",
        "evaluation": "auto-proxy-v1p1-heldout240-20260812",
        "rules": "rules-router-heldout-v1",
        "qwen4": "agent-qwen3vl4b-conditional-heldout-v1",
        "qwen8": "agent-qwen3vl8b-conditional-heldout-v1",
        "smol": "agent-smolvlm2-conditional-heldout-v2",
    },
}
METHODS = ("direct_warp", "crop", "seam", "mesh")


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _metrics(run: Path, evaluation: str, task_id: str) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for path in (run / "evaluations" / evaluation / "metrics").glob(f"{task_id}--*.json"):
        payload = _json(path)
        method = payload["candidate_id"].split("--")[-2]
        rows[method] = payload["metrics"]
    if set(rows) != set(METHODS):
        raise RuntimeError(f"missing traditional metrics for {task_id}: {sorted(rows)}")
    return rows


def _candidate_records(run: Path, task_id: str) -> dict[str, dict[str, Any]]:
    return {
        method: _json(run / "candidates" / task_id / method / "candidate.json")
        for method in METHODS
    }


def _method_from_candidate(candidate_id: str) -> str:
    method = candidate_id.split("--")[-2]
    if method not in METHODS:
        raise RuntimeError(f"unknown method in candidate id: {candidate_id}")
    return method


def _agent_decision(run: Path, agent_run: str, task_id: str) -> dict[str, Any]:
    return _json(run / "agent-runs" / agent_run / "decisions" / f"{task_id}.json")


def _agent_usage(run: Path, agent_run: str, decision: dict[str, Any]) -> tuple[float, int]:
    call_id = decision.get("agent_call_id")
    if not call_id:
        return 0.0, 0
    call = _json(run / "agent-runs" / agent_run / "calls" / f"{call_id}.json")
    return float(call["latency_seconds"]), int(call.get("tokens") or 0)


def _summary(
    name: str,
    metrics: list[dict[str, Any]],
    latencies: list[float],
    *,
    observed_quality_count: int | None = None,
    api_cost_min: Decimal = Decimal("0"),
    api_cost_max: Decimal = Decimal("0"),
    agent_tokens: int = 0,
    api_call_count: int = 0,
    notes: str = "",
) -> dict[str, Any]:
    observed = [
        float(item["quality_score"]) for item in metrics if item.get("quality_score") is not None
    ]
    successes = sum(bool(item.get("proxy_business_success")) for item in metrics)
    grades = Counter(str(item.get("proxy_grade")) for item in metrics)
    denominator = len(metrics)
    return {
        "route": name,
        "task_count": denominator,
        "quality_observed_count": observed_quality_count
        if observed_quality_count is not None
        else len(observed),
        "quality_score_mean_observed": mean(observed) if observed else None,
        "quality_score_p50_observed": float(np.percentile(observed, 50)) if observed else None,
        "proxy_success_count": successes,
        "proxy_success_rate_complete_denominator": successes / denominator,
        "proxy_a_count": grades["proxy_a"],
        "proxy_a_rate_complete_denominator": grades["proxy_a"] / denominator,
        "latency_seconds_mean": mean(latencies),
        "latency_seconds_p50": float(np.percentile(latencies, 50)),
        "latency_seconds_p95": float(np.percentile(latencies, 95)),
        "api_call_count": api_call_count,
        "api_cost_min_cny": str(api_cost_min),
        "api_cost_max_cny": str(api_cost_max),
        "agent_tokens_observed": agent_tokens,
        "agent_token_cost_cny_company_scenario": "0",
        "api_cost_per_proxy_success_min_cny": str(api_cost_min / successes) if successes else None,
        "api_cost_per_proxy_success_max_cny": str(api_cost_max / successes) if successes else None,
        "notes": notes,
    }


def main() -> None:
    selection = yaml.safe_load(
        (ROOT / "datasets/retarget_square_public_v2/aigc30_selection.yaml").read_text(
            encoding="utf-8"
        )
    )
    route_metrics: dict[str, list[dict[str, Any]]] = {
        name: []
        for name in (
            *METHODS,
            "technical_top1",
            "rules_router",
            "qwen4_conditional",
            "qwen8_conditional",
            "smol_conditional",
            "pure_seedream5",
            "qwen4_seedream5_hybrid",
        )
    }
    route_latency = {name: [] for name in route_metrics}
    route_tokens = Counter()
    q4_external_count = 0
    q4_external_success = 0
    q4_fallback_count = 0
    hybrid_cost_min = Decimal("0")
    hybrid_cost_max = Decimal("0")
    pure_cost_min = Decimal("0")
    pure_cost_max = Decimal("0")
    detail_rows: list[dict[str, Any]] = []

    for selected in selection["tasks"]:
        task_id = selected["task_id"]
        split = selected["split"]
        spec = RUNS[split]
        run = spec["root"]
        evaluation = spec["evaluation"]
        metrics = _metrics(run, evaluation, task_id)
        candidates = _candidate_records(run, task_id)
        method_latency = {
            method: float(candidates[method]["performance"]["wall_seconds"]) for method in METHODS
        }
        portfolio_latency = sum(method_latency.values())
        for method in METHODS:
            route_metrics[method].append(metrics[method])
            route_latency[method].append(method_latency[method])

        technical = _json(run / "decisions" / f"{task_id}.json")
        technical_method = _method_from_candidate(technical["best_candidate_id"])
        route_metrics["technical_top1"].append(metrics[technical_method])
        route_latency["technical_top1"].append(portfolio_latency)

        decisions: dict[str, dict[str, Any]] = {}
        for short, key in (
            ("rules_router", "rules"),
            ("qwen4_conditional", "qwen4"),
            ("qwen8_conditional", "qwen8"),
            ("smol_conditional", "smol"),
        ):
            decision = _agent_decision(run, str(spec[key]), task_id)
            decisions[short] = decision
            selected_method = _method_from_candidate(decision["selected_candidate_id"])
            call_latency, tokens = _agent_usage(run, str(spec[key]), decision)
            route_metrics[short].append(metrics[selected_method])
            route_latency[short].append(portfolio_latency + call_latency)
            route_tokens[short] += tokens

        aigc_result = _json(AIGC_RUN / "results" / f"{task_id}.json")
        aigc_metric = _json(AIGC_EVALUATION / "metrics" / f"{task_id}--seedream5.json")["metrics"]
        route_metrics["pure_seedream5"].append(aigc_metric)
        route_latency["pure_seedream5"].append(float(aigc_result["wall_seconds"]))
        pure_cost_min += Decimal(aigc_result["estimated_cost_min_cny"])
        pure_cost_max += Decimal(aigc_result["estimated_cost_max_cny"])

        q4 = decisions["qwen4_conditional"]
        q4_method = _method_from_candidate(q4["selected_candidate_id"])
        q4_call_latency, _ = _agent_usage(run, str(spec["qwen4"]), q4)
        hybrid_metric = metrics[q4_method]
        hybrid_latency = portfolio_latency + q4_call_latency
        hybrid_source = q4_method
        if q4["route_action"] == "CALL_EXTERNAL_AIGC":
            q4_external_count += 1
            hybrid_cost_min += Decimal(aigc_result["estimated_cost_min_cny"])
            hybrid_cost_max += Decimal(aigc_result["estimated_cost_max_cny"])
            hybrid_latency += float(aigc_result["wall_seconds"])
            if aigc_result["status"] == "success":
                hybrid_metric = aigc_metric
                hybrid_source = "seedream5"
                q4_external_success += 1
            else:
                q4_fallback_count += 1
                hybrid_source = f"fallback:{q4_method}"
        route_metrics["qwen4_seedream5_hybrid"].append(hybrid_metric)
        route_latency["qwen4_seedream5_hybrid"].append(hybrid_latency)
        detail_rows.append(
            {
                "task_id": task_id,
                "scene_category": selected["scene_category"],
                "difficulty_tier": selected["difficulty_tier"],
                "qwen4_route_action": q4["route_action"],
                "aigc_status": aigc_result["status"],
                "aigc_quality_score": aigc_metric.get("quality_score"),
                "hybrid_source": hybrid_source,
                "hybrid_quality_score": hybrid_metric.get("quality_score"),
                "hybrid_proxy_grade": hybrid_metric.get("proxy_grade"),
                "aigc_wall_seconds": aigc_result["wall_seconds"],
                "aigc_cost_min_cny": aigc_result["estimated_cost_min_cny"],
                "aigc_cost_max_cny": aigc_result["estimated_cost_max_cny"],
            }
        )

    rows: list[dict[str, Any]] = []
    for route in route_metrics:
        api_min = Decimal("0")
        api_max = Decimal("0")
        api_calls = 0
        notes = "API-only company scenario; Agent token cost is zero."
        if route == "pure_seedream5":
            api_min, api_max, api_calls = pure_cost_min, pure_cost_max, 30
            notes += " Failed generations stay in the 30-task denominator."
        elif route == "qwen4_seedream5_hybrid":
            api_min, api_max, api_calls = (
                hybrid_cost_min,
                hybrid_cost_max,
                q4_external_count,
            )
            notes += " Failed AIGC calls fall back to Qwen4's selected traditional candidate."
        rows.append(
            _summary(
                route,
                route_metrics[route],
                route_latency[route],
                api_cost_min=api_min,
                api_cost_max=api_max,
                agent_tokens=(
                    route_tokens["qwen4_conditional"]
                    if route == "qwen4_seedream5_hybrid"
                    else route_tokens[route]
                ),
                api_call_count=api_calls,
                notes=notes,
            )
        )

    output = AIGC_RUN / "benchmarks/aigc30-api-only-v1"
    output.mkdir(parents=True, exist_ok=False)
    report = {
        "benchmark_id": "retarget_square_aigc30_v1",
        "task_count": 30,
        "cost_scenario": "company_agent_tokens_zero_only_aigc_api_counted",
        "pure_aigc": {
            "calls": 30,
            "successes": sum(
                item["metrics"].get("generation_status") == "success"
                for item in [
                    _json(AIGC_EVALUATION / "metrics" / f"{task['task_id']}--seedream5.json")
                    for task in selection["tasks"]
                ]
            ),
            "estimated_cost_min_cny": str(pure_cost_min),
            "estimated_cost_max_cny": str(pure_cost_max),
            "actual_cost_cny": None,
        },
        "hybrid": {
            "qwen4_external_requests": q4_external_count,
            "aigc_successes": q4_external_success,
            "aigc_failures_fallback": q4_fallback_count,
            "estimated_cost_min_cny": str(hybrid_cost_min),
            "estimated_cost_max_cny": str(hybrid_cost_max),
        },
        "routes": rows,
        "limitations": [
            "Automatic proxy metrics are uncalibrated and do not replace blinded human review.",
            "Provider actual billing was absent; costs are conservative 0.30-0.60 CNY ranges.",
            "AIGC transform safety is unavailable because no geometric transform log exists.",
            "Latency is observed end-to-end wall time; hybrid assumes sequential routing.",
        ],
    }
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with (output / "routes.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    with (output / "tasks.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(detail_rows[0]))
        writer.writeheader()
        writer.writerows(detail_rows)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
