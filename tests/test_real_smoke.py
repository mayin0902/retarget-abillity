from __future__ import annotations

import math

from retarget_agent.real_smoke import (
    HD_TARGETS,
    SQUARE_BENCHMARK_TARGETS,
    TARGETS,
    _select_targets,
)


def test_real_smoke_selects_two_farthest_nontrivial_ratios() -> None:
    for width, height in ((1200, 800), (800, 1200), (1000, 1000), (1920, 1080)):
        selected = _select_targets(width, height)
        assert len({target["target_id"] for target in selected}) == 2
        source_ratio = width / height
        pressures = [
            abs(math.log((target["width"] / target["height"]) / source_ratio))
            for target in selected
        ]
        assert min(pressures) >= 0.25


def test_real_smoke_target_catalog_has_three_distinct_ratios() -> None:
    ratios = {target["width"] / target["height"] for target in TARGETS}
    assert len(ratios) == 3


def test_hd_target_catalog_is_review_resolution_and_keeps_ratio_pressure() -> None:
    assert min(max(target["width"], target["height"]) for target in HD_TARGETS) >= 1024
    ratios = {target["width"] / target["height"] for target in HD_TARGETS}
    assert len(ratios) == 3
    selected = _select_targets(1920, 1080, HD_TARGETS)
    assert len(selected) == 2


def test_square_benchmark_selects_exactly_one_hd_target() -> None:
    selected = _select_targets(2170, 3072, SQUARE_BENCHMARK_TARGETS, target_count=1)
    assert selected == SQUARE_BENCHMARK_TARGETS
    assert selected[0]["width"] == selected[0]["height"] == 1536
