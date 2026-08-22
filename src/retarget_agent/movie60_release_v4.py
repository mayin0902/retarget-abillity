"""Build and validate Movie60 review v4 from the immutable v3 release plus one Agent replay."""

from __future__ import annotations

import csv
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .agent_human_gate import evaluate_agent_human_gate
from .movie60_release import (
    METHODS,
    _human_review_snapshot,
    validate_movie60_review_v3,
)
from .review_localization import localize_reason_codes
from .strategy import load_strategy_bundle

RELEASE_ID = "movie60-review-v4"
AGENT_EVIDENCE_ID = "chinese-agent-v8"
CURRENT_STRATEGY = "retarget@1.0.0"
CURRENT_STRATEGY_SHA256 = (
    "78a62685c08a110d4e8d3e001498eba2d6f427524ec4c484df3ef96373d37f82"
)
AGENT_GRADE = {
    "proxy_a": "A",
    "proxy_b": "B",
    "proxy_c": "C",
    "proxy_d": "D",
}


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return payload


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    if not rows:
        raise ValueError(f"refusing to write empty CSV: {path}")
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _method(candidate_id: str) -> str:
    parts = candidate_id.split("--")
    if len(parts) != 3 or parts[1] not in METHODS:
        raise ValueError(f"invalid Movie60 candidate ID: {candidate_id}")
    return parts[1]


def _copy_current_runtime(repository: Path, root: Path) -> None:
    package = root / "_runtime" / "src" / "retarget_agent"
    for name in ("__init__.py", "models.py", "movie60_review_app.py"):
        shutil.copy2(repository / "src" / "retarget_agent" / name, package / name)
    target_web = package / "web_movie60"
    if target_web.exists():
        shutil.rmtree(target_web)
    shutil.copytree(repository / "src" / "retarget_agent" / "web_movie60", target_web)
    documentation = root / "documentation"
    documentation.mkdir(exist_ok=True)
    for name in (
        "QUICKSTART.md",
        "REVIEW_AND_SCORING.md",
        "ARCHITECTURE.md",
        "ADVANCED.md",
        "CODE_GUIDE.md",
    ):
        shutil.copy2(repository / "docs" / name, documentation / name)


