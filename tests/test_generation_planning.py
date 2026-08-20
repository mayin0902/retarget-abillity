from __future__ import annotations

from pathlib import Path

from retarget_agent.generation_planning import plan_external_generation
from retarget_agent.storage import LocalArtifactStore


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    run_dir = tmp_path / "run"
    store = LocalArtifactStore(run_dir)
    task_ids = ("source-1__square", "source-2__square")
    candidate_ids = [f"{task}--crop--v1" for task in task_ids]
    store.write_json(
        "run.json",
        {
            "run_id": "run-1",
            "dataset_id": "dataset-1",
            "dataset_fingerprint": "a" * 64,
            "status": "COMPLETED",
            "methods": ["crop"],
            "config_hash": "b" * 64,
            "config_snapshot": "config/run.yaml",
            "code_version": "test",
            "python_version": "3.13",
            "dependency_versions": {},
            "task_ids": task_ids,
            "candidate_ids": candidate_ids,
        },
    )
    for index, candidate_id in enumerate(candidate_ids):
        store.write_json(
            f"evaluations/eval-1/metrics/{candidate_id}.json",
            {
                "candidate_id": candidate_id,
                "metrics": {"quality_score": 60 + index},
            },
        )
    for run_id, model in (
        ("conditional-a", "model-a"),
        ("always-a", "model-a"),
        ("agent-b", "model-b"),
    ):
        store.write_json(
            f"agent-runs/{run_id}/agent-run.json",
            {"model_version": model},
        )
        for task_id in task_ids:
            store.write_json(
                f"agent-runs/{run_id}/decisions/{task_id}.json",
                {"task_id": task_id, "route_action": "CALL_EXTERNAL_AIGC"},
            )
    audit = tmp_path / "audit.csv"
    audit.write_text(
        "source_id,source_kind,license_status,api_egress_allowed\n"
        "source-1,public_real,audited,true\n"
        "source-2,public_real,audited,false\n",
        encoding="utf-8",
    )
    return run_dir, audit


def test_generation_plan_deduplicates_model_votes_and_enforces_egress(tmp_path: Path) -> None:
    run_dir, audit = _fixture(tmp_path)
    plan = plan_external_generation(
        run_dir,
        "eval-1",
        "plan-1",
        ("conditional-a", "always-a", "agent-b"),
        audit,
        maximum_paid_calls=12,
    )

    assert plan["requested_task_count"] == 2
    assert plan["eligible_task_count"] == 1
    assert plan["selected_paid_call_count"] == 1
    assert plan["estimated_cost_max_cny"] == 0.60
    by_task = {item["task_id"]: item for item in plan["entries"]}
    assert by_task["source-1__square"]["model_vote_count"] == 2
    assert by_task["source-1__square"]["selected_for_generation"] is True
    assert by_task["source-2__square"]["selected_for_generation"] is False
    assert "api_egress_not_approved" in by_task["source-2__square"]["reason_codes"]
