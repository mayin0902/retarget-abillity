from __future__ import annotations

from pathlib import Path

import numpy as np

from retarget_agent.evaluation import (
    EvaluationConfig,
    character_recall,
    compute_proxy_metrics,
    evaluate_run,
    normalize_ocr_text,
    transform_safety_score,
)
from retarget_agent.hashing import sha256_file
from retarget_agent.models import (
    AnalysisArtifact,
    ArtifactRef,
    CandidateRecord,
    GenerationStatus,
    ProxyGrade,
    Rect,
    RegionKind,
    RegionRecord,
    RunManifest,
    SceneProfile,
    SourceRecord,
    TargetSpec,
    TaskSpec,
    TransformRecord,
)
from retarget_agent.storage import LocalArtifactStore


def _task() -> TaskSpec:
    source = SourceRecord(
        source_id="source-1",
        image_path="images/source.png",
        width=128,
        height=64,
        sha256="a" * 64,
        scene_profile=SceneProfile.COVERAGE,
    )
    target = TargetSpec(target_id="square-128x128", width=128, height=128)
    return TaskSpec(
        dataset_id="dataset-1",
        task_id="source-1__square-128x128",
        source=source,
        target=target,
    )


def _pattern(height: int = 128, width: int = 128) -> np.ndarray:
    yy, xx = np.mgrid[:height, :width]
    image = np.zeros((height, width, 3), dtype=np.uint8)
    image[..., 0] = (xx * 3 + yy) % 255
    image[..., 1] = (yy * 5) % 255
    image[20:80, 30:100] = (240, 220, 30)
    return image


def _text_region(text: str) -> RegionRecord:
    return RegionRecord(
        region_id="text-1",
        kind=RegionKind.MUST_KEEP,
        rect=Rect(x1=10, y1=10, x2=90, y2=30),
        importance=1.0,
        tolerance=0.0,
        confidence=0.9,
        source="test-ocr",
        label="text",
        attributes={
            "semantic_type": "text",
            "recognized_text": text,
            "recognition_confidence": 0.9,
        },
    )


def test_ocr_normalization_and_multiset_recall() -> None:
    assert normalize_ocr_text(" 价格：￥１２８！ ") == "价格128"
    assert character_recall("核心文案ABC", "ABC核心文") == 6 / 7
    assert character_recall("", "anything") is None


def test_mesh_foldover_is_a_hard_failure() -> None:
    transform = TransformRecord(
        method_id="mesh",
        method_version="1.0.0",
        operations=(),
        risk_features={"foldover_count": 1, "max_axis_anisotropy": 1.1},
    )
    score, failures = transform_safety_score(transform)
    assert score is not None and score < 0.01
    assert failures == ("mesh_foldover",)


def test_full_mesh_uses_the_same_foldover_safety_contract() -> None:
    transform = TransformRecord(
        method_id="mesh_full",
        method_version="2.0.0",
        operations=(),
        risk_features={"foldover_count": 0, "max_axis_anisotropy": 2.0},
    )
    score, failures = transform_safety_score(transform)
    assert score is not None and 0.0 < score < 1.0
    assert failures == ()


def test_full_and_hybrid_seam_use_residual_anisotropy() -> None:
    full = TransformRecord(
        method_id="seam_full",
        method_version="2.0.0",
        operations=(),
        risk_features={"mean_seam_importance": 0.2, "residual_alignment_anisotropy": 1.0},
    )
    hybrid = TransformRecord(
        method_id="seam_scale",
        method_version="1.0.0",
        operations=(),
        risk_features={"mean_seam_importance": 0.2, "residual_alignment_anisotropy": 1.5},
    )
    full_score, full_failures = transform_safety_score(full)
    hybrid_score, hybrid_failures = transform_safety_score(hybrid)
    assert full_score is not None and hybrid_score is not None
    assert full_score > hybrid_score
    assert full_failures == hybrid_failures == ()


def test_identical_nonblank_image_receives_high_proxy_score() -> None:
    image = _pattern()
    metrics = compute_proxy_metrics(
        source=image,
        candidate=image.copy(),
        task=_task(),
        source_regions=(),
        candidate_regions=(),
        transform=TransformRecord(
            method_id="crop",
            method_version="1.0.0",
            operations=(),
            risk_features={"importance_coverage": 1.0, "cut_must_keep_count": 0},
        ),
        config=EvaluationConfig(rerun_detectors=False),
    )
    assert metrics["quality_score"] >= 80
    assert metrics["proxy_grade"] == ProxyGrade.A.value
    assert metrics["calibration_status"] == "uncalibrated_no_human_ground_truth"


def test_abcd_score_ranges_can_assign_d_without_changing_code() -> None:
    image = _pattern()
    metrics = compute_proxy_metrics(
        source=image,
        candidate=np.zeros_like(image) + 127,
        task=_task(),
        source_regions=(),
        candidate_regions=(),
        transform=None,
        config=EvaluationConfig(
            rerun_detectors=False,
            blank_std_threshold=0.0,
            proxy_a_threshold=90.0,
            proxy_b_threshold=80.0,
            proxy_c_threshold=75.0,
        ),
    )
    assert metrics["quality_score"] < 75.0
    assert metrics["proxy_grade"] == ProxyGrade.D.value


