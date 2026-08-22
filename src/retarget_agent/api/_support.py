"""Private construction helpers for the in-memory public interfaces."""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np

from ..analysis import ProtectionAnalyzerCore
from ..config import AnalysisConfig
from ..defaults import current_strategy_path
from ..hashing import sha256_json, short_hash
from ..models import (
    AnalysisArtifact,
    ArtifactRef,
    SceneProfile,
    SourceRecord,
    TargetSpec,
    TaskSpec,
)
from ..protocols import AnalysisOutput
from ..strategy import LoadedStrategyBundle, load_strategy_bundle


def require_rgb(image: np.ndarray, *, name: str) -> np.ndarray:
    if not isinstance(image, np.ndarray):
        raise TypeError(f"{name} must be a numpy.ndarray")
    if image.ndim != 3 or image.shape[2] != 3:
        raise ValueError(f"{name} must have shape (height, width, 3)")
    if image.dtype != np.uint8:
        raise ValueError(f"{name} must use uint8 RGB pixels")
    if image.shape[0] < 2 or image.shape[1] < 2:
        raise ValueError(f"{name} dimensions must be at least 2x2")
    return np.ascontiguousarray(image)


def image_sha256(image: np.ndarray) -> str:
    digest = hashlib.sha256()
    digest.update(str(image.shape).encode("ascii"))
    digest.update(image.dtype.str.encode("ascii"))
    digest.update(memoryview(np.ascontiguousarray(image)))
    return digest.hexdigest()


def validate_target(target: tuple[int, int]) -> tuple[int, int]:
    if len(target) != 2 or any(isinstance(value, bool) for value in target):
        raise ValueError("target must be a (width, height) pair")
    width, height = target
    if not isinstance(width, int) or not isinstance(height, int):
        raise TypeError("target width and height must be integers")
    if not (2 <= width <= 16384 and 2 <= height <= 16384):
        raise ValueError("target dimensions must be between 2 and 16384")
    return width, height


def task_for_image(
    image: np.ndarray,
    *,
    target: tuple[int, int],
    scene: str,
    source_id: str = "source",
) -> TaskSpec:
    width, height = validate_target(target)
    source = SourceRecord(
        source_id=source_id,
        image_path=f"memory/{source_id}.png",
        width=image.shape[1],
        height=image.shape[0],
        sha256=image_sha256(image),
        scene_profile=SceneProfile.BALANCED,
        source_kind="in_memory_public_api",
        license_status="caller_managed",
        scene_category=scene,
    )
    target_spec = TargetSpec(
        target_id=f"target-{width}x{height}",
        width=width,
        height=height,
    )
    return TaskSpec(
        dataset_id="public-api",
        task_id=f"{source.source_id}__{target_spec.target_id}",
        source=source,
        target=target_spec,
    )


def memory_artifact(
    task: TaskSpec,
    output: AnalysisOutput,
    config: AnalysisConfig,
) -> AnalysisArtifact:
    importance_hash = hashlib.sha256(output.importance_map.tobytes()).hexdigest()
    tolerance_hash = hashlib.sha256(output.tolerance_map.tobytes()).hexdigest()
    config_hash = sha256_json(config.model_dump(mode="json"))
    return AnalysisArtifact(
        artifact_id=f"analysis-{short_hash(task.task_id + config_hash)}",
        analysis_version=ProtectionAnalyzerCore.analyzer_version,
        task_id=task.task_id,
        source_id=task.source.source_id,
        target_id=task.target.target_id,
        source_width=task.source.width,
        source_height=task.source.height,
        scene_profile=task.source.scene_profile,
        regions=output.regions,
        importance_map=ArtifactRef(
            relative_path="memory/importance.npy",
            sha256=importance_hash,
            media_type="application/x-npy",
            width=task.source.width,
            height=task.source.height,
        ),
        tolerance_map=ArtifactRef(
            relative_path="memory/tolerance.npy",
            sha256=tolerance_hash,
            media_type="application/x-npy",
            width=task.source.width,
            height=task.source.height,
        ),
        analyzer_ids=output.analyzer_ids,
        config_hash=config_hash,
        warnings=output.warnings,
    )


def default_analysis_config(detector_suite_plugin: str = "company_cpu_v2") -> AnalysisConfig:
    return AnalysisConfig(
        gradient_weight=0.40,
        contrast_weight=0.30,
        center_weight=0.30,
        region_padding_ratio=0.025,
        detector_mode="required",
        detector_suite_plugin=detector_suite_plugin,
        model_root="models/analyzers",
    )


def resolve_strategy(
    strategy: LoadedStrategyBundle | Path | None,
) -> LoadedStrategyBundle:
    if isinstance(strategy, LoadedStrategyBundle):
        return strategy
    if strategy is not None:
        return load_strategy_bundle(strategy)
    try:
        path = current_strategy_path()
    except FileNotFoundError:
        # A wheel has no checkout-level registry. Keep the public in-memory API usable
        # with the exact active Strategy snapshot shipped inside the package.
        path = (
            Path(__file__).resolve().parents[1]
            / "resources"
            / "strategies"
            / "retarget"
            / "v1"
            / "bundle.yaml"
        )
        if not path.is_file():
            raise FileNotFoundError(
                "cannot locate a checkout strategy registry or the packaged retarget@1.0.0 "
                "snapshot"
            ) from None
    return load_strategy_bundle(path)


__all__ = [
    "default_analysis_config",
    "image_sha256",
    "memory_artifact",
    "require_rgb",
    "resolve_strategy",
    "task_for_image",
    "validate_target",
]
