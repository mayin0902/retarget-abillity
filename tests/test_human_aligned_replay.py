from __future__ import annotations

from pathlib import Path

import pytest

from retarget_agent.human_aligned_replay import replay_human_aligned_evaluation
from retarget_agent.models import (
    ArtifactRef,
    CandidateRecord,
    EvaluationManifest,
    GenerationStatus,
    MetricBundle,
    RunManifest,
    SourceRecord,
    TargetSpec,
    TaskSpec,
)
from retarget_agent.storage import LocalArtifactStore
from retarget_agent.strategy import load_strategy_bundle

ROOT = Path(__file__).resolve().parents[1]


def test_metric_replay_is_traceable_complete_and_refuses_overwrite(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    task = TaskSpec(
        dataset_id="fixture-set",
        task_id="source-1__square-128",
        source=SourceRecord(
            source_id="source-1",
            image_path="images/source.png",
            width=128,
            height=128,
            sha256="a" * 64,
            scene_category="person",
        ),
        target=TargetSpec(target_id="square-128", width=128, height=128),
    )
    candidate = CandidateRecord(
        candidate_id=f"{task.task_id}--crop--fixture",
        task_id=task.task_id,
        method_id="crop",
        method_version="1.0.0",
        variant_id="default",
        run_id="run-1",
        input_sha256="a" * 64,
        output=ArtifactRef(
            relative_path="candidate.png",
            sha256="b" * 64,
            media_type="image/png",
            width=128,
            height=128,
        ),
        target_width=128,
        target_height=128,
        seed=1,
        config_hash="c" * 64,
        analysis_artifact_id="analysis-1",
        generation_status=GenerationStatus.SUCCESS,
    )
    store.write_json(f"tasks/{task.task_id}.json", task)
    store.write_json(f"candidates/{task.task_id}/crop/candidate.json", candidate)
    store.write_json(
        "run.json",
        RunManifest(
            run_id="run-1",
            dataset_id=task.dataset_id,
            dataset_fingerprint="d" * 64,
            status="COMPLETED",
            methods=("crop",),
            config_hash="e" * 64,
            config_snapshot="config/run.yaml",
            code_version="fixture",
            python_version="3.13",
            dependency_versions={},
            task_ids=(task.task_id,),
            candidate_ids=(candidate.candidate_id,),
        ),
    )
    source_metric = MetricBundle(
        metric_bundle_id="metric-source",
        candidate_id=candidate.candidate_id,
        evaluator_id="source-evaluator",
        evaluator_version="1.0",
        metrics={
            "quality_score": 80.0,
            "proxy_grade": "proxy_b",
            "content_fidelity_score": 0.9,
            "visual_integrity_score": 0.8,
            "composition_score": 0.7,
            "critical_regressions": "",
            "hard_failures": "",
        },
    )
    store.write_json(f"evaluations/source/metrics/{candidate.candidate_id}.json", source_metric)
    store.write_json(
        "evaluations/source/evaluation.json",
        EvaluationManifest(
            evaluation_id="source",
            source_run_id="run-1",
            evaluator_id="source-evaluator",
            evaluator_version="1.0",
            config_hash="f" * 64,
            task_ids=(task.task_id,),
            candidate_ids=(candidate.candidate_id,),
            metric_bundle_ids=(source_metric.metric_bundle_id,),
        ),
    )
    strategy = load_strategy_bundle(ROOT / "strategies/movie60/v3_2/bundle.yaml")

    manifest = replay_human_aligned_evaluation(
        tmp_path,
        source_evaluation_id="source",
        evaluation_id="replayed",
        strategy_bundle=strategy,
    )

    assert manifest.strategy_sha256 == strategy.source_sha256
    replayed = store.read_json(
        f"evaluations/replayed/metrics/{candidate.candidate_id}.json"
    )["metrics"]
    assert replayed["source_evaluation_id"] == "source"
    assert replayed["source_metric_sha256"]
    assert replayed["metric_replay_status"] == "reused_frozen_detector_measurements"
    assert store.path("evaluations/replayed/strategy/snapshot.json").is_file()
    with pytest.raises(FileExistsError):
        replay_human_aligned_evaluation(
            tmp_path,
            source_evaluation_id="source",
            evaluation_id="replayed",
            strategy_bundle=strategy,
        )