def materialize_movie60_review_v4(
    base_v3: Path,
    agent_run: Path,
    repository: Path,
    skill_dir: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Create a new non-overwriting v4 workspace and retain v3 evidence as history."""

    base_v3 = base_v3.resolve()
    agent_run = agent_run.resolve()
    repository = repository.resolve()
    skill_dir = skill_dir.resolve()
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise FileExistsError(output_dir)
    validate_movie60_review_v3(base_v3)
    manifest = _read_json(agent_run / "agent-run.json")
    task_ids = tuple(str(item) for item in manifest.get("task_ids") or [])
    if manifest.get("mode") != "always_on_agent" or len(task_ids) != 60:
        raise ValueError("v4 requires one complete 60-task always-on Agent replay")
    if len(set(task_ids)) != 60:
        raise ValueError("Agent replay task IDs are not unique")
    if manifest.get("model_version") != "qwen3vl-4b":
        raise ValueError("v4 Agent replay must use the frozen qwen3vl-4b deployment alias")
    if not all((agent_run / "decisions" / f"{task_id}.json").is_file() for task_id in task_ids):
        raise ValueError("Agent replay decisions are incomplete")
    if not (skill_dir / "agent-skill.yaml").is_file() or not (
        skill_dir / "agent-knowledge.yaml"
    ).is_file():
        raise FileNotFoundError("Chinese Agent v8 Skill/Knowledge is incomplete")

    source_reviews = base_v3 / "all60" / "candidate-review.csv"
    gate = evaluate_agent_human_gate(source_reviews, agent_run)

    with tempfile.TemporaryDirectory(prefix="movie60-v4-", dir=output_dir.parent) as temp:
        root = Path(temp) / RELEASE_ID
        shutil.copytree(base_v3, root)
        all60 = root / "all60"
        history = all60 / "history" / "movie60-review-v3"
        history.mkdir(parents=True)
        for name in ("candidate-review.csv", "summary.csv", "machine-summary.json"):
            source = all60 / name
            if source.is_file():
                shutil.copy2(source, history / name)

        decisions = {
            task_id: _read_json(agent_run / "decisions" / f"{task_id}.json")
            for task_id in task_ids
        }
        calls = {
            payload["agent_call_id"]: payload
            for payload in (
                _read_json(path) for path in sorted((agent_run / "calls").glob("*.json"))
            )
        }
        rows = _read_csv(source_reviews)
        for row in rows:
            decision = decisions[row["task_id"]]
            ranking = [str(item) for item in decision["candidate_ranking"]]
            candidate_id = row["candidate_id"]
            if set(ranking) != {
                item["candidate_id"] for item in rows if item["task_id"] == row["task_id"]
            }:
                raise ValueError(f"{row['task_id']}: Agent ranking does not cover seven candidates")
            call = calls.get(str(decision.get("agent_call_id")))
            parsed = call.get("parsed_output", {}) if call is not None else {}
            reason_codes = [str(value) for value in decision.get("reason_codes", [])]
            rank = ranking.index(candidate_id) + 1
            roles = []
            if candidate_id == decision["deterministic_candidate_id"]:
                roles.append("Rule Top1")
            if candidate_id == decision["selected_candidate_id"]:
                roles.append("中文Agent v8 Top1")
            if candidate_id == decision.get("agent_challenger_candidate_id"):
                roles.append("Agent Challenger")
            distortion = str(parsed.get("visible_distortion") or "未单独描述")
            selected = candidate_id == decision["selected_candidate_id"]
            agent_grade = AGENT_GRADE.get(str(decision.get("proxy_grade"))) if selected else None
            if rank == 1:
                reason = (
                    f"中文Agent v8总览建议第1名；建议等级：{agent_grade or '未判级'}；"
                    f"可见形变：{distortion}；"
                    f"选择置信度 {float(decision.get('selection_confidence') or 0):.2f}。"
                )
            else:
                reason = (
                    f"中文Agent v8七候选总览排名 {rank}/7；"
                    "本次Agent只对Top1给出任务级等级和形变描述。"
                )
            row.update(
                {
                    "agent_rank": str(rank),
                    "agent_role": " + ".join(roles) if roles else "普通候选",
                    "agent_review_scope": "中文Agent v8七候选高清总览",
                    "agent_grade": agent_grade or "",
                    "agent_directly_usable": (
                        str(agent_grade in {"A", "B"}).lower() if agent_grade else ""
                    ),
                    "agent_confidence": (
                        str(decision.get("selection_confidence") or "") if rank == 1 else ""
                    ),
                    "agent_reason": reason,
                    "agent_reason_codes": ";".join(reason_codes),
                    "agent_reason_codes_zh": ";".join(localize_reason_codes(reason_codes)),
                }
            )
        _write_csv(all60 / "candidate-review.csv", rows)

        summaries = _read_csv(all60 / "summary.csv")
        for row in summaries:
            decision = decisions[row["task_id"]]
            row["agent_method"] = _method(str(decision["selected_candidate_id"]))
            row["agent_grade"] = AGENT_GRADE.get(str(decision.get("proxy_grade")), "")
            row["agent_overrode_rule"] = str(bool(decision.get("changed_top1"))).lower()
            row["aigc_requested"] = str(
                decision.get("route_action") == "REQUEST_EXTERNAL_AIGC"
            ).lower()
        _write_csv(all60 / "summary.csv", summaries)

        evidence = root / "agent-evidence" / AGENT_EVIDENCE_ID
        shutil.copytree(agent_run, evidence / "agent-run")
        shutil.copytree(skill_dir, evidence / "skill")
        _write_json(evidence / "human-promotion-gate.json", gate.as_dict())
        current_strategy = root / "strategy" / "retarget-v1.0.0"
        shutil.copytree(repository / "strategies" / "retarget" / "v1", current_strategy)
        loaded_current = load_strategy_bundle(current_strategy / "bundle.yaml")
        if loaded_current.source_sha256 != CURRENT_STRATEGY_SHA256:
            raise ValueError("current retarget@1.0.0 strategy hash changed during v4 build")
        _copy_current_runtime(repository, root)

        version = _read_json(root / "VERSION.json")
        version.update(
            {
                "schema_version": "1.2",
                "release_id": RELEASE_ID,
                "previous_release_id": "movie60-review-v3",
                "software_release": "v0.8.0",
                "agent_evidence_id": AGENT_EVIDENCE_ID,
                "agent_run_id": manifest["agent_run_id"],
                "agent_model_version": manifest["model_version"],
                "agent_skill_sha256": manifest.get("skill_sha256"),
                "agent_task_count": 60,
                "agent_human_gate_passed": gate.passed,
                "current_strategy": CURRENT_STRATEGY,
                "current_strategy_sha256": CURRENT_STRATEGY_SHA256,
                "rule_evidence_strategy": "movie60@3.3.0",
            }
        )
        _write_json(root / "VERSION.json", version)
        _write_json(all60 / "machine-summary.json", version)
        (root / "README.md").write_text(
            "# Movie60 Review v4\n\n"
            "当前包包含60张原图、420张七方法候选、Rule证据、中文Agent v8完整回放、"
            "18个Task的已确认人工标签和可直接启动的Windows评审端。\n\n"
            "`strategy/retarget-v1.0.0/` 是当前可插拔策略；"
            "`strategy/movie60-v3.3.0/` 是生成本包 Rule 证据时的不可变历史快照。\n\n"
            "`all60/candidate-review.csv` 是当前表；`all60/history/movie60-review-v3/`"
            "保留上一版机器表。人工字段及其哈希在升级过程中保持不变。\n",
            encoding="utf-8",
        )
        shutil.move(str(root), output_dir)
    return validate_movie60_review_v4(output_dir)


def validate_movie60_review_v4(root: Path) -> dict[str, Any]:
    root = root.resolve()
    version = _read_json(root / "VERSION.json")
    expected = {
        "release_id": RELEASE_ID,
        "software_release": "v0.8.0",
        "task_count": 60,
        "candidate_count": 420,
        "agent_task_count": 60,
    }
    for key, value in expected.items():
        if version.get(key) != value:
            raise ValueError(f"VERSION.json {key} mismatch")
    all60 = root / "all60"
    summaries = _read_csv(all60 / "summary.csv")
    candidates = _read_csv(all60 / "candidate-review.csv")
    if len(summaries) != 60 or len({row["task_id"] for row in summaries}) != 60:
        raise ValueError("v4 must contain exactly 60 tasks")
    if len(candidates) != 420 or len(
        {(row["task_id"], row["method"]) for row in candidates}
    ) != 420:
        raise ValueError("v4 must contain exactly 420 unique candidates")
    if any(not row.get("agent_rank") for row in candidates):
        raise ValueError("v4 candidate table is missing Chinese Agent ranks")
    human = _human_review_snapshot(candidates)
    human_only = _human_review_snapshot(_read_csv(all60 / "human-review-current.csv"))
    if human != human_only or human["sha256"] != version.get("human_review_sha256"):
        raise ValueError("v4 changed the confirmed human review snapshot")
    agent_root = root / "agent-evidence" / AGENT_EVIDENCE_ID
    agent_manifest = _read_json(agent_root / "agent-run" / "agent-run.json")
    if len(agent_manifest.get("task_ids") or []) != 60:
        raise ValueError("v4 Agent evidence denominator is incomplete")
    gate = _read_json(agent_root / "human-promotion-gate.json")
    if not isinstance(version.get("agent_human_gate_passed"), bool):
        raise ValueError("v4 Agent human promotion gate status is missing")
    if gate.get("passed") is not version["agent_human_gate_passed"]:
        raise ValueError("v4 Agent human promotion gate status is inconsistent")
    if version.get("current_strategy") != CURRENT_STRATEGY or version.get(
        "current_strategy_sha256"
    ) != CURRENT_STRATEGY_SHA256:
        raise ValueError("v4 current Strategy metadata is missing or inconsistent")
    current_strategy = root / "strategy" / "retarget-v1.0.0" / "bundle.yaml"
    if load_strategy_bundle(current_strategy).source_sha256 != CURRENT_STRATEGY_SHA256:
        raise ValueError("v4 current Strategy snapshot is invalid")
    for row in summaries:
        task = all60 / "tasks" / row["task_id"]
        if {path.stem for path in (task / "candidates").glob("*.png")} != set(METHODS):
            raise ValueError(f"{row['task_id']}: seven-candidate set is incomplete")
    required = (
        "START_HERE.html",
        "INSTALL_WINDOWS.bat",
        "START_REVIEW.bat",
        "_runtime/run_review_ui.py",
        "_runtime/src/retarget_agent/movie60_review_app.py",
        "documentation/CODE_GUIDE.md",
    )
    if not all((root / item).is_file() for item in required):
        raise ValueError("v4 is missing Windows runtime or handoff documentation")
    return {
        "status": "valid",
        "release_id": RELEASE_ID,
        "task_count": 60,
        "candidate_count": 420,
        "agent_task_count": 60,
        "human_reviewed_task_count": human["task_count"],
        "human_reviewed_candidate_count": human["candidate_count"],
        "human_review_sha256": human["sha256"],
    }


__all__ = [
    "materialize_movie60_review_v4",
    "validate_movie60_review_v4",
]
