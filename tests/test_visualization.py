from __future__ import annotations

from types import SimpleNamespace

import numpy as np
from PIL import Image

from retarget_agent.models import GenerationStatus
from retarget_agent.visualization import _letterbox_preview, comparison_grid


def test_letterbox_preview_preserves_wide_source_aspect_ratio() -> None:
    source = Image.new("RGB", (200, 100), (230, 20, 20))

    preview = _letterbox_preview(source, 100, 100)

    assert preview.size == (100, 100)
    assert preview.getpixel((50, 25)) == (230, 20, 20)
    assert preview.getpixel((50, 24)) == (12, 12, 12)
    assert preview.getpixel((50, 75)) == (12, 12, 12)


def test_comparison_grid_keeps_all_seven_candidates() -> None:
    source = np.full((100, 200, 3), (230, 20, 20), dtype=np.uint8)
    task = SimpleNamespace(target=SimpleNamespace(width=1536, height=1536))
    candidates = [
        SimpleNamespace(
            candidate_id=f"candidate-{index}",
            method_id=f"method-{index}",
            generation_status=GenerationStatus.UNSAFE,
            output=None,
            error_summary="fixture",
        )
        for index in range(7)
    ]
    decision = SimpleNamespace(best_candidate_id="candidate-6")

    grid = comparison_grid(source, task, candidates, decision, run_dir=None)  # type: ignore[arg-type]

    assert grid.shape == (1076, 2048, 3)
    # The eighth cell is candidate seven; its failure color proves it was not truncated.
    assert tuple(grid[564, 1544]) == (90, 25, 25)
