from __future__ import annotations

import cv2
import numpy as np

from retarget_agent.models import (
    AnalysisArtifact,
    ExecutionContext,
    GenerationStatus,
    HumanGuidance,
    MethodConfig,
    RegionKind,
    TaskSpec,
    TransformRecord,
)
from retarget_agent.protocols import MethodOutput


def _axis_positions(maximum: int, step: int, anchors: list[int]) -> list[int]:
    values = {0, maximum, maximum // 2}
    values.update(range(0, maximum + 1, max(step, 1)))
    values.update(max(0, min(maximum, anchor)) for anchor in anchors)
    return sorted(values)


class ProtectionCropMethod:
    method_id = "crop"
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
        del tolerance_map, context
        height, width = image.shape[:2]
        target_ratio = task.target.width / task.target.height
        source_ratio = width / height
        if source_ratio >= target_ratio:
            maximum_width = min(width, int(round(height * target_ratio)))
            maximum_height = height
        else:
            maximum_width = width
            maximum_height = min(height, int(round(width / target_ratio)))

        raw_scales = config.parameters.get("scales", [1.0, 0.94, 0.88])
        scales = [float(value) for value in raw_scales]
        step = int(config.parameters.get("grid_step", 8))
        candidates: list[tuple[float, tuple[int, int, int, int], dict[str, object]]] = []
        must_keep = [region for region in analysis.regions if region.kind == RegionKind.MUST_KEEP]
        region_centers_x = [(region.rect.x1 + region.rect.x2) // 2 for region in analysis.regions]
        region_centers_y = [(region.rect.y1 + region.rect.y2) // 2 for region in analysis.regions]
        if guidance and guidance.target_anchor:
            region_centers_x.append(int(guidance.target_anchor[0] * width))
            region_centers_y.append(int(guidance.target_anchor[1] * height))

        importance_integral = cv2.integral(importance_map, sdepth=cv2.CV_64F)
        total_importance = float(importance_integral[-1, -1]) + 1e-8
        for scale in scales:
            crop_width = max(2, min(width, int(round(maximum_width * scale))))
            crop_height = max(2, min(height, int(round(crop_width / target_ratio))))
            if crop_height > height:
                crop_height = max(2, min(height, int(round(maximum_height * scale))))
                crop_width = max(2, min(width, int(round(crop_height * target_ratio))))
            max_x = width - crop_width
            max_y = height - crop_height
            x_anchors = [center - crop_width // 2 for center in region_centers_x]
            y_anchors = [center - crop_height // 2 for center in region_centers_y]
            for x1 in _axis_positions(max_x, step, x_anchors):
                for y1 in _axis_positions(max_y, step, y_anchors):
                    x2 = x1 + crop_width
                    y2 = y1 + crop_height
                    captured_sum = (
                        importance_integral[y2, x2]
                        - importance_integral[y1, x2]
                        - importance_integral[y2, x1]
                        + importance_integral[y1, x1]
                    )
                    captured = float(captured_sum) / total_importance
                    cut_regions: list[str] = []
                    for region in must_keep:
                        rect = region.rect
                        contained = (
                            x1 <= rect.x1 and y1 <= rect.y1 and x2 >= rect.x2 and y2 >= rect.y2
                        )
                        if not contained:
                            cut_regions.append(region.region_id)
                    center_distance = (
                        abs((x1 + x2) / 2 - width / 2) / width
                        + abs((y1 + y2) / 2 - height / 2) / height
                    )
                    cropped_fraction = 1.0 - (crop_width * crop_height) / (width * height)
                    score = captured - 4.0 * len(cut_regions) - 0.08 * center_distance
                    score -= 0.05 * cropped_fraction
                    candidates.append(
                        (
                            score,
                            (x1, y1, x2, y2),
                            {
                                "importance_coverage": captured,
                                "cut_must_keep_regions": cut_regions,
                                "cropped_fraction": cropped_fraction,
                            },
                        )
                    )
        if not candidates:
            raise RuntimeError("crop search produced no candidate windows")
        score, window, details = max(candidates, key=lambda item: item[0])
        x1, y1, x2, y2 = window
        cropped = image[y1:y2, x1:x2]
        output = cv2.resize(
            cropped,
            (task.target.width, task.target.height),
            interpolation=cv2.INTER_LANCZOS4,
        )
        unsafe = bool(details["cut_must_keep_regions"])
        status = GenerationStatus.UNSAFE if unsafe else GenerationStatus.SUCCESS
        warnings = ("no crop window can contain every must_keep region",) if unsafe else ()
        transform = TransformRecord(
            method_id=self.method_id,
            method_version=self.method_version,
            operations=(
                {
                    "operation": "protection_weighted_window_search",
                    "evaluated_windows": len(candidates),
                    "selected_score": score,
                    "window_xyxy_half_open": list(window),
                },
                {
                    "operation": "uniform_resize",
                    "source_size": [x2 - x1, y2 - y1],
                    "target_size": [task.target.width, task.target.height],
                    "interpolation": "lanczos",
                },
            ),
            risk_features={
                "importance_coverage": float(details["importance_coverage"]),
                "cropped_fraction": float(details["cropped_fraction"]),
                "cut_must_keep_count": len(details["cut_must_keep_regions"]),
            },
            warnings=warnings,
        )
        return MethodOutput(
            image=output,
            transform=transform,
            status=status.value,
            warnings=warnings,
        )
