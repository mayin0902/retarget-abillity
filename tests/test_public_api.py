from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from retarget_agent.analysis import ProtectionAnalyzerCore
from retarget_agent.api import _support, generate_candidates, retarget_image, score_pair
from retarget_agent.config import AnalysisConfig
from retarget_agent.models import Rect, RegionKind, RegionRecord
from retarget_agent.scenes import normalize_scene


def test_packaged_default_strategy_supports_wheel_installation(monkeypatch) -> None:
    def no_checkout_strategy() -> Path:
        raise FileNotFoundError("no checkout")

    monkeypatch.setattr(_support, "current_strategy_path", no_checkout_strategy)

    loaded = _support.resolve_strategy(None)

    assert loaded.bundle.strategy_id == "retarget"
    assert loaded.bundle.version == "1.0.0"
    assert loaded.source_sha256 == (
        "78a62685c08a110d4e8d3e001498eba2d6f427524ec4c484df3ef96373d37f82"
    )


def _image(height: int = 72, width: int = 112) -> np.ndarray:
    yy, xx = np.mgrid[0:height, 0:width]
    return np.stack(
        (
            (xx * 5 + yy * 2) % 256,
            (xx * 2 + yy * 7) % 256,
            (xx * 3 + yy * 3) % 256,
        ),
        axis=2,
    ).astype(np.uint8)


def _disabled_analysis() -> AnalysisConfig:
    return AnalysisConfig(detector_mode="disabled")


class _FakeDetectorSuite:
    analyzer_ids = ("fake_detector:1.0.0",)

    def __init__(self) -> None:
        self.calls = 0

    def detect(self, image_rgb: np.ndarray, padding_ratio: float) -> tuple[RegionRecord, ...]:
        self.calls += 1
        return (
            RegionRecord(
                region_id="face-1",
                kind=RegionKind.MUST_KEEP,
                rect=Rect(x1=5, y1=5, x2=20, y2=20),
                importance=1.0,
                tolerance=0.0,
                confidence=0.9,
                source="fake",
                label="face",
                attributes={"semantic_type": "face"},
            ),
        )


def test_protection_analyzer_core_accepts_in_memory_regions_and_reuses_detection_cache() -> None:
    image = _image()
    suite = _FakeDetectorSuite()
    config = AnalysisConfig(detector_mode="required")
    analyzer = ProtectionAnalyzerCore(config, detector_suite=suite)
    from retarget_agent.api._support import task_for_image

    task = task_for_image(image, target=(64, 64), scene="movie_poster")
    provided = RegionRecord(
        region_id="text-1",
        kind=RegionKind.PREFER_KEEP,
        rect=Rect(x1=30, y1=20, x2=70, y2=35),
        importance=0.8,
        tolerance=0.1,
        confidence=1.0,
        source="caller",
        label="text",
        attributes={"semantic_type": "text"},
    )
    first = analyzer.analyze(image, task, provided_regions=(provided,))
    second = analyzer.analyze(image, task, provided_regions=(provided,))

    assert suite.calls == 1
    assert {region.region_id for region in first.regions} == {"face-1", "text-1"}
    assert first.importance_map.shape == image.shape[:2]
    assert np.array_equal(first.importance_map, second.importance_map)


def test_retarget_image_is_in_memory_and_returns_requested_shape(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = retarget_image(
        _image(),
        (64, 64),
        method="crop",
        scene="movie_poster",
        analysis_config=_disabled_analysis(),
    )

    assert result.status == "SUCCESS"
    assert result.method == "crop"
    assert result.image is not None
    assert result.image.shape == (64, 64, 3)
    assert not list(tmp_path.iterdir())


def test_generate_candidates_preserves_requested_order_and_isolates_failures() -> None:
    results = generate_candidates(
        _image(),
        (64, 64),
        methods=("direct_warp", "crop"),
        scene="film_still",
        analysis_config=_disabled_analysis(),
    )

    assert [result.method for result in results] == ["direct_warp", "crop"]
    assert all(result.status == "SUCCESS" for result in results)
    assert all(result.image is not None for result in results)


def test_score_pair_returns_business_fields_without_writing_files(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    source = _image()
    generated = retarget_image(
        source,
        (64, 64),
        method="crop",
        scene="movie_poster",
        analysis_config=_disabled_analysis(),
    )
    assert generated.image is not None

    score = score_pair(
        source,
        generated.image,
        scene="movie_poster",
        transform=generated.transform,
        analysis_config=_disabled_analysis(),
    )

    assert score.quality_score is not None
    assert score.grade in {"A", "B", "C", "D"}
    assert score.metrics["quality_score"] == score.quality_score
    assert score.visual_integrity is not None
    assert score.content_fidelity is None  # disabled detectors provide no semantic evidence
    assert not list(tmp_path.iterdir())


def test_public_api_rejects_ambiguous_or_invalid_inputs() -> None:
    image = _image()
    analyzer = ProtectionAnalyzerCore(_disabled_analysis())
    with pytest.raises(ValueError, match="analyzer or analysis_config"):
        retarget_image(
            image,
            (64, 64),
            analyzer=analyzer,
            analysis_config=_disabled_analysis(),
        )
    with pytest.raises(ValueError, match="unknown methods"):
        generate_candidates(
            image,
            (64, 64),
            methods=("missing",),
            analysis_config=_disabled_analysis(),
        )
    with pytest.raises(ValueError, match="scene must be one of"):
        normalize_scene("unknown")
