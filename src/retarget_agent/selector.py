"""Uncalibrated technical-risk selector used only to make the M4 UI navigable."""

from __future__ import annotations

import math
import uuid

from .models import CandidateRecord, DecisionRecord, GenerationStatus, TaskSpec, TransformRecord


def _technical_risk(transform: TransformRecord) -> float:
    risk = transform.risk_features
    if transform.method_id == "direct_warp":
        return float(risk.get("d_stretch", 10.0))
    if transform.method_id == "crop":
        coverage = float(risk.get("importance_coverage", 0.0))
        cut_count = int(risk.get("cut_must_keep_count", 0))
        cropped = float(risk.get("cropped_fraction", 1.0))
        return (1.0 - coverage) + 10.0 * cut_count + 0.1 * cropped
    if transform.method_id == "seam":
        anisotropy = float(risk.get("final_alignment_anisotropy", 10.0))
        seam_importance = float(risk.get("mean_seam_importance", 1.0))
        return max(0.0, math.log(max(anisotropy, 1e-8))) + 2.0 * seam_importance
    if transform.method_id == "mesh":
        anisotropy = float(risk.get("max_axis_anisotropy", 10.0))
        foldovers = int(risk.get("foldover_count", 1))
        return max(0.0, math.log(max(anisotropy, 1e-8))) + 100.0 * foldovers
    return 1_000.0


def select_by_technical_risk(
    task: TaskSpec,
    run_id: str,
    candidates: list[CandidateRecord],
    transforms: dict[str, TransformRecord],
) -> DecisionRecord:
    successful = [
        candidate
        for candidate in candidates
        if candidate.generation_status == GenerationStatus.SUCCESS and candidate.output is not None
    ]
    usable = successful or [
        candidate
        for candidate in candidates
        if candidate.output is not None
        and candidate.generation_status
        in {GenerationStatus.UNSAFE, GenerationStatus.NEEDS_MANUAL_REVIEW}
    ]
    best = (
        min(usable, key=lambda item: _technical_risk(transforms[item.candidate_id]))
        if usable
        else None
    )
    failed = [candidate.candidate_id for candidate in candidates if candidate.output is None]
    reason_codes = ("uncalibrated_technical_risk_only",)
    if not successful and best is not None:
        reason_codes += ("no_technically_safe_candidate",)
    return DecisionRecord(
        decision_id=f"decision-{uuid.uuid4().hex}",
        run_id=run_id,
        task_id=task.task_id,
        selector_id="technical_risk_v1",
        selector_version="1.0.0",
        best_candidate_id=best.candidate_id if best else None,
        candidate_ids=tuple(candidate.candidate_id for candidate in candidates if candidate.output),
        failed_candidate_ids=tuple(failed),
        selection_confidence=None,
        reason_codes=reason_codes,
    )
