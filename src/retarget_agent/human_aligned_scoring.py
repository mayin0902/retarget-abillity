"""Config-driven Movie60 business-usability scoring.

The adapter keeps traditional image measurements intact, then interprets them
through a frozen strategy.  It deliberately has no task-ID or filename seam.
"""

from __future__ import annotations

from typing import Any

from .evaluation import compute_proxy_metrics
from .models import ProxyGrade
from .strategy import HumanMetricCondition, ScoringPolicy

_GRADE_ORDER = {"D": 0, "C": 1, "B": 2, "A": 3}
_PROXY_BY_GRADE = {
    "A": ProxyGrade.A.value,
    "B": ProxyGrade.B.value,
    "C": ProxyGrade.C.value,
    "D": ProxyGrade.D.value,
}


def _codes(value: object) -> tuple[str, ...]:
    if not isinstance(value, str) or not value:
        return ()
    return tuple(item for item in value.split("|") if item)


def _matches_condition(
    condition: HumanMetricCondition,
    metrics: dict[str, Any],
) -> bool:
    raw = metrics.get(condition.metric)
    if raw is None or isinstance(raw, bool):
        return False
    value = float(raw)
    threshold = condition.threshold
    return {
        "lt": value < threshold,
        "lte": value <= threshold,
        "gt": value > threshold,
        "gte": value >= threshold,
    }[condition.operator]


def _grade_for_score(score: float, policy: ScoringPolicy) -> str:
    if score >= policy.proxy_a_threshold:
        return "A"
    if score >= policy.proxy_b_threshold:
        return "B"
    if score >= policy.proxy_c_threshold:
        return "C"
    return "D"


def _cap_grade(grade: str, outcome: str) -> str:
    return outcome if _GRADE_ORDER[outcome] < _GRADE_ORDER[grade] else grade


def component_quality_score(metrics: dict[str, Any], scoring_policy: ScoringPolicy) -> float | None:
    """Recombine stored components with the active strategy's top-level weights."""

    weighted: list[tuple[float, float]] = []
    for metric, weight in (
        ("content_fidelity_score", scoring_policy.total_weights.content),
        ("visual_integrity_score", scoring_policy.total_weights.integrity),
        ("composition_score", scoring_policy.total_weights.composition),
    ):
        value = metrics.get(metric)
        if value is not None and weight > 0:
            weighted.append((float(value), weight))
    if not weighted:
        return None
    return (
        100.0
        * sum(value * weight for value, weight in weighted)
        / sum(weight for _, weight in weighted)
    )


def apply_human_aligned_policy(
    metrics: dict[str, Any],
    *,
    scene_category: str,
    method_id: str,
    scoring_policy: ScoringPolicy,
) -> dict[str, Any]:
    """Return a new metric mapping with transparent score and gate evidence."""

    config = scoring_policy.human_alignment
    if config is None or not config.enabled:
        return dict(metrics)
    base_score = component_quality_score(metrics, scoring_policy)
    if base_score is None:
        base_score = metrics.get("quality_score")
    if base_score is None:
        return {
            **metrics,
            "human_alignment_status": "not_available_without_quality_score",
        }

    score = float(base_score)
    adjustment_ids: list[str] = []
    for adjustment in config.score_adjustments:
        if adjustment.scenes and scene_category not in adjustment.scenes:
            continue
        if adjustment.methods and method_id not in adjustment.methods:
            continue
        score += adjustment.amount
        adjustment_ids.append(adjustment.adjustment_id)

    base_regressions = _codes(metrics.get("critical_regressions"))
    penalty_by_code = {item.regression_code: item.amount for item in config.regression_penalties}
    remaining_regressions: list[str] = []
    applied_penalties: list[str] = []
    for code in base_regressions:
        if code in penalty_by_code:
            score += penalty_by_code[code]
            applied_penalties.append(code)
        else:
            remaining_regressions.append(code)

    score = max(0.0, min(100.0, score))
    grade = _grade_for_score(score, scoring_policy)
    matched_gates: list[str] = []
    for gate in config.gates:
        if gate.scenes and scene_category not in gate.scenes:
            continue
        if gate.methods and method_id not in gate.methods:
            continue
        if not all(_matches_condition(condition, metrics) for condition in gate.conditions):
            continue
        grade = _cap_grade(grade, gate.outcome)
        matched_gates.append(gate.gate_id)

    hard_failures = _codes(metrics.get("hard_failures"))
    if hard_failures:
        grade = _cap_grade(grade, config.hard_failure_outcome)
    final_regressions = remaining_regressions + [
        f"human_gate:{gate_id}" for gate_id in matched_gates
    ]
    return {
        **metrics,
        "base_quality_score": float(base_score),
        "base_critical_regressions": "|".join(base_regressions),
        "quality_score": score,
        "proxy_grade": _PROXY_BY_GRADE[grade],
        "proxy_business_success": grade in {"A", "B"},
        "proxy_is_a": grade == "A",
        "critical_regressions": "|".join(final_regressions),
        "human_alignment_status": "human_screened_proxy_policy",
        "human_alignment_scene": scene_category,
        "human_alignment_method": method_id,
        "human_alignment_adjustments": "|".join(adjustment_ids),
        "human_alignment_soft_regressions": "|".join(applied_penalties),
        "human_alignment_matched_gates": "|".join(matched_gates),
        "calibration_status": "human_screened_proxy_labels_not_human_ground_truth",
    }


def compute_human_aligned_metrics(
    *,
    source: Any,
    candidate: Any,
    task: Any,
    source_regions: Any,
    candidate_regions: Any,
    transform: Any,
    config: Any,
    scoring_policy: ScoringPolicy | None = None,
) -> dict[str, Any]:
    """Reference-scorer plugin compatible with ``compute_proxy_metrics``."""

    metrics = compute_proxy_metrics(
        source=source,
        candidate=candidate,
        task=task,
        source_regions=source_regions,
        candidate_regions=candidate_regions,
        transform=transform,
        config=config,
        scoring_policy=scoring_policy,
    )
    if scoring_policy is None:
        return metrics
    scene = str(getattr(task.source, "scene_category", "unknown") or "unknown")
    method = str(getattr(transform, "method_id", "unknown") or "unknown")
    return apply_human_aligned_policy(
        metrics,
        scene_category=scene,
        method_id=method,
        scoring_policy=scoring_policy,
    )


__all__ = [
    "apply_human_aligned_policy",
    "component_quality_score",
    "compute_human_aligned_metrics",
]
