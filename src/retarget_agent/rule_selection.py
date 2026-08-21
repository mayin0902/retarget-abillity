"""One authoritative seam for Rule ranking, persistence and selected outputs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .agents import deterministic_ranking, evidence_from_metrics
from .hashing import short_hash
from .models import (
    CandidateRecord,
    EvaluationManifest,
    GenerationStatus,
    RuleDecisionRecord,
    RunManifest,
)
from .storage import LocalArtifactStore
from .strategy import LoadedStrategyBundle, load_strategy_bundle


def _candidate_records(run_dir: Path, task_id: str) -> tuple[CandidateRecord, ...]:
    return tuple(
        CandidateRecord.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted((run_dir / "candidates" / task_id).glob("*/candidate.json"))
    )


def _metrics(evaluation_dir: Path, candidate_id: str) -> dict[str, Any]:
    payload = json.loads(
        (evaluation_dir / "metrics" / f"{candidate_id}.json").read_text(encoding="utf-8")
    )
    return dict(payload["metrics"])


def build_rule_decision(
    *,
    source_run: RunManifest,
    evaluation: EvaluationManifest,
    task_id: str,
    candidates: tuple[CandidateRecord, ...],
    metrics_by_candidate: dict[str, dict[str, Any]],
    strategy_bundle: LoadedStrategyBundle | None,
    decision_source: str = "evaluation",
) -> RuleDecisionRecord:
    """Rank one task once through the configured Rule selector."""
    evidence = tuple(
        evidence_from_metrics(
            candidate.candidate_id,
            candidate.method_id,
            metrics_by_candidate[candidate.candidate_id],
            candidate.model_dump(mode="json"),
        )
        for candidate in candidates
    )
    if strategy_bundle is None:
        selector = deterministic_ranking
        selection = None
        selector_id = "deterministic_rule_ranking_v1"
        selector_version = "1.0.0"
    else:
        from .plugin_catalog import built_in_plugin_catalog

        selector_id = strategy_bundle.bundle.rule_selector_plugin
        selector = built_in_plugin_catalog().selectors.get(selector_id)
        selection = strategy_bundle.selection
        selector_version = strategy_bundle.selection.version
    ranking = selector(evidence, selection)
    if len(ranking) != len(candidates) or set(ranking) != {
        candidate.candidate_id for candidate in candidates
    }:
        raise ValueError("Rule selector must return every candidate exactly once")
    by_id = {candidate.candidate_id: candidate for candidate in candidates}
    selected = next(
        (
            candidate_id
            for candidate_id in ranking
            if by_id[candidate_id].output is not None
            and by_id[candidate_id].generation_status is not GenerationStatus.FAILED
        ),
        None,
    )
    failed = tuple(
        candidate.candidate_id
        for candidate in candidates
        if candidate.output is None or candidate.generation_status is GenerationStatus.FAILED
    )
    reasons = ["complete_rule_ranking_frozen"]
    if selected is None:
        reasons.append("no_available_candidate")
    elif ranking and selected != ranking[0]:
        reasons.append("unavailable_ranking_head_skipped")
    return RuleDecisionRecord(
        decision_id=f"rule-decision-{short_hash(evaluation.evaluation_id + task_id)}",
        source_run_id=source_run.run_id,
        evaluation_id=evaluation.evaluation_id,
        task_id=task_id,
        selector_id=selector_id,
        selector_version=selector_version,
        strategy_id=evaluation.strategy_id,
        strategy_version=evaluation.strategy_version,
        strategy_sha256=evaluation.strategy_sha256,
        candidate_ranking=ranking,
        selected_candidate_id=selected,
        failed_candidate_ids=failed,
        reason_codes=tuple(reasons),
        decision_source=decision_source,
    )


def materialize_rule_decisions(
    run_dir: Path,
    evaluation: EvaluationManifest,
    strategy_bundle: LoadedStrategyBundle | None,
) -> tuple[RuleDecisionRecord, ...]:
    """Persist one complete Rule decision per Evaluation task."""
    root = run_dir.resolve()
    store = LocalArtifactStore(root)
    source_run = RunManifest.model_validate(store.read_json("run.json"))
    evaluation_dir = root / "evaluations" / evaluation.evaluation_id
    decisions = []
    for task_id in evaluation.task_ids:
        candidates = _candidate_records(root, task_id)
        metrics_by_candidate = {
            candidate.candidate_id: _metrics(evaluation_dir, candidate.candidate_id)
            for candidate in candidates
        }
        decision = build_rule_decision(
            source_run=source_run,
            evaluation=evaluation,
            task_id=task_id,
            candidates=candidates,
            metrics_by_candidate=metrics_by_candidate,
            strategy_bundle=strategy_bundle,
        )
        store.write_json(
            f"evaluations/{evaluation.evaluation_id}/rule-decisions/{task_id}.json",
            decision,
        )
        decisions.append(decision)
    store.write_json(
        f"evaluations/{evaluation.evaluation_id}/rule-decisions.json",
        {
            "schema_version": "1.0",
            "evaluation_id": evaluation.evaluation_id,
            "task_ids": list(evaluation.task_ids),
            "decision_ids": [decision.decision_id for decision in decisions],
        },
    )
    return tuple(decisions)


def load_rule_decision(
    run_dir: Path,
    evaluation_id: str,
    task_id: str,
    *,
    allow_legacy_reconstruction: bool = True,
) -> RuleDecisionRecord:
    """Load a frozen decision; reconstruct old Runs through the same seam when requested."""
    root = run_dir.resolve()
    path = root / "evaluations" / evaluation_id / "rule-decisions" / f"{task_id}.json"
    if path.is_file():
        return RuleDecisionRecord.model_validate_json(path.read_text(encoding="utf-8"))
    if not allow_legacy_reconstruction:
        raise FileNotFoundError(path)
    source_run = RunManifest.model_validate_json((root / "run.json").read_text(encoding="utf-8"))
    evaluation_dir = root / "evaluations" / evaluation_id
    evaluation_payload = json.loads(
        (evaluation_dir / "evaluation.json").read_text(encoding="utf-8")
    )
    try:
        evaluation = EvaluationManifest.model_validate(evaluation_payload)
    except ValueError:
        # Early 0.x Runs and lightweight fixtures did not yet freeze the complete
        # Evaluation contract.  Reconstruct only enough metadata to rank through
        # the same module, and label the result legacy_reconstructed below.
        evaluation = EvaluationManifest(
            evaluation_id=evaluation_id,
            source_run_id=source_run.run_id,
            evaluator_id=str(evaluation_payload.get("evaluator_id", "legacy-evaluator")),
            evaluator_version=str(evaluation_payload.get("evaluator_version", "0.0.0")),
            config_hash=str(evaluation_payload.get("config_hash", source_run.config_hash)),
            strategy_id=evaluation_payload.get("strategy_id"),
            strategy_version=evaluation_payload.get("strategy_version"),
            strategy_sha256=evaluation_payload.get("strategy_sha256"),
            strategy_snapshot=evaluation_payload.get("strategy_snapshot"),
            task_ids=source_run.task_ids,
            candidate_ids=source_run.candidate_ids,
            metric_bundle_ids=(),
        )
    strategy = None
    if evaluation.strategy_snapshot:
        snapshot_bundle = root / evaluation.strategy_snapshot / "bundle.yaml"
        if snapshot_bundle.is_file():
            strategy = load_strategy_bundle(snapshot_bundle)
    candidates = _candidate_records(root, task_id)
    return build_rule_decision(
        source_run=source_run,
        evaluation=evaluation,
        task_id=task_id,
        candidates=candidates,
        metrics_by_candidate={
            candidate.candidate_id: _metrics(evaluation_dir, candidate.candidate_id)
            for candidate in candidates
        },
        strategy_bundle=strategy,
        decision_source="legacy_reconstructed",
    )


def materialize_selected_results(run_dir: Path, evaluation_id: str) -> dict[str, Any]:
    """Copy Rule-selected outputs into stable result paths for single and batch runs."""
    root = run_dir.resolve()
    store = LocalArtifactStore(root)
    evaluation = EvaluationManifest.model_validate(
        store.read_json(f"evaluations/{evaluation_id}/evaluation.json")
    )
    outputs: list[dict[str, Any]] = []
    for task_id in evaluation.task_ids:
        decision = load_rule_decision(
            root, evaluation_id, task_id, allow_legacy_reconstruction=False
        )
        if decision.selected_candidate_id is None:
            outputs.append({"task_id": task_id, "status": "no_available_candidate"})
            continue
        selected = next(
            candidate
            for candidate in _candidate_records(root, task_id)
            if candidate.candidate_id == decision.selected_candidate_id
        )
        if selected.output is None:
            raise ValueError("selected Rule candidate has no output")
        metric = _metrics(root / "evaluations" / evaluation_id, selected.candidate_id)
        result_dir = f"results/{evaluation_id}/{task_id}"
        result_ref = store.copy_file(
            f"{result_dir}/result.png",
            root / selected.output.relative_path,
            "image/png",
        )
        payload = {
            "schema_version": "1.0",
            "run_id": decision.source_run_id,
            "evaluation_id": evaluation_id,
            "task_id": task_id,
            "selected_candidate_id": selected.candidate_id,
            "selected_method_id": selected.method_id,
            "candidate_ranking": list(decision.candidate_ranking),
            "quality_score": metric.get("quality_score"),
            "proxy_grade": metric.get("proxy_grade"),
            "result": result_ref.model_dump(mode="json"),
            "rule_decision": (
                f"evaluations/{evaluation_id}/rule-decisions/{task_id}.json"
            ),
        }
        store.write_json(f"{result_dir}/result.json", payload)
        outputs.append(payload)
    if len(outputs) == 1 and outputs[0].get("status") != "no_available_candidate":
        source = root / str(outputs[0]["result"]["relative_path"])
        store.copy_file("result.png", source, "image/png")
        store.write_json("result.json", outputs[0])
    return {"evaluation_id": evaluation_id, "results": outputs}


__all__ = [
    "build_rule_decision",
    "load_rule_decision",
    "materialize_rule_decisions",
    "materialize_selected_results",
]
