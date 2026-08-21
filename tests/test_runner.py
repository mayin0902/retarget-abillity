from __future__ import annotations

import csv
from pathlib import Path

import pytest
import yaml

from retarget_agent.config import RunConfig
from retarget_agent.fixtures import materialize_fixture_dataset
from retarget_agent.methods import built_in_methods
from retarget_agent.models import CandidateRecord, RunManifest
from retarget_agent.protocols import MethodOutput
from retarget_agent.registry import Registry
from retarget_agent.runner import GenerationRunner


class FailingSeam:
    method_id = "seam"
    method_version = "failure-test"

    def generate(self, *args, **kwargs) -> MethodOutput:
        del args, kwargs
        raise RuntimeError("intentional isolated seam failure")


def _two_task_dataset(root: Path) -> Path:
    dataset = materialize_fixture_dataset(root)
    tasks_path = dataset / "tasks.csv"
    with tasks_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))[:2]
        fieldnames = list(rows[0])
    with tasks_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return dataset


def test_two_tasks_generate_eight_frozen_candidate_records(tmp_path: Path) -> None:
    dataset = _two_task_dataset(tmp_path / "dataset")
    config_path = tmp_path / "config.yaml"
    raw = {
        "dataset_root": str(dataset),
        "output_root": str(tmp_path / "runs"),
        "run_id": "fixture-run",
        "methods": ["direct_warp", "crop", "seam", "mesh"],
        "method_parameters": {"seam": {"max_seams_per_axis": 4}},
    }
    config_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    manifest = GenerationRunner.default().run(RunConfig.model_validate(raw), config_path)
    run_dir = tmp_path / "runs" / "fixture-run"
    assert manifest.status == "COMPLETED"
    assert len(manifest.candidate_ids) == 8
    records = [
        CandidateRecord.model_validate_json(path.read_text(encoding="utf-8"))
        for path in run_dir.glob("candidates/*/*/candidate.json")
    ]
    assert len(records) == 8
    assert {record.method_id for record in records} == {
        "direct_warp",
        "crop",
        "seam",
        "mesh",
    }
    assert all(record.output is not None for record in records)
    assert all(record.performance is not None for record in records)
    assert (
        RunManifest.model_validate_json(
            (run_dir / "run.json").read_text(encoding="utf-8")
        ).candidate_ids
        == manifest.candidate_ids
    )


def test_same_run_resumes_without_overwriting_candidates(tmp_path: Path) -> None:
    dataset = _two_task_dataset(tmp_path / "dataset")
    config_path = tmp_path / "config.yaml"
    raw = {
        "dataset_root": str(dataset),
        "output_root": str(tmp_path / "runs"),
        "run_id": "resume-run",
        "method_parameters": {"seam_full": {"proxy_long_edge": 96}},
    }
    config_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    runner = GenerationRunner.default()
    first = runner.run(RunConfig.model_validate(raw), config_path)
    candidate_path = next((tmp_path / "runs" / "resume-run").glob("candidates/*/*/candidate.png"))
    first_mtime = candidate_path.stat().st_mtime_ns
    second = runner.run(RunConfig.model_validate(raw), config_path)
    assert first.candidate_ids == second.candidate_ids
    assert candidate_path.stat().st_mtime_ns == first_mtime


def test_one_method_failure_does_not_block_other_methods(tmp_path: Path) -> None:
    dataset = _two_task_dataset(tmp_path / "dataset")
    config_path = tmp_path / "config.yaml"
    raw = {
        "dataset_root": str(dataset),
        "output_root": str(tmp_path / "runs"),
        "run_id": "failure-run",
    }
    config_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    builtins = built_in_methods()
    registry: Registry[object] = Registry("method")
    for method_id in (
        "direct_warp",
        "crop",
        "seam",
        "mesh",
        "mesh_full",
        "seam_scale",
    ):
        registry.register(method_id, builtins.get(method_id))
    failing = FailingSeam()
    failing.method_id = "seam_full"
    registry.register("seam_full", failing)
    runner = GenerationRunner.default()
    runner.methods = registry
    manifest = runner.run(RunConfig.model_validate(raw), config_path)
    assert manifest.status == "PARTIAL_COMPLETED"
    assert len(manifest.failed_candidate_ids) == 2
    records = [
        CandidateRecord.model_validate_json(path.read_text(encoding="utf-8"))
        for path in (tmp_path / "runs" / "failure-run").glob("candidates/*/*/candidate.json")
    ]
    assert len(records) == 14
    assert sum(record.output is not None for record in records) == 12


def test_run_marks_manifest_failed_when_analyzer_cannot_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset = _two_task_dataset(tmp_path / "dataset")
    config_path = tmp_path / "config.yaml"
    raw = {
        "dataset_root": str(dataset),
        "output_root": str(tmp_path / "runs"),
        "run_id": "analyzer-failure-run",
    }
    config_path.write_text(yaml.safe_dump(raw), encoding="utf-8")

    def fail_to_start(*_args, **_kwargs):
        raise FileNotFoundError("missing pinned detector model")

    monkeypatch.setattr("retarget_agent.runner.SharedProtectionAnalyzer", fail_to_start)

    with pytest.raises(FileNotFoundError, match="missing pinned detector model"):
        GenerationRunner.default().run(RunConfig.model_validate(raw), config_path)

    manifest = RunManifest.model_validate_json(
        (tmp_path / "runs/analyzer-failure-run/run.json").read_text(encoding="utf-8")
    )
    assert manifest.status == "FAILED"
    assert manifest.completed_at is not None
