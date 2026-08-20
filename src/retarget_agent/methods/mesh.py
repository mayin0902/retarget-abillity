"""Two-dimensional content-aware mesh optimization with foldover protection."""

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
class _MeshSolution:
    source_vertices: np.ndarray
    target_vertices: np.ndarray
    uniform_vertices: np.ndarray
    rows: int
    columns: int
    blend_to_uniform: float
    residual: float


def _vertex_index(row: int, column: int, columns: int) -> int:
    return row * (columns + 1) + column


def _grid_vertices(width: int, height: int, columns: int, rows: int) -> np.ndarray:
    x = np.linspace(0.0, width - 1.0, columns + 1)
    y = np.linspace(0.0, height - 1.0, rows + 1)
    xx, yy = np.meshgrid(x, y)
    return np.stack((xx, yy), axis=-1).reshape(-1, 2)


def _sample_vertex_rigidity(
    importance: np.ndarray,
    tolerance: np.ndarray,
    source_vertices: np.ndarray,
    protection_gain: float,
) -> np.ndarray:
    height, width = importance.shape
    x = np.clip(np.rint(source_vertices[:, 0]).astype(int), 0, width - 1)
    y = np.clip(np.rint(source_vertices[:, 1]).astype(int), 0, height - 1)
    protected = importance[y, x]
    rigid = 1.0 - tolerance[y, x]
    return 0.12 + protection_gain * protected + 0.8 * rigid


def _append_equation(
    rows: list[np.ndarray],
    values: list[float],
    coefficients: dict[int, float],
    value: float,
    weight: float,
    variable_count: int,
) -> None:
    equation = np.zeros(variable_count, dtype=np.float64)
    for index, coefficient in coefficients.items():
        equation[index] = coefficient * weight
    rows.append(equation)
    values.append(value * weight)


def _solve_axis(
    source: np.ndarray,
    uniform: np.ndarray,
    rigidity: np.ndarray,
    rows_count: int,
    columns_count: int,
    axis: int,
    isotropic_scale: float,
    anchor_weight: float,
    smoothness_weight: float,
) -> tuple[np.ndarray, float]:
    variable_count = source.shape[0]
    equations: list[np.ndarray] = []
    values: list[float] = []
    for row in range(rows_count + 1):
        for column in range(columns_count):
            left = _vertex_index(row, column, columns_count)
            right = _vertex_index(row, column + 1, columns_count)
            weight = float(np.sqrt(rigidity[left] * rigidity[right]))
            source_delta = source[right, axis] - source[left, axis]
            desired = source_delta * isotropic_scale if axis == 0 else 0.0
            _append_equation(
                equations,
                values,
                {left: -1.0, right: 1.0},
                desired,
                weight,
                variable_count,
            )
    for row in range(rows_count):
        for column in range(columns_count + 1):
            top = _vertex_index(row, column, columns_count)
            bottom = _vertex_index(row + 1, column, columns_count)
            weight = float(np.sqrt(rigidity[top] * rigidity[bottom]))
            source_delta = source[bottom, axis] - source[top, axis]
            desired = source_delta * isotropic_scale if axis == 1 else 0.0
            _append_equation(
                equations,
                values,
                {top: -1.0, bottom: 1.0},
                desired,
                weight,
                variable_count,
            )

    for index, target in enumerate(uniform[:, axis]):
        _append_equation(
            equations,
            values,
            {index: 1.0},
            float(target),
            anchor_weight,
            variable_count,
        )

    for row in range(rows_count + 1):
        for column in range(1, columns_count):
            left = _vertex_index(row, column - 1, columns_count)
            middle = _vertex_index(row, column, columns_count)
            right = _vertex_index(row, column + 1, columns_count)
            _append_equation(
                equations,
                values,
                {left: 1.0, middle: -2.0, right: 1.0},
                0.0,
                smoothness_weight,
                variable_count,
            )
    for row in range(1, rows_count):
        for column in range(columns_count + 1):
            top = _vertex_index(row - 1, column, columns_count)
            middle = _vertex_index(row, column, columns_count)
            bottom = _vertex_index(row + 1, column, columns_count)
            _append_equation(
                equations,
                values,
                {top: 1.0, middle: -2.0, bottom: 1.0},
                0.0,
                smoothness_weight,
                variable_count,
            )

    boundary_weight = 5000.0
    if axis == 0:
        for row in range(rows_count + 1):
            left = _vertex_index(row, 0, columns_count)
            right = _vertex_index(row, columns_count, columns_count)
            _append_equation(equations, values, {left: 1.0}, 0.0, boundary_weight, variable_count)
            _append_equation(
                equations,
                values,
                {right: 1.0},
                float(uniform[right, 0]),
                boundary_weight,
                variable_count,
            )
    else:
        for column in range(columns_count + 1):
            top = _vertex_index(0, column, columns_count)
            bottom = _vertex_index(rows_count, column, columns_count)
            _append_equation(equations, values, {top: 1.0}, 0.0, boundary_weight, variable_count)
            _append_equation(
                equations,
                values,
                {bottom: 1.0},
                float(uniform[bottom, 1]),
                boundary_weight,
                variable_count,
            )

    matrix = np.stack(equations)
    vector = np.asarray(values)
    solution, residuals, _, _ = np.linalg.lstsq(matrix, vector, rcond=None)
    residual = float(residuals[0] / len(vector)) if residuals.size else 0.0
    return solution, residual


