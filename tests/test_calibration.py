from __future__ import annotations

import pytest

from retarget_agent.calibration import CalibrationObservation, compute_grade_calibration


def _row(task: str, candidate: str, grade: str, quality: float) -> CalibrationObservation:
    return CalibrationObservation(task, candidate, candidate, grade, quality)


def test_calibration_separates_ordering_from_same_grade_gaps() -> None:
    report = compute_grade_calibration(
        [
            _row("mixed", "crop", "A", 81),
            _row("mixed", "seam", "A", 76),
            _row("mixed", "mesh", "B", 70),
            _row("all-a", "crop", "A", 85),
            _row("all-a", "seam", "A", 72),
        ]
    )
    ordering = report["different_grade_ordering"]
    assert ordering["pair_count"] == 2
    assert ordering["ordering_accuracy"] == 1.0
    assert report["same_grade_score_gap"]["A"]["count"] == 2
    assert report["all_same_grade_tasks"]["count"] == 1
    assert report["all_a_tasks"]["median"] == 13
    assert report["top1"]["eligible_task_count"] == 1
    assert report["top1"]["hit_rate"] == 1.0


def test_calibration_accepts_any_human_tied_best_for_top1() -> None:
    report = compute_grade_calibration(
        [
            _row("task", "crop", "A", 76),
            _row("task", "seam", "A", 81),
            _row("task", "mesh", "B", 80),
        ]
    )
    assert report["top1"]["hit_rate"] == 1.0


def test_calibration_rejects_duplicate_candidate_identity() -> None:
    row = _row("task", "crop", "A", 80)
    with pytest.raises(ValueError, match="duplicate"):
        compute_grade_calibration([row, row])
