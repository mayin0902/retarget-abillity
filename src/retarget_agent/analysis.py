"""Shared deterministic protection analysis for the four M0-M4 methods."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from pydantic import ValidationError

from .config import AnalysisConfig
from .datasets import read_region_rows
from .models import HumanGuidance, Rect, RegionKind, RegionRecord, TaskSpec
from .protocols import AnalysisOutput


def _normalize(array: np.ndarray) -> np.ndarray:
    minimum = float(array.min())
    maximum = float(array.max())
    if maximum - minimum < 1e-8:
        return np.zeros_like(array, dtype=np.float32)
    return ((array - minimum) / (maximum - minimum)).astype(np.float32)


class SharedProtectionAnalyzer:
    analyzer_id = "shared_protection"
    analyzer_version = "2.0.0"

    def __init__(self, dataset_root: Path, config: AnalysisConfig) -> None:
        self.dataset_root = dataset_root
        self.config = config
        self._region_rows = read_region_rows(dataset_root)
        self._detector_suite: object | None = None
        self._detector_warning: str | None = None
        self._detection_cache: dict[str, tuple[RegionRecord, ...]] = {}
        self._saliency_cache_key: str | None = None
        self._saliency_cache_maps: tuple[np.ndarray, np.ndarray] | None = None
        if config.detector_mode != "disabled":
            try:
                from .plugin_catalog import built_in_plugin_catalog

                factory = built_in_plugin_catalog().detector_suites.get(
                    config.detector_suite_plugin
                )
                self._detector_suite = factory(config)
            except (FileNotFoundError, OSError, ValueError, cv2.error) as error:
                if config.detector_mode == "required":
                    raise
                self._detector_warning = f"detector_pipeline_unavailable: {error}"

    def analyze(
        self,
        image: np.ndarray,
        task: TaskSpec,
        guidance: HumanGuidance | None = None,
    ) -> AnalysisOutput:
        if image.ndim != 3 or image.shape[2] != 3:
            raise ValueError("analysis expects an RGB image")
        height, width = image.shape[:2]
        if self._saliency_cache_key == task.source.sha256 and self._saliency_cache_maps is not None:
            importance, tolerance = (array.copy() for array in self._saliency_cache_maps)
        else:
            importance, tolerance = self._base_saliency(image)
            self._saliency_cache_key = task.source.sha256
            self._saliency_cache_maps = (importance.copy(), tolerance.copy())

        regions: list[RegionRecord] = []
        warnings: list[str] = []
        analyzer_ids: list[str] = [
            "image_metadata:1.0.0",
            "gradient_contrast_saliency:1.0.0",
            "dataset_regions:1.0.0",
        ]
        if self.config.detector_mode == "disabled":
            warnings.append("detector_pipeline_disabled_by_config")
        elif self._detector_warning is not None:
            warnings.append(self._detector_warning)
        elif self._detector_suite is not None:
            analyzer_ids.extend(self._detector_suite.analyzer_ids)  # type: ignore[attr-defined]
            detected = self._detection_cache.get(task.source.sha256)
            if detected is None:
                try:
                    detected = self._detector_suite.detect(  # type: ignore[attr-defined]
                        image, self.config.region_padding_ratio
                    )
                except (OSError, ValueError, cv2.error) as error:
                    if self.config.detector_mode == "required":
                        raise
                    warnings.append(f"detector_inference_failed: {error}")
                    detected = ()
                self._detection_cache[task.source.sha256] = detected
            regions.extend(detected)
            for region in detected:
                self._apply_region(importance, tolerance, region)
        for row in self._region_rows:
            if row.get("source_id") != task.source.source_id:
                continue
            try:
                region = RegionRecord(
                    region_id=row["region_id"],
                    kind=row["kind"],
                    rect=Rect(
                        x1=int(row["x1"]),
                        y1=int(row["y1"]),
                        x2=int(row["x2"]),
                        y2=int(row["y2"]),
                    ),
                    importance=float(row["importance"]),
                    tolerance=float(row["tolerance"]),
                    confidence=float(row["confidence"]),
                    source=row["source"],
                )
            except (KeyError, ValueError, ValidationError) as error:
                warnings.append(f"invalid annotation row ignored: {error}")
                continue
            if region.rect.x2 > width or region.rect.y2 > height:
                warnings.append(
                    f"region {region.region_id} is outside source bounds and was ignored"
                )
                continue
            regions.append(region)
            self._apply_region(importance, tolerance, region)

        if guidance is not None:
            for index, rect in enumerate(guidance.must_keep):
                region = RegionRecord(
                    region_id=f"guidance-must-{index}",
                    kind=RegionKind.MUST_KEEP,
                    rect=rect,
                    importance=1.0,
                    tolerance=0.0,
                    confidence=1.0,
                    source=f"human-guidance:{guidance.guidance_id}",
                )
                regions.append(region)
                self._apply_region(importance, tolerance, region)
            for index, rect in enumerate(guidance.removable):
                region = RegionRecord(
                    region_id=f"guidance-drop-{index}",
                    kind=RegionKind.REMOVABLE,
                    rect=rect,
                    importance=0.0,
                    tolerance=1.0,
                    confidence=1.0,
                    source=f"human-guidance:{guidance.guidance_id}",
                )
                regions.append(region)
                self._apply_region(importance, tolerance, region)

        return AnalysisOutput(
            importance_map=importance,
            tolerance_map=tolerance,
            regions=tuple(regions),
            analyzer_ids=tuple(analyzer_ids),
            warnings=tuple(warnings),
        )

    def _base_saliency(self, image: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        height, width = image.shape[:2]
        gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
        sobel_x = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
        sobel_y = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
        gradient = _normalize(cv2.magnitude(sobel_x, sobel_y))
        local_mean = cv2.GaussianBlur(gray, (0, 0), sigmaX=5.0)
        contrast = _normalize(np.abs(gray - local_mean))
        yy, xx = np.mgrid[0:height, 0:width]
        center = np.exp(
            -(
                ((xx - (width - 1) / 2) / max(width * 0.42, 1.0)) ** 2
                + ((yy - (height - 1) / 2) / max(height * 0.42, 1.0)) ** 2
            )
        ).astype(np.float32)
        total_weight = (
            self.config.gradient_weight + self.config.contrast_weight + self.config.center_weight
        )
        if total_weight <= 0:
            raise ValueError("analysis weights must sum to a positive value")
        importance = (
            self.config.gradient_weight * gradient
            + self.config.contrast_weight * contrast
            + self.config.center_weight * center
        ) / total_weight
        importance = np.clip(importance, 0.0, 1.0).astype(np.float32)
        return importance, (1.0 - importance).astype(np.float32)

    @staticmethod
    def _apply_region(importance: np.ndarray, tolerance: np.ndarray, region: RegionRecord) -> None:
        rect = region.rect
        target_importance = importance[rect.y1 : rect.y2, rect.x1 : rect.x2]
        target_tolerance = tolerance[rect.y1 : rect.y2, rect.x1 : rect.x2]
        if region.kind == RegionKind.REMOVABLE:
            target_importance[:] = np.minimum(target_importance, region.importance)
            target_tolerance[:] = np.maximum(target_tolerance, region.tolerance)
        else:
            target_importance[:] = np.maximum(target_importance, region.importance)
            target_tolerance[:] = np.minimum(target_tolerance, region.tolerance)
