from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from retarget_agent.aigc_experiment import (
    load_aigc_human_calibration_feedback,
    run_seedream_plan,
    should_rule_request_aigc,
)


def test_rule_aigc_requires_both_proxy_c() -> None:
    triggered, reasons = should_rule_request_aigc(
        ({"proxy_grade": "proxy_c"}, {"proxy_grade": "proxy_c"})
    )

    assert triggered
    assert reasons == ("rule_top2_both_proxy_c",)


def test_rule_aigc_triggers_on_shared_material_b_defect() -> None:
    triggered, reasons = should_rule_request_aigc(
        (
            {"proxy_grade": "proxy_b", "ocr_character_recall": 0.5},
            {"proxy_grade": "proxy_b", "ocr_character_recall": 0.6},
        )
    )

    assert triggered
    assert "shared:text_damage" in reasons


def test_rule_aigc_does_not_trigger_only_because_result_is_not_a() -> None:
    triggered, reasons = should_rule_request_aigc(
        (
            {"proxy_grade": "proxy_b", "ocr_character_recall": 0.95},
            {"proxy_grade": "proxy_b", "ocr_character_recall": 0.96},
        )
    )

    assert not triggered
    assert reasons == ()


@pytest.mark.parametrize("timeout", [29.9, 1800.1])
def test_seedream_execution_rejects_unbounded_timeout(timeout: float) -> None:
    with pytest.raises(ValueError, match="read_timeout_seconds"):
        run_seedream_plan(
            Path("missing-run"),
            "missing-plan",
            limit=1,
            budget_cny=Decimal("0.60"),
            read_timeout_seconds=timeout,
        )


def test_explicit_human_aigc_calibration_overrides_only_matching_pixels(
    tmp_path: Path,
) -> None:
    feedback_path = (
        tmp_path / "external-generation" / "human-calibration" / "user-feedback-v1" / "task-1.json"
    )
    feedback_path.parent.mkdir(parents=True)
    feedback_path.write_text(
        json.dumps(
            {
                "task_id": "task-1",
                "candidate_id": "task-1--seedream--v1",
                "candidate_sha256": "a" * 64,
                "feedback_source": "user_explicit_visual_review",
                "human_grade": "A",
                "directly_usable": True,
            }
        ),
        encoding="utf-8",
    )

    feedback = load_aigc_human_calibration_feedback(
        tmp_path,
        "task-1",
        "task-1--seedream--v1",
        "a" * 64,
    )

    assert feedback is not None
    assert feedback["human_grade"] == "A"
    with pytest.raises(ValueError, match="hash mismatch"):
        load_aigc_human_calibration_feedback(
            tmp_path,
            "task-1",
            "task-1--seedream--v1",
            "b" * 64,
        )
