"""Legacy seam carving with a fixed per-axis seam budget.

Kept as an explicit baseline. New production profiles use ``seam_full``.
"""

from __future__ import annotations

import cv2
import numpy as np

from retarget_agent.models import (
    AnalysisArtifact,
    ExecutionContext,
    HumanGuidance,
    MethodConfig,
    TaskSpec,
    TransformRecord,
)
from retarget_agent.protocols import MethodOutput


def _energy(
    image: np.ndarray,
    importance: np.ndarray,
    tolerance: np.ndarray,
    protection_weight: float,
    tolerance_weight: float,
) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
    dx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    dy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    base = cv2.magnitude(dx, dy)
    combined = base + protection_weight * importance - tolerance_weight * tolerance
    combined -= float(combined.min())
    return combined + 1e-6


def _find_vertical_seam(energy: np.ndarray) -> np.ndarray:
    height, width = energy.shape
    cumulative = energy.copy()
    backtrack = np.zeros((height, width), dtype=np.int8)
    infinity = np.float32(np.inf)
    for row in range(1, height):
        previous = cumulative[row - 1]
        left = np.concatenate(([infinity], previous[:-1]))
        middle = previous
        right = np.concatenate((previous[1:], [infinity]))
        choices = np.stack((left, middle, right), axis=0)
        selected = np.argmin(choices, axis=0)
        cumulative[row] += choices[selected, np.arange(width)]
        backtrack[row] = selected.astype(np.int8) - 1
    seam = np.empty(height, dtype=np.int32)
    seam[-1] = int(np.argmin(cumulative[-1]))
    for row in range(height - 2, -1, -1):
        seam[row] = seam[row + 1] + int(backtrack[row + 1, seam[row + 1]])
    return seam


def _remove_vertical(array: np.ndarray, seam: np.ndarray) -> np.ndarray:
    height, width = array.shape[:2]
    mask = np.ones((height, width), dtype=bool)
    mask[np.arange(height), seam] = False
    if array.ndim == 3:
        return array[mask].reshape(height, width - 1, array.shape[2])
    return array[mask].reshape(height, width - 1)


def _remove_seams(
    image: np.ndarray,
    importance: np.ndarray,
    tolerance: np.ndarray,
    count: int,
    protection_weight: float,
    tolerance_weight: float,
    horizontal: bool = False,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[float]]:
    if horizontal:
        image = np.transpose(image, (1, 0, 2))
        importance = importance.T
        tolerance = tolerance.T
    protection_hits: list[float] = []
    for _ in range(count):
        if image.shape[1] <= 2:
            break
        seam = _find_vertical_seam(
            _energy(image, importance, tolerance, protection_weight, tolerance_weight)
        )
        protection_hits.append(float(importance[np.arange(image.shape[0]), seam].mean()))
        image = _remove_vertical(image, seam)
        importance = _remove_vertical(importance, seam)
        tolerance = _remove_vertical(tolerance, seam)
    if horizontal:
        image = np.transpose(image, (1, 0, 2))
        importance = importance.T
        tolerance = tolerance.T
    return image, importance, tolerance, protection_hits


class LimitedSeamMethod:
    # Compatibility ID used by the original retarget-agent runs.
    method_id = "seam"
    method_version = "1.0.0"

    def generate(
        self,
        image: np.ndarray,
        task: TaskSpec,
        analysis: AnalysisArtifact,
        importance_map: np.ndarray,
        tolerance_map: np.ndarray,
        guidance: HumanGuidance | None,
        config: MethodConfig,
        context: ExecutionContext,
    ) -> MethodOutput:
        del guidance, context
        source_height, source_width = image.shape[:2]
        target_width, target_height = task.target.width, task.target.height
        cover_scale = max(target_width / source_width, target_height / source_height)
        work_width = max(target_width, int(round(source_width * cover_scale)))
        work_height = max(target_height, int(round(source_height * cover_scale)))
        work = cv2.resize(image, (work_width, work_height), interpolation=cv2.INTER_LANCZOS4)
        importance = cv2.resize(
            importance_map, (work_width, work_height), interpolation=cv2.INTER_LINEAR
        )
        tolerance = cv2.resize(
            tolerance_map, (work_width, work_height), interpolation=cv2.INTER_LINEAR
        )
        requested_vertical = max(0, work_width - target_width)
        requested_horizontal = max(0, work_height - target_height)
        budget = int(config.parameters.get("max_seams_per_axis", 24))
        protection_weight = float(config.parameters.get("protection_weight", 18.0))
        tolerance_weight = float(config.parameters.get("tolerance_weight", 3.0))
        vertical_count = min(requested_vertical, budget)
        horizontal_count = min(requested_horizontal, budget)
        work, importance, tolerance, vertical_hits = _remove_seams(
            work,
            importance,
            tolerance,
            vertical_count,
            protection_weight,
            tolerance_weight,
        )
        work, importance, tolerance, horizontal_hits = _remove_seams(
            work,
            importance,
            tolerance,
            horizontal_count,
            protection_weight,
            tolerance_weight,
            horizontal=True,
        )
        del importance, tolerance
        before_height, before_width = work.shape[:2]
        output = cv2.resize(work, (target_width, target_height), interpolation=cv2.INTER_LANCZOS4)
        final_sx = target_width / before_width
        final_sy = target_height / before_height
        budget_exhausted = (
            vertical_count < requested_vertical or horizontal_count < requested_horizontal
        )
        warnings = (
            ("legacy seam budget exhausted; residual non-uniform scaling applied",)
            if budget_exhausted
            else ()
        )
        hits = vertical_hits + horizontal_hits
        transform = TransformRecord(
            method_id=self.method_id,
            method_version=self.method_version,
            operations=(
                {
                    "operation": "legacy_uniform_cover_resize",
                    "source_size": [source_width, source_height],
                    "work_size": [work_width, work_height],
                },
                {
                    "operation": "legacy_capped_seam_removal",
                    "requested_vertical": requested_vertical,
                    "removed_vertical": vertical_count,
                    "requested_horizontal": requested_horizontal,
                    "removed_horizontal": horizontal_count,
                    "max_seams_per_axis": budget,
                    "analysis_artifact_id": analysis.artifact_id,
                },
                {
                    "operation": "residual_size_alignment",
                    "sx": final_sx,
                    "sy": final_sy,
                },
            ),
            risk_features={
                "seams_removed": vertical_count + horizontal_count,
                "mean_seam_importance": float(np.mean(hits)) if hits else 0.0,
                "max_seam_importance": float(np.max(hits)) if hits else 0.0,
                "budget_exhausted": budget_exhausted,
                "final_alignment_anisotropy": max(
                    final_sx / max(final_sy, 1e-8), final_sy / max(final_sx, 1e-8)
                ),
            },
            warnings=warnings,
        )
        return MethodOutput(image=output, transform=transform, warnings=warnings)


__all__ = ["LimitedSeamMethod"]
