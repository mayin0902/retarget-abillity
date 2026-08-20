"""Deterministically replay a grading-only strategy patch over completed Agent evidence."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from .hashing import sha256_file
from .rule_anchored_review import RuleAnchoredTaskDecision
from .strategy import LoadedStrategyBundle
from .strict_review import MachineGrade


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def replay_combination_policy(
    run_dir: Path,
    *,
    source_review_run_id: str,
    overview_agent_run_id: str,
    evaluation_id: str,
    review_run_id: str,
    strategy: LoadedStrategyBundle,
) -> dict[str, Any]:
    """Reuse immutable visual decisions and recompute only the final combined grade."""

    run_dir = run_dir.resolve()
    source = run_dir / "strict-reviews" / source_review_run_id
    source_summary_path = source / "summary.json"
    if not source_summary_path.is_file():
        raise ValueError("source strict review has no completed summary")
    source_summary = _read_json(source_summary_path)
    if source_summary.get("complete") is not True:
        raise ValueError("source strict review is not complete")
    expected_parent = strategy.bundle.parent_strategy
    observed_parent = (
        f"{source_summary.get('strategy_id')}@{source_summary.get('strategy_version')}"
    )
    if expected_parent != observed_parent:
        raise ValueError(
            f"strategy parent {expected_parent!r} does not match source {observed_parent!r}"
        )
    if strategy.override.combined_grade_source != "rule_metric":
        raise ValueError("combination replay requires combined_grade_source=rule_metric")

    evaluation = run_dir / "evaluations" / evaluation_id
    evaluation_summary_path = evaluation / "summary.json"
    evaluation_manifest_path = evaluation / "evaluation.json"
    if not evaluation_summary_path.is_file() or not evaluation_manifest_path.is_file():
        raise ValueError("target evaluation is incomplete")
    if _read_json(evaluation_summary_path).get("complete") is not True:
        raise ValueError("target evaluation is not complete")

    source_decision_paths = sorted((source / "decisions").glob("*.json"))
    if len(source_decision_paths) != int(source_summary.get("task_count", -1)):
        raise ValueError("source decision denominator differs from completed summary")
    source_overview_id = str(source_summary.get("overview_agent_run_id"))
    source_overview_manifest_path = (
        run_dir / "agent-runs" / source_overview_id / "agent-run.json"
    )
    overview = run_dir / "agent-runs" / overview_agent_run_id
    overview_manifest_path = overview / "agent-run.json"
    overview_summary_path = overview / "summary.json"
    if not source_overview_manifest_path.is_file():
        raise ValueError("source strict review has no auditable overview manifest")
    if not overview_manifest_path.is_file() or not overview_summary_path.is_file():
        raise ValueError("target overview Agent Run is incomplete")
    source_overview_manifest = _read_json(source_overview_manifest_path)
    overview_manifest = _read_json(overview_manifest_path)
    overview_summary = _read_json(overview_summary_path)
    if overview_manifest.get("strategy_sha256") != strategy.source_sha256:
        raise ValueError("target overview strategy differs from combination strategy")
    if overview_manifest.get("config_hash") != source_overview_manifest.get("config_hash"):
        raise ValueError("target overview config differs from reused visual evidence")
    if overview_manifest.get("task_ids") != source_overview_manifest.get("task_ids"):
        raise ValueError("target overview task denominator differs from reused visual evidence")
    if int(overview_summary.get("task_count", -1)) != len(source_decision_paths):
        raise ValueError("target overview denominator differs from strict-review denominator")
    if float(overview_summary.get("agent_cache_hit_rate", -1.0)) != 1.0:
        raise ValueError("target overview did not reuse the frozen model responses exactly")

    output = run_dir / "strict-reviews" / review_run_id
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    strategy.snapshot_to(output / "strategy")

    decisions: list[RuleAnchoredTaskDecision] = []
    source_hashes: dict[str, str] = {}
    for source_path in source_decision_paths:
        source_hashes[source_path.stem] = sha256_file(source_path)
        decision = RuleAnchoredTaskDecision.model_validate(_read_json(source_path))
        advisory_only = strategy.override.agent_selection_mode == "advisory_only"
        selected_candidate_id = (
            decision.rule_top1_candidate_id if advisory_only else decision.selected_candidate_id
        )
        metric_path = evaluation / "metrics" / f"{selected_candidate_id}.json"
        if not metric_path.is_file():
            raise ValueError(f"missing selected-candidate metric: {selected_candidate_id}")
        metric = _read_json(metric_path).get("metrics") or {}
        raw_grade = metric.get("proxy_grade")
        if raw_grade is None:
            raise ValueError(
                f"selected candidate has no proxy_grade: {selected_candidate_id}"
            )
        combined_grade = MachineGrade(str(raw_grade).removeprefix("proxy_").upper())
        block_reasons = decision.override_block_reasons
        decision_codes = decision.decision_reason_codes
        if advisory_only:
            block_reasons = tuple(dict.fromkeys((*block_reasons, "agent_advisory_only")))
            decision_codes = (
                "rule_retained",
                *(f"blocked:{reason}" for reason in block_reasons),
            )
        updated = decision.model_copy(
            update={
                "schema_version": "1.2",
                "selected_candidate_id": selected_candidate_id,
                "selected_grade": decision.rule_grade if advisory_only else decision.selected_grade,
                "combined_grade": combined_grade,
                "combined_grade_source": "rule_metric",
                "selected_directly_usable": combined_grade in {MachineGrade.A, MachineGrade.B},
                "agent_overrode_rule": False if advisory_only else decision.agent_overrode_rule,
                "override_block_reasons": block_reasons,
                "decision_reason_codes": decision_codes,
                "request_external_aigc": combined_grade.value
                in set(strategy.override.request_aigc_grades),
            }
        )
        decisions.append(updated)
        _write_json(
            output / "decisions" / f"{updated.task_id}.json",
            updated.model_dump(mode="json"),
        )

    grade_counts = Counter(item.combined_grade.value for item in decisions)
    summary = {
        **source_summary,
        "schema_version": "1.2",
        "review_run_id": review_run_id,
        "evaluation_id": evaluation_id,
        "overview_agent_run_id": overview_agent_run_id,
        "strategy_id": strategy.bundle.strategy_id,
        "strategy_version": strategy.bundle.version,
        "strategy_sha256": strategy.source_sha256,
        "strategy_snapshot": "strategy",
        "source_review_run_id": source_review_run_id,
        "visual_evidence_reused": True,
        "visual_model_call_count": 0,
        "combined_grade_source": "rule_metric",
        "agent_selection_mode": strategy.override.agent_selection_mode,
        "agent_override_count": sum(item.agent_overrode_rule for item in decisions),
        "override_block_reason_counts": dict(
            Counter(reason for item in decisions for reason in item.override_block_reasons)
        ),
        "selected_grade_counts": dict(grade_counts),
        "selected_ab_count": sum(item.selected_directly_usable for item in decisions),
        "selected_ab_rate": sum(item.selected_directly_usable for item in decisions)
        / len(decisions),
        "aigc_request_count": sum(item.request_external_aigc for item in decisions),
        "complete": len(decisions) == len(source_decision_paths),
    }
    _write_json(output / "summary.json", summary)
    _write_json(
        output / "replay-manifest.json",
        {
            "schema_version": "1.0",
            "review_run_id": review_run_id,
            "source_review_run_id": source_review_run_id,
            "overview_agent_run_id": overview_agent_run_id,
            "source_summary_sha256": sha256_file(source_summary_path),
            "source_decision_sha256": source_hashes,
            "evaluation_id": evaluation_id,
            "evaluation_manifest_sha256": sha256_file(evaluation_manifest_path),
            "overview_manifest_sha256": sha256_file(overview_manifest_path),
            "strategy_sha256": strategy.source_sha256,
            "visual_model_calls": 0,
            "agent_selection_mode": strategy.override.agent_selection_mode,
        },
    )
    return summary


__all__ = ["replay_combination_policy"]