def _triangles(rows: int, columns: int) -> list[tuple[int, int, int]]:
    output: list[tuple[int, int, int]] = []
    for row in range(rows):
        for column in range(columns):
            top_left = _vertex_index(row, column, columns)
            top_right = _vertex_index(row, column + 1, columns)
            bottom_left = _vertex_index(row + 1, column, columns)
            bottom_right = _vertex_index(row + 1, column + 1, columns)
            output.append((top_left, top_right, bottom_right))
            output.append((top_left, bottom_right, bottom_left))
    return output


def _signed_double_area(points: np.ndarray) -> float:
    a, b, c = points
    first = b - a
    second = c - a
    return float(first[0] * second[1] - first[1] * second[0])


def _foldover_count(vertices: np.ndarray, triangles: list[tuple[int, int, int]]) -> int:
    return sum(_signed_double_area(vertices[list(indices)]) <= 1e-6 for indices in triangles)


def _optimize_mesh(
    source_width: int,
    source_height: int,
    target_width: int,
    target_height: int,
    importance: np.ndarray,
    tolerance: np.ndarray,
    columns: int,
    rows: int,
    protection_gain: float,
    anchor_weight: float,
    smoothness_weight: float,
) -> _MeshSolution:
    source = _grid_vertices(source_width, source_height, columns, rows)
    uniform = _grid_vertices(target_width, target_height, columns, rows)
    rigidity = _sample_vertex_rigidity(importance, tolerance, source, protection_gain)
    isotropic_scale = float(
        np.sqrt(
            ((target_width - 1) * (target_height - 1))
            / max((source_width - 1) * (source_height - 1), 1)
        )
    )
    target_x, residual_x = _solve_axis(
        source,
        uniform,
        rigidity,
        rows,
        columns,
        0,
        isotropic_scale,
        anchor_weight,
        smoothness_weight,
    )
    target_y, residual_y = _solve_axis(
        source,
        uniform,
        rigidity,
        rows,
        columns,
        1,
        isotropic_scale,
        anchor_weight,
        smoothness_weight,
    )
    optimized = np.stack((target_x, target_y), axis=1)
    optimized[:, 0] = np.clip(optimized[:, 0], 0.0, target_width - 1.0)
    optimized[:, 1] = np.clip(optimized[:, 1], 0.0, target_height - 1.0)
    triangles = _triangles(rows, columns)
    blend = 0.0
    target = optimized
    while _foldover_count(target, triangles) and blend < 1.0:
        blend = 0.5 if blend == 0.0 else min(1.0, blend + (1.0 - blend) * 0.5)
        target = (1.0 - blend) * optimized + blend * uniform
    if _foldover_count(target, triangles):
        target = uniform.copy()
        blend = 1.0
    return _MeshSolution(
        source_vertices=source,
        target_vertices=target,
        uniform_vertices=uniform,
        rows=rows,
        columns=columns,
        blend_to_uniform=blend,
        residual=residual_x + residual_y,
    )


def _rasterize_coordinate_map(
    solution: _MeshSolution, target_width: int, target_height: int
) -> tuple[np.ndarray, np.ndarray]:
    uniform_x = np.linspace(
        0.0, float(solution.source_vertices[:, 0].max()), target_width, dtype=np.float32
    )
    uniform_y = np.linspace(
        0.0, float(solution.source_vertices[:, 1].max()), target_height, dtype=np.float32
    )
    map_x = np.tile(uniform_x, (target_height, 1))
    map_y = np.tile(uniform_y[:, None], (1, target_width))
    for indices in _triangles(solution.rows, solution.columns):
        target = solution.target_vertices[list(indices)]
        source = solution.source_vertices[list(indices)]
        min_x = max(0, int(np.floor(target[:, 0].min())))
        max_x = min(target_width - 1, int(np.ceil(target[:, 0].max())))
        min_y = max(0, int(np.floor(target[:, 1].min())))
        max_y = min(target_height - 1, int(np.ceil(target[:, 1].max())))
        if min_x > max_x or min_y > max_y:
            continue
        xx, yy = np.meshgrid(
            np.arange(min_x, max_x + 1, dtype=np.float64),
            np.arange(min_y, max_y + 1, dtype=np.float64),
        )
        a, b, c = target
        denominator = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
        if abs(denominator) <= 1e-8:
            continue
        weight_a = ((b[1] - c[1]) * (xx - c[0]) + (c[0] - b[0]) * (yy - c[1])) / denominator
        weight_b = ((c[1] - a[1]) * (xx - c[0]) + (a[0] - c[0]) * (yy - c[1])) / denominator
        weight_c = 1.0 - weight_a - weight_b
        inside = (weight_a >= -1e-6) & (weight_b >= -1e-6) & (weight_c >= -1e-6)
        source_x = weight_a * source[0, 0] + weight_b * source[1, 0] + weight_c * source[2, 0]
        source_y = weight_a * source[0, 1] + weight_b * source[1, 1] + weight_c * source[2, 1]
        region_x = map_x[min_y : max_y + 1, min_x : max_x + 1]
        region_y = map_y[min_y : max_y + 1, min_x : max_x + 1]
        region_x[inside] = source_x[inside].astype(np.float32)
        region_y[inside] = source_y[inside].astype(np.float32)
    return map_x, map_y


