from __future__ import annotations

import json
from pathlib import Path

import pytest

from retarget_agent.review_merge import (
    merge_agent_overview_shards,
    merge_rule_anchored_review_shards,
)
from retarget_agent.rule_anchored_review import (
    PairPreference,
    RuleAnchoredTaskDecision,
)
from retarget_agent.strict_review import MachineGrade


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _decision(task_id: str) -> RuleAnchoredTaskDecision:
    candidate = f"{task_id}--crop--v1"
    return RuleAnchoredTaskDecision(
        task_id=task_id,
        phase="development",
        rule_complete_ranking=(candidate,),
        rule_top1_candidate_id=candidate,
        agent_proposed_candidate_id=candidate,
        reviewed_candidate_ids=(candidate,),
        rule_grade=MachineGrade.A,
        agent_grade=MachineGrade.A,
        pair_preference=PairPreference.RULE,
        pair_clear_visual_evidence=True,
        pair_evidence_consistent=True,
        agent_core_content_preserved=True,
        selected_candidate_id=candidate,
        selected_grade=MachineGrade.A,
        combined_grade=MachineGrade.A,
        selected_directly_usable=True,
        agent_overrode_rule=False,
        override_block_reasons=("agent_advisory_only",),
        decision_reason_codes=("rule_retained",),
        request_external_aigc=False,
        task_review_wall_seconds=1.0,
        within_soft_target_120s=True,
    )


def _shard(run: Path, shard_id: str, overview_id: str, task_id: str) -> None:
    root = run / "strict-reviews" / shard_id
    for dirname in (
        "candidate-reviews",
        "candidate-sheets",
        "decisions",
        "pair-reviews",
        "pair-sheets",
    ):
        (root / dirname).mkdir(parents=True, exist_ok=True)
    (root / "strategy").mkdir()
    (root / "strategy" / "bundle.yaml").write_text("version: 3.3.0\n", encoding="utf-8")
    _write_json(
        root / "decisions" / f"{task_id}.json",
        _decision(task_id).model_dump(mode="json"),
    )
    _write_json(
        root / "summary.json",
        {
            "complete": True,
            "review_run_id": shard_id,
            "source_run_id": "run-v1",
            "evaluation_id": "eval-v1",
            "overview_agent_run_id": overview_id,
            "phase": "development",
            "policy_sha256": "policy",
            "strategy_id": "movie60",
            "strategy_version": "3.3.0",
            "strategy_sha256": "strategy",
            "max_agent_challengers": 2,
            "strategy_frozen_for_holdout": True,
            "policy_frozen_after_calibration": False,
            "task_count": 1,
            "candidate_review_count": 1,
            "pair_call_count": 0,
            "rule_forced_review_count": 1,
            "agent_proposal_review_count": 0,
            "agent_override_count": 0,
            "selected_ab_count": 1,
            "aigc_request_count": 0,
            "within_soft_target_120s_count": 1,
        },
    )


def _overview_shard(run: Path, run_id: str, task_id: str) -> None:
    root = run / "agent-runs" / run_id
    (root / "calls").mkdir(parents=True)
    (root / "decisions").mkdir()
    (root / "strategy").mkdir()
    (root / "strategy" / "bundle.yaml").write_text("version: 3.3.0\n", encoding="utf-8")
    _write_json(
        root / "agent-run.json",
        {
            "agent_run_id": run_id,
            "source_run_id": "run-v1",
            "evaluation_id": "eval-v1",
            "mode": "always_on_agent",
            "agent_id": "vision-agent",
            "agent_version": "1.0.0",
            "model_version": "model-v1",
            "prompt_version": "prompt-v1",
            "skill_sha256": "skill",
            "comparison_input": "inputs-v1",
            "strategy_id": "movie60",
            "strategy_version": "3.3.0",
            "strategy_sha256": "strategy",
            "strategy_snapshot": f"agent-runs/{run_id}/strategy",
            "config_hash": run_id,
            "task_ids": [task_id],
        },
    )
    _write_json(root / "decisions" / f"{task_id}.json", {"task_id": task_id})
    _write_json(
        root / "calls" / f"call-{task_id}.json",
        {"task_id": task_id, "latency_seconds": 2.0},
    )
    _write_json(
        root / "summary.json",
        {
            "schema_version": "1.0",
            "task_count": 1,
            "agent_call_count": 1,
            "agent_cache_hit_rate": 0.0,
            "agent_call_rate": 1.0,
            "agent_estimated_cost_cny_total": None,
            "agent_latency_seconds_mean": 2.0,
            "agent_latency_seconds_p95": 2.0,
            "beneficial_change_rate": None,
            "calibration_status": "uncalibrated",
            "external_aigc_fallback_rate": 0.0,
            "harmful_change_rate": None,
            "notes": ["fixture"],
            "proxy_delta_vs_deterministic_mean": 0.0,
            "proxy_routing_regret_max": 1.0,
            "proxy_routing_regret_mean": 0.5,
            "schema_valid_rate": 1.0,
            "selected_proxy_a_rate": 1.0,
            "selected_proxy_success_rate": 1.0,
            "selected_quality_score_mean": 90.0,
            "top1_change_rate": 0.0,
        },
    )


