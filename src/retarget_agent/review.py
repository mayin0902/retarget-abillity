"""Human review application use cases over a frozen Generation Run."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any
from uuid import uuid4

from .events import SqliteEventStore
from .models import (
    CandidateRecord,
    DecisionRecord,
    ReviewDimension,
    ReviewDimensionGrade,
    ReviewEvent,
    ReviewGrade,
    RunManifest,
    TaskSpec,
    validate_id,
)
from .storage import LocalArtifactStore

FAILURE_REASONS = (
    "content_cutoff",
    "text_or_logo_damage",
    "person_or_product_distortion",
    "structure_bending",
    "layout_imbalance",
    "important_content_too_small",
    "visible_seam_or_artifact",
    "wrong_target_composition",
    "technical_failure",
    "other",
)

REVIEW_DIMENSIONS = (
    (ReviewDimension.SUBJECT, "主体完整与显著性"),
    (ReviewDimension.FACE_BODY, "人脸与人体自然度"),
    (ReviewDimension.TEXT, "文字与关键数字可读性"),
    (ReviewDimension.PRODUCT_LOGO, "商品与 Logo 完整性"),
    (ReviewDimension.COMPOSITION, "构图与视觉平衡"),
    (ReviewDimension.ARTIFACT, "接缝、拉伸与伪影控制"),
)


def _active_by_candidate(events: list[ReviewEvent], reviewer_id: str) -> dict[str, ReviewEvent]:
    """Return the latest non-superseded event for each reviewed candidate."""
    superseded = {event.supersedes_event_id for event in events if event.supersedes_event_id}
    active = [
        event
        for event in events
        if event.reviewer_id == reviewer_id and event.event_id not in superseded
    ]
    latest: dict[str, ReviewEvent] = {}
    for event in sorted(active, key=lambda item: (item.created_at, item.event_id)):
        latest[event.candidate_id] = event
    return latest


def load_review_workspace(run_dir: Path, reviewer_id: str) -> dict[str, Any]:
    """Load immutable artifacts plus this reviewer's resumable current state."""
    reviewer_id = validate_id(reviewer_id)
    run_dir = run_dir.resolve()
    store = LocalArtifactStore(run_dir)
    manifest = RunManifest.model_validate(store.read_json("run.json"))
    event_store = SqliteEventStore(run_dir / "events.sqlite")
    event_store.initialize()
    active = _active_by_candidate(event_store.review_events(manifest.run_id), reviewer_id)

    grouped: dict[str, list[CandidateRecord]] = defaultdict(list)
    for path in sorted(run_dir.glob("candidates/*/*/candidate.json")):
        candidate = CandidateRecord.model_validate_json(path.read_text(encoding="utf-8"))
        grouped[candidate.task_id].append(candidate)

    tasks: list[dict[str, Any]] = []
    completed_tasks = 0
    for task_id in manifest.task_ids:
        task = TaskSpec.model_validate(store.read_json(f"tasks/{task_id}.json"))
        decision = DecisionRecord.model_validate(store.read_json(f"decisions/{task_id}.json"))
        candidates = sorted(
            grouped[task_id],
            key=lambda item: manifest.methods.index(item.method_id),
        )
        reviews = {
            candidate.candidate_id: active.get(candidate.candidate_id) for candidate in candidates
        }
        if candidates and all(reviews.values()):
            completed_tasks += 1
        source_suffix = Path(task.source.image_path).suffix.lower() or ".img"
        source_path = run_dir / "sources" / f"{task.source.source_id}{source_suffix}"
        tasks.append(
            {
                "task": task.model_dump(mode="json"),
                "source_path": str(source_path),
                "decision": decision.model_dump(mode="json"),
                "candidates": [
                    {
                        **candidate.model_dump(mode="json"),
                        "image_path": (
                            str(run_dir / candidate.output.relative_path)
                            if candidate.output
                            else None
                        ),
                        "review": (
                            reviews[candidate.candidate_id].model_dump(mode="json")
                            if reviews[candidate.candidate_id]
                            else None
                        ),
                    }
                    for candidate in candidates
                ],
            }
        )
    return {
        "run_id": manifest.run_id,
        "reviewer_id": reviewer_id,
        "task_count": len(tasks),
        "completed_task_count": completed_tasks,
        "tasks": tasks,
        "failure_reasons": list(FAILURE_REASONS),
        "review_dimensions": [
            {"dimension": dimension.value, "label": label} for dimension, label in REVIEW_DIMENSIONS
        ],
    }


