"""Unified human-review workspace over Movie60, Generation Runs and imported cases.

The public seam is intentionally small: open one directory, list modes/tasks, save one
complete task, and resolve indexed media.  Source-specific layout knowledge stays behind
the adapters in this module and never leaks into the browser frontend.
"""

from __future__ import annotations

import csv
import hashlib
import json
import mimetypes
import re
import shutil
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import CandidateRecord, RunManifest, TaskSpec, validate_id
from .movie60_review_app import (
    Movie60Media,
    Movie60ReviewItem,
    Movie60ReviewSubmission,
    Movie60ReviewWorkspace,
)

HUMAN_GRADES = {"A", "B", "C", "D"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
SIDECAR_FIELDS = (
    "mode",
    "task_id",
    "candidate_id",
    "route",
    "method",
    "human_grade",
    "human_reason",
    "human_issue_codes",
    "human_confirmed",
    "reviewer_id",
    "updated_at",
    "event_id",
)


class ReviewItem(BaseModel):
    """One candidate judgement used by every review workspace adapter."""

    model_config = ConfigDict(extra="forbid")

    route: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    grade: str
    reason: str = Field(min_length=3, max_length=2000)
    issue_codes: tuple[str, ...] = Field(default=(), max_length=16)

    @field_validator("grade")
    @classmethod
    def valid_grade(cls, value: str) -> str:
        if value not in HUMAN_GRADES:
            raise ValueError("grade must be A, B, C or D")
        return value


class ReviewSubmission(BaseModel):
    """A complete review of every available route for one task."""

    model_config = ConfigDict(extra="forbid")

    mode: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    reviewer_id: str
    task_id: str
    reviews: tuple[ReviewItem, ...] = Field(min_length=1, max_length=32)

    _reviewer_id = field_validator("reviewer_id")(validate_id)
    _task_id = field_validator("task_id")(validate_id)


@dataclass(frozen=True)
class ReviewMedia:
    path: Path
    media_type: str
    sha256: str


class ReviewWorkspaceAdapter(Protocol):
    """The sole interface consumed by the unified HTTP review module."""

    def ready(self) -> dict[str, Any]: ...

    def workspace(self, mode: str) -> dict[str, Any]: ...

    def save(self, submission: ReviewSubmission) -> dict[str, Any]: ...

    def media(self, mode: str, task_id: str, media_id: str) -> ReviewMedia: ...


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON document must be an object: {path}")
    return payload


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv_atomic(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SIDECAR_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _grade(value: Any) -> str | None:
    raw = str(value or "").strip().upper()
    aliases = {"PROXY_A": "A", "PROXY_B": "B", "PROXY_C": "C", "PROXY_D": "D"}
    normalized = aliases.get(raw, raw)
    return normalized if normalized in HUMAN_GRADES else None


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return default if value is None else float(value)
    except (TypeError, ValueError):
        return default


def _percent(value: Any) -> str:
    if value is None:
        return "未观测"
    try:
        return f"{100 * float(value):.1f}%"
    except (TypeError, ValueError):
        return "未观测"


def _human_review(row: dict[str, str] | None) -> dict[str, Any] | None:
    if not row or row.get("human_confirmed") != "true":
        return None
    grade = _grade(row.get("human_grade"))
    if grade is None:
        return None
    return {
        "grade": grade,
        "reason": row.get("human_reason", ""),
        "issue_codes": [
            value for value in row.get("human_issue_codes", "").split(";") if value
        ],
    }


def _safe_route(value: str, used: set[str]) -> str:
    route = re.sub(r"[^a-z0-9_]+", "_", value.lower()).strip("_") or "candidate"
    route = route[:56]
    candidate = route
    index = 2
    while candidate in used:
        candidate = f"{route}_{index}"
        index += 1
    used.add(candidate)
    return candidate


class HumanReviewSidecar:
    """Append-only review history plus a compact current-state CSV."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.current_path = self.root / "human-review-current.csv"
        self.events_path = self.root / "human-review-events.jsonl"
        self.status_path = self.root / "review-status.json"
        self._lock = threading.Lock()

    def current(self) -> dict[tuple[str, str, str], dict[str, str]]:
        return {
            (row["mode"], row["task_id"], row["route"]): row
            for row in _read_csv(self.current_path)
        }

    def save(
        self,
        submission: ReviewSubmission,
        candidates: dict[str, dict[str, str]],
    ) -> dict[str, Any]:
        expected = set(candidates)
        provided = [item.route for item in submission.reviews]
        if len(provided) != len(set(provided)) or set(provided) != expected:
            raise ValueError("all available routes must be reviewed exactly once")
        timestamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        with self._lock:
            current = self.current()
            events: list[dict[str, Any]] = []
            for review in submission.reviews:
                candidate = candidates[review.route]
                event_id = f"review-{uuid4().hex}"
                row = {
                    "mode": submission.mode,
                    "task_id": submission.task_id,
                    "candidate_id": candidate["candidate_id"],
                    "route": review.route,
                    "method": candidate["method"],
                    "human_grade": review.grade,
                    "human_reason": review.reason.strip(),
                    "human_issue_codes": ";".join(review.issue_codes),
                    "human_confirmed": "true",
                    "reviewer_id": submission.reviewer_id,
                    "updated_at": timestamp,
                    "event_id": event_id,
                }
                current[(submission.mode, submission.task_id, review.route)] = row
                events.append({"schema_version": "1.0", **row})
            rows = sorted(
                current.values(),
                key=lambda row: (row["mode"], row["task_id"], row["route"]),
            )
            _write_csv_atomic(self.current_path, rows)
            self.events_path.parent.mkdir(parents=True, exist_ok=True)
            with self.events_path.open("a", encoding="utf-8") as handle:
                for event in events:
                    handle.write(json.dumps(event, ensure_ascii=False) + "\n")
            task_keys = {(row["mode"], row["task_id"]) for row in rows}
            grade_counts = {grade: 0 for grade in sorted(HUMAN_GRADES)}
            for row in rows:
                grade_counts[row["human_grade"]] += 1
            _write_json_atomic(
                self.status_path,
                {
                    "schema_version": "1.0",
                    "reviewed_candidate_count": len(rows),
                    "reviewed_task_mode_count": len(task_keys),
                    "grade_counts": grade_counts,
                    "updated_at": timestamp,
                    "current_csv_sha256": _sha256(self.current_path),
                },
            )
        return {
            "status": "saved",
            "mode": submission.mode,
            "task_id": submission.task_id,
            "reviewer_id": submission.reviewer_id,
            "saved_count": len(events),
        }


class Movie60ReviewAdapter:
    """Compatibility adapter for the existing Movie60 release workspace."""

    def __init__(self, root: Path) -> None:
        self.workspace_module = Movie60ReviewWorkspace(root)

    def ready(self) -> dict[str, Any]:
        ready = self.workspace_module.ready()
        ready["workspace_kind"] = "movie60"
        ready["modes"] = [
            {
                "id": "all60",
                "label": "完整 60 张",
                "detail": "7候选逐张评分（420张）",
            },
            {"id": "focus20", "label": "重点 20 张", "detail": "Rule / Agent / AIGC"},
        ]
        return ready

    def workspace(self, mode: str) -> dict[str, Any]:
        if mode not in {"all60", "focus20"}:
            raise ValueError(f"unknown Movie60 review mode: {mode}")
        payload = self.workspace_module.workspace(mode)  # type: ignore[arg-type]
        payload.update(
            {
                "mode_label": "完整 60 张" if mode == "all60" else "重点 20 张",
                "mode_description": (
                    "完整60张：逐张校对七种候选，共420张；同时核对Rule与Agent判分。"
                    if mode == "all60"
                    else "重点20张：分别评价Rule、Agent和成功回图的AIGC。"
                ),
                "candidate_heading": "全部七种候选" if mode == "all60" else "Rule / Agent / AIGC",
            }
        )
        return payload

    def save(self, submission: ReviewSubmission) -> dict[str, Any]:
        legacy = Movie60ReviewSubmission(
            mode=submission.mode,  # type: ignore[arg-type]
            reviewer_id=submission.reviewer_id,
            task_id=submission.task_id,
            reviews=tuple(
                Movie60ReviewItem(
                    route=item.route,
                    grade=item.grade,  # type: ignore[arg-type]
                    reason=item.reason,
                    issue_codes=item.issue_codes,
                )
                for item in submission.reviews
            ),
        )
        return self.workspace_module.save(legacy)

    def media(self, mode: str, task_id: str, media_id: str) -> ReviewMedia:
        item: Movie60Media = self.workspace_module.media(mode, task_id, media_id)
        return ReviewMedia(path=item.path, media_type=item.media_type, sha256=item.sha256)


class _IndexedAdapter:
    """Shared indexed-media and human-sidecar behaviour for Run/import adapters."""

    def __init__(self, root: Path, *, review_root: Path) -> None:
        self.root = root.resolve()
        self.sidecar = HumanReviewSidecar(review_root)
        self._media: dict[tuple[str, str, str], ReviewMedia] = {}
        self._tasks_by_mode: dict[str, list[dict[str, Any]]] = {}

    def _add_media(self, mode: str, task_id: str, media_id: str, path: Path) -> None:
        resolved = path.resolve()
        if not resolved.is_file():
            return
        self._media[(mode, task_id, media_id)] = ReviewMedia(
            path=resolved,
            media_type=mimetypes.guess_type(resolved.name)[0] or "application/octet-stream",
            sha256=_sha256(resolved),
        )

    def media(self, mode: str, task_id: str, media_id: str) -> ReviewMedia:
        validate_id(task_id)
        key = (mode, task_id, media_id)
        if key not in self._media:
            raise KeyError("review media is not indexed")
        return self._media[key]

    @staticmethod
    def _complete(task: dict[str, Any]) -> bool:
        available = [candidate for candidate in task["candidates"] if candidate["available"]]
        return bool(available) and all(candidate["review"] is not None for candidate in available)

    def workspace(self, mode: str) -> dict[str, Any]:
        if mode not in self._tasks_by_mode:
            raise ValueError(f"unknown review mode: {mode}")
        reviews = self.sidecar.current()
        tasks = json.loads(json.dumps(self._tasks_by_mode[mode], ensure_ascii=False))
        for task in tasks:
            for candidate in task["candidates"]:
                candidate["review"] = _human_review(
                    reviews.get((mode, task["task_id"], candidate["route"]))
                )
        return {
            "mode": mode,
            "release": self.release,
            "mode_label": self.mode_metadata[mode]["label"],
            "mode_description": self.mode_metadata[mode]["description"],
            "candidate_heading": self.mode_metadata[mode]["heading"],
            "task_count": len(tasks),
            "completed_task_count": sum(self._complete(task) for task in tasks),
            "tasks": tasks,
        }

    def save(self, submission: ReviewSubmission) -> dict[str, Any]:
        if submission.mode not in self._tasks_by_mode:
            raise ValueError("task is not part of this review mode")
        task = next(
            (
                task
                for task in self._tasks_by_mode[submission.mode]
                if task["task_id"] == submission.task_id
            ),
            None,
        )
        if task is None:
            raise ValueError("task is not part of this review mode")
        candidates = {
            candidate["route"]: {
                "candidate_id": candidate["candidate_id"],
                "method": candidate["method"],
            }
            for candidate in task["candidates"]
            if candidate["available"]
        }
        return self.sidecar.save(submission, candidates)


class RunReviewAdapter(_IndexedAdapter):
    """Adapt one standard Generation Run to the current Movie60-shaped UI."""

    def __init__(
        self,
        root: Path,
        *,
        evaluation_id: str | None = None,
        agent_run_id: str | None = None,
    ) -> None:
        super().__init__(root, review_root=root / "reviews")
        self.manifest = RunManifest.model_validate(_read_json(self.root / "run.json"))
        self.evaluation_dir = self._evaluation(evaluation_id)
        self.evaluation = _read_json(self.evaluation_dir / "evaluation.json")
        self.metrics = {
            str(payload["candidate_id"]): payload.get("metrics", {})
            for path in sorted((self.evaluation_dir / "metrics").glob("*.json"))
            for payload in [_read_json(path)]
        }
        self.agent_id, self.agent_decisions = self._agent(agent_run_id)
        self.release = {
            "release_id": self.manifest.run_id,
            "strategy_version": self.evaluation.get("strategy_version", "current"),
            "evaluation_id": self.evaluation.get("evaluation_id", self.evaluation_dir.name),
            "workspace_kind": "run",
        }
        self.mode_metadata = {
            "all": {
                "label": "全部候选",
                "description": "当前 Run：逐任务检查全部成功候选、Rule 指标与可选 Agent 结果。",
                "heading": "全部候选",
            },
            "routes": {
                "label": "路线对比",
                "description": "对比 Rule Top1、Agent Top1 与可用的 AIGC 结果。",
                "heading": "Rule / Agent / AIGC",
            },
        }
        self._build()

    def _evaluation(self, requested: str | None) -> Path:
        parent = self.root / "evaluations"
        if requested:
            validate_id(requested)
            chosen = parent / requested
            if not (chosen / "evaluation.json").is_file():
                raise ValueError(f"evaluation does not exist: {requested}")
            return chosen
        choices = (
            [path for path in parent.iterdir() if (path / "evaluation.json").is_file()]
            if parent.is_dir()
            else []
        )
        if not choices:
            raise ValueError("Run has no completed evaluation; run score/evaluate first")
        return max(choices, key=lambda path: (path.stat().st_mtime_ns, path.name))

    def _agent(self, requested: str | None) -> tuple[str | None, dict[str, dict[str, Any]]]:
        parent = self.root / "agent-runs"
        if not parent.is_dir():
            return None, {}
        choices = [path for path in parent.iterdir() if (path / "agent-run.json").is_file()]
        if requested:
            validate_id(requested)
            choices = [path for path in choices if path.name == requested]
            if not choices:
                raise ValueError(f"Agent run does not exist: {requested}")
        matching = []
        for path in choices:
            manifest = _read_json(path / "agent-run.json")
            if manifest.get("evaluation_id") == self.evaluation_dir.name:
                matching.append(path)
        if not matching:
            return None, {}
        chosen = max(matching, key=lambda path: (path.stat().st_mtime_ns, path.name))
        decisions = {
            str(payload["task_id"]): payload
            for path in sorted((chosen / "decisions").glob("*.json"))
            for payload in [_read_json(path)]
        }
        return chosen.name, decisions

    @staticmethod
    def _rule_reason(metrics: dict[str, Any]) -> str:
        parts = [
            f"质量分 {_number(metrics.get('quality_score')):.2f}",
            f"内容保真 {_percent(metrics.get('content_fidelity_score'))}",
            f"视觉完整 {_percent(metrics.get('visual_integrity_score'))}",
            f"构图 {_percent(metrics.get('composition_score'))}",
        ]
        failures = str(
            metrics.get("critical_regressions") or metrics.get("hard_failures") or ""
        ).strip()
        if failures:
            parts.append(f"门禁/回归：{failures}")
        return "；".join(parts) + "。这是可回放 Rule 证据，不是人工金标。"

    def _source_path(self, task: TaskSpec) -> Path:
        suffix = Path(task.source.image_path).suffix.lower()
        exact = self.root / "sources" / f"{task.source.source_id}{suffix}"
        if exact.is_file():
            return exact
        matches = sorted((self.root / "sources").glob(f"{task.source.source_id}.*"))
        if len(matches) != 1:
            raise ValueError(f"cannot resolve frozen source for {task.task_id}")
        return matches[0]

    def _aigc_image(self, task_id: str) -> Path | None:
        parent = self.root / "external-generation"
        if not parent.is_dir():
            return None
        matches = [
            path
            for path in parent.rglob("*")
            if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES and task_id in path.name
        ]
        return max(matches, key=lambda path: path.stat().st_mtime_ns) if matches else None

    def _build(self) -> None:
        candidates_by_task: dict[str, list[CandidateRecord]] = {}
        for path in sorted(self.root.glob("candidates/*/*/candidate.json")):
            record = CandidateRecord.model_validate_json(path.read_text(encoding="utf-8"))
            if record.output is not None and record.candidate_id in self.metrics:
                candidates_by_task.setdefault(record.task_id, []).append(record)
        all_tasks: list[dict[str, Any]] = []
        route_tasks: list[dict[str, Any]] = []
        for task_id in self.manifest.task_ids:
            task = TaskSpec.model_validate(_read_json(self.root / "tasks" / f"{task_id}.json"))
            source = self._source_path(task)
            records = candidates_by_task.get(task_id, [])
            ranked = sorted(
                records,
                key=lambda item: (
                    -_number(self.metrics[item.candidate_id].get("quality_score")),
                    item.method_id,
                ),
            )
            agent = self.agent_decisions.get(task_id)
            agent_ranking = list(agent.get("candidate_ranking", ())) if agent else []
            agent_rank = {
                candidate_id: index + 1
                for index, candidate_id in enumerate(agent_ranking)
            }
            agent_top = agent_ranking[0] if agent_ranking else None
            candidates: list[dict[str, Any]] = []
            used_routes: set[str] = set()
            for rule_rank, record in enumerate(ranked, 1):
                route = _safe_route(record.method_id, used_routes)
                metrics = self.metrics[record.candidate_id]
                self._add_media(
                    "all",
                    task_id,
                    f"candidate_{route}",
                    self.root / record.output.relative_path,
                )
                is_agent_top = record.candidate_id == agent_top
                candidates.append(
                    {
                        "candidate_id": record.candidate_id,
                        "route": route,
                        "title": f"Rule 第{rule_rank}名 · {record.method_id}",
                        "method": record.method_id,
                        "machine_grade": _grade(metrics.get("proxy_grade")) or "N/A",
                        "machine_score": _number(metrics.get("quality_score")),
                        "codex_grade": (
                            _grade(agent.get("proxy_grade"))
                            if is_agent_top and agent
                            else None
                        ),
                        "available": True,
                        "status_text": (
                            "Rule Top1"
                            if rule_rank == 1
                            else "Agent Top1"
                            if is_agent_top
                            else "普通候选"
                        ),
                        "image_url": f"/v1/media/all/{task_id}/candidate_{route}",
                        "native_url": None,
                        "rule_rank": rule_rank,
                        "rule_denominator": len(ranked),
                        "rule_reason": self._rule_reason(metrics),
                        "rule_metrics": {
                            "ocr_recall": metrics.get("ocr_character_recall"),
                            "person_preservation": metrics.get("person_count_preservation"),
                            "face_preservation": metrics.get("face_count_preservation"),
                            "product_preservation": metrics.get("product_count_preservation"),
                            "logo_preservation": metrics.get("logo_count_preservation"),
                        },
                        "agent_rank": agent_rank.get(record.candidate_id),
                        "agent_role": (
                            "Agent Top1"
                            if is_agent_top
                            else "Agent 已运行"
                            if agent
                            else "Agent 未运行"
                        ),
                        "agent_review_scope": "候选总览排序" if agent else "未运行",
                        "agent_grade": (
                            _grade(agent.get("proxy_grade"))
                            if is_agent_top and agent
                            else None
                        ),
                        "agent_directly_usable": None,
                        "agent_confidence": (
                            _number(agent.get("selection_confidence"))
                            if is_agent_top and agent
                            else None
                        ),
                        "agent_reason": (
                            "Agent 总览选择；原因代码：" + "、".join(agent.get("reason_codes", ()))
                            if is_agent_top and agent
                            else "Agent 已完成排序，只有 Top1 显示任务级建议。"
                            if agent
                            else "Agent 未运行；当前结果仅使用 Rule。"
                        ),
                        "agent_reason_codes": list(agent.get("reason_codes", ())) if agent else [],
                        "agent_reason_codes_raw": (
                            list(agent.get("reason_codes", ())) if agent else []
                        ),
                        "review_rationale": None,
                        "final_selected": rule_rank == 1,
                        "model_advice_grade": None,
                        "model_advice_reason": "",
                        "model_advice_scope": "未运行",
                        "review": None,
                    }
                )
            self._add_media("all", task_id, "source", source)
            self._add_media("all", task_id, "comparison", source)
            all_tasks.append(
                {
                    "task_id": task_id,
                    "split": task.source.split,
                    "scene_category": task.source.scene_category,
                    "source_url": f"/v1/media/all/{task_id}/source",
                    "comparison_url": f"/v1/media/all/{task_id}/comparison",
                    "candidates": candidates,
                }
            )
            route_candidates = self._route_candidates(task_id, source, candidates, agent_top)
            route_tasks.append(
                {
                    "task_id": task_id,
                    "split": task.source.split,
                    "scene_category": task.source.scene_category,
                    "source_url": f"/v1/media/routes/{task_id}/source",
                    "comparison_url": f"/v1/media/routes/{task_id}/comparison",
                    "candidates": route_candidates,
                }
            )
        self._tasks_by_mode = {"all": all_tasks, "routes": route_tasks}

    def _route_candidates(
        self,
        task_id: str,
        source: Path,
        all_candidates: list[dict[str, Any]],
        agent_top: str | None,
    ) -> list[dict[str, Any]]:
        self._add_media("routes", task_id, "source", source)
        self._add_media("routes", task_id, "comparison", source)
        if not all_candidates:
            return []
        rule = dict(all_candidates[0])
        rule.update(
            {
                "route": "rule",
                "title": "Rule Top1",
                "image_url": f"/v1/media/routes/{task_id}/rule",
            }
        )
        source_media = self._media[("all", task_id, f"candidate_{all_candidates[0]['route']}")]
        self._media[("routes", task_id, "rule")] = source_media
        result = [rule]
        agent_candidate = next(
            (candidate for candidate in all_candidates if candidate["candidate_id"] == agent_top),
            None,
        )
        if agent_candidate and agent_candidate["candidate_id"] != rule["candidate_id"]:
            agent = dict(agent_candidate)
            agent.update(
                {
                    "route": "agent",
                    "title": "Agent Top1",
                    "image_url": f"/v1/media/routes/{task_id}/agent",
                    "final_selected": False,
                }
            )
            self._media[("routes", task_id, "agent")] = self._media[
                ("all", task_id, f"candidate_{agent_candidate['route']}")
            ]
            result.append(agent)
        else:
            result.append(
                {
                    "candidate_id": f"{task_id}--agent--unavailable",
                    "route": "agent",
                    "title": "Agent Top1",
                    "method": "agent",
                    "machine_grade": "N/A",
                    "available": False,
                    "status_text": "Agent 未运行或与 Rule Top1 相同",
                    "image_url": None,
                    "native_url": None,
                    "review": None,
                }
            )
        aigc_path = self._aigc_image(task_id)
        if aigc_path:
            self._add_media("routes", task_id, "aigc", aigc_path)
            result.append(
                {
                    "candidate_id": f"{task_id}--aigc--external",
                    "route": "aigc",
                    "title": "AIGC",
                    "method": "aigc",
                    "machine_grade": "N/A",
                    "available": True,
                    "status_text": "存在外部生成结果；尚无 Rule 分数",
                    "image_url": f"/v1/media/routes/{task_id}/aigc",
                    "native_url": None,
                    "review_rationale": "请按高清成图独立评价。",
                    "review": None,
                }
            )
        else:
            result.append(
                {
                    "candidate_id": f"{task_id}--aigc--unavailable",
                    "route": "aigc",
                    "title": "AIGC",
                    "method": "aigc",
                    "machine_grade": "N/A",
                    "available": False,
                    "status_text": "AIGC 未运行",
                    "image_url": None,
                    "native_url": None,
                    "review": None,
                }
            )
        return result

    def ready(self) -> dict[str, Any]:
        return {
            "status": "ready",
            "workspace_kind": "run",
            "run_id": self.manifest.run_id,
            "evaluation_id": self.evaluation_dir.name,
            "agent_run_id": self.agent_id,
            "task_count": len(self.manifest.task_ids),
            "candidate_count": sum(len(task["candidates"]) for task in self._tasks_by_mode["all"]),
            "media_count": len(self._media),
            "modes": [
                {"id": mode, "label": data["label"], "detail": data["heading"]}
                for mode, data in self.mode_metadata.items()
            ],
        }


class ImportedReviewAdapter(_IndexedAdapter):
    """Review adapter for a materialized ordinary source+candidates directory."""

    def __init__(self, root: Path) -> None:
        super().__init__(root, review_root=root / "reviews")
        manifest = _read_json(self.root / "review-workspace.json")
        task_id = str(manifest["task_id"])
        validate_id(task_id)
        source = self.root / str(manifest["source"])
        self.release = {
            "release_id": str(manifest["workspace_id"]),
            "strategy_version": "not-scored",
            "workspace_kind": "imported",
        }
        self.mode_metadata = {
            "all": {
                "label": "全部候选",
                "description": (
                    "外部图片目录：逐张评价导入候选；"
                    "未提供的 Rule/Agent 证据显示为未运行。"
                ),
                "heading": "全部候选",
            }
        }
        self._add_media("all", task_id, "source", source)
        self._add_media("all", task_id, "comparison", source)
        candidates = []
        for item in manifest["candidates"]:
            route = str(item["route"])
            path = self.root / str(item["image"])
            self._add_media("all", task_id, f"candidate_{route}", path)
            candidates.append(
                {
                    "candidate_id": str(item["candidate_id"]),
                    "route": route,
                    "title": str(item["method"]),
                    "method": str(item["method"]),
                    "machine_grade": "N/A",
                    "available": True,
                    "status_text": "外部候选；Rule、Agent 未运行",
                    "image_url": f"/v1/media/all/{task_id}/candidate_{route}",
                    "native_url": None,
                    "rule_reason": "",
                    "review_rationale": "此候选没有机器评分，请直接进行人工判断。",
                    "review": None,
                }
            )
        self._tasks_by_mode = {
            "all": [
                {
                    "task_id": task_id,
                    "split": "imported",
                    "scene_category": "external",
                    "source_url": f"/v1/media/all/{task_id}/source",
                    "comparison_url": f"/v1/media/all/{task_id}/comparison",
                    "candidates": candidates,
                }
            ]
        }

    def ready(self) -> dict[str, Any]:
        candidates = self._tasks_by_mode["all"][0]["candidates"]
        return {
            "status": "ready",
            "workspace_kind": "imported",
            "task_count": 1,
            "candidate_count": len(candidates),
            "media_count": len(self._media),
            "modes": [{"id": "all", "label": "全部候选", "detail": f"{len(candidates)} 个候选"}],
        }


def open_review_workspace(
    root: Path,
    *,
    evaluation_id: str | None = None,
    agent_run_id: str | None = None,
) -> ReviewWorkspaceAdapter:
    """Detect and open exactly one supported review workspace."""
    resolved = root.resolve()
    if (resolved / "all60" / "candidate-review.csv").is_file():
        return Movie60ReviewAdapter(resolved)
    if (resolved / "run.json").is_file():
        return RunReviewAdapter(
            resolved,
            evaluation_id=evaluation_id,
            agent_run_id=agent_run_id,
        )
    if (resolved / "review-workspace.json").is_file():
        return ImportedReviewAdapter(resolved)
    raise ValueError(
        "unsupported review directory: expected Movie60, a standard Run, or review-workspace.json"
    )


def import_review_case(source_dir: Path, output_dir: Path) -> dict[str, Any]:
    """Materialize one ordinary source+candidates folder as a review workspace."""
    source_root = source_dir.resolve()
    target = output_dir.resolve()
    if target.exists():
        raise FileExistsError(f"review workspace already exists: {target}")
    source_matches = [
        path
        for path in source_root.iterdir()
        if path.is_file()
        and path.stem.lower() == "source"
        and path.suffix.lower() in IMAGE_SUFFIXES
    ]
    candidate_dir = source_root / "candidates"
    candidate_paths = (
        [
            path
            for path in sorted(candidate_dir.iterdir())
            if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
        ]
        if candidate_dir.is_dir()
        else []
    )
    if len(source_matches) != 1:
        raise ValueError(
            "external review case must contain exactly one source image named source.*"
        )
    if not candidate_paths:
        raise ValueError("external review case must contain candidates/ with at least one image")
    workspace_id = _safe_route(source_root.name, set())
    validate_id(workspace_id)
    task_id = f"{workspace_id}__imported"
    media = target / "media"
    media.mkdir(parents=True)
    frozen_source = media / f"source{source_matches[0].suffix.lower()}"
    shutil.copy2(source_matches[0], frozen_source)
    used: set[str] = set()
    candidates = []
    for index, path in enumerate(candidate_paths, 1):
        route = _safe_route(path.stem, used)
        frozen = media / f"candidate_{route}{path.suffix.lower()}"
        shutil.copy2(path, frozen)
        candidates.append(
            {
                "candidate_id": f"{task_id}--{route}--imported",
                "route": route,
                "method": path.stem,
                "image": frozen.relative_to(target).as_posix(),
                "sha256": _sha256(frozen),
                "display_order": index,
            }
        )
    manifest = {
        "schema_version": "1.0",
        "workspace_id": workspace_id,
        "task_id": task_id,
        "source": frozen_source.relative_to(target).as_posix(),
        "source_sha256": _sha256(frozen_source),
        "candidates": candidates,
    }
    _write_json_atomic(target / "review-workspace.json", manifest)
    return {
        "workspace": str(target),
        "workspace_id": workspace_id,
        "task_count": 1,
        "candidate_count": len(candidates),
    }


def latest_completed_run(runs_root: Path) -> Path:
    """Return the most recently written completed standard Run."""
    root = runs_root.resolve()
    choices: list[Path] = []
    if root.is_dir():
        for child in root.iterdir():
            manifest_path = child / "run.json"
            if not manifest_path.is_file():
                continue
            manifest = _read_json(manifest_path)
            status = manifest.get("status", manifest.get("state", ""))
            if str(status).upper() == "COMPLETED":
                choices.append(child)
    if not choices:
        raise FileNotFoundError(f"no completed Generation Run under {root}")
    return max(choices, key=lambda path: ((path / "run.json").stat().st_mtime_ns, path.name))
