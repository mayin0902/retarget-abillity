"""High-resolution protected seam carving and seam/scale hybrid methods.

The expensive dynamic program runs on a bounded proxy, but it carves source-coordinate
maps rather than pixels. The final target is sampled once from the original image, so
proxy analysis does not turn the deliverable into an enlarged low-resolution bitmap.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from retarget_agent.models import (
    AnalysisArtifact,
    ExecutionContext,
    GenerationStatus,
    HumanGuidance,
    MethodConfig,
    TaskSpec,
    TransformRecord,
)
from retarget_agent.protocols import MethodOutput


@dataclass(slots=True)
class _CarveResult:
    map_x: np.ndarray
    map_y: np.ndarray
    importance_hits: list[float]
    peak_hits: list[float]
    removed_vertical: int
    removed_horizontal: int
    proxy_source_size: tuple[int, int]
    proxy_carved_size: tuple[int, int]


def _base_energy(
    image: np.ndarray,
    importance: np.ndarray,
    tolerance: np.ndarray,
    protection_weight: float,
    tolerance_weight: float,
) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
    dx = cv2.Scharr(gray, cv2.CV_32F, 1, 0)
    dy = cv2.Scharr(gray, cv2.CV_32F, 0, 1)
    gradient = cv2.magnitude(dx, dy)
    scale = float(np.percentile(gradient, 95))
    if scale > 1e-8:
        gradient /= scale
    combined = gradient + protection_weight * importance - tolerance_weight * tolerance
    return np.maximum(combined, 1e-6).astype(np.float32)


def _find_vertical_forward_seam(image: np.ndarray, energy: np.ndarray) -> np.ndarray:
    """Rubinstein-style forward energy with deterministic leftmost tie breaking."""

    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
    height, width = gray.shape
    left = np.concatenate((gray[:, :1], gray[:, :-1]), axis=1)
    right = np.concatenate((gray[:, 1:], gray[:, -1:]), axis=1)
    up = np.concatenate((gray[:1], gray[:-1]), axis=0)
    cost_up = np.abs(right - left)
    cost_left = cost_up + np.abs(up - left)
    cost_right = cost_up + np.abs(up - right)

    cumulative = np.empty_like(energy, dtype=np.float32)
    cumulative[0] = energy[0]
    backtrack = np.zeros((height, width), dtype=np.int8)
    infinity = np.float32(np.inf)
    columns = np.arange(width)
    for row in range(1, height):
        previous = cumulative[row - 1]
        from_left = np.concatenate(([infinity], previous[:-1])) + cost_left[row]
        from_up = previous + cost_up[row]
        from_right = np.concatenate((previous[1:], [infinity])) + cost_right[row]
        choices = np.stack((from_left, from_up, from_right), axis=0)
        selected = np.argmin(choices, axis=0)
        cumulative[row] = energy[row] + choices[selected, columns]
        backtrack[row] = selected.astype(np.int8) - 1

    seam = np.empty(height, dtype=np.int32)
    seam[-1] = int(np.argmin(cumulative[-1]))
    for row in range(height - 2, -1, -1):
        seam[row] = seam[row + 1] + int(backtrack[row + 1, seam[row + 1]])
    return seam


def _remove_vertical(array: np.ndarray, seam: np.ndarray) -> np.ndarray:
    height, width = array.shape[:2]
    keep = np.ones((height, width), dtype=bool)
    keep[np.arange(height), seam] = False
    if array.ndim == 3:
        return array[keep].reshape(height, width - 1, array.shape[2])
    return array[keep].reshape(height, width - 1)


def _transpose(array: np.ndarray) -> np.ndarray:
    return np.transpose(array, (1, 0, 2)) if array.ndim == 3 else array.T


def _remove_axis(
    image: np.ndarray,
    importance: np.ndarray,
    tolerance: np.ndarray,
    map_x: np.ndarray,
    map_y: np.ndarray,
    count: int,
    *,
    horizontal: bool,
    protection_weight: float,
    tolerance_weight: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, list[float], list[float]]:
    if horizontal:
        image, importance, tolerance, map_x, map_y = (
            _transpose(value) for value in (image, importance, tolerance, map_x, map_y)
        )
    means: list[float] = []
    peaks: list[float] = []
    for _ in range(count):
        if image.shape[1] <= 2:
            break
        seam = _find_vertical_forward_seam(
            image,
            _base_energy(
                image,
                importance,
                tolerance,
                protection_weight,
                tolerance_weight,
            ),
        )
        values = importance[np.arange(image.shape[0]), seam]
        means.append(float(values.mean()))
        peaks.append(float(values.max()))
        image = _remove_vertical(image, seam)
        importance = _remove_vertical(importance, seam)
        tolerance = _remove_vertical(tolerance, seam)
        map_x = _remove_vertical(map_x, seam)
        map_y = _remove_vertical(map_y, seam)
    if horizontal:
        image, importance, tolerance, map_x, map_y = (
            _transpose(value) for value in (image, importance, tolerance, map_x, map_y)
        )
    return image, importance, tolerance, map_x, map_y, means, peaks


def _proxy_size(width: int, height: int, maximum_long_edge: int) -> tuple[int, int]:
    scale = min(1.0, maximum_long_edge / max(width, height))
    return max(3, int(round(width * scale))), max(3, int(round(height * scale)))


def _target_proxy_size(work_width: int, work_height: int, target_ratio: float) -> tuple[int, int]:
    source_ratio = work_width / work_height
    if source_ratio >= target_ratio:
        return max(2, int(round(work_height * target_ratio))), work_height
    return work_width, max(2, int(round(work_width / target_ratio)))


def _carve_coordinate_map(
    image: np.ndarray,
    importance_map: np.ndarray,
    tolerance_map: np.ndarray,
    target_ratio: float,
    *,
    proxy_long_edge: int,
    seam_fraction: float,
    protection_weight: float,
    tolerance_weight: float,
) -> _CarveResult:
    source_height, source_width = image.shape[:2]
    proxy_width, proxy_height = _proxy_size(source_width, source_height, proxy_long_edge)
    work = cv2.resize(image, (proxy_width, proxy_height), interpolation=cv2.INTER_AREA)
    importance = cv2.resize(
        importance_map, (proxy_width, proxy_height), interpolation=cv2.INTER_LINEAR
    ).astype(np.float32)
    tolerance = cv2.resize(
        tolerance_map, (proxy_width, proxy_height), interpolation=cv2.INTER_LINEAR
    ).astype(np.float32)
    x_line = np.linspace(0.0, source_width - 1.0, proxy_width, dtype=np.float32)
    y_line = np.linspace(0.0, source_height - 1.0, proxy_height, dtype=np.float32)
    map_x = np.tile(x_line, (proxy_height, 1))
    map_y = np.tile(y_line[:, None], (1, proxy_width))

    target_proxy_width, target_proxy_height = _target_proxy_size(
        proxy_width, proxy_height, target_ratio
    )
    requested_vertical = proxy_width - target_proxy_width
    requested_horizontal = proxy_height - target_proxy_height
    vertical_count = min(requested_vertical, int(round(requested_vertical * seam_fraction)))
    horizontal_count = min(requested_horizontal, int(round(requested_horizontal * seam_fraction)))
    work, importance, tolerance, map_x, map_y, v_means, v_peaks = _remove_axis(
        work,
        importance,
        tolerance,
        map_x,
        map_y,
        vertical_count,
        horizontal=False,
        protection_weight=protection_weight,
        tolerance_weight=tolerance_weight,
    )
    work, importance, tolerance, map_x, map_y, h_means, h_peaks = _remove_axis(
        work,
        importance,
        tolerance,
        map_x,
        map_y,
        horizontal_count,
        horizontal=True,
        protection_weight=protection_weight,
        tolerance_weight=tolerance_weight,
    )
    del work, importance, tolerance
    return _CarveResult(
        map_x=map_x,
        map_y=map_y,
        importance_hits=v_means + h_means,
        peak_hits=v_peaks + h_peaks,
        removed_vertical=vertical_count,
        removed_horizontal=horizontal_count,
        proxy_source_size=(proxy_width, proxy_height),
        proxy_carved_size=(map_x.shape[1], map_x.shape[0]),
    )


def _render_from_map(
    image: np.ndarray, result: _CarveResult, target_width: int, target_height: int
) -> np.ndarray:
    map_x = cv2.resize(result.map_x, (target_width, target_height), interpolation=cv2.INTER_LINEAR)
    map_y = cv2.resize(result.map_y, (target_width, target_height), interpolation=cv2.INTER_LINEAR)
    return cv2.remap(
        image,
        map_x.astype(np.float32),
        map_y.astype(np.float32),
        interpolation=cv2.INTER_LANCZOS4,
        borderMode=cv2.BORDER_REPLICATE,
    )


class _BaseSeamMethod:
    seam_fraction = 1.0

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
        target_width, target_height = task.target.width, task.target.height
        proxy_long_edge = int(config.parameters.get("proxy_long_edge", 512))
        if not 64 <= proxy_long_edge <= 1024:
            raise ValueError("proxy_long_edge must be between 64 and 1024")
        seam_fraction = float(config.parameters.get("seam_fraction", self.seam_fraction))
        if not 0.0 <= seam_fraction <= 1.0:
            raise ValueError("seam_fraction must be between 0 and 1")
        protection_weight = float(config.parameters.get("protection_weight", 24.0))
        tolerance_weight = float(config.parameters.get("tolerance_weight", 2.5))
        unsafe_mean = float(config.parameters.get("unsafe_mean_importance", 0.45))
        unsafe_peak = float(config.parameters.get("unsafe_peak_importance", 0.90))
        result = _carve_coordinate_map(
            image,
            importance_map,
            tolerance_map,
            target_width / target_height,
            proxy_long_edge=proxy_long_edge,
            seam_fraction=seam_fraction,
            protection_weight=protection_weight,
            tolerance_weight=tolerance_weight,
        )
        output = _render_from_map(image, result, target_width, target_height)
        hit_mean = float(np.mean(result.importance_hits)) if result.importance_hits else 0.0
        hit_peak = float(np.max(result.peak_hits)) if result.peak_hits else 0.0
        unsafe = hit_mean > unsafe_mean or hit_peak > unsafe_peak
        warnings = (
            ("seam path crossed a high-importance shared protection region",) if unsafe else ()
        )
        carved_width, carved_height = result.proxy_carved_size
        residual_sx = target_width / carved_width
        residual_sy = target_height / carved_height
        transform = TransformRecord(
            method_id=self.method_id,
            method_version=self.method_version,
            operations=(
                {
                    "operation": "shared_protection_forward_energy",
                    "analysis_artifact_id": analysis.artifact_id,
                    "region_count": len(analysis.regions),
                    "energy": "scharr+importance-tolerance+forward_disruption",
                },
                {
                    "operation": "proxy_coordinate_field_seam_removal",
                    "proxy_source_size": list(result.proxy_source_size),
                    "proxy_carved_size": list(result.proxy_carved_size),
                    "removed_vertical": result.removed_vertical,
                    "removed_horizontal": result.removed_horizontal,
                    "fixed_seam_limit": None,
                    "seam_fraction": seam_fraction,
                },
                {
                    "operation": "original_resolution_coordinate_remap",
                    "target_size": [target_width, target_height],
                    "interpolation": "lanczos4",
                },
            ),
            risk_features={
                "seams_removed": result.removed_vertical + result.removed_horizontal,
                "mean_seam_importance": hit_mean,
                "max_seam_importance": hit_peak,
                "protected_path_warning": unsafe,
                "fixed_seam_limit": False,
                "proxy_long_edge": proxy_long_edge,
                "residual_alignment_anisotropy": max(
                    residual_sx / max(residual_sy, 1e-8),
                    residual_sy / max(residual_sx, 1e-8),
                ),
            },
            warnings=warnings,
        )
        return MethodOutput(
            image=output,
            transform=transform,
            status=GenerationStatus.UNSAFE.value if unsafe else GenerationStatus.SUCCESS.value,
            warnings=warnings,
        )


class FullSeamMethod(_BaseSeamMethod):
    method_id = "seam_full"
    method_version = "2.0.0"
    seam_fraction = 1.0


class SeamScaleMethod(_BaseSeamMethod):
    method_id = "seam_scale"
    method_version = "1.0.0"
    seam_fraction = 0.35


__all__ = ["FullSeamMethod", "SeamScaleMethod"]
