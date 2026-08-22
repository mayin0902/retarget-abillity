from __future__ import annotations

import csv
import json
from pathlib import Path

from retarget_agent.agent_human_gate import evaluate_agent_human_gate


def _write_reviews(path: Path) -> None:
    rows = []
    for task, grades in {"poster_001": "AABCDDD", "poster_002": "ABBCCDD"}.items():
        for index, grade in enumerate(grades):
            rows.append(
                {
                    "task_id": task,
                    "candidate_id": f"{task}--method_{index}--hash",
                    "human_grade": grade,
                }
            )
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_run(path: Path, *, worse: bool = False) -> None:
    (path / "decisions").mkdir(parents=True)
    (path / "agent-run.json").write_text(
        json.dumps(
            {
                "mode": "always_on_agent",
                "task_ids": ["poster_001", "poster_002"],
            }
        ),
        encoding="utf-8",
    )
    selected = {"poster_001": 1, "poster_002": 0 if not worse else 6}
    for task in ("poster_001", "poster_002"):
        payload = {
            "deterministic_candidate_id": f"{task}--method_0--hash",
            "selected_candidate_id": f"{task}--method_{selected[task]}--hash",
            "agent_schema_valid": True,
        }
        (path / "decisions" / f"{task}.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )


def test_human_gate_accepts_non_regressing_agent(tmp_path: Path) -> None:
    reviews = tmp_path / "reviews.csv"
    run = tmp_path / "run"
    _write_reviews(reviews)
    _write_run(run)

    result = evaluate_agent_human_gate(reviews, run)

    assert result.passed
    assert result.reviewed_task_count == 2
    assert result.agent_human_best_hit_count == result.rule_human_best_hit_count == 2
    assert result.agent_serious_cd_count == 0
    assert result.agent_schema_valid_rate == 1.0
    assert result.reviewed_agent_schema_valid_rate == 1.0


def test_human_gate_rejects_more_serious_human_errors(tmp_path: Path) -> None:
    reviews = tmp_path / "reviews.csv"
    run = tmp_path / "run"
    _write_reviews(reviews)
    _write_run(run, worse=True)

    result = evaluate_agent_human_gate(reviews, run)

    assert not result.passed
    assert result.agent_serious_cd_count == 1
    assert result.agent_grade_decline_count == 1


def test_human_gate_rejects_safe_rule_fallback_disguised_as_agent_success(
    tmp_path: Path,
) -> None:
    reviews = tmp_path / "reviews.csv"
    run = tmp_path / "run"
    _write_reviews(reviews)
    _write_run(run)
    for decision_path in (run / "decisions").glob("*.json"):
        payload = json.loads(decision_path.read_text(encoding="utf-8"))
        payload["agent_schema_valid"] = False
        payload["selected_candidate_id"] = payload["deterministic_candidate_id"]
        decision_path.write_text(json.dumps(payload), encoding="utf-8")

    result = evaluate_agent_human_gate(reviews, run)

    assert not result.passed
    assert result.agent_human_best_hit_count == result.rule_human_best_hit_count
    assert result.agent_schema_valid_rate == 0.0
    assert "Agent完整任务结构化响应有效率低于95%" in result.reasons
