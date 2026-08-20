from __future__ import annotations

import csv
from pathlib import Path

import pytest

from retarget_agent.storage import LocalArtifactStore
from retarget_agent.stratified_reporting import build_stratified_benchmark_report

METHODS = ("direct_warp", "crop", "seam", "mesh")


def _write_fixture(root: Path, source_manifest: Path, *, omit_last_mesh: bool = False) -> Path:
    store = LocalArtifactStore(root)
    tasks = ("source-1__square", "source-2__square")
    sources = ("source-1", "source-2")
    source_manifest.parent.mkdir(parents=True, exist_ok=True)
    with source_manifest.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=("source_id", "scene_category", "difficulty_tier")
        )
        writer.writeheader()
        writer.writerow(
            {
                "source_id": "source-1",
                "scene_category": "poster",
                "difficulty_tier": "aspect_hard_1",
            }
        )
        writer.writerow(
            {
                "source_id": "source-2",
                "scene_category": "multi_person",
                "difficulty_tier": "aspect_hard_2",
            }
        )

    candidate_ids: list[str] = []
    for task_index, (task_id, source_id) in enumerate(zip(tasks, sources, strict=True)):
        store.write_json(
            f"tasks/{task_id}.json",
            {"task_id": task_id, "source": {"source_id": source_id}},
        )
        for method_index, method_id in enumerate(METHODS):
            if omit_last_mesh and task_index == 1 and method_id == "mesh":
                continue
            candidate_id = f"{task_id}--{method_id}--v1"
            candidate_ids.append(candidate_id)
            cpu_seconds = None if task_index == 1 and method_id == "crop" else 0.5
            store.write_json(
                f"candidates/{task_id}/{method_id}/candidate.json",
                {
                    "candidate_id": candidate_id,
                    "task_id": task_id,
                    "method_id": method_id,
                    "performance": {
                        "wall_seconds": 1.0 + method_index,
                        "cpu_seconds": cpu_seconds,
                        "peak_rss_bytes": 1000 + method_index,
                    },
                },
            )
            store.write_json(
                f"evaluations/eval-1/metrics/{candidate_id}.json",
                {
                    "candidate_id": candidate_id,
                    "metrics": {
                        "quality_score": 90.0 - method_index - task_index,
                        "proxy_grade": "proxy_a" if method_index < 2 else "proxy_b",
                        "ocr_character_recall": 0.8 if task_index == 0 else None,
                        "face_count_preservation": None,
                        "person_count_preservation": 0.75 if task_index == 1 else None,
                        "product_count_preservation": None,
                        "logo_count_preservation": None,
                        "structure_line_similarity": 0.6 + 0.01 * method_index,
                        "evaluation_wall_seconds": 0.2,
                        "evaluation_cpu_seconds": 0.1,
                        "evaluation_rss_delta_bytes": 50,
                    },
                },
            )
    store.write_json(
        "run.json",
        {
            "run_id": "run-1",
            "dataset_id": "dataset-1",
            "dataset_fingerprint": "a" * 64,
            "status": "COMPLETED",
            "methods": list(METHODS),
            "config_hash": "b" * 64,
            "config_snapshot": "config/run.yaml",
            "code_version": "test",
            "python_version": "3.13",
            "dependency_versions": {},
            "task_ids": list(tasks),
            "candidate_ids": candidate_ids,
        },
    )
    store.write_json(
        "evaluations/eval-1/evaluation.json",
        {
            "evaluation_id": "eval-1",
            "source_run_id": "run-1",
            "task_ids": list(tasks),
            "candidate_ids": candidate_ids,
        },
    )
    return root


def test_stratified_report_has_complete_global_and_crossed_rows(tmp_path: Path) -> None:
    manifest = tmp_path / "dataset" / "source_manifest.csv"
    run_dir = _write_fixture(tmp_path / "run", manifest)

    report = build_stratified_benchmark_report(run_dir, "eval-1", manifest, "bench-1")

    assert report["all_method_denominators_complete"]
    assert report["task_count"] == 2
    assert report["candidate_count"] == 8
    assert len(report["rows"]) == 12
    crop_global = next(
        row
        for row in report["rows"]
        if row["method_id"] == "crop" and row["stratum_level"] == "global_method"
    )
    assert crop_global["count"] == 2
    assert crop_global["quality_score_mean"] == 88.5
    assert crop_global["proxy_a_rate"] == 1.0
    assert crop_global["proxy_success_rate"] == 1.0
    assert crop_global["ocr_character_recall_mean"] == 0.8
    assert crop_global["ocr_character_recall_observed_count"] == 1
    assert crop_global["face_count_preservation_mean"] is None
    assert crop_global["face_count_preservation_observed_count"] == 0
    assert crop_global["generation_cpu_seconds_mean"] is None
    assert crop_global["generation_cpu_seconds_total"] is None
    assert crop_global["generation_cpu_seconds_observed_count"] == 1
    assert (run_dir / "benchmarks" / "bench-1" / "stratified-report.json").is_file()
    assert (run_dir / "benchmarks" / "bench-1" / "strata.csv").is_file()


def test_stratified_report_rejects_incomplete_four_method_denominator(tmp_path: Path) -> None:
    manifest = tmp_path / "dataset" / "source_manifest.csv"
    run_dir = _write_fixture(tmp_path / "run", manifest, omit_last_mesh=True)

    with pytest.raises(ValueError, match="declared method profile"):
        build_stratified_benchmark_report(run_dir, "eval-1", manifest, "bench-1")
    assert not (run_dir / "benchmarks" / "bench-1").exists()


def test_stratified_report_never_overwrites_benchmark_id(tmp_path: Path) -> None:
    manifest = tmp_path / "dataset" / "source_manifest.csv"
    run_dir = _write_fixture(tmp_path / "run", manifest)
    build_stratified_benchmark_report(run_dir, "eval-1", manifest, "bench-1")

    with pytest.raises(FileExistsError, match="benchmark_id already exists"):
        build_stratified_benchmark_report(run_dir, "eval-1", manifest, "bench-1")
