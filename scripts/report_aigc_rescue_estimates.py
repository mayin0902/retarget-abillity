"""Validate and estimate AIGC rescue policies on the frozen Full300 benchmark.

No provider call is made. The script joins frozen Qwen4/rules decisions with the
already completed AIGC30 generation and evaluation evidence, then extrapolates
only the untested denominator explicitly described in the output.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
AIGC_RUN = ROOT / "runs/aigc30-seedream5-v3-20260812"
AIGC_EVALUATION = AIGC_RUN / "evaluations/auto-proxy-v1p1-aigc30-20260812"
FULL300_BENCHMARK = (
    ROOT / "runs/full300-square-public-v2-20260812/benchmarks/full300-agent-complete-v2/report.json"
)
BASELINE_ARM_IDS = (
    "posthoc-proxy-upper-bound",
    "method-direct-warp",
    "method-crop",
    "method-seam",
    "method-mesh",
    "no-agent-selector",
    "rules-router",
    "qwen3vl4b-always",
    "qwen3vl4b-conditional",
    "qwen3vl8b-conditional",
    "smolvlm2-conditional",
)
ROUNDS = {
    "pilot60": {
        "run": ROOT / "runs/square-public-v2-pilot60-20260812",
        "qwen4": "agent-qwen3vl4b-conditional-pilot-v1",
        "rules": "rules-router-v1",
    },
    "heldout240": {
        "run": ROOT / "runs/square-public-v2-heldout240-20260812",
        "qwen4": "agent-qwen3vl4b-conditional-heldout-v1",
        "rules": "rules-router-heldout-v1",
    },
}


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _wilson_interval(successes: int, total: int) -> tuple[float, float]:
    """Return a two-sided 95% Wilson interval for a binomial proportion."""

    if not 0 <= successes <= total or total <= 0:
        raise ValueError("invalid binomial counts")
    z = 1.959963984540054
    proportion = successes / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    half_width = (
        z
        * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total))
        / denominator
    )
    return center - half_width, center + half_width


def _all_decisions(route: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for spec in ROUNDS.values():
        directory = spec["run"] / "agent-runs" / spec[route] / "decisions"
        records.extend(_read_json(path) for path in sorted(directory.glob("*.json")))
    return records


def _aigc_rows(selection: dict[str, Any]) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for task in selection["tasks"]:
        task_id = task["task_id"]
        result = _read_json(AIGC_RUN / "results" / f"{task_id}.json")
        metrics = _read_json(AIGC_EVALUATION / "metrics" / f"{task_id}--seedream5.json")["metrics"]
        records[task_id] = {"selection": task, "result": result, "metrics": metrics}
    return records


def _observations(task_ids: set[str], aigc: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rows = [aigc[task_id] for task_id in sorted(task_ids & aigc.keys())]
    return {
        "observed_count": len(rows),
        "generation_success_count": sum(row["result"]["status"] == "success" for row in rows),
        "proxy_rescue_count": sum(bool(row["metrics"]["proxy_business_success"]) for row in rows),
        "charge_risk_count": sum(
            float(row["result"]["estimated_cost_max_cny"]) > 0 for row in rows
        ),
        "estimated_cost_min_cny": sum(
            float(row["result"]["estimated_cost_min_cny"]) for row in rows
        ),
        "estimated_cost_max_cny": sum(
            float(row["result"]["estimated_cost_max_cny"]) for row in rows
        ),
    }


def _measured_baselines() -> dict[str, dict[str, Any]]:
    """Read the frozen Full300 arms used as the no-AIGC comparison."""

    report = _read_json(FULL300_BENCHMARK)
    if report.get("task_count") != 300 or not report.get("all_arms_complete"):
        raise RuntimeError("Full300 benchmark must be complete with exactly 300 tasks")
    rows = {row["arm_id"]: row for row in report["rows"]}
    if missing := set(BASELINE_ARM_IDS) - rows.keys():
        raise RuntimeError(f"Full300 benchmark is missing baseline arms: {sorted(missing)}")
    baselines: dict[str, dict[str, Any]] = {}
    for arm_id in BASELINE_ARM_IDS:
        row = rows[arm_id]
        if row["required_task_count"] != 300 or row["completed_task_count"] != 300:
            raise RuntimeError(f"baseline arm has an incomplete denominator: {arm_id}")
        baselines[arm_id] = {
            "arm_type": row["arm_type"],
            "model_version": row["model_version"],
            "quality_score_mean": row["quality_score_mean"],
            "proxy_success_count": round(row["proxy_success_rate"] * 300),
            "proxy_success_rate": row["proxy_success_rate"],
            "proxy_routing_regret_mean": row["proxy_routing_regret_mean"],
            "agent_call_count": row["agent_call_count"],
            "agent_call_rate": row["agent_call_rate"],
            "agent_schema_valid_count": row["agent_schema_valid_count"],
            "agent_schema_valid_rate": row["agent_schema_valid_rate"],
            "agent_latency_seconds_mean": row["agent_latency_seconds_mean"],
            "agent_observed_tokens_total": row["agent_observed_tokens_total"],
            "route_external_request_count": row["external_generation_count"],
            "paid_aigc_call_count": 0,
            "paid_aigc_cost_cny": 0.0,
            "wall_seconds_total": row["wall_seconds_total"],
            "cpu_seconds_total": row["cpu_seconds_total"],
        }
    return baselines


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate and estimate Full300 AIGC rescue policies."
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional UTF-8 JSON output path; stdout is used when omitted.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    selection = yaml.safe_load(
        (ROOT / "datasets/retarget_square_public_v2/aigc30_selection.yaml").read_text(
            encoding="utf-8"
        )
    )
    aigc = _aigc_rows(selection)
    qwen4 = _all_decisions("qwen4")
    rules = _all_decisions("rules")
    if len(qwen4) != 300 or len(rules) != 300 or len(aigc) != 30:
        raise RuntimeError("expected exact Full300 and AIGC30 denominators")

    qwen4_success = {
        row["task_id"] for row in qwen4 if row["proxy_grade"] in {"proxy_a", "proxy_b"}
    }
    qwen4_external = {
        row["task_id"] for row in qwen4 if row["route_action"] == "CALL_EXTERNAL_AIGC"
    }
    qwen4_failure = {row["task_id"] for row in qwen4} - qwen4_success
    rules_success = {
        row["task_id"] for row in rules if row["proxy_grade"] in {"proxy_a", "proxy_b"}
    }
    rules_external = {
        row["task_id"] for row in rules if row["route_action"] == "CALL_EXTERNAL_AIGC"
    }
    rules_failure = {row["task_id"] for row in rules} - rules_success
    if qwen4_external != qwen4_failure:
        raise RuntimeError("Qwen4 external route must equal its Proxy-C set")
    if not rules_external <= rules_failure:
        raise RuntimeError("rules external route contains a Proxy-success task")

    all_observed = _observations(set(aigc), aigc)
    qwen4_observed = _observations(qwen4_external, aigc)
    qwen4_nonexternal_observed = _observations(
        {row["task_id"] for row in qwen4} - qwen4_external, aigc
    )
    rules_observed = _observations(rules_external, aigc)
    missing_qwen4 = qwen4_external - aigc.keys()
    with (ROOT / "datasets/retarget_square_public_v2/full300_source_manifest.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as handle:
        source_rows = {
            f"{row['source_id']}__square-1024x1024": row for row in csv.DictReader(handle)
        }
    missing_profiles = Counter(
        (source_rows[task_id]["scene_category"], source_rows[task_id]["difficulty_tier"])
        for task_id in missing_qwen4
    )
    missing_rows = [
        row
        for row in (
            item
            for item in selection["tasks"]
            if item["scene_category"] == "landscape_architecture_structure"
            and item["qwen4_route_action"] == "CALL_EXTERNAL_AIGC"
        )
    ]
    landscape_ids = {row["task_id"] for row in missing_rows}
    landscape_observed = _observations(landscape_ids, aigc)
    if (
        missing_profiles != Counter({("landscape_architecture_structure", "aspect_extreme"): 4})
        or landscape_observed["observed_count"] != 4
    ):
        raise RuntimeError("expected four missing Qwen4 failures and four scene analogues")

    all_generation_rate = all_observed["generation_success_count"] / 30
    all_proxy_rate = all_observed["proxy_rescue_count"] / 30
    all_proxy_ci = _wilson_interval(all_observed["proxy_rescue_count"], 30)
    all_generation_ci = _wilson_interval(all_observed["generation_success_count"], 30)
    qwen4_failure_weight = len(qwen4_external)
    qwen4_success_weight = 300 - qwen4_failure_weight
    reweighted_generation_count = qwen4_failure_weight * (
        qwen4_observed["generation_success_count"] / qwen4_observed["observed_count"]
    ) + qwen4_success_weight * (
        qwen4_nonexternal_observed["generation_success_count"]
        / qwen4_nonexternal_observed["observed_count"]
    )
    reweighted_proxy_count = qwen4_failure_weight * (
        qwen4_observed["proxy_rescue_count"] / qwen4_observed["observed_count"]
    ) + qwen4_success_weight * (
        qwen4_nonexternal_observed["proxy_rescue_count"]
        / qwen4_nonexternal_observed["observed_count"]
    )
    reweighted_charge_risk_count = qwen4_failure_weight * (
        qwen4_observed["charge_risk_count"] / qwen4_observed["observed_count"]
    ) + qwen4_success_weight * (
        qwen4_nonexternal_observed["charge_risk_count"]
        / qwen4_nonexternal_observed["observed_count"]
    )

    missing_generation = 4 * (
        landscape_observed["generation_success_count"] / landscape_observed["observed_count"]
    )
    missing_rescue = 4 * (
        landscape_observed["proxy_rescue_count"] / landscape_observed["observed_count"]
    )
    qwen4_estimated_generation = qwen4_observed["generation_success_count"] + missing_generation
    qwen4_estimated_rescue = qwen4_observed["proxy_rescue_count"] + missing_rescue
    qwen4_rescue_ci = _wilson_interval(
        qwen4_observed["proxy_rescue_count"], qwen4_observed["observed_count"]
    )
    qwen4_estimated_cost_min = qwen4_observed["estimated_cost_min_cny"] + 4 * (
        landscape_observed["estimated_cost_min_cny"] / landscape_observed["observed_count"]
    )
    qwen4_estimated_cost_max = qwen4_observed["estimated_cost_max_cny"] + 4 * (
        landscape_observed["estimated_cost_max_cny"] / landscape_observed["observed_count"]
    )

    rules_scale = len(rules_external) / rules_observed["observed_count"]
    rules_estimated_generation = rules_observed["generation_success_count"] * rules_scale
    rules_estimated_rescue = rules_observed["proxy_rescue_count"] * rules_scale
    rules_rescue_ci = _wilson_interval(
        rules_observed["proxy_rescue_count"], rules_observed["observed_count"]
    )
    rules_estimated_cost_min = rules_observed["estimated_cost_min_cny"] * rules_scale
    rules_estimated_cost_max = rules_observed["estimated_cost_max_cny"] * rules_scale
    measured_baselines = _measured_baselines()
    qwen4_always = measured_baselines["qwen3vl4b-always"]
    qwen4_conditional = measured_baselines["qwen3vl4b-conditional"]
    no_agent = measured_baselines["no-agent-selector"]

    report = {
        "schema_version": "1.1",
        "benchmark_id": "retarget_square_public_v2_full300_aigc_rescue_estimate_v2",
        "no_new_api_calls": True,
        "success_definition": (
            "proxy_business_success (Proxy A/B); provider output success is reported separately"
        ),
        "observed_evidence": {
            "aigc30": all_observed,
            "qwen4_failed_tasks_in_aigc30": qwen4_observed,
            "qwen4_nonfailed_tasks_in_aigc30": qwen4_nonexternal_observed,
            "rules_external_tasks_in_aigc30": rules_observed,
            "landscape_analogues_for_four_missing_qwen4_tasks": landscape_observed,
        },
        "measured_full300_no_aigc_baselines": measured_baselines,
        "measured_qwen4_comparisons": {
            "conditional_vs_no_agent": {
                "quality_score_delta": qwen4_conditional["quality_score_mean"]
                - no_agent["quality_score_mean"],
                "proxy_success_count_delta": qwen4_conditional["proxy_success_count"]
                - no_agent["proxy_success_count"],
                "proxy_success_rate_delta": qwen4_conditional["proxy_success_rate"]
                - no_agent["proxy_success_rate"],
            },
            "conditional_vs_always": {
                "agent_calls_saved": qwen4_always["agent_call_count"]
                - qwen4_conditional["agent_call_count"],
                "agent_call_reduction_rate": (
                    qwen4_always["agent_call_count"] - qwen4_conditional["agent_call_count"]
                )
                / qwen4_always["agent_call_count"],
                "quality_score_delta": qwen4_conditional["quality_score_mean"]
                - qwen4_always["quality_score_mean"],
                "proxy_success_count_delta": qwen4_conditional["proxy_success_count"]
                - qwen4_always["proxy_success_count"],
            },
        },
        "all_aigc_full300_estimate": {
            "api_call_count": 300,
            "generation_success_rate": all_generation_rate,
            "generation_success_count": 300 * all_generation_rate,
            "generation_success_rate_wilson95": all_generation_ci,
            "proxy_success_rate": all_proxy_rate,
            "proxy_success_count": 300 * all_proxy_rate,
            "proxy_success_rate_wilson95": all_proxy_ci,
            "estimated_cost_min_cny": 10 * all_observed["estimated_cost_min_cny"],
            "estimated_cost_max_cny": 10 * all_observed["estimated_cost_max_cny"],
            "planning_hard_max_cny": 300 * 0.60,
            "full300_qwen4_stratum_reweighted": {
                "generation_success_rate": reweighted_generation_count / 300,
                "generation_success_count": reweighted_generation_count,
                "proxy_success_rate": reweighted_proxy_count / 300,
                "proxy_success_count": reweighted_proxy_count,
                "estimated_cost_min_cny": reweighted_charge_risk_count * 0.30,
                "estimated_cost_max_cny": reweighted_charge_risk_count * 0.60,
                "note": (
                    "Reweights the 17 Qwen4-failure and 13 nonfailure AIGC30 strata to "
                    "the Full300 21/279 distribution; controls were still difficulty-selected."
                ),
            },
        },
        "qwen4_auto_aigc_estimate": {
            "baseline_proxy_success_count": len(qwen4_success),
            "baseline_proxy_success_rate": len(qwen4_success) / 300,
            "aigc_trigger_count": len(qwen4_external),
            "directly_observed_trigger_count": qwen4_observed["observed_count"],
            "estimated_aigc_generation_success_count": qwen4_estimated_generation,
            "estimated_aigc_rescue_count": qwen4_estimated_rescue,
            "estimated_rescue_rate": qwen4_estimated_rescue / len(qwen4_external),
            "direct_rescue_rate_wilson95": qwen4_rescue_ci,
            "estimated_final_proxy_success_count": len(qwen4_success) + qwen4_estimated_rescue,
            "estimated_final_proxy_success_rate": (len(qwen4_success) + qwen4_estimated_rescue)
            / 300,
            "estimated_cost_min_cny": qwen4_estimated_cost_min,
            "estimated_cost_max_cny": qwen4_estimated_cost_max,
            "planning_hard_max_cny": len(qwen4_external) * 0.60,
            "missing_task_ids": sorted(missing_qwen4),
        },
        "rules_auto_aigc_estimate": {
            "baseline_proxy_success_count": len(rules_success),
            "baseline_proxy_success_rate": len(rules_success) / 300,
            "baseline_proxy_failure_count": len(rules_failure),
            "aigc_trigger_count": len(rules_external),
            "non_triggered_proxy_failure_count": len(rules_failure - rules_external),
            "directly_observed_trigger_count": rules_observed["observed_count"],
            "estimated_aigc_generation_success_count": rules_estimated_generation,
            "estimated_aigc_rescue_count": rules_estimated_rescue,
            "estimated_rescue_rate": rules_estimated_rescue / len(rules_external),
            "direct_rescue_rate_wilson95": rules_rescue_ci,
            "estimated_final_proxy_success_count": len(rules_success) + rules_estimated_rescue,
            "estimated_final_proxy_success_rate": (len(rules_success) + rules_estimated_rescue)
            / 300,
            "estimated_cost_min_cny": rules_estimated_cost_min,
            "estimated_cost_max_cny": rules_estimated_cost_max,
            "planning_hard_max_cny": len(rules_external) * 0.60,
        },
        "notes": [
            (
                "All measured_full300_no_aigc_baselines made zero paid AIGC calls; "
                "route_external_request_count is a frozen routing decision, not a provider call."
            ),
            (
                "Qwen4 uses direct evidence for 17/21 failed tasks; four missing "
                "extreme-structure tasks use the 2/4 same-scene Proxy-success rate."
            ),
            (
                "Rules uses direct evidence for 17/34 external-route tasks and scales "
                "that route-conditioned sample by exactly two."
            ),
            "AIGC30 is small; Wilson intervals show sampling uncertainty and are not SLA bounds.",
            (
                "Costs use observed charge-risk outcomes and the user-provided 0.30-0.60 "
                "CNY range; hard max assumes every call costs 0.60 CNY."
            ),
            (
                "Fallback preserves an output for every task, but a Proxy-C fallback is "
                "not counted as a quality success."
            ),
        ],
    }
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output is None:
        print(payload, end="")
        return
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(payload, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
