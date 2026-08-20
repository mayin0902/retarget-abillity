from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np

from retarget_agent.methods import built_in_methods
from retarget_agent.models import (
    AnalysisArtifact,
    ArtifactRef,
    ExecutionContext,
    MethodConfig,
    Rect,
    RegionKind,
    RegionRecord,
    SceneProfile,
    SourceRecord,
    TargetSpec,
    TaskSpec,
)


def _task() -> TaskSpec:
    source = SourceRecord(
        source_id="fixture",
        image_path="images/fixture.png",
        width=96,
        height=64,
        sha256="a" * 64,
        split="smoke",
    )
    return TaskSpec(
        dataset_id="fixture-v1",
        task_id="fixture__portrait-48x80",
        source=source,
        target=TargetSpec(target_id="portrait-48x80", width=48, height=80),
    )


def _inputs() -> tuple[np.ndarray, np.ndarray, np.ndarray, AnalysisArtifact]:
    yy, xx = np.mgrid[0:64, 0:96]
    image = np.stack(((xx * 2) % 255, (yy * 4) % 255, ((xx + yy) * 3) % 255), axis=-1).astype(
        np.uint8
    )
    importance = np.full((64, 96), 0.1, dtype=np.float32)
    importance[16:52, 34:62] = 1.0
    tolerance = 1.0 - importance
    fake = ArtifactRef(relative_path="maps/fake.png", sha256="b" * 64, media_type="image/png")
    artifact = AnalysisArtifact(
        artifact_id="analysis-fixture",
        analysis_version="1.0.0",
        task_id=_task().task_id,
        source_id="fixture",
        target_id="portrait-48x80",
        source_width=96,
        source_height=64,
        scene_profile=SceneProfile.BALANCED,
        regions=(
            RegionRecord(
                region_id="subject",
                kind=RegionKind.MUST_KEEP,
                rect=Rect(x1=34, y1=16, x2=62, y2=52),
                importance=1.0,
                tolerance=0.0,
                confidence=1.0,
                source="test",
            ),
        ),
        importance_map=fake,
        tolerance_map=fake,
        analyzer_ids=("test:1",),
        config_hash="c" * 64,
    )
    return image, importance, tolerance, artifact


def test_four_methods_are_registered_and_produce_target_size(tmp_path: Path) -> None:
    image, importance, tolerance, artifact = _inputs()
    registry = built_in_methods()
    assert registry.ids() == (
        "crop",
        "direct_warp",
        "mesh",
        "mesh_full",
        "seam",
        "seam_full",
        "seam_scale",
    )
    hashes: set[str] = set()
    for method_id in ("direct_warp", "crop", "seam", "mesh"):
        parameters = {"max_seams_per_axis": 6} if method_id == "seam" else {}
        output = registry.get(method_id).generate(
            image,
            _task(),
            artifact,
            importance,
            tolerance,
            None,
            MethodConfig(method_id=method_id, parameters=parameters),
            ExecutionContext(run_id="test-run", run_root=str(tmp_path)),
        )
        assert output.image is not None
        assert output.image.shape == (80, 48, 3)
        assert output.transform.method_id == method_id
        hashes.add(hashlib.sha256(output.image.tobytes()).hexdigest())
    assert len(hashes) == 4


def test_seam_records_real_seam_removal(tmp_path: Path) -> None:
    image, importance, tolerance, artifact = _inputs()
    output = (
        built_in_methods()
        .get("seam")
        .generate(
            image,
            _task(),
            artifact,
            importance,
            tolerance,
            None,
            MethodConfig(method_id="seam", parameters={"max_seams_per_axis": 6}),
            ExecutionContext(run_id="test-run", run_root=str(tmp_path)),
        )
    )
    assert output.transform.risk_features["seams_removed"] > 0
    assert output.transform.operations[1]["operation"] == "legacy_capped_seam_removal"


def test_mesh_is_monotonic_and_has_no_foldovers(tmp_path: Path) -> None:
    image, importance, tolerance, artifact = _inputs()
    output = (
        built_in_methods()
        .get("mesh")
        .generate(
            image,
            _task(),
            artifact,
            importance,
            tolerance,
            None,
            MethodConfig(method_id="mesh"),
            ExecutionContext(run_id="test-run", run_root=str(tmp_path)),
        )
    )
    assert output.transform.risk_features["foldover_count"] == 0
    assert output.transform.risk_features["min_cell_jacobian"] > 0


def test_cn_square_profile_methods_produce_target_size(tmp_path: Path) -> None:
    image, importance, tolerance, artifact = _inputs()
    registry = built_in_methods()
    hashes: set[str] = set()
    for method_id in ("direct_warp", "crop", "seam_full", "mesh_full", "seam_scale"):
        output = registry.get(method_id).generate(
            image,
            _task(),
            artifact,
            importance,
            tolerance,
            None,
            MethodConfig(
                method_id=method_id,
                parameters={"proxy_long_edge": 96} if method_id.startswith("seam_") else {},
            ),
            ExecutionContext(run_id="test-run", run_root=str(tmp_path)),
        )
        assert output.image is not None
        assert output.image.shape == (80, 48, 3)
        assert output.transform.method_id == method_id
        hashes.add(hashlib.sha256(output.image.tobytes()).hexdigest())
    assert len(hashes) >= 4


def test_full_seam_has_no_fixed_count_limit_and_uses_shared_analysis(tmp_path: Path) -> None:
    image, importance, tolerance, artifact = _inputs()
    output = (
        built_in_methods()
        .get("seam_full")
        .generate(
            image,
            _task(),
            artifact,
            importance,
            tolerance,
            None,
            MethodConfig(method_id="seam_full", parameters={"proxy_long_edge": 96}),
            ExecutionContext(run_id="test-run", run_root=str(tmp_path)),
        )
    )
    operation = output.transform.operations[1]
    proxy_width, proxy_height = operation["proxy_source_size"]
    carved_width, carved_height = operation["proxy_carved_size"]
    expected = (proxy_width - carved_width) + (proxy_height - carved_height)
    assert output.transform.risk_features["fixed_seam_limit"] is False
    assert output.transform.risk_features["seams_removed"] == expected
    assert expected > 24
    assert output.transform.operations[0]["analysis_artifact_id"] == artifact.artifact_id


def test_legacy_seam_keeps_explicit_budget(tmp_path: Path) -> None:
    image, importance, tolerance, artifact = _inputs()
    output = (
        built_in_methods()
        .get("seam")
        .generate(
            image,
            _task(),
            artifact,
            importance,
            tolerance,
            None,
            MethodConfig(method_id="seam", parameters={"max_seams_per_axis": 3}),
            ExecutionContext(run_id="test-run", run_root=str(tmp_path)),
        )
    )
    assert output.transform.risk_features["seams_removed"] <= 6
    assert output.transform.risk_features["budget_exhausted"] is True


def test_full_mesh_is_true_2d_and_foldover_checked(tmp_path: Path) -> None:
    image, importance, tolerance, artifact = _inputs()
    output = (
        built_in_methods()
        .get("mesh_full")
        .generate(
            image,
            _task(),
            artifact,
            importance,
            tolerance,
            None,
            MethodConfig(method_id="mesh_full"),
            ExecutionContext(run_id="test-run", run_root=str(tmp_path)),
        )
    )
    risk = output.transform.risk_features
    assert risk["true_2d_mesh"] is True
    assert risk["foldover_count"] == 0
    assert risk["min_cell_jacobian"] > 0
    assert output.transform.operations[0]["analysis_artifact_id"] == artifact.artifact_id
