from __future__ import annotations

import json
from pathlib import Path

import pytest

from retarget_agent.combination_replay import replay_combination_policy
from retarget_agent.rule_anchored_review import PairPreference, RuleAnchoredTaskDecision
from retarget_agent.strategy import load_strategy_bundle
from retarget_agent.strict_review import MachineGrade

ROOT = Path(__file__).resolve().parents[1]


def _write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _fixture_run(tmp_path: Path) -> Path:
    run = tmp_path / "run"
    source_id = "source-review"
    decision = RuleAnchoredTaskDecision(
        task_id="task-1",
        phase="development",
        rule_complete_ranking=("rule", "agent"),
        rule_top1_candidate_id="rule",
        agent_proposed_candidate_id="agent",
        agent_challenger_candidate_ids=("agent",),
        reviewed_candidate_ids=("rule", "agent"),
        pair_reviewed_candidate_ids=("agent",),
        rule_numeric_score=76.0,
        rule_numeric_grade="B",
        rule_grade=MachineGrade.B,
        agent_grade=MachineGrade.C,
        pair_preference=PairPreference.AGENT,
        pair_clear_visual_evidence=True,
        pair_evidence_consistent=True,
        agent_core_content_preserved=True,
        selected_candidate_id="agent",
        selected_grade=MachineGrade.C,
        combined_grade=MachineGrade.C,
        selected_directly_usable=False,
        agent_overrode_rule=True,
        override_block_reasons=(),
        decision_reason_codes=("clear_visual_override",),
        request_external_aigc=True,
        task_review_wall_seconds=10.0,
        within_soft_target_120s=True,
    )
    _write(
        run / "strict-reviews" / source_id / "summary.json",
        {
            "complete": True,
            "task_count": 1,
            "strategy_id": "movie60",
            "strategy_version": "3.2.0",
            "overview_agent_run_id": "overview-source",
            "candidate_review_count": 3,
            "pair_call_count": 2,
            "agent_override_count": 1,
        },
    )
    _write(
        run / "strict-reviews" / source_id / "decisions" / "task-1.json",
        decision.model_dump(mode="json"),
    )
    source_overview = {
        "strategy_sha256": "source-strategy",
        "config_hash": "same-config",
        "task_ids": ["task-1"],
    }
    _write(run / "agent-runs/overview-source/agent-run.json", source_overview)
    target_strategy = load_strategy_bundle(ROOT / "strategies/movie60/v3_2_1/bundle.yaml")
    _write(
        run / "agent-runs/overview-target/agent-run.json",
        {
            **source_overview,
            "strategy_sha256": target_strategy.source_sha256,
        },
    )
    _write(
        run / "agent-runs/overview-target/summary.json",
        {"task_count": 1, "agent_cache_hit_rate": 1.0},
    )
    _write(run / "evaluations" / "eval-new" / "summary.json", {"complete": True})
    _write(run / "evaluations" / "eval-new" / "evaluation.json", {"candidate_ids": ["agent"]})
    _write(
        run / "evaluations" / "eval-new" / "metrics" / "agent.json",
        {"metrics": {"proxy_grade": "proxy_b"}},
    )
    return run


def test_replay_reuses_selection_and_changes_only_combined_grade(tmp_path: Path) -> None:
    run = _fixture_run(tmp_path)
    strategy = load_strategy_bundle(ROOT / "strategies/movie60/v3_2_1/bundle.yaml")

    summary = replay_combination_policy(
        run,
        source_review_run_id="source-review",
        overview_agent_run_id="overview-target",
        evaluation_id="eval-new",
        review_run_id="replayed",
        strategy=strategy,
    )
    output = json.loads(
        (run / "strict-reviews/replayed/decisions/task-1.json").read_text(encoding="utf-8")
    )

    assert output["selected_candidate_id"] == "agent"
    assert output["selected_grade"] == "C"
    assert output["combined_grade"] == "B"
    assert output["combined_grade_source"] == "rule_metric"
    assert output["selected_directly_usable"] is True
    assert output["request_external_aigc"] is False
    assert summary["visual_model_call_count"] == 0
    assert summary["complete"] is True


def test_replay_refuses_overwrite(tmp_path: Path) -> None:
    run = _fixture_run(tmp_path)
    strategy = load_strategy_bundle(ROOT / "strategies/movie60/v3_2_1/bundle.yaml")
    arguments = {
        "source_review_run_id": "source-review",
        "overview_agent_run_id": "overview-target",
        "evaluation_id": "eval-new",
        "review_run_id": "replayed",
        "strategy": strategy,
    }
    replay_combination_policy(run, **arguments)
    with pytest.raises(FileExistsError):
        replay_combination_policy(run, **arguments)


def test_advisory_replay_retains_rule_top1_and_agent_evidence(tmp_path: Path) -> None:
    run = _fixture_run(tmp_path)
    source_summary = run / "strict-reviews/source-review/summary.json"
    payload = json.loads(source_summary.read_text(encoding="utf-8"))
    payload["strategy_version"] = "3.2.1"
    _write(source_summary, payload)

    strategy = load_strategy_bundle(ROOT / "strategies/movie60/v3_2_2/bundle.yaml")
    target_manifest = run / "agent-runs/overview-target/agent-run.json"
    payload = json.loads(target_manifest.read_text(encoding="utf-8"))
    payload["strategy_sha256"] = strategy.source_sha256
    _write(target_manifest, payload)
    _write(
        run / "evaluations/eval-new/metrics/rule.json",
        {"metrics": {"proxy_grade": "proxy_a"}},
    )

    summary = replay_combination_policy(
        run,
        source_review_run_id="source-review",
        overview_agent_run_id="overview-target",
        evaluation_id="eval-new",
        review_run_id="advisory-replay",
        strategy=strategy,
    )
    output = json.loads(
        (run / "strict-reviews/advisory-replay/decisions/task-1.json").read_text(
            encoding="utf-8"
        )
    )

    assert output["agent_proposed_candidate_id"] == "agent"
    assert output["selected_candidate_id"] == "rule"
    assert output["selected_grade"] == "B"
    assert output["combined_grade"] == "A"
    assert output["agent_overrode_rule"] is False
    assert "agent_advisory_only" in output["override_block_reasons"]
    assert summary["agent_selection_mode"] == "advisory_only"
    assert summary["agent_override_count"] == 0
