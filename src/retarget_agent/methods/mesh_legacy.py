"""Legacy separable axis-aligned mesh retained for regression comparison."""

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


def _target_edges(
    source_edges: np.ndarray,
    importance_projection: np.ndarray,
    tolerance_projection: np.ndarray,
    target_length: int,
    gain: float,
    minimum_cell_fraction: float,
) -> tuple[np.ndarray, np.ndarray]:
    segment_weights: list[float] = []
    source_lengths = np.diff(source_edges)
    for index, length in enumerate(source_lengths):
        start = int(round(source_edges[index]))
        stop = max(start + 1, int(round(source_edges[index + 1])))
        importance = float(importance_projection[start:stop].mean())
        tolerance = float(tolerance_projection[start:stop].mean())
        weight = float(length) * (1.0 + gain * importance) / (1.0 + 0.75 * tolerance)
        segment_weights.append(max(weight, 1e-6))
    weights = np.asarray(segment_weights, dtype=np.float64)
    allocations = target_length * weights / weights.sum()
    uniform = target_length / len(source_lengths)
    allocations = np.maximum(allocations, uniform * minimum_cell_fraction)
    allocations *= target_length / allocations.sum()
    edges = np.concatenate(([0.0], np.cumsum(allocations)))
    edges[-1] = float(target_length - 1)
    return edges, allocations


class AxisAlignedMeshMethod:
    # Compatibility ID used by the original retarget-agent runs.
    method_id = "mesh"
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
        columns = min(
            max(2, int(config.parameters.get("grid_columns", 12))),
            source_width - 1,
            target_width - 1,
        )
        rows = min(
            max(2, int(config.parameters.get("grid_rows", 12))),
            source_height - 1,
            target_height - 1,
        )
        gain = float(config.parameters.get("protection_gain", 1.8))
        minimum_fraction = float(config.parameters.get("minimum_cell_fraction", 0.25))
        source_x = np.linspace(0.0, source_width - 1.0, columns + 1)
        source_y = np.linspace(0.0, source_height - 1.0, rows + 1)
        target_x, allocated_widths = _target_edges(
            source_x,
            importance_map.mean(axis=0),
            tolerance_map.mean(axis=0),
            target_width,
            gain,
            minimum_fraction,
        )
        target_y, allocated_heights = _target_edges(
            source_y,
            importance_map.mean(axis=1),
            tolerance_map.mean(axis=1),
            target_height,
            gain,
            minimum_fraction,
        )
        map_x_line = np.interp(np.arange(target_width), target_x, source_x).astype(np.float32)
        map_y_line = np.interp(np.arange(target_height), target_y, source_y).astype(np.float32)
        output = cv2.remap(
            image,
            np.tile(map_x_line, (target_height, 1)),
            np.tile(map_y_line[:, None], (1, target_width)),
            interpolation=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_REPLICATE,
        )
        sx = allocated_widths / np.diff(source_x)
        sy = allocated_heights / np.diff(source_y)
        jacobians = np.outer(sy, sx)
        anisotropy = max(sx.max() / max(sy.min(), 1e-8), sy.max() / max(sx.min(), 1e-8))
        transform = TransformRecord(
            method_id=self.method_id,
            method_version=self.method_version,
            operations=(
                {
                    "operation": "legacy_separable_axis_aligned_mesh",
                    "source_grid_x": source_x.tolist(),
                    "source_grid_y": source_y.tolist(),
                    "target_grid_x": target_x.tolist(),
                    "target_grid_y": target_y.tolist(),
                    "analysis_artifact_id": analysis.artifact_id,
                },
            ),
            risk_features={
                "grid_columns": columns,
                "grid_rows": rows,
                "foldover_count": 0,
                "min_cell_jacobian": float(jacobians.min()),
                "max_cell_jacobian": float(jacobians.max()),
                "max_axis_anisotropy": float(anisotropy),
                "minimum_cell_fraction": minimum_fraction,
            },
        )
        return MethodOutput(image=output, transform=transform)


__all__ = ["AxisAlignedMeshMethod"]
