from __future__ import annotations

import json
from pathlib import Path

import pytest

from retarget_agent.round_aggregation import aggregate_benchmark_rounds


def _write_json(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _report(
    path: Path,
    *,
    benchmark_id: str,
    run_id: str,
    task_count: int,
    quality: float,
    cost: float | None,
    model_version: str = "model-v1",
    all_complete: bool = True,
    agent_complete: bool = True,
) -> Path:
    return _write_json(
        path,
        {
            "benchmark_id": benchmark_id,
            "run_id": run_id,
            "task_count": task_count,
            "all_arms_complete": all_complete,
            "rows": [
                {
                    "arm_id": "source-method",
                    "arm_type": "traditional_method",
                    "complete": True,
                    "required_task_count": task_count,
                    "completed_task_count": task_count,
                    "model_version": None,
                    "route_mode": None,
                    "quality_score_mean": quality - 5,
                    "proxy_a_rate": 0.5,
                    "proxy_success_rate": 0.8,
                    "wall_seconds_total": float(task_count),
                    "cpu_seconds_total": float(task_count) / 2,
                    "direct_cost_cny_total": 0.0,
                    "external_generation_count": 0,
                    "peak_rss_bytes_max": 100,
                    "end_to_end_wall_seconds_p50": 1.0,
                    "end_to_end_wall_seconds_p95": 2.0,
                },
                {
                    "arm_id": "source-agent",
                    "arm_type": "agent_or_routing_policy",
                    "complete": agent_complete,
                    "required_task_count": task_count,
                    "completed_task_count": task_count,
                    "model_version": model_version,
                    "route_mode": "always_on_agent",
                    "quality_score_mean": quality,
                    "proxy_a_rate": 0.75,
                    "proxy_success_rate": 0.9,
                    "proxy_routing_regret_mean": 1.0,
                    "agent_call_rate": 1.0,
                    "agent_schema_valid_rate": 0.95,
                    "agent_top1_change_rate": 0.25,
                    "agent_latency_seconds_mean": 2.0,
                    "wall_seconds_total": float(task_count) * 2,
                    "cpu_seconds_total": None,
                    "direct_cost_cny_total": cost,
                    "agent_call_count": task_count,
                    "agent_schema_valid_count": task_count - 1,
                    "agent_cache_hit_count": task_count - 2,
                    "agent_attempt_count": task_count + 1,
                    "agent_input_tokens_total": task_count * 100,
                    "agent_output_tokens_total": task_count * 10,
                    "agent_tokens_total": task_count * 110,
                    "agent_token_observation_count": task_count,
                    "agent_observed_tokens_total": task_count * 110,
                    "agent_failure_count": 1,
                    "external_generation_count": 2,
                    "peak_rss_bytes_max": None,
                    "end_to_end_wall_seconds_p50": 2.0,
                    "end_to_end_wall_seconds_p95": 3.0,
                    "agent_latency_seconds_p95": 3.0,
                },
            ],
        },
    )


def _spec(
    path: Path,
    first: Path,
    second: Path,
    *,
    second_map: dict[str, str] | None = None,
) -> Path:
    arm_map = {"method": "source-method", "agent": "source-agent"}
    return _write_json(
        path,
        {
            "rounds": [
                {"name": "round-a", "benchmark_report": str(first), "arm_map": arm_map},
                {
                    "name": "round-b",
                    "benchmark_report": str(second),
                    "arm_map": second_map if second_map is not None else arm_map,
                },
            ]
        },
    )


def _two_reports(tmp_path: Path, *, second_cost: float | None = 3.0) -> tuple[Path, Path]:
    first = _report(
        tmp_path / "round-a.json",
        benchmark_id="bench-a",
        run_id="run-a",
        task_count=10,
        quality=80.0,
        cost=1.0,
    )
    second = _report(
        tmp_path / "round-b.json",
        benchmark_id="bench-b",
        run_id="run-b",
        task_count=30,
        quality=90.0,
        cost=second_cost,
    )
    return first, second


def test_complete_rounds_are_weighted_and_totals_are_summed(tmp_path: Path) -> None:
    first, second = _two_reports(tmp_path)
    spec = _spec(tmp_path / "spec.json", first, second)

    report = aggregate_benchmark_rounds(spec, tmp_path / "output", "full-v1")

    agent = next(row for row in report["rows"] if row["arm_id"] == "agent")
    assert report["task_count"] == 40
    assert agent["quality_score_mean"] == 87.5
    assert agent["proxy_a_rate"] == 0.75
    assert agent["agent_schema_valid_rate"] == 0.95
    assert agent["wall_seconds_total"] == 80.0
    assert agent["direct_cost_cny_total"] == 4.0
    assert agent["agent_call_count"] == 40
    assert agent["agent_schema_valid_count"] == 38
    assert agent["agent_schema_valid_rate"] == 0.95
    assert agent["agent_cache_hit_count"] == 36
    assert agent["agent_cache_hit_rate"] == 0.9
    assert agent["agent_attempt_count"] == 42
    assert agent["agent_input_tokens_total"] == 4000
    assert agent["agent_output_tokens_total"] == 400
    assert agent["agent_tokens_total"] == 4400
    assert agent["agent_token_observation_count"] == 40
    assert agent["agent_observed_tokens_total"] == 4400
    assert agent["agent_failure_count"] == 2
    assert agent["end_to_end_wall_seconds_p50"] is None
    assert agent["end_to_end_wall_seconds_p95"] is None
    assert agent["agent_latency_seconds_p95"] is None
    assert report["sources"][0]["report_sha256"]
    assert (tmp_path / "output" / "full-v1" / "report.json").is_file()
    assert (tmp_path / "output" / "full-v1" / "arms.csv").is_file()


def test_missing_canonical_mapping_is_rejected(tmp_path: Path) -> None:
    first, second = _two_reports(tmp_path)
    spec = _spec(
        tmp_path / "spec.json",
        first,
        second,
        second_map={"agent": "source-agent"},
    )

    with pytest.raises(ValueError, match="canonical arm sets"):
        aggregate_benchmark_rounds(spec, tmp_path / "output", "full-v1")


def test_partial_benchmark_is_rejected(tmp_path: Path) -> None:
    first, _ = _two_reports(tmp_path)
    second = _report(
        tmp_path / "partial.json",
        benchmark_id="bench-partial",
        run_id="run-partial",
        task_count=30,
        quality=90.0,
        cost=3.0,
        all_complete=False,
    )
    spec = _spec(tmp_path / "spec.json", first, second)

    with pytest.raises(ValueError, match="not all-arms complete"):
        aggregate_benchmark_rounds(spec, tmp_path / "output", "full-v1")


def test_unknown_cost_propagates_to_aggregate(tmp_path: Path) -> None:
    first, second = _two_reports(tmp_path, second_cost=None)
    spec = _spec(tmp_path / "spec.json", first, second)

    report = aggregate_benchmark_rounds(spec, tmp_path / "output", "full-v1")

    agent = next(row for row in report["rows"] if row["arm_id"] == "agent")
    assert agent["direct_cost_cny_total"] is None


def test_model_version_mismatch_is_rejected(tmp_path: Path) -> None:
    first, _ = _two_reports(tmp_path)
    second = _report(
        tmp_path / "round-b.json",
        benchmark_id="bench-b",
        run_id="run-b",
        task_count=30,
        quality=90.0,
        cost=3.0,
        model_version="model-v2",
    )
    spec = _spec(tmp_path / "spec.json", first, second)

    with pytest.raises(ValueError, match="model_version mismatch"):
        aggregate_benchmark_rounds(spec, tmp_path / "output", "full-v1")


def test_existing_report_id_is_never_overwritten(tmp_path: Path) -> None:
    first, second = _two_reports(tmp_path)
    spec = _spec(tmp_path / "spec.json", first, second)
    aggregate_benchmark_rounds(spec, tmp_path / "output", "full-v1")

    with pytest.raises(FileExistsError, match="report ID already exists"):
        aggregate_benchmark_rounds(spec, tmp_path / "output", "full-v1")
