from __future__ import annotations

import csv
from pathlib import Path

import yaml

from retarget_agent.config import RunConfig
from retarget_agent.fixtures import materialize_fixture_dataset
from retarget_agent.replay import run_evaluation_replay
from retarget_agent.reporting import build_run_report
from retarget_agent.runner import GenerationRunner


def _run_one_source(tmp_path: Path) -> Path:
    dataset = materialize_fixture_dataset(tmp_path / "dataset", source_limit=1)
    tasks_path = dataset / "tasks.csv"
    with tasks_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    config_path = tmp_path / "config.yaml"
    raw = {
        "dataset_root": str(dataset),
        "output_root": str(tmp_path / "runs"),
        "run_id": "report-run",
        "method_parameters": {"seam": {"max_seams_per_axis": 1}},
    }
    config_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    GenerationRunner.default().run(RunConfig.model_validate(raw), config_path)
    assert len(rows) == 2
    return tmp_path / "runs" / "report-run"


def test_replay_does_not_modify_candidates(tmp_path: Path) -> None:
    run_dir = _run_one_source(tmp_path)
    candidate_paths = sorted(run_dir.glob("candidates/*/*/candidate.png"))
    mtimes = {path: path.stat().st_mtime_ns for path in candidate_paths}
    replay = run_evaluation_replay(run_dir, "replay-v1")
    assert len(replay.candidate_ids) == 8
    assert all(path.stat().st_mtime_ns == mtimes[path] for path in candidate_paths)
    assert (run_dir / "replays" / "replay-v1" / "replay.json").is_file()


def test_report_contains_method_and_performance_summaries(tmp_path: Path) -> None:
    run_dir = _run_one_source(tmp_path)
    report = build_run_report(run_dir)
    assert report["task_count"] == 2
    assert report["candidate_attempt_count"] == 8
    assert set(report["method_summary"]) == {"direct_warp", "crop", "seam", "mesh"}
    assert report["performance"]["candidate_wall_seconds_p95"] is not None
    assert report["reviews"]["scored_count_excluding_skip"] == 0
