"""Human-label promotion gate for an immutable Agent replay."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path

from .models import validate_id

GRADE_VALUE = {"A": 3, "B": 2, "C": 1, "D": 0}


@dataclass(frozen=True)
class HumanGateResult:
    total_task_count: int
    reviewed_task_count: int
    agent_schema_valid_count: int
    reviewed_agent_schema_valid_count: int
    rule_human_best_hit_count: int
    agent_human_best_hit_count: int
    rule_serious_cd_count: int
    agent_serious_cd_count: int
    agent_grade_improvement_count: int
    agent_grade_decline_count: int
    passed: bool
    reasons: tuple[str, ...]

    @property
    def agent_schema_valid_rate(self) -> float:
        return self.agent_schema_valid_count / self.total_task_count

    @property
    def reviewed_agent_schema_valid_rate(self) -> float:
        return self.reviewed_agent_schema_valid_count / self.reviewed_task_count

    def as_dict(self) -> dict[str, object]:
        denominator = self.reviewed_task_count
        return {
            **self.__dict__,
            "reasons": list(self.reasons),
            "rule_human_best_hit_rate": self.rule_human_best_hit_count / denominator,
            "agent_human_best_hit_rate": self.agent_human_best_hit_count / denominator,
            "rule_serious_cd_rate": self.rule_serious_cd_count / denominator,
            "agent_serious_cd_rate": self.agent_serious_cd_count / denominator,
            "agent_schema_valid_rate": self.agent_schema_valid_rate,
            "reviewed_agent_schema_valid_rate": self.reviewed_agent_schema_valid_rate,
        }


def _human_labels(path: Path) -> dict[str, dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    labels: dict[str, dict[str, str]] = {}
    for row in rows:
        grade = (row.get("human_grade") or "").strip().upper()
        if grade not in GRADE_VALUE:
            continue
        task_id = validate_id(row.get("task_id") or "")
        candidate_id = row.get("candidate_id") or ""
        if not candidate_id:
            raise ValueError("human review row is missing candidate_id")
        task_labels = labels.setdefault(task_id, {})
        if candidate_id in task_labels:
            raise ValueError(f"duplicate human label: {candidate_id}")
        task_labels[candidate_id] = grade
    complete = {task: values for task, values in labels.items() if len(values) == 7}
    if not complete:
        raise ValueError("no fully human-reviewed seven-candidate tasks")
    if len(complete) != len(labels):
        incomplete = sorted(set(labels) - set(complete))
        raise ValueError(f"partially reviewed tasks are not valid promotion evidence: {incomplete}")
    return complete


def evaluate_agent_human_gate(
    human_review_csv: Path,
    agent_run_dir: Path,
) -> HumanGateResult:
    """Compare Agent selection with its Rule anchor on complete human-labelled tasks."""

    labels = _human_labels(human_review_csv.resolve())
    agent_run_dir = agent_run_dir.resolve()
    manifest = json.loads((agent_run_dir / "agent-run.json").read_text(encoding="utf-8"))
    if manifest.get("mode") != "always_on_agent":
        raise ValueError("promotion evidence must use always_on_agent")
    task_ids = set(manifest.get("task_ids") or [])
    missing = sorted(set(labels) - task_ids)
    if missing:
        raise ValueError(f"Agent replay does not cover human-reviewed tasks: {missing}")

    decisions: dict[str, dict[str, object]] = {
        task_id: json.loads(
            (agent_run_dir / "decisions" / f"{task_id}.json").read_text(
                encoding="utf-8"
            )
        )
        for task_id in sorted(task_ids)
    }
    agent_schema_valid = sum(
        bool(decision.get("agent_schema_valid")) for decision in decisions.values()
    )
    reviewed_agent_schema_valid = 0
    rule_best = agent_best = 0
    rule_serious = agent_serious = 0
    improvements = declines = 0
    for task_id, grades in sorted(labels.items()):
        decision = decisions[task_id]
        reviewed_agent_schema_valid += bool(decision.get("agent_schema_valid"))
        rule_id = str(decision["deterministic_candidate_id"])
        agent_id = str(decision["selected_candidate_id"])
        if rule_id not in grades or agent_id not in grades:
            raise ValueError(f"{task_id}: selected candidate lacks a human label")
        best_value = max(GRADE_VALUE[value] for value in grades.values())
        rule_value = GRADE_VALUE[grades[rule_id]]
        agent_value = GRADE_VALUE[grades[agent_id]]
        rule_best += rule_value == best_value
        agent_best += agent_value == best_value
        rule_serious += rule_value <= GRADE_VALUE["C"]
        agent_serious += agent_value <= GRADE_VALUE["C"]
        improvements += agent_value > rule_value
        declines += agent_value < rule_value

    reasons = []
    if agent_best < rule_best:
        reasons.append("Agent命中人工最佳集合的任务数低于Rule")
    if agent_serious > rule_serious:
        reasons.append("Agent选择人工C/D的任务数高于Rule")
    if agent_schema_valid / len(task_ids) < 0.95:
        reasons.append("Agent完整任务结构化响应有效率低于95%")
    if reviewed_agent_schema_valid != len(labels):
        reasons.append("存在未经过有效Agent判断的人工金标任务")
    passed = not reasons
    return HumanGateResult(
        total_task_count=len(task_ids),
        reviewed_task_count=len(labels),
        agent_schema_valid_count=agent_schema_valid,
        reviewed_agent_schema_valid_count=reviewed_agent_schema_valid,
        rule_human_best_hit_count=rule_best,
        agent_human_best_hit_count=agent_best,
        rule_serious_cd_count=rule_serious,
        agent_serious_cd_count=agent_serious,
        agent_grade_improvement_count=improvements,
        agent_grade_decline_count=declines,
        passed=passed,
        reasons=tuple(reasons),
    )


__all__ = ["HumanGateResult", "evaluate_agent_human_gate"]
