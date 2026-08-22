"""Pure in-memory source/candidate scoring interface."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ..analysis import ProtectionAnalyzerCore
from ..config import AnalysisConfig
from ..evaluation import EvaluationConfig
from ..models import RegionRecord, TransformRecord
from ..plugin_catalog import PluginCatalog, built_in_plugin_catalog
from ..scenes import normalize_scene
from ..strategy import LoadedStrategyBundle
from ._support import default_analysis_config, require_rgb, resolve_strategy, task_for_image

_GRADE_FROM_PROXY = {
    "proxy_a": "A",
    "proxy_b": "B",
    "proxy_c": "C",
    "proxy_d": "D",
}


@dataclass(frozen=True, slots=True)
class PairScoreResult:
    quality_score: float | None
    grade: str | None
    business_success: bool
    content_fidelity: float | None
    visual_integrity: float | None
    composition: float | None
    metrics: dict[str, Any]
    gates: tuple[str, ...]
    source_regions: tuple[RegionRecord, ...]
    candidate_regions: tuple[RegionRecord, ...]
    analyzer_ids: tuple[str, ...]
    elapsed_seconds: float


def evaluation_config_for_strategy(strategy: LoadedStrategyBundle) -> EvaluationConfig:
    policy = strategy.scoring
    return EvaluationConfig(
        evaluator_id=policy.evaluator_id,
        evaluator_version=policy.evaluator_version,
        rerun_detectors=True,
        max_analysis_edge=policy.max_analysis_edge,
        proxy_a_threshold=policy.proxy_a_threshold,
        proxy_b_threshold=policy.proxy_b_threshold,
        proxy_c_threshold=policy.proxy_c_threshold,
        critical_text_recall=policy.critical_text_recall,
        blank_std_threshold=policy.blank_std_threshold,
        direct_warp_proxy_a_cap_d_stretch=policy.direct_warp_proxy_a_cap_d_stretch,
        direct_warp_proxy_c_cap_d_stretch=policy.direct_warp_proxy_c_cap_d_stretch,
    )


def score_pair(
    source: np.ndarray,
    candidate: np.ndarray,
    *,
    scene: str = "unspecified",
    transform: TransformRecord | None = None,
    strategy: LoadedStrategyBundle | Path | None = None,
    analyzer: ProtectionAnalyzerCore | None = None,
    analysis_config: AnalysisConfig | None = None,
    plugin_catalog: PluginCatalog | None = None,
) -> PairScoreResult:
    """Compare two RGB arrays without copying inputs or writing reports."""

    source = require_rgb(source, name="source")
    candidate = require_rgb(candidate, name="candidate")
    scene = normalize_scene(scene)
    loaded = resolve_strategy(strategy)
    catalog = plugin_catalog or built_in_plugin_catalog()
    if analyzer is not None and analysis_config is not None:
        raise ValueError("pass analyzer or analysis_config, not both")
    config = analysis_config or (
        analyzer.config
        if analyzer is not None
        else default_analysis_config(loaded.bundle.detector_suite_plugin)
    )
    if analyzer is None and (
        plugin_catalog is None or config.detector_mode == "disabled"
    ):
        analyzer = ProtectionAnalyzerCore(config)
    elif analyzer is None:
        factory = catalog.detector_suites.get(config.detector_suite_plugin)
        analyzer = ProtectionAnalyzerCore(config, detector_suite=factory(config))

    task = task_for_image(
        source,
        target=(candidate.shape[1], candidate.shape[0]),
        scene=scene,
        source_id="source",
    )
    candidate_task = task_for_image(
        candidate,
        target=(candidate.shape[1], candidate.shape[0]),
        scene=scene,
        source_id="candidate",
    )
    started = time.perf_counter()
    source_analysis = analyzer.analyze(source, task)
    candidate_analysis = analyzer.analyze(candidate, candidate_task)
    scorer = catalog.reference_scorers.get(loaded.bundle.reference_scorer_plugin)
    metrics = scorer(
        source=source,
        candidate=candidate,
        task=task,
        source_regions=source_analysis.regions,
        candidate_regions=candidate_analysis.regions,
        transform=transform,
        config=evaluation_config_for_strategy(loaded),
        scoring_policy=loaded.scoring,
    )
    gates = tuple(
        value
        for value in str(metrics.get("human_alignment_matched_gates") or "").split("|")
        if value
    )
    quality = metrics.get("quality_score")
    return PairScoreResult(
        quality_score=float(quality) if quality is not None else None,
        grade=_GRADE_FROM_PROXY.get(str(metrics.get("proxy_grade"))),
        business_success=bool(metrics.get("proxy_business_success")),
        content_fidelity=(
            float(metrics["content_fidelity_score"])
            if metrics.get("content_fidelity_score") is not None
            else None
        ),
        visual_integrity=(
            float(metrics["visual_integrity_score"])
            if metrics.get("visual_integrity_score") is not None
            else None
        ),
        composition=(
            float(metrics["composition_score"])
            if metrics.get("composition_score") is not None
            else None
        ),
        metrics=dict(metrics),
        gates=gates,
        source_regions=source_analysis.regions,
        candidate_regions=candidate_analysis.regions,
        analyzer_ids=source_analysis.analyzer_ids,
        elapsed_seconds=time.perf_counter() - started,
    )


__all__ = ["PairScoreResult", "evaluation_config_for_strategy", "score_pair"]
