from __future__ import annotations

import math

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

INTERPOLATIONS = {
    "nearest": cv2.INTER_NEAREST,
    "linear": cv2.INTER_LINEAR,
    "cubic": cv2.INTER_CUBIC,
    "lanczos": cv2.INTER_LANCZOS4,
    "area": cv2.INTER_AREA,
}


class DirectWarpMethod:
    method_id = "direct_warp"
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
        del analysis, importance_map, tolerance_map, guidance, context
        source_height, source_width = image.shape[:2]
        target_width = task.target.width
        target_height = task.target.height
        interpolation_name = str(config.parameters.get("interpolation", "lanczos"))
        if interpolation_name not in INTERPOLATIONS:
            raise ValueError(f"unsupported interpolation: {interpolation_name}")
        output = cv2.resize(
            image,
            (target_width, target_height),
            interpolation=INTERPOLATIONS[interpolation_name],
        )
        sx = target_width / source_width
        sy = target_height / source_height
        distortion_ratio = max(sx / sy, sy / sx)
        target_ratio = target_width / target_height
        source_ratio = source_width / source_height
        stretch_prior = abs(math.log(target_ratio / source_ratio))
        transform = TransformRecord(
            method_id=self.method_id,
            method_version=self.method_version,
            operations=(
                {
                    "operation": "non_uniform_resize",
                    "source_size": [source_width, source_height],
                    "target_size": [target_width, target_height],
                    "interpolation": interpolation_name,
                },
            ),
            risk_features={
                "sx": sx,
                "sy": sy,
                "anisotropy_ratio": distortion_ratio,
                "d_stretch": stretch_prior,
            },
        )
        return MethodOutput(image=output, transform=transform)
