"""Proxy-label reporting for Rule-only, Agent-only, and combined routes."""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from .hashing import sha256_file
from .models import CandidateRecord

_GRADE_RANK = {"A": 3, "B": 2, "C": 1, "D": 0}


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _grade(value: object) -> str:
    grade = str(value).removeprefix("proxy_").upper()
    if grade not in _GRADE_RANK:
        raise ValueError(f"invalid grade: {value}")
    return grade


def summarize_route_rows(rows: list[dict[str, Any]], route: str) -> dict[str, Any]:
    predicted_key = f"{route}_predicted_grade"
    truth_key = f"{route}_truth_grade"
    method_key = f"{route}_method"
    exact = sum(row[predicted_key] == row[truth_key] for row in rows)
    pass_agreement = sum(
        (row[predicted_key] in {"A", "B"}) == (row[truth_key] in {"A", "B"})
        for row in rows
    )
    truth_cd = sum(row[truth_key] in {"C", "D"} for row in rows)
    predicted_cd = sum(row[predicted_key] in {"C", "D"} for row in rows)
    true_cd = sum(
        row[truth_key] in {"C", "D"} and row[predicted_key] in {"C", "D"}
        for row in rows
    )
    return {
        "task_count": len(rows),
        "exact_grade_accuracy": exact / len(rows),
        "ab_cd_agreement": pass_agreement / len(rows),
        "cd_recall": true_cd / truth_cd if truth_cd else None,
        "cd_precision": true_cd / predicted_cd if predicted_cd else None,
        "selected_candidate_proxy_ab_rate": sum(
            row[truth_key] in {"A", "B"} for row in rows
        )
        / len(rows),
        "proxy_best_method_hit_rate": sum(
            row[method_key] in row["proxy_best_methods"] for row in rows
        )
        / len(rows),
        "predicted_grade_counts": dict(Counter(row[predicted_key] for row in rows)),
        "selected_truth_grade_counts": dict(Counter(row[truth_key] for row in rows)),
    }