def _mesh_risks(solution: _MeshSolution) -> tuple[int, float, float, float]:
    foldovers = 0
    minimum_jacobian = float("inf")
    maximum_jacobian = 0.0
    maximum_anisotropy = 1.0
    for indices in _triangles(solution.rows, solution.columns):
        source = solution.source_vertices[list(indices)]
        target = solution.target_vertices[list(indices)]
        source_basis = np.stack((source[1] - source[0], source[2] - source[0]), axis=1)
        target_basis = np.stack((target[1] - target[0], target[2] - target[0]), axis=1)
        transform = target_basis @ np.linalg.inv(source_basis)
        determinant = float(np.linalg.det(transform))
        if determinant <= 1e-8:
            foldovers += 1
        minimum_jacobian = min(minimum_jacobian, determinant)
        maximum_jacobian = max(maximum_jacobian, determinant)
        singular = np.linalg.svd(transform, compute_uv=False)
        maximum_anisotropy = max(
            maximum_anisotropy, float(singular.max() / max(singular.min(), 1e-8))
        )
    return foldovers, minimum_jacobian, maximum_jacobian, maximum_anisotropy


class FullMeshMethod:
    method_id = "mesh_full"
    method_version = "2.0.0"

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
        columns = min(max(3, int(config.parameters.get("grid_columns", 12))), source_width - 1)
        rows = min(max(3, int(config.parameters.get("grid_rows", 12))), source_height - 1)
        protection_gain = float(config.parameters.get("protection_gain", 5.0))
        anchor_weight = float(config.parameters.get("uniform_anchor_weight", 0.18))
        smoothness_weight = float(config.parameters.get("smoothness_weight", 0.65))
        unsafe_anisotropy = float(config.parameters.get("unsafe_anisotropy", 4.5))
        solution = _optimize_mesh(
            source_width,
            source_height,
            target_width,
            target_height,
            importance_map,
            tolerance_map,
            columns,
            rows,
            protection_gain,
            anchor_weight,
            smoothness_weight,
        )
        map_x, map_y = _rasterize_coordinate_map(solution, target_width, target_height)
        output = cv2.remap(
            image,
            map_x,
            map_y,
            interpolation=cv2.INTER_LANCZOS4,
            borderMode=cv2.BORDER_REPLICATE,
        )
        foldovers, minimum_jacobian, maximum_jacobian, anisotropy = _mesh_risks(solution)
        unsafe = foldovers > 0 or anisotropy > unsafe_anisotropy
        warnings: tuple[str, ...] = ()
        if solution.blend_to_uniform > 0.0:
            warnings += ("optimized mesh was blended toward uniform mapping to prevent foldover",)
        if unsafe:
            warnings += ("mesh exceeds the configured local deformation safety threshold",)
        transform = TransformRecord(
            method_id=self.method_id,
            method_version=self.method_version,
            operations=(
                {
                    "operation": "shared_protection_2d_mesh_optimization",
                    "analysis_artifact_id": analysis.artifact_id,
                    "grid_shape": [columns, rows],
                    "objective": [
                        "protected_region_local_rigidity",
                        "boundary_constraints",
                        "second_difference_smoothness",
                        "weak_uniform_anchor",
                    ],
                    "solver": "weighted_linear_least_squares",
                },
                {
                    "operation": "piecewise_affine_original_resolution_remap",
                    "triangle_count": 2 * columns * rows,
                    "target_size": [target_width, target_height],
                },
            ),
            risk_features={
                "grid_columns": columns,
                "grid_rows": rows,
                "foldover_count": foldovers,
                "min_cell_jacobian": minimum_jacobian,
                "max_cell_jacobian": maximum_jacobian,
                "max_axis_anisotropy": anisotropy,
                "blend_to_uniform": solution.blend_to_uniform,
                "optimization_residual": solution.residual,
                "true_2d_mesh": True,
            },
            warnings=warnings,
        )
        return MethodOutput(
            image=output,
            transform=transform,
            status=GenerationStatus.UNSAFE.value if unsafe else GenerationStatus.SUCCESS.value,
            warnings=warnings,
        )


__all__ = ["FullMeshMethod"]
