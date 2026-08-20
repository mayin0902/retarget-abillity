"""Budgeted, auditable task selection for external-generation materialization."""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from .models import RunManifest, validate_id
from .storage import LocalArtifactStore


def _truthy(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes"}


def _source_id(task_id: str) -> str:
    return task_id.split("__", 1)[0]


def _top_scores(run_dir: Path, evaluation_id: str) -> dict[str, float]:
    scores: dict[str, float] = {}
    metric_dir = run_dir / "evaluations" / evaluation_id / "metrics"
    for path in metric_dir.glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        candidate_id = str(payload["candidate_id"])
        task_id = candidate_id.split("--", 1)[0]
        score = float(payload["metrics"]["quality_score"])
        scores[task_id] = max(scores.get(task_id, float("-inf")), score)
    return scores


def plan_external_generation(
    run_dir: Path,
    evaluation_id: str,
    generation_plan_id: str,
    agent_run_ids: tuple[str, ...],
    source_audit_path: Path,
    *,
    maximum_paid_calls: int = 12,
    estimated_cost_min_cny: float = 0.30,
    estimated_cost_max_cny: float = 0.60,
) -> dict[str, Any]:
    """Freeze a multi-Agent vote plan without making any provider call."""

    validate_id(generation_plan_id)
    if maximum_paid_calls < 0:
        raise ValueError("maximum_paid_calls must be non-negative")
    if not 0 <= estimated_cost_min_cny <= estimated_cost_max_cny:
        raise ValueError("invalid estimated provider cost range")
    run_dir = run_dir.resolve()
    store = LocalArtifactStore(run_dir)
    output_path = store.path(f"generation-plans/{generation_plan_id}/plan.json")
    if output_path.exists():
        raise FileExistsError(f"generation_plan_id already exists: {generation_plan_id}")
    run = RunManifest.model_validate(store.read_json("run.json"))
    scores = _top_scores(run_dir, evaluation_id)
    if set(scores) != set(run.task_ids):
        raise ValueError("evaluation score denominator does not match the Generation Run")
    with source_audit_path.open("r", encoding="utf-8-sig", newline="") as handle:
        audits = {row["source_id"]: row for row in csv.DictReader(handle)}

    votes: dict[str, set[str]] = defaultdict(set)
    requested_by: dict[str, list[str]] = defaultdict(list)
    for agent_run_id in agent_run_ids:
        agent_base = run_dir / "agent-runs" / agent_run_id
        manifest = json.loads((agent_base / "agent-run.json").read_text(encoding="utf-8"))
        model_identity = str(
            manifest.get("model_version") or manifest.get("agent_id") or agent_run_id
        )
        decision_files = sorted((agent_base / "decisions").glob("*.json"))
        decisions = [json.loads(path.read_text(encoding="utf-8")) for path in decision_files]
        if len(decisions) != len(run.task_ids) or {
            str(item["task_id"]) for item in decisions
        } != set(run.task_ids):
            raise ValueError(f"Agent run {agent_run_id} is not complete")
        for decision in decisions:
            if decision["route_action"] == "CALL_EXTERNAL_AIGC":
                task_id = str(decision["task_id"])
                votes[task_id].add(model_identity)
                requested_by[task_id].append(agent_run_id)

    ranked: list[dict[str, Any]] = []
    for task_id in sorted(votes):
        source_id = _source_id(task_id)
        audit = audits.get(source_id, {})
        egress_allowed = _truthy(audit.get("api_egress_allowed"))
        public_source = str(audit.get("source_kind", "public_real")) == "public_real"
        license_audited = str(audit.get("license_status", "audited")) == "audited"
        eligible = egress_allowed and public_source and license_audited
        reasons: list[str] = []
        if not egress_allowed:
            reasons.append("api_egress_not_approved")
        if not public_source:
            reasons.append("source_not_public_real")
        if not license_audited:
            reasons.append("license_not_audited")
        ranked.append(
            {
                "task_id": task_id,
                "source_id": source_id,
                "model_vote_count": len(votes[task_id]),
                "model_votes": sorted(votes[task_id]),
                "requested_by_agent_runs": sorted(requested_by[task_id]),
                "top_traditional_proxy_score": scores[task_id],
                "api_egress_allowed": egress_allowed,
                "eligible": eligible,
                "selected_for_generation": False,
                "priority_rank": None,
                "reason_codes": reasons,
            }
        )
    ranked.sort(
        key=lambda item: (
            not item["eligible"],
            -int(item["model_vote_count"]),
            float(item["top_traditional_proxy_score"]),
            str(item["task_id"]),
        )
    )
    selected = 0
    for item in ranked:
        if item["eligible"] and selected < maximum_paid_calls:
            selected += 1
            item["selected_for_generation"] = True
            item["priority_rank"] = selected
        elif item["eligible"]:
            item["reason_codes"].append("global_paid_call_cap_reached")
    report = {
        "schema_version": "1.0",
        "generation_plan_id": generation_plan_id,
        "run_id": run.run_id,
        "evaluation_id": evaluation_id,
        "agent_run_ids": list(agent_run_ids),
        "task_count": len(run.task_ids),
        "requested_task_count": len(ranked),
        "eligible_task_count": sum(bool(item["eligible"]) for item in ranked),
        "selected_paid_call_count": selected,
        "maximum_paid_calls": maximum_paid_calls,
        "estimated_cost_min_cny": selected * estimated_cost_min_cny,
        "estimated_cost_max_cny": selected * estimated_cost_max_cny,
        "actual_cost_cny": None,
        "entries": ranked,
        "notes": [
            "This plan performs no provider calls.",
            "Conditional and always-on runs of the same model contribute one model vote.",
            "Non-selected requests use recorded traditional fallback outputs.",
        ],
    }
    store.write_json(f"generation-plans/{generation_plan_id}/plan.json", report)
    return report
