from __future__ import annotations

from retarget_agent.audit import _cross_task_outputs_are_distinct


def test_identical_outputs_across_methods_are_not_placeholder_evidence() -> None:
    passed, evidence = _cross_task_outputs_are_distinct(
        {
            "crop": ["task-1-output", "task-2-crop"],
            "warp": ["task-1-output", "task-2-warp"],
        },
        ("crop", "warp"),
        2,
    )

    assert passed
    assert evidence == ["crop:2/2", "warp:2/2"]


def test_repeated_output_within_one_method_is_placeholder_evidence() -> None:
    passed, evidence = _cross_task_outputs_are_distinct(
        {"crop": ["placeholder", "placeholder"]},
        ("crop",),
        2,
    )

    assert not passed
    assert evidence == ["crop:1/2"]
