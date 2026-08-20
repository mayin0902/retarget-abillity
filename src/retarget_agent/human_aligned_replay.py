"""Fast, immutable strategy replay over already measured candidate metrics."""

from __future__ import annotations

import time
from collections import Counter
from pathlib import Path

from .hashing import sha256_file, sha256_json, short_hash
from .human_aligned_scoring import apply_human_aligned_policy
from .models import (
    CandidateRecord,
    EvaluationManifest,
    MetricBundle,
    RunManifest,
    TaskSpec,
    validate_id,
)
from .storage import LocalArtifactStore
from .strategy import LoadedStrategyBundle


def replay_human_aligned_evaluation(
    run_dir: Path,
    *,
    source_evaluation_id: str,
    evaluation_id: str,
    strategy_bundle: LoadedStrategyBundle,
) -> EvaluationManifest:
    """Apply one frozen scoring bundle without rerunning detectors or generation.

    The source evaluation remains untouched.  Every copied metric records its source
    evaluation and file hash, while the new evaluation snapshots the entire strategy.
    """

    validate_id(source_evaluation_id)
    validate_id(evaluation_id)
    run_dir = run_dir.resolve()
    store = LocalArtifactStore(run_dir)
    destination = store.path(f"evaluations/{evaluation_id}")
    if destination.exists():
        raise FileExistsError(f"evaluation_id already exists: {evaluation_id}")
    source_manifest = EvaluationManifest.model_validate(
        store.read_json(f"evaluations/{source_evaluation_id}/evaluation.json")
    )
    run = RunManifest.model_validate(store.read_json("run.json"))
    if source_manifest.source_run_id != run.run_id:
        raise ValueError("source evaluation belongs to a different Run")
    source_ids = set(source_manifest.candidate_ids)
    candidates = {
        record.candidate_id: record
        for path in sorted(run_dir.glob("candidates/*/*/candidate.json"))
        if (record := CandidateRecord.model_validate_json(path.read_text(encoding="utf-8")))
    }
    if source_ids != set(candidates):
        raise ValueError("source evaluation denominator does not equal frozen candidates")

    started = time.perf_counter()
    strategy_bundle.snapshot_to(destination / "strategy")
    metric_ids: list[str] = []
    grades: Counter[str] = Counter()
    for task_id in run.task_ids:
        task = TaskSpec.model_validate(store.read_json(f"tasks/{task_id}.json"))
        task_candidates = sorted(
            (candidate for candidate in candidates.values() if candidate.task_id == task_id),
            key=lambda item: item.candidate_id,
        )
        for candidate in task_candidates:
            source_path = store.path(
                f"evaluations/{source_evaluation_id}/metrics/{candidate.candidate_id}.json"
            )
            source_metric = MetricBundle.model_validate_json(
                source_path.read_text(encoding="utf-8")
            )
            metrics = apply_human_aligned_policy(
                source_metric.metrics,
                scene_category=task.source.scene_category or "unknown",
                method_id=candidate.method_id,
                scoring_policy=strategy_bundle.scoring,
            )
            metrics.update(
                {
                    "metric_replay_status": "reused_frozen_detector_measurements",
                    "source_evaluation_id": source_evaluation_id,
                    "source_metric_sha256": sha256_file(source_path),
                    "strategy_sha256": strategy_bundle.source_sha256,
                }
            )
            metric_id = (
                f"metric-{short_hash(candidate.candidate_id + strategy_bundle.source_sha256)}"
            )
            metric = MetricBundle(
                metric_bundle_id=metric_id,
                candidate_id=candidate.candidate_id,
                evaluator_id=strategy_bundle.scoring.evaluator_id,
                evaluator_version=strategy_bundle.scoring.evaluator_version,
                metrics=metrics,
            )
            store.write_json(
                f"evaluations/{evaluation_id}/metrics/{candidate.candidate_id}.json",
                metric,
            )
            metric_ids.append(metric_id)
            grades[str(metrics.get("proxy_grade"))] += 1

    config_hash = sha256_json(
        {
            "source_evaluation_id": source_evaluation_id,
            "source_evaluation_config_hash": source_manifest.config_hash,
            "strategy_sha256": strategy_bundle.source_sha256,
            "replay_implementation": "human-aligned-metric-replay-v1",
        }
    )
    manifest = EvaluationManifest(
        evaluation_id=evaluation_id,
        source_run_id=run.run_id,
        evaluator_id=strategy_bundle.scoring.evaluator_id,
        evaluator_version=strategy_bundle.scoring.evaluator_version,
        config_hash=config_hash,
        strategy_id=strategy_bundle.bundle.strategy_id,
        strategy_version=strategy_bundle.bundle.version,
        strategy_sha256=strategy_bundle.source_sha256,
        strategy_snapshot=f"evaluations/{evaluation_id}/strategy",
        task_ids=run.task_ids,
        candidate_ids=source_manifest.candidate_ids,
        metric_bundle_ids=tuple(metric_ids),
    )
    store.write_json(
        f"evaluations/{evaluation_id}/summary.json",
        {
            "schema_version": "1.0",
            "source_evaluation_id": source_evaluation_id,
            "source_candidate_count": len(source_manifest.candidate_ids),
            "candidate_count": len(metric_ids),
            "grade_counts": dict(grades),
            "metric_replay_wall_seconds": time.perf_counter() - started,
            "detectors_rerun": False,
            "generation_rerun": False,
            "calibration_status": "human_screened_proxy_labels_not_human_ground_truth",
            "complete": len(metric_ids) == len(source_manifest.candidate_ids),
        },
    )
    store.write_json(f"evaluations/{evaluation_id}/evaluation.json", manifest)
    return manifest


__all__ = ["replay_human_aligned_evaluation"]
