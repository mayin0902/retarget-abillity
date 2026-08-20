"""Allowlisted runtime plugin catalog.

This is the only place that maps configuration IDs to executable Python.
Strategy files cannot import modules or execute arbitrary paths; adding an
implementation requires a reviewed registration here (or constructing an
explicit catalog in an embedding application/test).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .registry import Registry

DetectorSuiteFactory = Callable[[Any], Any]
ReferenceScorer = Callable[..., dict[str, float | int | bool | str | None]]
StandaloneScorer = Callable[..., dict[str, Any]]
SelectorImplementation = Callable[..., Any]
AgentBackendFactory = Callable[..., Any]


@dataclass(slots=True)
class PluginCatalog:
    detector_suites: Registry[DetectorSuiteFactory]
    reference_scorers: Registry[ReferenceScorer]
    standalone_scorers: Registry[StandaloneScorer]
    selectors: Registry[SelectorImplementation]
    agent_backends: Registry[AgentBackendFactory]

    @classmethod
    def empty(cls) -> PluginCatalog:
        return cls(
            detector_suites=Registry("detector-suite"),
            reference_scorers=Registry("reference-scorer"),
            standalone_scorers=Registry("standalone-scorer"),
            selectors=Registry("selector"),
            agent_backends=Registry("agent-backend"),
        )

    def describe(self) -> dict[str, tuple[str, ...]]:
        return {
            "detector_suites": self.detector_suites.ids(),
            "reference_scorers": self.reference_scorers.ids(),
            "standalone_scorers": self.standalone_scorers.ids(),
            "selectors": self.selectors.ids(),
            "agent_backends": self.agent_backends.ids(),
        }


def built_in_plugin_catalog() -> PluginCatalog:
    """Build a fresh catalog so tests/embedders cannot mutate global state."""

    from .agents import OpenAICompatibleVisionBackend, deterministic_ranking
    from .evaluation import compute_proxy_metrics
    from .image_scoring import (
        OpenAICompatibleImageReviewBackend,
        compute_no_reference_metrics,
    )
    from .protection_detectors import CompanyCpuProtectionDetectorSuite, ProtectionDetectorSuite
    from .rule_anchored_review import QwenRuleAnchoredReviewAdapter
    from .selector import select_by_technical_risk
    from .strict_review import StrictVisionReviewBackend

    catalog = PluginCatalog.empty()
    catalog.detector_suites.register("legacy_opencv_v1", ProtectionDetectorSuite)
    catalog.detector_suites.register("company_cpu_v2", CompanyCpuProtectionDetectorSuite)
    catalog.reference_scorers.register("auto_proxy_v1", compute_proxy_metrics)
    catalog.standalone_scorers.register(
        "technical_no_reference_v1", compute_no_reference_metrics
    )
    catalog.selectors.register("technical_risk_v1", select_by_technical_risk)
    catalog.selectors.register("deterministic_rule_ranking_v1", deterministic_ranking)
    catalog.agent_backends.register(
        "openai_compatible_vision_v1", OpenAICompatibleVisionBackend
    )
    catalog.agent_backends.register(
        "openai_compatible_strict_review_v1", StrictVisionReviewBackend
    )
    catalog.agent_backends.register(
        "rule_anchored_pair_review_v1", QwenRuleAnchoredReviewAdapter
    )
    catalog.agent_backends.register(
        "openai_compatible_image_review_v1", OpenAICompatibleImageReviewBackend
    )
    return catalog


__all__ = ["PluginCatalog", "built_in_plugin_catalog"]