def save_task_reviews(
    run_dir: Path,
    reviewer_id: str,
    task_id: str,
    reviews: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Append one complete task review; edits supersede but never mutate old events."""
    reviewer_id = validate_id(reviewer_id)
    task_id = validate_id(task_id)
    run_dir = run_dir.resolve()
    store = LocalArtifactStore(run_dir)
    manifest = RunManifest.model_validate(store.read_json("run.json"))
    if task_id not in manifest.task_ids:
        raise ValueError(f"task is not part of run: {task_id}")
    candidates = {
        record.candidate_id: record
        for path in sorted((run_dir / "candidates" / task_id).glob("*/candidate.json"))
        for record in [CandidateRecord.model_validate_json(path.read_text(encoding="utf-8"))]
    }
    submitted = {str(item["candidate_id"]) for item in reviews}
    if submitted != set(candidates):
        raise ValueError("a task save must include every attempted candidate exactly once")
    if len(reviews) != len(submitted):
        raise ValueError("duplicate candidate review")

    grades = {str(item["candidate_id"]): ReviewGrade(item["grade"]) for item in reviews}
    best_ids = [str(item["candidate_id"]) for item in reviews if bool(item.get("is_best"))]
    if len(best_ids) > 1:
        raise ValueError("at most one best candidate may be selected per task")
    if best_ids and grades[best_ids[0]] not in {ReviewGrade.A, ReviewGrade.B}:
        raise ValueError("the best candidate must have grade A or B")

    event_store = SqliteEventStore(run_dir / "events.sqlite")
    event_store.initialize()
    previous = _active_by_candidate(event_store.review_events(manifest.run_id), reviewer_id)
    saved: list[ReviewEvent] = []
    for display_order, item in enumerate(reviews):
        candidate_id = str(item["candidate_id"])
        candidate = candidates[candidate_id]
        failure_reasons = tuple(str(reason) for reason in item.get("failure_reasons", ()))
        unknown = set(failure_reasons) - set(FAILURE_REASONS)
        if unknown:
            raise ValueError(f"unknown failure reasons: {sorted(unknown)}")
        grade = grades[candidate_id]
        if grade in {ReviewGrade.C, ReviewGrade.D} and not failure_reasons:
            raise ValueError("grade C or D requires at least one issue reason")
        if grade in {ReviewGrade.A, ReviewGrade.SKIP} and failure_reasons:
            raise ValueError("issue reasons are only valid for grades B, C, or D")
        dimension_grades = tuple(
            ReviewDimensionGrade.model_validate(value) for value in item.get("dimension_grades", ())
        )
        expected_dimensions = {dimension for dimension, _ in REVIEW_DIMENSIONS}
        submitted_dimensions = {entry.dimension for entry in dimension_grades}
        if len(dimension_grades) != len(submitted_dimensions):
            raise ValueError("review dimensions must be unique")
        if grade is not ReviewGrade.SKIP and submitted_dimensions != expected_dimensions:
            raise ValueError("non-Skip reviews require every rubric dimension exactly once")
        if grade is ReviewGrade.SKIP and dimension_grades:
            raise ValueError("Skip reviews cannot contain dimension grades")
        prior = previous.get(candidate_id)
        event = ReviewEvent(
            event_id=f"review-{uuid4().hex}",
            run_id=manifest.run_id,
            reviewer_id=reviewer_id,
            task_id=task_id,
            candidate_id=candidate_id,
            method_id=candidate.method_id,
            grade=grade,
            is_best=bool(item.get("is_best")),
            failure_reasons=failure_reasons,
            dimension_grades=dimension_grades,
            note=(str(item["note"]).strip() or None) if item.get("note") else None,
            display_order=int(item.get("display_order", display_order)),
            method_name_visible=True,
            supersedes_event_id=prior.event_id if prior else None,
        )
        event_store.append_review(event)
        saved.append(event)
    return [event.model_dump(mode="json") for event in saved]
