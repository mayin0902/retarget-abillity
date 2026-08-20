from __future__ import annotations

import json
from pathlib import Path

import pytest

from retarget_agent.resource_cost_reporting import build_resource_cost_report


def _write_json(path: Path, payload: dict[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _benchmark(root: Path) -> Path:
    return _write_json(
        root / "benchmarks" / "bench-source" / "report.json",
        {
            "schema_version": "1.0",
            "benchmark_id": "bench-source",
            "run_id": "run-1",
            "all_arms_complete": True,
            "rows": [
                {
                    "arm_id": "route:qwen3vl4b-always",
                    "arm_type": "agent_or_routing_policy",
                    "complete": True,
                    "required_task_count": 10,
                    "direct_cost_cny_total": None,
                    "agent_call_count": 10,
                    "agent_call_rate": 1.0,
                    "agent_latency_seconds_mean": 2.5,
                    "agent_latency_seconds_p95": 3.5,
                    "schema_valid_rate": 0.9,
                },
                {
                    "arm_id": "route:smolvlm2-always",
                    "arm_type": "agent_or_routing_policy",
                    "complete": True,
                    "required_task_count": 10,
                    "direct_cost_cny_total": 0.0,
                    "agent_call_count": 10,
                    "agent_call_rate": 1.0,
                    "agent_latency_seconds_mean": 1.5,
                    "agent_latency_seconds_p95": 2.0,
                    "schema_valid_rate": 1.0,
                },
            ],
        },
    )


def _observation(
    path: Path,
    *,
    model: str,
    scope: str | None,
    active_seconds: float | None,
    energy_coverage: str | None = None,
) -> Path:
    workload: dict[str, object] = {
        "served_model_name": model,
        "model_id": f"org/{model}",
        "revision": "abc123",
    }
    if scope is not None:
        workload["capture_scope"] = scope
    observation: dict[str, object] = {
        "gpu_index": 0,
        "workload": workload,
        "activity": {"active_seconds_left_rectangle": active_seconds},
        "power": {
            "active_mean_watts_time_weighted_left_rectangle": 200.0,
            "peak_watts": 300.0,
            "energy_wh_active_left_rectangle": 20.0,
            "energy_wh_total_trapezoidal": 25.0,
        },
        "memory": {"peak_used_mib": 12000.0},
    }
    if energy_coverage is not None:
        observation["energy_coverage"] = energy_coverage
    return _write_json(
        path,
        {
            "schema_version": "1.0",
            "observation_id": f"obs-{model}",
            "environment": {"gpu_model": "Test GPU"},
            "observations": [observation],
        },
    )


def test_complete_observation_cost_and_benchmark_fields_are_preserved(tmp_path: Path) -> None:
    benchmark = _benchmark(tmp_path)
    observation = _observation(
        tmp_path / "qwen.json",
        model="qwen3vl4b",
        scope="complete 10-image replay; excludes service startup",
        active_seconds=360.0,
    )

    report = build_resource_cost_report(
        benchmark,
        {"route:qwen3vl4b-always": observation},
        "resource-v1",
        (10.0,),
    )

    row = report["rows"][0]
    assert row["energy_coverage"] == "complete"
    assert row["model_id"] == "org/qwen3vl4b"
    assert row["revision"] == "abc123"
    assert row["peak_memory_mib"] == 12000.0
    assert row["active_energy_wh"] == 20.0
    assert row["total_energy_wh"] == 25.0
    assert row["peak_power_watts"] == 300.0
    assert row["active_mean_power_watts"] == 200.0
    assert row["observed_active_gpu_cost_cny"] == 1.0
    assert row["observed_active_gpu_cost_cny_per_task"] == 0.1
    assert row["direct_cost_cny_total"] is None
    assert row["agent_latency_seconds_mean"] == 2.5
    assert row["schema_valid_rate"] == 0.9
    assert row["agent_call_rate"] == 1.0
    assert (tmp_path / "benchmarks" / "resource-v1" / "resource-costs.json").is_file()
    assert (tmp_path / "benchmarks" / "resource-v1" / "resource-costs.csv").is_file()


def test_incomplete_capture_is_forced_to_explicit_partial(tmp_path: Path) -> None:
    benchmark = _benchmark(tmp_path)
    observation = _observation(
        tmp_path / "smol.json",
        model="smolvlm2",
        scope="diagnostics only; excludes the original complete 10-image replay",
        active_seconds=180.0,
        energy_coverage="complete",
    )

    report = build_resource_cost_report(
        benchmark,
        {"route:smolvlm2-always": observation},
        "resource-v1",
        (2.0,),
    )

    assert report["rows"][0]["energy_coverage"] == "explicit_partial"
    assert report["rows"][0]["observed_active_gpu_cost_cny"] == 0.1


def test_unknown_measurement_and_scope_remain_null_or_unknown(tmp_path: Path) -> None:
    benchmark = _benchmark(tmp_path)
    observation = _observation(
        tmp_path / "qwen.json",
        model="qwen3vl4b",
        scope=None,
        active_seconds=None,
    )

    report = build_resource_cost_report(
        benchmark,
        {"route:qwen3vl4b-always": observation},
        "resource-v1",
        (1.0, 2.0),
    )

    assert len(report["rows"]) == 2
    assert all(row["energy_coverage"] == "unknown" for row in report["rows"])
    assert all(row["active_seconds"] is None for row in report["rows"])
    assert all(row["observed_active_gpu_cost_cny"] is None for row in report["rows"])
    assert all(row["observed_active_gpu_cost_cny_per_task"] is None for row in report["rows"])


def test_unknown_arm_is_rejected_before_output(tmp_path: Path) -> None:
    benchmark = _benchmark(tmp_path)
    observation = _observation(
        tmp_path / "qwen.json",
        model="qwen3vl4b",
        scope="complete replay",
        active_seconds=1.0,
    )

    with pytest.raises(ValueError, match="arm does not exist"):
        build_resource_cost_report(
            benchmark,
            {"route:missing": observation},
            "resource-v1",
        )
    assert not (tmp_path / "benchmarks" / "resource-v1").exists()


def test_existing_report_id_is_never_overwritten(tmp_path: Path) -> None:
    benchmark = _benchmark(tmp_path)
    observation = _observation(
        tmp_path / "qwen.json",
        model="qwen3vl4b",
        scope="complete replay",
        active_seconds=1.0,
    )
    mapping = {"route:qwen3vl4b-always": observation}
    build_resource_cost_report(benchmark, mapping, "resource-v1", (1.0,))

    with pytest.raises(FileExistsError, match="report ID already exists"):
        build_resource_cost_report(benchmark, mapping, "resource-v1", (1.0,))


def test_agent_run_ids_model_key_flat_fields_and_retry_scope(tmp_path: Path) -> None:
    benchmark = _benchmark(tmp_path)
    observation = _write_json(
        tmp_path / "mixed.json",
        {
            "observation_id": "mixed-v1-v2",
            "environment": {"gpu_model": "Flat GPU"},
            "observations": [
                {
                    "model_key": "unrelated-model",
                    "agent_run_ids": ["another-arm"],
                    "active_seconds_left_rectangle": 999.0,
                },
                {
                    "model_key": "smolvlm2-2p2b",
                    "model_id": "org/smol",
                    "revision": "revision-flat",
                    "agent_run_ids": [
                        "smol-v1",
                        "smol-v2",
                        "smolvlm2-always",
                    ],
                    "capture_scope": (
                        "complete v1 replay and v2 retries; excludes service startup"
                    ),
                    "active_seconds_left_rectangle": 360.0,
                    "energy_wh_active_left_rectangle": 21.0,
                    "energy_wh_total_trapezoidal": 30.0,
                    "power_watts_peak": 310.0,
                    "power_watts_active_mean_time_weighted": 210.0,
                    "memory_used_mib_peak": 22000.0,
                },
            ],
        },
    )

    report = build_resource_cost_report(
        benchmark,
        {"route:smolvlm2-always": observation},
        "resource-v1",
        (10.0,),
    )

    row = report["rows"][0]
    assert row["model_key"] == "smolvlm2-2p2b"
    assert row["revision"] == "revision-flat"
    assert row["active_seconds"] == 360.0
    assert row["active_energy_wh"] == 21.0
    assert row["total_energy_wh"] == 30.0
    assert row["peak_power_watts"] == 310.0
    assert row["active_mean_power_watts"] == 210.0
    assert row["peak_memory_mib"] == 22000.0


def test_retry_version_is_partial_but_complete_cached_run_is_complete(tmp_path: Path) -> None:
    benchmark = _benchmark(tmp_path)
    payload = {
        "observation_id": "mixed-v1-v2",
        "observations": [
            {
                "model_key": "smolvlm2-2p2b",
                "agent_run_ids": [
                    "smolvlm2-always",
                    "smolvlm2-always-v2",
                ],
                "capture_scope": "complete v1 replay and v2 retries",
                "active_seconds_left_rectangle": 10.0,
            }
        ],
    }
    observation = _write_json(tmp_path / "retry.json", payload)
    benchmark_payload = json.loads(benchmark.read_text(encoding="utf-8"))
    benchmark_payload["rows"].append(
        {
            "arm_id": "route:smolvlm2-always-v2",
            "arm_type": "agent_or_routing_policy",
            "complete": True,
            "required_task_count": 10,
        }
    )
    benchmark.write_text(json.dumps(benchmark_payload), encoding="utf-8")

    complete = build_resource_cost_report(
        benchmark,
        {"route:smolvlm2-always": observation},
        "resource-complete",
        (1.0,),
    )
    retry = build_resource_cost_report(
        benchmark,
        {"route:smolvlm2-always-v2": observation},
        "resource-retry",
        (1.0,),
    )

    assert complete["rows"][0]["energy_coverage"] == "complete"
    assert retry["rows"][0]["energy_coverage"] == "explicit_partial"


def test_model_grouped_remote_observation_is_normalized(tmp_path: Path) -> None:
    benchmark = _benchmark(tmp_path)
    observation = _write_json(
        tmp_path / "remote.json",
        {
            "schema_version": "1.0",
            "models": [
                {
                    "model_id": "org/qwen3vl4b",
                    "revision": "revision-grouped",
                    "served_model_name": "qwen3vl4b",
                    "gpu_index": 1,
                    "sampler_window_scope": "service start plus complete benchmark experiment",
                    "agent_runs": [
                        {
                            "agent_run_id": "qwen3vl4b-always",
                            "task_set_complete": True,
                        }
                    ],
                    "resource_observation": {
                        "active_seconds": 360.0,
                        "memory_peak_mib": 19711.0,
                        "power_active_mean_watts": 250.0,
                        "power_peak_watts": 349.0,
                        "energy_active_wh": 25.0,
                        "energy_total_window_wh": 31.0,
                    },
                }
            ],
        },
    )

    report = build_resource_cost_report(
        benchmark,
        {"route:qwen3vl4b-always": observation},
        "resource-grouped",
        (10.0,),
    )

    row = report["rows"][0]
    assert row["served_model_name"] == "qwen3vl4b"
    assert row["revision"] == "revision-grouped"
    assert row["gpu_index"] == 1
    assert row["active_seconds"] == 360.0
    assert row["peak_memory_mib"] == 19711.0
    assert row["active_mean_power_watts"] == 250.0
    assert row["peak_power_watts"] == 349.0
    assert row["active_energy_wh"] == 25.0
    assert row["total_energy_wh"] == 31.0
    assert row["energy_coverage"] == "unknown"
    assert row["observed_active_gpu_cost_cny"] == 1.0
