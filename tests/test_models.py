from __future__ import annotations

import pytest
from pydantic import ValidationError

from retarget_agent.models import DatasetDescriptor, Rect, SourceRecord, TargetSpec, TaskSpec


def source() -> SourceRecord:
    return SourceRecord(
        source_id="poster-001",
        image_path="images/poster-001.png",
        width=320,
        height=240,
        sha256="a" * 64,
        split="smoke",
    )


def test_task_id_is_source_plus_target() -> None:
    target = TargetSpec(target_id="square-192", width=192, height=192)
    task = TaskSpec(
        dataset_id="smoke-v1",
        task_id="poster-001__square-192",
        source=source(),
        target=target,
    )
    assert task.target.aspect_ratio == 1.0


def test_invalid_task_id_is_rejected() -> None:
    target = TargetSpec(target_id="square-192", width=192, height=192)
    with pytest.raises(ValidationError, match="task_id must be"):
        TaskSpec(dataset_id="smoke-v1", task_id="wrong", source=source(), target=target)


def test_rectangles_are_half_open_and_positive() -> None:
    rect = Rect(x1=4, y1=5, x2=14, y2=25)
    assert (rect.width, rect.height) == (10, 20)
    with pytest.raises(ValidationError):
        Rect(x1=4, y1=5, x2=4, y2=25)


def test_dataset_paths_cannot_escape_root() -> None:
    with pytest.raises(ValidationError, match="dataset root"):
        source().model_copy(update={"image_path": "../secret.png"}).model_validate(
            {**source().model_dump(), "image_path": "../secret.png"}
        )


def test_dataset_descriptor_preserves_evaluation_resolution_policy() -> None:
    descriptor = DatasetDescriptor(
        dataset_id="public-v2",
        version="2.0.0",
        evaluation_canvas="1024x1024",
        generation_originals_may_be_retained_at_2k=True,
        silent_upsampling_forbidden=True,
    )
    assert descriptor.evaluation_canvas == "1024x1024"
    assert descriptor.silent_upsampling_forbidden
    with pytest.raises(ValidationError, match="positive WIDTHxHEIGHT"):
        DatasetDescriptor(
            dataset_id="public-v2",
            version="2.0.0",
            evaluation_canvas="1024 square",
        )
