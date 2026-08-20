"""Reports rebuilt entirely from frozen records and append-only review events."""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from .events import SqliteEventStore
from .models import CandidateRecord, DecisionRecord, ReviewEvent, ReviewGrade, RunManifest
from .storage import LocalArtifactStore


def active_review_events(events: list[ReviewEvent]) -> list[ReviewEvent]:
    superseded = {event.supersedes_event_id for event in events if event.supersedes_event_id}
    active = [event for event in events if event.event_id not in superseded]
    latest: dict[tuple[str, str], ReviewEvent] = {}
    for event in sorted(active, key=lambda item: (item.created_at, item.event_id)):
        latest[(event.reviewer_id, event.candidate_id)] = event
    return list(latest.values())


def _percentile(values: list[float], percentile: float) -> float | None:
    return float(np.percentile(values, percentile)) if values else None


def build_run_report(run_dir: Path) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    store = LocalArtifactStore(run_dir)
    manifest = RunManifest.model_validate(store.read_json("run.json"))
    event_store = SqliteEventStore(run_dir / "events.sqlite")
    candidates = [
        CandidateRecord.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted(run_dir.glob("candidates/*/*/candidate.json"))
    ]
    grouped_by_method: dict[str, list[CandidateRecord]] = defaultdict(list)
    grouped_by_task: dict[str, list[CandidateRecord]] = defaultdict(list)
    for candidate in candidates:
        grouped_by_method[candidate.method_id].append(candidate)
        grouped_by_task[candidate.task_id].append(candidate)

    method_summary: dict[str, Any] = {}
    all_durations: list[float] = []
    peak_memory = 0
    for method_id, records in sorted(grouped_by_method.items()):
        status_counts = Counter(record.generation_status.value for record in records)
        durations = [
            record.performance.wall_seconds
            for record in records
            if record.performance and record.performance.wall_seconds is not None
        ]
        all_durations.extend(durations)
        peaks = [
            record.performance.peak_rss_bytes
            for record in records
            if record.performance and record.performance.peak_rss_bytes is not None
        ]
        peak_memory = max(peak_memory, max(peaks, default=0))
        method_summary[method_id] = {
            "attempts": len(records),
            "output_count": sum(record.output is not None for record in records),
            "technical_completion_rate": (
                sum(record.output is not None for record in records) / len(records)
                if records
                else None
            ),
            "status_counts": dict(status_counts),
            "exception_rate": status_counts.get("FAILED", 0) / len(records) if records else None,
            "wall_seconds_p50": _percentile(durations, 50),
            "wall_seconds_p95": _percentile(durations, 95),
            "cold_first_seconds": durations[0] if durations else None,
            "warm_p50_seconds": _percentile(durations[1:], 50),
        }

    reviews = active_review_events(event_store.review_events(manifest.run_id))
    grade_counts = Counter(event.grade.value for event in reviews)
    scored_grades = (ReviewGrade.A, ReviewGrade.B, ReviewGrade.C, ReviewGrade.D)
    scored_count = sum(grade_counts.get(grade.value, 0) for grade in scored_grades)
    reviewed_candidate_ids = {event.candidate_id for event in reviews}
    grades_by_candidate: dict[str, set[ReviewGrade]] = defaultdict(set)
    for event in reviews:
        grades_by_candidate[event.candidate_id].add(event.grade)
    task_any_a = 0
    task_any_success = 0
    reviewed_tasks = 0
    for _task_id, records in grouped_by_task.items():
        task_grades = set().union(
            *(grades_by_candidate.get(record.candidate_id, set()) for record in records)
        )
        if task_grades:
            reviewed_tasks += 1
        task_any_a += ReviewGrade.A in task_grades
        task_any_success += bool({ReviewGrade.A, ReviewGrade.B} & task_grades)

    best_distribution = Counter(
        event.method_id for event in reviews if event.is_best and event.grade != ReviewGrade.SKIP
    )
    failure_reasons = Counter(
        reason
        for event in reviews
        if event.grade in {ReviewGrade.C, ReviewGrade.D}
        for reason in event.failure_reasons
    )

    top1_a = 0
    top1_success = 0
    top1_reviewed = 0
    for task_id in manifest.task_ids:
        decision_path = run_dir / "decisions" / f"{task_id}.json"
        if not decision_path.is_file():
            continue
        decision = DecisionRecord.model_validate_json(decision_path.read_text(encoding="utf-8"))
        if decision.best_candidate_id is None:
            continue
        grades = grades_by_candidate.get(decision.best_candidate_id, set())
        non_skip = grades - {ReviewGrade.SKIP}
        if non_skip:
            top1_reviewed += 1
            top1_a += ReviewGrade.A in non_skip
            top1_success += bool({ReviewGrade.A, ReviewGrade.B} & non_skip)

    review_summary = {
        "active_review_events": len(reviews),
        "reviewed_candidate_count": len(reviewed_candidate_ids),
        "review_completion_rate": (
            len(reviewed_candidate_ids) / len(candidates) if candidates else None
        ),
        "reviewed_task_count": reviewed_tasks,
        "grade_counts": dict(grade_counts),
        "skip_count": grade_counts.get(ReviewGrade.SKIP.value, 0),
        "scored_count_excluding_skip": scored_count,
        "a_rate": grade_counts.get(ReviewGrade.A.value, 0) / scored_count if scored_count else None,
        "a_plus_b_rate": (
            (grade_counts.get(ReviewGrade.A.value, 0) + grade_counts.get(ReviewGrade.B.value, 0))
            / scored_count
            if scored_count
            else None
        ),
        "any_method_a_tasks": task_any_a,
        "any_method_success_tasks": task_any_success,
        "top1_a_rate": top1_a / top1_reviewed if top1_reviewed else None,
        "top1_success_rate": top1_success / top1_reviewed if top1_reviewed else None,
        "best_candidate_distribution": dict(best_distribution),
        "failure_reasons": dict(failure_reasons),
    }
    report = {
        "schema_version": "1.0",
        "run_id": manifest.run_id,
        "dataset_id": manifest.dataset_id,
        "dataset_fingerprint": manifest.dataset_fingerprint,
        "source_count": len(
            {TaskSource.from_record(path).source_id for path in run_dir.glob("tasks/*.json")}
        ),
        "task_count": len(manifest.task_ids),
        "candidate_attempt_count": len(candidates),
        "candidate_output_count": sum(candidate.output is not None for candidate in candidates),
        "candidate_generation_completion_rate": (
            sum(candidate.output is not None for candidate in candidates) / len(candidates)
            if candidates
            else None
        ),
        "method_summary": method_summary,
        "performance": {
            "candidate_wall_seconds_p50": _percentile(all_durations, 50),
            "candidate_wall_seconds_p95": _percentile(all_durations, 95),
            "peak_rss_bytes": peak_memory or None,
            "gpu": None,
        },
        "reviews": review_summary,
        "notes": [
            "Smoke metrics validate the pipeline and are not algorithm-quality conclusions.",
            "technical_risk_v1 is uncalibrated and is not an M5 routing result.",
        ],
    }
    store.write_json("reports/summary.json", report, overwrite=True)
    return report


class TaskSource:
    @staticmethod
    def from_record(path: Path):
        from .models import TaskSpec

        return TaskSpec.model_validate_json(path.read_text(encoding="utf-8")).source