def test_missing_detected_text_is_a_critical_proxy_regression() -> None:
    source = _pattern(64, 128)
    candidate = _pattern()
    metrics = compute_proxy_metrics(
        source=source,
        candidate=candidate,
        task=_task(),
        source_regions=(_text_region("核心价格128"),),
        candidate_regions=(),
        transform=None,
        config=EvaluationConfig(rerun_detectors=False),
    )
    assert metrics["ocr_character_recall"] == 0.0
    assert metrics["critical_regressions"] == "critical_text_missing"
    assert metrics["proxy_grade"] == ProxyGrade.C.value


def test_strict_direct_warp_caps_visible_stretch_at_b_or_c() -> None:
    image = _pattern()
    config = EvaluationConfig(
        rerun_detectors=False,
        proxy_a_threshold=90.0,
        proxy_b_threshold=72.0,
        direct_warp_proxy_a_cap_d_stretch=0.15,
        direct_warp_proxy_c_cap_d_stretch=0.45,
    )

    moderate = compute_proxy_metrics(
        source=image,
        candidate=image.copy(),
        task=_task(),
        source_regions=(),
        candidate_regions=(),
        transform=TransformRecord(
            method_id="direct_warp",
            method_version="1.0.0",
            operations=(),
            risk_features={"d_stretch": 0.30},
        ),
        config=config,
    )
    severe = compute_proxy_metrics(
        source=image,
        candidate=image.copy(),
        task=_task(),
        source_regions=(),
        candidate_regions=(),
        transform=TransformRecord(
            method_id="direct_warp",
            method_version="1.0.0",
            operations=(),
            risk_features={"d_stretch": 0.60},
        ),
        config=config,
    )

    assert moderate["proxy_grade"] == ProxyGrade.B.value
    assert moderate["quality_score"] < 90.0
    assert severe["proxy_grade"] == ProxyGrade.C.value
    assert severe["quality_score"] < 72.0
    assert "severe_global_stretch" in severe["critical_regressions"]


def test_evaluate_run_writes_separate_replay_artifacts(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-1"
    store = LocalArtifactStore(run_dir)
    task = _task()
    source = _pattern(64, 128)
    source_ref = store.write_image("sources/source-1.png", source)
    store.write_json("sources/source-1.json", source_ref)
    store.write_json(f"tasks/{task.task_id}.json", task)
    importance = store.write_numpy(
        f"analysis/{task.task_id}/importance.npy", np.ones((64, 128), np.float32)
    )
    tolerance = store.write_numpy(
        f"analysis/{task.task_id}/tolerance.npy", np.zeros((64, 128), np.float32)
    )
    analysis = AnalysisArtifact(
        artifact_id="analysis-1",
        analysis_version="test",
        task_id=task.task_id,
        source_id=task.source.source_id,
        target_id=task.target.target_id,
        source_width=128,
        source_height=64,
        scene_profile=SceneProfile.COVERAGE,
        importance_map=importance,
        tolerance_map=tolerance,
        analyzer_ids=("fixture",),
        config_hash="b" * 64,
    )
    store.write_json(f"analysis/{task.task_id}/analysis.json", analysis)
    output = store.write_image(f"candidates/{task.task_id}/crop/candidate.png", _pattern())
    transform = TransformRecord(
        method_id="crop",
        method_version="1.0.0",
        operations=(),
        risk_features={"importance_coverage": 1.0, "cut_must_keep_count": 0},
    )
    transform_path = store.write_json(f"candidates/{task.task_id}/crop/transform.json", transform)
    transform_ref = ArtifactRef(
        relative_path=transform_path.relative_to(run_dir).as_posix(),
        sha256=sha256_file(transform_path),
        media_type="application/json",
    )
    candidate = CandidateRecord(
        candidate_id=f"{task.task_id}--crop--fixture",
        task_id=task.task_id,
        method_id="crop",
        method_version="1.0.0",
        variant_id="default",
        run_id="run-1",
        input_sha256=task.source.sha256,
        output=output,
        target_width=128,
        target_height=128,
        seed=1,
        config_hash="c" * 64,
        analysis_artifact_id=analysis.artifact_id,
        transform=transform_ref,
        generation_status=GenerationStatus.SUCCESS,
    )
    candidate_path = f"candidates/{task.task_id}/crop/candidate.json"
    store.write_json(candidate_path, candidate)
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
            code_version="test",
            python_version="3.13",
            dependency_versions={},
            task_ids=(task.task_id,),
            candidate_ids=(candidate.candidate_id,),
        ),
    )
    config_path = store.path("config/run.yaml")
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("analysis:\n  detector_mode: disabled\n", encoding="utf-8")
    candidate_hash_before = sha256_file(store.path(candidate_path))

    manifest = evaluate_run(
        run_dir,
        "evaluation-1",
        EvaluationConfig(rerun_detectors=False),
    )

    assert manifest.candidate_ids == (candidate.candidate_id,)
    assert store.path("evaluations/evaluation-1/summary.json").is_file()
    assert sha256_file(store.path(candidate_path)) == candidate_hash_before