def test_merge_disjoint_agent_overviews(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _overview_shard(run, "overview-a", "task-a")
    _overview_shard(run, "overview-b", "task-b")

    result = merge_agent_overview_shards(
        run,
        source_agent_run_ids=("overview-a", "overview-b"),
        agent_run_id="overview-merged",
    )

    assert result["task_ids"] == ["task-a", "task-b"]
    summary = json.loads(
        (run / "agent-runs" / "overview-merged" / "summary.json").read_text()
    )
    assert summary["task_count"] == 2
    assert summary["schema_valid_rate"] == 1.0
    assert summary["agent_latency_seconds_p95"] == 2.0


def test_merge_agent_overviews_refuses_duplicate_tasks(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _overview_shard(run, "overview-a", "task-a")
    _overview_shard(run, "overview-b", "task-a")

    with pytest.raises(ValueError, match="duplicate task"):
        merge_agent_overview_shards(
            run,
            source_agent_run_ids=("overview-a", "overview-b"),
            agent_run_id="overview-merged",
        )


def test_merge_disjoint_review_shards(tmp_path: Path) -> None:
    run = tmp_path / "run"
    task_ids = ("task-a", "task-b")
    _write_json(
        run / "agent-runs" / "overview-full-v1" / "agent-run.json",
        {"task_ids": list(task_ids)},
    )
    _write_json(
        run / "agent-runs" / "overview-a-v1" / "agent-run.json",
        {"task_ids": [task_ids[0]]},
    )
    _write_json(
        run / "agent-runs" / "overview-b-v1" / "agent-run.json",
        {"task_ids": [task_ids[1]]},
    )
    _shard(run, "shard-a", "overview-a-v1", task_ids[0])
    _shard(run, "shard-b", "overview-b-v1", task_ids[1])

    result = merge_rule_anchored_review_shards(
        run,
        source_review_run_ids=("shard-a", "shard-b"),
        overview_agent_run_id="overview-full-v1",
        review_run_id="merged-v1",
    )

    assert result["complete"] is True
    assert result["task_count"] == 2
    assert result["selected_ab_count"] == 2
    assert result["selected_ab_rate"] == 1.0
    assert set((run / "strict-reviews" / "merged-v1" / "decisions").glob("*.json")) == {
        run / "strict-reviews" / "merged-v1" / "decisions" / "task-a.json",
        run / "strict-reviews" / "merged-v1" / "decisions" / "task-b.json",
    }


def test_merge_refuses_duplicate_tasks(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_json(
        run / "agent-runs" / "overview-v1" / "agent-run.json",
        {"task_ids": ["task-a"]},
    )
    _shard(run, "shard-a", "overview-v1", "task-a")
    _shard(run, "shard-b", "overview-v1", "task-a")

    with pytest.raises(ValueError, match="duplicate task"):
        merge_rule_anchored_review_shards(
            run,
            source_review_run_ids=("shard-a", "shard-b"),
            overview_agent_run_id="overview-v1",
            review_run_id="merged-v1",
        )
