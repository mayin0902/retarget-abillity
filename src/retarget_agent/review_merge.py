"""Fail-closed merge for disjoint completed Rule-anchored review shards."""

from __future__ import annotations

import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

from .hashing import sha256_file
from .models import validate_id
from .rule_anchored_review import RuleAnchoredTaskDecision

_IDENTITY_FIELDS = (
    "source_run_id",
    "evaluation_id",
    "phase",
    "policy_sha256",
    "strategy_id",
    "strategy_version",
    "strategy_sha256",
    "max_agent_challengers",
)
_SUM_FIELDS = (
    "candidate_review_count",
    "pair_call_count",
    "rule_forced_review_count",
    "agent_proposal_review_count",
    "agent_override_count",
    "selected_ab_count",
    "aigc_request_count",
    "within_soft_target_120s_count",
)
_ARTIFACT_DIRS = (
    "candidate-reviews",
    "candidate-sheets",
    "decisions",
    "pair-reviews",
    "pair-sheets",
)
_OVERVIEW_IDENTITY_FIELDS = (
    "source_run_id",
    "evaluation_id",
    "mode",
    "agent_id",
    "agent_version",
    "model_version",
    "prompt_version",
    "skill_sha256",
    "comparison_input",
    "strategy_id",
    "strategy_version",
    "strategy_sha256",
)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _tree_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): sha256_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _percentile_95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, (95 * len(ordered) + 99) // 100 - 1)
    return ordered[index]


