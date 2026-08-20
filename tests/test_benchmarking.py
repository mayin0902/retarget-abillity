from __future__ import annotations

import json
from pathlib import Path

import pytest

from retarget_agent.benchmarking import build_benchmark_report
from retarget_agent.storage import LocalArtifactStore


def _write_benchmark_fixture(root: Path, *, omit_route_task: bool = False) -> Path:
    store = LocalArtifactStore(root)
    tasks = ("task-1", "task-2")
    candidate_ids: list[str] = []
    for task_index, task_id in enumerate(tasks):
        for method_index, method_id in enumerate(("crop", "mesh")):
            candidate_id = f"{task_id}--{method_id}--v1"
            candidate_ids.append(candidate_id)
            store.write_json(
                f"candidates/{task_id}/{method_id}/candidate.json",
                {
                    "candidate_id": candidate_id,
                    "task_id": task_id,
                    "method_id": method_id,
                    "performance": {
                        "wall_seconds": 1.0 + method_index,
                        "cpu_seconds": 0.5 + method_index,
                        "peak_rss_bytes": 100 + method_index,
                    },
                },
            )
            store.write_json(
                f"evaluations/eval-1/metrics/{candidate_id}.json",
                {
                    "candidate_id": candidate_id,
                    "metrics": {
                        "quality_score": 90 - 10 * method_index - task_index,
                        "proxy_grade": "proxy_a" if method_index == 0 else "proxy_b",
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
            "methods": ["crop", "mesh"],
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
        {"evaluation_id": "eval-1", "task_ids": list(tasks)},
    )
    store.write_json(
        "agent-runs/fixed-crop/agent-run.json",
        {"evaluation_id": "eval-1"},
    )
    decision_tasks = tasks[:1] if omit_route_task else tasks
    for task_id in decision_tasks:
        selected = f"{task_id}--crop--v1"
        store.write_json(
            f"agent-runs/fixed-crop/decisions/{task_id}.json",
            {
                "task_id": task_id,
                "selected_candidate_id": selected,
                "deterministic_candidate_id": selected,
                "agent_call_id": None,
                "route_action": "USE_BEST_TRADITIONAL",
            },
        )
    return root


def test_benchmark_report_includes_only_complete_denominators(tmp_path: Path) -> None:
    run_dir = _write_benchmark_fixture(tmp_path / "run")
    report = build_benchmark_report(run_dir, "eval-1", "bench-1", ("fixed-crop",))
    assert report["all_arms_complete"]
    assert report["task_count"] == 2
    assert len(report["rows"]) == 4
    assert report["rows"][0]["completed_task_count"] == 2
    oracle_row = next(
        row for row in report["rows"] if row["arm_id"] == "upper-bound:posthoc-proxy-argmax"
    )
    assert oracle_row["quality_score_mean"] == 89.5
    assert oracle_row["proxy_routing_regret_mean"] == 0.0
    route_row = next(row for row in report["rows"] if row["arm_id"] == "route:fixed-crop")
    assert route_row["proxy_routing_regret_mean"] == 0.0
    assert route_row["proxy_delta_vs_deterministic_mean"] == 0.0
    assert route_row["agent_call_rate"] == 0.0
    assert route_row["agent_schema_valid_rate"] is None
    assert (run_dir / "benchmarks" / "bench-1" / "arms.csv").is_file()


def test_benchmark_report_rejects_partial_route(tmp_path: Path) -> None:
    run_dir = _write_benchmark_fixture(tmp_path / "run", omit_route_task=True)
    with pytest.raises(ValueError, match="route fixed-crop is incomplete"):
        build_benchmark_report(run_dir, "eval-1", "bench-1", ("fixed-crop",))


def _add_observed_calls(run_dir: Path, *, missing_input_tokens: bool = False) -> None:
    for index, task_id in enumerate(("task-1", "task-2"), start=1):
        call_id = f"call-{index}"
        decision_path = run_dir / "agent-runs" / "fixed-crop" / "decisions" / f"{task_id}.json"
        decision = json.loads(decision_path.read_text(encoding="utf-8"))
        decision["agent_call_id"] = call_id
        decision_path.write_text(json.dumps(decision), encoding="utf-8")
        LocalArtifactStore(run_dir).write_json(
            f"agent-runs/fixed-crop/calls/{call_id}.json",
            {
                "agent_call_id": call_id,
                "success": True,
                "cache_hit": False,
                "latency_seconds": float(index),
                "attempt_count": index,
                "input_tokens": None if missing_input_tokens and index == 2 else 100 * index,
                "output_tokens": 10 * index,
                "estimated_cost": None,
            },
        )


def test_agent_token_and_attempt_totals_preserve_observed_values(tmp_path: Path) -> None:
    run_dir = _write_benchmark_fixture(tmp_path / "run")
    _add_observed_calls(run_dir)

    report = build_benchmark_report(run_dir, "eval-1", "bench-tokens", ("fixed-crop",))

    route = next(row for row in report["rows"] if row["arm_id"] == "route:fixed-crop")
    assert route["agent_schema_valid_count"] == 2
    assert route["agent_cache_hit_count"] == 0
    assert route["agent_attempt_count"] == 3
    assert route["agent_input_tokens_total"] == 300
    assert route["agent_output_tokens_total"] == 30
    assert route["agent_tokens_total"] == 330
    assert route["agent_token_observation_count"] == 2
    assert route["agent_observed_tokens_total"] == 330


def test_agent_token_total_is_unknown_when_any_call_is_unobserved(tmp_path: Path) -> None:
    run_dir = _write_benchmark_fixture(tmp_path / "run")
    _add_observed_calls(run_dir, missing_input_tokens=True)

    report = build_benchmark_report(run_dir, "eval-1", "bench-unknown-tokens", ("fixed-crop",))

    route = next(row for row in report["rows"] if row["arm_id"] == "route:fixed-crop")
    assert route["agent_input_tokens_total"] is None
    assert route["agent_output_tokens_total"] == 30
    assert route["agent_tokens_total"] is None
    assert route["agent_token_observation_count"] == 1
    assert route["agent_observed_tokens_total"] == 110