def _review_by_candidate(review_root: Path, task_id: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for path in sorted((review_root / "candidate-reviews" / task_id).glob("*.json")):
        payload = _read(path)
        result[str(payload["candidate_id"])] = payload
    return result


def _resolve_visual_evidence_review(
    run_dir: Path,
    review_run_id: str,
) -> tuple[str, Path]:
    """Follow immutable replay ancestry to the run that owns image-level reviews."""

    current = review_run_id
    visited: set[str] = set()
    while True:
        if current in visited:
            raise ValueError("strict-review replay ancestry contains a cycle")
        visited.add(current)
        root = run_dir / "strict-reviews" / current
        summary = _read(root / "summary.json")
        if summary.get("complete") is not True:
            raise ValueError("replayed strict review refers to incomplete visual evidence")
        parent = summary.get("source_review_run_id")
        if not parent:
            return current, root
        if summary.get("visual_evidence_reused") is not True:
            raise ValueError("replayed strict review does not declare visual evidence reuse")
        current = str(parent)


def build_agent_proxy_report(
    run_dir: Path,
    *,
    ratings_csv: Path,
    split_manifest: Path,
    partition: str,
    evaluation_id: str,
    overview_agent_run_id: str,
    review_run_id: str,
) -> dict[str, Any]:
    """Compare three route outputs against candidate-specific screened suggestions."""

    run_dir = run_dir.resolve()
    split = _read(split_manifest)
    if split["ratings_sha256"] != sha256_file(ratings_csv):
        raise ValueError("ratings file differs from frozen split evidence")
    selected_tasks = {
        item["task_id"]
        for item in split["records"]
        if item["partition"] == partition
    }
    review_root = run_dir / "strict-reviews" / review_run_id
    overview_root = run_dir / "agent-runs" / overview_agent_run_id
    review_summary = _read(review_root / "summary.json")
    overview_manifest = _read(overview_root / "agent-run.json")
    if not review_summary.get("complete"):
        raise ValueError("strict review is incomplete")
    if set(overview_manifest["task_ids"]) != selected_tasks:
        raise ValueError("overview task denominator differs from frozen partition")
    if review_summary["task_count"] != len(selected_tasks):
        raise ValueError("strict review task denominator differs from frozen partition")
    evidence_review_run_id, evidence_review_root = _resolve_visual_evidence_review(
        run_dir,
        review_run_id,
    )

    candidates: dict[str, CandidateRecord] = {}
    for path in sorted(run_dir.glob("candidates/*/*/candidate.json")):
        candidate = CandidateRecord.model_validate_json(path.read_text(encoding="utf-8"))
        candidates[candidate.candidate_id] = candidate
    truth_by_candidate: dict[str, str] = {}
    truth_by_task: dict[str, dict[str, str]] = {}
    with ratings_csv.open(encoding="utf-8-sig", newline="") as handle:
        for rating in csv.DictReader(handle):
            task_id = rating["task_id"]
            if task_id not in selected_tasks:
                continue
            matching = [
                candidate
                for candidate in candidates.values()
                if candidate.task_id == task_id and candidate.method_id == rating["method"]
            ]
            if len(matching) != 1 or matching[0].output is None:
                raise ValueError(f"cannot resolve candidate for {task_id}/{rating['method']}")
            candidate = matching[0]
            if candidate.output.sha256 != rating["image_sha256"]:
                raise ValueError(f"candidate hash mismatch for {candidate.candidate_id}")
            truth = _grade(rating["suggested_grade"])
            truth_by_candidate[candidate.candidate_id] = truth
            truth_by_task.setdefault(task_id, {})[candidate.method_id] = truth

    rows: list[dict[str, Any]] = []
    for task_id in overview_manifest["task_ids"]:
        overview = _read(overview_root / "decisions" / f"{task_id}.json")
        decision = _read(review_root / "decisions" / f"{task_id}.json")
        reviews = _review_by_candidate(evidence_review_root, task_id)
        rule_id = str(decision["rule_top1_candidate_id"])
        agent_id = str(decision["agent_proposed_candidate_id"])
        combined_id = str(decision["selected_candidate_id"])
        if agent_id not in reviews:
            raise ValueError(f"{task_id}: Agent proposal lacks strict review")
        agent_invocation = reviews[agent_id]["invocation"]
        best_rank = max(_GRADE_RANK[value] for value in truth_by_task[task_id].values())
        proxy_best = sorted(
            method
            for method, grade in truth_by_task[task_id].items()
            if _GRADE_RANK[grade] == best_rank
        )
        rule_metric = _read(
            run_dir / "evaluations" / evaluation_id / "metrics" / f"{rule_id}.json"
        )["metrics"]
        rows.append(
            {
                "task_id": task_id,
                "partition": partition,
                "proxy_best_methods": proxy_best,
                "rule_candidate_id": rule_id,
                "rule_method": candidates[rule_id].method_id,
                "rule_predicted_grade": _grade(rule_metric["proxy_grade"]),
                "rule_truth_grade": truth_by_candidate[rule_id],
                "rule_numeric_score": rule_metric["quality_score"],
                "agent_candidate_id": agent_id,
                "agent_method": candidates[agent_id].method_id,
                "agent_predicted_grade": _grade(
                    agent_invocation["review"]["overall_grade"]
                ),
                "agent_truth_grade": truth_by_candidate[agent_id],
                "agent_confidence": agent_invocation["review"]["confidence"],
                "agent_reason": agent_invocation["review"]["summary"],
                "combined_candidate_id": combined_id,
                "combined_method": candidates[combined_id].method_id,
                "combined_predicted_grade": _grade(decision["combined_grade"]),
                "combined_truth_grade": truth_by_candidate[combined_id],
                "agent_overrode_rule": decision["agent_overrode_rule"],
                "overview_schema_valid": overview["agent_schema_valid"],
                "override_block_reasons": decision["override_block_reasons"],
                "task_review_wall_seconds": decision["task_review_wall_seconds"],
            }
        )
    return {
        "schema_version": "1.0",
        "partition": partition,
        "label_provenance": "human_screened_large_model_proxy_not_human_ground_truth",
        "independent_human_validation": False,
        "evaluation_id": evaluation_id,
        "overview_agent_run_id": overview_agent_run_id,
        "review_run_id": review_run_id,
        "visual_evidence_review_run_id": evidence_review_run_id,
        "strategy_id": review_summary["strategy_id"],
        "strategy_version": review_summary["strategy_version"],
        "strategy_sha256": review_summary["strategy_sha256"],
        "rule_only": summarize_route_rows(rows, "rule"),
        "agent_only": summarize_route_rows(rows, "agent"),
        "combined": summarize_route_rows(rows, "combined"),
        "overview_schema_valid_rate": sum(row["overview_schema_valid"] is True for row in rows)
        / len(rows),
        "agent_override_count": sum(row["agent_overrode_rule"] for row in rows),
        "mean_strict_review_wall_seconds": sum(
            float(row["task_review_wall_seconds"]) for row in rows
        )
        / len(rows),
        "rows": rows,
    }


__all__ = ["build_agent_proxy_report", "summarize_route_rows"]
