from __future__ import annotations

import csv
from pathlib import Path

import pytest
import yaml

from retarget_agent.config import RunConfig
from retarget_agent.events import SqliteEventStore
from retarget_agent.fixtures import materialize_fixture_dataset
from retarget_agent.reporting import build_run_report
from retarget_agent.review import load_review_workspace, save_task_reviews
from retarget_agent.runner import GenerationRunner


def _review_run(tmp_path: Path) -> Path:
    dataset = materialize_fixture_dataset(tmp_path / "dataset", source_limit=1)
    with (dataset / "tasks.csv").open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    rows = rows[:1]
    with (dataset / "tasks.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    raw = {
        "dataset_root": str(dataset),
        "output_root": str(tmp_path / "runs"),
        "run_id": "review-run",
        "method_parameters": {"seam_full": {"proxy_long_edge": 96}},
    }
    config_path = tmp_path / "run.yaml"
    config_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    GenerationRunner.default().run(RunConfig.model_validate(raw), config_path)
    return tmp_path / "runs" / "review-run"


def _payload(workspace: dict[str, object], grades: list[str]) -> list[dict[str, object]]:
    candidates = workspace["tasks"][0]["candidates"]
    dimensions = workspace["review_dimensions"]
    return [
        {
            "candidate_id": candidate["candidate_id"],
            "grade": grade,
            "is_best": index == 0,
            "failure_reasons": ["content_cutoff"] if grade in {"B", "C", "D"} else [],
            "dimension_grades": (
                [
                    {"dimension": item["dimension"], "grade": grade, "reason_codes": []}
                    for item in dimensions
                ]
                if grade != "Skip"
                else []
            ),
            "note": "checked",
            "display_order": index,
        }
        for index, (candidate, grade) in enumerate(zip(candidates, grades, strict=True))
    ]


def test_review_save_resume_and_edit_are_append_only(tmp_path: Path) -> None:
    run_dir = _review_run(tmp_path)
    workspace = load_review_workspace(run_dir, "reviewer-one")
    assert workspace["completed_task_count"] == 0
    first = save_task_reviews(
        run_dir,
        "reviewer-one",
        workspace["tasks"][0]["task"]["task_id"],
        _payload(workspace, ["A", "B", "C", "D", "Skip", "A", "B"]),
    )
    resumed = load_review_workspace(run_dir, "reviewer-one")
    assert resumed["completed_task_count"] == 1
    assert [item["review"]["grade"] for item in resumed["tasks"][0]["candidates"]] == [
        "A",
        "B",
        "C",
        "D",
        "Skip",
        "A",
        "B",
    ]
    second = save_task_reviews(
        run_dir,
        "reviewer-one",
        resumed["tasks"][0]["task"]["task_id"],
        _payload(resumed, ["B", "A", "C", "D", "Skip", "A", "B"]),
    )
    assert all(
        new["supersedes_event_id"] == old["event_id"]
        for old, new in zip(first, second, strict=True)
    )
    events = SqliteEventStore(run_dir / "events.sqlite").review_events("review-run")
    assert len(events) == 14
    report = build_run_report(run_dir)
    assert report["reviews"]["active_review_events"] == 7
    assert report["reviews"]["skip_count"] == 1
    assert report["reviews"]["scored_count_excluding_skip"] == 6


def test_review_rejects_incomplete_task_and_invalid_best(tmp_path: Path) -> None:
    run_dir = _review_run(tmp_path)
    workspace = load_review_workspace(run_dir, "reviewer-one")
    task_id = workspace["tasks"][0]["task"]["task_id"]
    payload = _payload(workspace, ["A", "B", "C", "D", "Skip", "A", "B"])
    with pytest.raises(ValueError, match="every attempted candidate"):
        save_task_reviews(run_dir, "reviewer-one", task_id, payload[:-1])
    payload[0]["grade"] = "Skip"
    with pytest.raises(ValueError, match="best candidate"):
        save_task_reviews(run_dir, "reviewer-one", task_id, payload)
