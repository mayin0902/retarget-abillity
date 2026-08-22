"""In-memory image-retargeting interface for embedding applications."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from ..analysis import ProtectionAnalyzerCore
from ..config import RETARGET_DEFAULT_METHODS, AnalysisConfig, method_parameters_for_profile
from ..methods import built_in_methods
from ..models import ExecutionContext, HumanGuidance, MethodConfig, RegionRecord, TransformRecord
from ..scenes import normalize_scene
from ._support import (
    default_analysis_config,
    memory_artifact,
    require_rgb,
    task_for_image,
    validate_target,
)


@dataclass(frozen=True, slots=True)
class RetargetResult:
    image: np.ndarray | None
    method: str
    status: str
    transform: TransformRecord | None
    warnings: tuple[str, ...]
    regions: tuple[RegionRecord, ...]
    analyzer_ids: tuple[str, ...]
    failure_type: str | None = None
    error_summary: str | None = None


def generate_candidates(
    image: np.ndarray,
    target: tuple[int, int],
    *,
    methods: Sequence[str] = RETARGET_DEFAULT_METHODS,
    scene: str = "unspecified",
    analyzer: ProtectionAnalyzerCore | None = None,
    analysis_config: AnalysisConfig | None = None,
    guidance: HumanGuidance | None = None,
    method_parameters: Mapping[str, Mapping[str, Any]] | None = None,
    seed: int = 20260821,
) -> tuple[RetargetResult, ...]:
    """Generate one candidate per method without creating files or a Run."""

    source = require_rgb(image, name="image")
    target = validate_target(target)
    scene = normalize_scene(scene)
    method_ids = tuple(methods)
    if not method_ids or len(method_ids) != len(set(method_ids)):
        raise ValueError("methods must be a non-empty sequence of unique IDs")
    registry = built_in_methods()
    unknown = set(method_ids) - set(registry.ids())
    if unknown:
        raise ValueError(f"unknown methods: {sorted(unknown)}")
    if analyzer is not None and analysis_config is not None:
        raise ValueError("pass analyzer or analysis_config, not both")
    config = analysis_config or (
        analyzer.config if analyzer is not None else default_analysis_config()
    )
    active_analyzer = analyzer or ProtectionAnalyzerCore(config)
    task = task_for_image(source, target=target, scene=scene)
    analysis_output = active_analyzer.analyze(source, task, guidance)
    artifact = memory_artifact(task, analysis_output, config)
    default_parameters = method_parameters_for_profile("retarget_default_v1")
    overrides = method_parameters or {}
    unknown_overrides = set(overrides) - set(method_ids)
    if unknown_overrides:
        raise ValueError(
            f"method_parameters contains disabled methods: {sorted(unknown_overrides)}"
        )
    context = ExecutionContext(run_id="public-api", run_root="memory", device="cpu")
    results: list[RetargetResult] = []
    for method_id in method_ids:
        method = registry.get(method_id)
        parameters = dict(default_parameters.get(method_id, {}))
        parameters.update(dict(overrides.get(method_id, {})))
        method_config = MethodConfig(
            method_id=method_id,
            method_version=method.method_version,
            seed=seed,
            parameters=parameters,
        )
        try:
            output = method.generate(
                source,
                task,
                artifact,
                analysis_output.importance_map.copy(),
                analysis_output.tolerance_map.copy(),
                guidance,
                method_config,
                context,
            )
            expected = (target[1], target[0], 3)
            if output.image is not None and output.image.shape != expected:
                raise ValueError(
                    f"method {method_id} returned shape {output.image.shape}; expected {expected}"
                )
            results.append(
                RetargetResult(
                    image=output.image,
                    method=method_id,
                    status=output.status,
                    transform=output.transform,
                    warnings=analysis_output.warnings + output.warnings,
                    regions=analysis_output.regions,
                    analyzer_ids=analysis_output.analyzer_ids,
                    failure_type=output.failure_type,
                    error_summary=output.error_summary,
                )
            )
        except Exception as error:  # candidate failures must not erase sibling results
            results.append(
                RetargetResult(
                    image=None,
                    method=method_id,
                    status="FAILED",
                    transform=None,
                    warnings=analysis_output.warnings,
                    regions=analysis_output.regions,
                    analyzer_ids=analysis_output.analyzer_ids,
                    failure_type=type(error).__name__,
                    error_summary=str(error)[:500],
                )
            )
    return tuple(results)


def retarget_image(
    image: np.ndarray,
    target: tuple[int, int],
    *,
    method: str = "crop",
    scene: str = "unspecified",
    analyzer: ProtectionAnalyzerCore | None = None,
    analysis_config: AnalysisConfig | None = None,
    guidance: HumanGuidance | None = None,
    parameters: Mapping[str, Any] | None = None,
    seed: int = 20260821,
) -> RetargetResult:
    """Generate one named method candidate through the same core as the Runner."""

    return generate_candidates(
        image,
        target,
        methods=(method,),
        scene=scene,
        analyzer=analyzer,
        analysis_config=analysis_config,
        guidance=guidance,
        method_parameters={method: dict(parameters or {})},
        seed=seed,
    )[0]


__all__ = ["RetargetResult", "generate_candidates", "retarget_image"]