def merge_agent_overview_shards(
    run_dir: Path,
    *,
    source_agent_run_ids: tuple[str, ...],
    agent_run_id: str,
) -> dict[str, Any]:
    """Merge disjoint successful overview shards without another model call."""

    validate_id(agent_run_id)
    if len(source_agent_run_ids) < 2 or len(source_agent_run_ids) != len(
        set(source_agent_run_ids)
    ):
        raise ValueError("merge requires at least two unique source Agent run IDs")
    run_dir = run_dir.resolve()
    output = run_dir / "agent-runs" / agent_run_id
    if output.exists():
        raise FileExistsError(output)
    sources = [run_dir / "agent-runs" / value for value in source_agent_run_ids]
    manifests = [_read_json(source / "agent-run.json") for source in sources]
    summaries = [_read_json(source / "summary.json") for source in sources]
    first = manifests[0]
    for manifest in manifests[1:]:
        for field in _OVERVIEW_IDENTITY_FIELDS:
            if manifest.get(field) != first.get(field):
                raise ValueError(f"Agent overview shard identity mismatch: {field}")

    task_ids: set[str] = set()
    source_hashes: dict[str, dict[str, str]] = {}
    strategy_hashes: dict[str, str] | None = None
    latencies: list[float] = []
    for source, source_id, manifest, summary in zip(
        sources, source_agent_run_ids, manifests, summaries, strict=True
    ):
        declared = tuple(str(value) for value in manifest["task_ids"])
        decision_paths = sorted((source / "decisions").glob("*.json"))
        decisions = [_read_json(path) for path in decision_paths]
        actual = {str(payload["task_id"]) for payload in decisions}
        if len(actual) != len(decisions) or actual != set(declared):
            raise ValueError(f"{source_id}: overview decision task set mismatch")
        if int(summary.get("task_count", -1)) != len(declared):
            raise ValueError(f"{source_id}: overview summary denominator mismatch")
        duplicate = task_ids & actual
        if duplicate:
            raise ValueError(f"duplicate task across overview shards: {sorted(duplicate)[0]}")
        task_ids.update(actual)
        current_strategy_hashes = _tree_hashes(source / "strategy")
        if strategy_hashes is None:
            strategy_hashes = current_strategy_hashes
        elif current_strategy_hashes != strategy_hashes:
            raise ValueError("overview shards contain different strategy snapshots")
        source_hashes[source_id] = _tree_hashes(source)
        for path in sorted((source / "calls").glob("*.json")):
            payload = _read_json(path)
            if payload.get("latency_seconds") is not None:
                latencies.append(float(payload["latency_seconds"]))

    output.mkdir(parents=True)
    shutil.copytree(sources[0] / "strategy", output / "strategy")
    for source in sources:
        for dirname in ("calls", "decisions"):
            for path in sorted((source / dirname).glob("*.json")):
                target = output / dirname / path.name
                if target.exists():
                    raise ValueError(f"Agent overview artifact collision: {target.name}")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
                if sha256_file(target) != sha256_file(path):
                    raise OSError(f"Agent overview copy verification failed: {path}")

    task_count = len(task_ids)
    weighted_fields = (
        "agent_cache_hit_rate",
        "agent_call_rate",
        "beneficial_change_rate",
        "external_aigc_fallback_rate",
        "harmful_change_rate",
        "proxy_delta_vs_deterministic_mean",
        "proxy_routing_regret_mean",
        "schema_valid_rate",
        "selected_proxy_a_rate",
        "selected_proxy_success_rate",
        "selected_quality_score_mean",
        "top1_change_rate",
    )
    merged_summary: dict[str, Any] = {
        "schema_version": "1.1",
        "task_count": task_count,
        "agent_call_count": sum(int(summary["agent_call_count"]) for summary in summaries),
    }
    for field in weighted_fields:
        values = [summary.get(field) for summary in summaries]
        merged_summary[field] = (
            None
            if any(value is None for value in values)
            else sum(
                float(value) * int(summary["task_count"])
                for value, summary in zip(values, summaries, strict=True)
            )
            / task_count
        )
    merged_summary["proxy_routing_regret_max"] = max(
        float(summary["proxy_routing_regret_max"]) for summary in summaries
    )
    merged_summary["agent_latency_seconds_mean"] = (
        sum(latencies) / len(latencies) if latencies else None
    )
    merged_summary["agent_latency_seconds_p95"] = _percentile_95(latencies)
    merged_summary["agent_estimated_cost_cny_total"] = (
        None
        if any(summary.get("agent_estimated_cost_cny_total") is None for summary in summaries)
        else sum(float(summary["agent_estimated_cost_cny_total"]) for summary in summaries)
    )
    calibration_values = {summary.get("calibration_status") for summary in summaries}
    if len(calibration_values) != 1:
        raise ValueError("Agent overview shard summary mismatch: calibration_status")
    merged_summary["calibration_status"] = calibration_values.pop()
    merged_summary["notes"] = sorted(
        {str(note) for summary in summaries for note in summary.get("notes", [])}
    )

    source_identity = json.dumps(source_hashes, sort_keys=True, separators=(",", ":"))
    manifest = {
        **first,
        "agent_run_id": agent_run_id,
        "config_hash": hashlib.sha256(source_identity.encode("utf-8")).hexdigest(),
        "strategy_snapshot": f"agent-runs/{agent_run_id}/strategy",
        "task_ids": sorted(task_ids),
    }
    (output / "agent-run.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / "summary.json").write_text(
        json.dumps(merged_summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / "merge-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "agent_run_id": agent_run_id,
                "source_agent_run_ids": list(source_agent_run_ids),
                "source_artifact_sha256": source_hashes,
                "strategy_snapshot_sha256": strategy_hashes,
                "task_ids": sorted(task_ids),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return manifest


def merge_rule_anchored_review_shards(
    run_dir: Path,
    *,
    source_review_run_ids: tuple[str, ...],
    overview_agent_run_id: str,
    review_run_id: str,
    calibration_review_run_id: str | None = None,
) -> dict[str, Any]:
    """Merge disjoint complete shards while preserving every source artifact."""

    validate_id(review_run_id)
    validate_id(overview_agent_run_id)
    if calibration_review_run_id is not None:
        validate_id(calibration_review_run_id)
    if len(source_review_run_ids) < 2 or len(source_review_run_ids) != len(
        set(source_review_run_ids)
    ):
        raise ValueError("merge requires at least two unique source review IDs")
    run_dir = run_dir.resolve()
    output = run_dir / "strict-reviews" / review_run_id
    if output.exists():
        raise FileExistsError(output)

    sources = [run_dir / "strict-reviews" / value for value in source_review_run_ids]
    summaries = [_read_json(source / "summary.json") for source in sources]
    if any(summary.get("complete") is not True for summary in summaries):
        raise ValueError("every source review shard must be complete")
    first = summaries[0]
    for summary in summaries[1:]:
        for field in _IDENTITY_FIELDS:
            if summary.get(field) != first.get(field):
                raise ValueError(f"review shard identity mismatch: {field}")
    if calibration_review_run_id is not None:
        calibration_summary = _read_json(
            run_dir
            / "strict-reviews"
            / calibration_review_run_id
            / "summary.json"
        )
        if calibration_summary.get("complete") is not True:
            raise ValueError("calibration review must be complete")
        if calibration_summary.get("strategy_sha256") != first.get("strategy_sha256"):
            raise ValueError("calibration review strategy differs from review shards")

    expected_tasks = tuple(
        _read_json(
            run_dir
            / "agent-runs"
            / overview_agent_run_id
            / "agent-run.json"
        )["task_ids"]
    )
    decisions: dict[str, RuleAnchoredTaskDecision] = {}
    source_hashes: dict[str, dict[str, str]] = {}
    strategy_hashes: dict[str, str] | None = None
    for source, source_id, source_summary in zip(
        sources, source_review_run_ids, summaries, strict=True
    ):
        current_strategy_hashes = _tree_hashes(source / "strategy")
        if strategy_hashes is None:
            strategy_hashes = current_strategy_hashes
        elif current_strategy_hashes != strategy_hashes:
            raise ValueError("review shards contain different strategy snapshots")
        source_hashes[source_id] = _tree_hashes(source)
        decision_paths = sorted((source / "decisions").glob("*.json"))
        if len(decision_paths) != int(source_summary["task_count"]):
            raise ValueError(f"{source_id}: decision denominator mismatch")
        shard_overview_id = str(source_summary["overview_agent_run_id"])
        shard_overview_tasks = tuple(
            _read_json(
                run_dir / "agent-runs" / shard_overview_id / "agent-run.json"
            )["task_ids"]
        )
        shard_decision_tasks = tuple(path.stem for path in decision_paths)
        if set(shard_decision_tasks) != set(shard_overview_tasks):
            raise ValueError(f"{source_id}: shard overview task set mismatch")
        for path in decision_paths:
            decision = RuleAnchoredTaskDecision.model_validate(_read_json(path))
            if decision.task_id in decisions:
                raise ValueError(f"duplicate task across review shards: {decision.task_id}")
            decisions[decision.task_id] = decision
    if set(decisions) != set(expected_tasks):
        raise ValueError("merged review task set differs from overview task set")

    output.mkdir(parents=True)
    shutil.copytree(sources[0] / "strategy", output / "strategy")
    for source in sources:
        for dirname in _ARTIFACT_DIRS:
            source_dir = source / dirname
            if not source_dir.is_dir():
                raise ValueError(f"review shard is missing {dirname}: {source.name}")
            for path in sorted(source_dir.rglob("*")):
                if not path.is_file():
                    continue
                target = output / dirname / path.relative_to(source_dir)
                if target.exists():
                    raise ValueError(f"review artifact collision: {target.relative_to(output)}")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
                if sha256_file(target) != sha256_file(path):
                    raise OSError(f"review artifact copy verification failed: {path}")

    grade_counts = Counter(decision.combined_grade.value for decision in decisions.values())
    block_counts = Counter(
        reason for decision in decisions.values() for reason in decision.override_block_reasons
    )
    task_count = len(decisions)
    summary = {
        **first,
        "schema_version": "1.3",
        "review_run_id": review_run_id,
        "overview_agent_run_id": overview_agent_run_id,
        "calibration_review_run_id": calibration_review_run_id
        if calibration_review_run_id is not None
        else first.get("calibration_review_run_id"),
        "strategy_snapshot": "strategy",
        "task_count": task_count,
        **{field: sum(int(item.get(field, 0)) for item in summaries) for field in _SUM_FIELDS},
        "selected_grade_counts": dict(grade_counts),
        "selected_ab_rate": sum(
            decision.selected_directly_usable for decision in decisions.values()
        )
        / task_count,
        "override_block_reason_counts": dict(block_counts),
        "complete": True,
        "merged_from_review_run_ids": list(source_review_run_ids),
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / "merge-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "review_run_id": review_run_id,
                "overview_agent_run_id": overview_agent_run_id,
                "calibration_review_run_id": calibration_review_run_id,
                "source_review_run_ids": list(source_review_run_ids),
                "source_overview_agent_run_ids": [
                    str(summary["overview_agent_run_id"]) for summary in summaries
                ],
                "source_artifact_sha256": source_hashes,
                "strategy_snapshot_sha256": strategy_hashes,
                "task_ids": list(expected_tasks),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return summary


__all__ = ["merge_agent_overview_shards", "merge_rule_anchored_review_shards"]
