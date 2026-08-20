"""Deterministic comparison grids for review and smoke diagnostics."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageOps

from .models import CandidateRecord, DecisionRecord, TaskSpec


def comparison_grid(
    source: np.ndarray,
    task: TaskSpec,
    candidates: list[CandidateRecord],
    decision: DecisionRecord,
    run_dir: Path,
    *,
    show_top1_marker: bool = True,
) -> np.ndarray:
    panel_count = 1 + len(candidates)
    columns = 3 if panel_count <= 6 else 4
    rows = (panel_count + columns - 1) // columns
    preview_limit = 640 if columns == 3 else 512
    preview_scale = min(1.0, preview_limit / max(task.target.width, task.target.height))
    panel_width = max(1, round(task.target.width * preview_scale))
    panel_height = max(1, round(task.target.height * preview_scale))
    label_height = 26
    cell_width = panel_width
    cell_height = panel_height + label_height
    canvas = Image.new("RGB", (cell_width * columns, cell_height * rows), (30, 30, 30))

    source_panel = _letterbox_preview(
        Image.fromarray(source, mode="RGB"), panel_width, panel_height
    )
    panels: list[tuple[str, Image.Image]] = [("SOURCE (aspect preserved)", source_panel)]
    for candidate in candidates:
        if candidate.output is None:
            panel = Image.new("RGB", (panel_width, panel_height), (90, 25, 25))
            draw = ImageDraw.Draw(panel)
            draw.text((8, 8), candidate.error_summary or "FAILED", fill=(255, 255, 255))
        else:
            with Image.open(run_dir / candidate.output.relative_path) as opened:
                panel = _letterbox_preview(
                    opened.convert("RGB"), panel_width, panel_height
                )
        marker = (
            " TOP-1"
            if show_top1_marker and candidate.candidate_id == decision.best_candidate_id
            else ""
        )
        panels.append(
            (
                f"{candidate.method_id} [{candidate.generation_status.value}]{marker}",
                panel,
            )
        )

    for index, (label, panel) in enumerate(panels):
        column = index % columns
        row = index // columns
        x = column * cell_width
        y = row * cell_height
        canvas.paste(panel, (x, y + label_height))
        draw = ImageDraw.Draw(canvas)
        draw.text((x + 6, y + 6), label, fill=(245, 245, 245))
    return np.asarray(canvas)


def _letterbox_preview(image: Image.Image, width: int, height: int) -> Image.Image:
    """Fit an image into a preview cell without changing its aspect ratio."""

    contained = ImageOps.contain(
        image.convert("RGB"), (width, height), Image.Resampling.LANCZOS
    )
    panel = Image.new("RGB", (width, height), (12, 12, 12))
    x = (width - contained.width) // 2
    y = (height - contained.height) // 2
    panel.paste(contained, (x, y))
    return panel
