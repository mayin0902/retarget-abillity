"""Local FastAPI review UI for the curated Movie60 human-review workspace."""

from __future__ import annotations

import csv
import hashlib
import mimetypes
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import HTTPException, RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import validate_id

ReviewMode = Literal["all60", "focus20"]
HumanGrade = Literal["A", "B", "C", "D"]


class Movie60ReviewItem(BaseModel):
    """One human route judgement with an explicit free-text reason."""

    model_config = ConfigDict(extra="forbid")

    route: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    grade: HumanGrade
    reason: str = Field(min_length=3, max_length=2000)
    issue_codes: tuple[str, ...] = Field(default=(), max_length=16)


class Movie60ReviewSubmission(BaseModel):
    """Complete review of all available routes for one Movie60 task."""

    model_config = ConfigDict(extra="forbid")

    mode: ReviewMode
    reviewer_id: str
    task_id: str
    reviews: tuple[Movie60ReviewItem, ...] = Field(min_length=1, max_length=8)

    _reviewer_id = field_validator("reviewer_id")(validate_id)
    _task_id = field_validator("task_id")(validate_id)


@dataclass(frozen=True)
class Movie60Media:
    path: Path
    media_type: str
    sha256: str


class Movie60WorkspaceError(Exception):
    """Raised for safe, user-facing review-workspace errors."""


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv_atomic(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _one_file(directory: Path, pattern: str) -> Path:
    matches = sorted(path for path in directory.glob(pattern) if path.is_file())
    if len(matches) != 1:
        raise ValueError(f"expected one {pattern} in {directory}, got {len(matches)}")
    return matches[0]


class Movie60ReviewWorkspace:
    """Owns package indexing and atomic CSV persistence for the two review modes."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.all60_dir = self.root / "all60"
        self.focus20_dir = self.root / "focus20"
        required = (
            self.all60_dir / "summary.csv",
            self.all60_dir / "review.csv",
            self.all60_dir / "candidate-review.csv",
            self.focus20_dir / "codex.csv",
            self.focus20_dir / "review.csv",
            self.focus20_dir / "aigc-status.csv",
        )
        if not all(path.is_file() for path in required):
            raise ValueError("the configured directory is not a complete Movie60 review workspace")
        self._lock = threading.Lock()
        self._media: dict[tuple[str, str, str], Movie60Media] = {}
        self._index_media()

    def _add_media(self, mode: str, task_id: str, media_id: str, path: Path) -> None:
        if not path.is_file():
            return
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self._media[(mode, task_id, media_id)] = Movie60Media(
            path=path,
            media_type=media_type,
            sha256=_sha256(path),
        )

    def _index_media(self) -> None:
        for task_dir in sorted((self.all60_dir / "tasks").iterdir()):
            if not task_dir.is_dir():
                continue
            task_id = task_dir.name
            self._add_media("all60", task_id, "source", task_dir / "00_source.jpg")
            self._add_media("all60", task_id, "final", task_dir / "01_final.png")
            self._add_media("all60", task_id, "comparison", task_dir / "02_comparison.jpg")
            candidate_dir = task_dir / "candidates"
            if candidate_dir.is_dir():
                for candidate in sorted(candidate_dir.glob("*.png")):
                    self._add_media(
                        "all60",
                        task_id,
                        f"candidate_{candidate.stem}",
                        candidate,
                    )
        for task_dir in sorted((self.focus20_dir / "tasks").iterdir()):
            if not task_dir.is_dir():
                continue
            task_id = task_dir.name
            for media_id, pattern in (
                ("source", "00_source.*"),
                ("rule", "01_rule_*"),
                ("agent", "02_agent_*"),
                ("aigc", "03_aigc_*"),
                ("aigc_native", "04_aigc_native_2k.*"),
                ("comparison", "collage.*"),
            ):
                matches = sorted(path for path in task_dir.glob(pattern) if path.is_file())
                if len(matches) > 1:
                    raise ValueError(f"ambiguous {media_id} media for {task_id}")
                if matches:
                    self._add_media("focus20", task_id, media_id, matches[0])

    def ready(self) -> dict[str, Any]:
        return {
            "status": "ready",
            "all60_task_count": len(_read_csv(self.all60_dir / "summary.csv")),
            "all60_candidate_count": len(_read_csv(self.all60_dir / "candidate-review.csv")),
            "focus20_task_count": len(_read_csv(self.focus20_dir / "codex.csv")),
            "media_count": len(self._media),
        }

    @staticmethod
    def _review_payload(
        row: dict[str, str],
        route: str,
        *,
        all60: bool = False,
    ) -> dict[str, Any] | None:
        grade_key = "human_grade" if all60 else f"human_{route}_grade"
        reason_key = "human_reason" if all60 else f"human_{route}_reason"
        issue_key = "human_issue_codes" if all60 else f"human_{route}_issue_codes"
        grade = row.get(grade_key, "").strip()
        if grade not in {"A", "B", "C", "D"}:
            return None
        return {
            "grade": grade,
            "reason": row.get(reason_key, ""),
            "issue_codes": [item for item in row.get(issue_key, "").split(";") if item],
        }

    def _all60_tasks(self) -> list[dict[str, Any]]:
        candidate_rows: dict[str, list[dict[str, str]]] = {}
        for row in _read_csv(self.all60_dir / "candidate-review.csv"):
            candidate_rows.setdefault(row["task_id"], []).append(row)
        tasks = []
        for row in _read_csv(self.all60_dir / "summary.csv"):
            task_id = row["task_id"]
            ranked = sorted(candidate_rows[task_id], key=lambda item: int(item["rule_rank"]))
            if len(ranked) != 7:
                raise ValueError(f"{task_id} does not have exactly seven review candidates")
            candidates = []
            for candidate in ranked:
                method = candidate["method"]
                agent_grade = candidate["agent_grade"].strip()
                candidates.append(
                    {
                        "route": method,
                        "title": f"Rule 第{candidate['rule_rank']}名 · {method}",
                        "method": method,
                        "machine_grade": candidate["rule_grade"],
                        "machine_score": float(candidate["rule_quality"]),
                        "codex_grade": agent_grade or None,
                        "available": True,
                        "status_text": candidate["agent_role"],
                        "image_url": (f"/v1/media/all60/{task_id}/candidate_{method}"),
                        "native_url": None,
                        "rule_rank": int(candidate["rule_rank"]),
                        "rule_reason": candidate["rule_reason"],
                        "rule_metrics": {
                            "ocr_recall": candidate["rule_ocr_recall"] or None,
                            "person_preservation": (candidate["rule_person_preservation"] or None),
                            "face_preservation": (candidate["rule_face_preservation"] or None),
                            "product_preservation": (
                                candidate["rule_product_preservation"] or None
                            ),
                            "logo_preservation": (candidate["rule_logo_preservation"] or None),
                        },
                        "agent_rank": (
                            int(candidate["agent_rank"]) if candidate["agent_rank"] else None
                        ),
                        "agent_role": candidate["agent_role"],
                        "agent_review_scope": candidate["agent_review_scope"],
                        "agent_grade": agent_grade or None,
                        "agent_directly_usable": (candidate["agent_directly_usable"] or None),
                        "agent_confidence": (
                            float(candidate["agent_confidence"])
                            if candidate["agent_confidence"]
                            else None
                        ),
                        "agent_reason": candidate["agent_reason"],
                        "agent_reason_codes": [
                            value
                            for value in candidate["agent_reason_codes_zh"].split(";")
                            if value
                        ],
                        "agent_reason_codes_raw": [
                            value
                            for value in candidate["agent_reason_codes"].split(";")
                            if value
                        ],
                        "review_rationale": None,
                        "final_selected": candidate["final_selected"] == "true",
                        "model_advice_grade": (candidate["model_advice_grade"] or None),
                        "model_advice_reason": candidate["model_advice_reason"],
                        "model_advice_scope": candidate["model_advice_scope"],
                        "review": self._review_payload(candidate, method, all60=True),
                    }
                )
            tasks.append(
                {
                    "task_id": task_id,
                    "split": row["phase"],
                    "scene_category": row["scene_category"],
                    "source_url": f"/v1/media/all60/{task_id}/source",
                    "comparison_url": f"/v1/media/all60/{task_id}/comparison",
                    "candidates": candidates,
                }
            )
        return tasks

    def _focus20_tasks(self) -> list[dict[str, Any]]:
        reviews = {row["task_id"]: row for row in _read_csv(self.focus20_dir / "review.csv")}
        statuses = {row["task_id"]: row for row in _read_csv(self.focus20_dir / "aigc-status.csv")}
        tasks = []
        for row in _read_csv(self.focus20_dir / "codex.csv"):
            task_id = row["task_id"]
            status = statuses[task_id]
            review_row = reviews[task_id]
            candidates = []
            for route, title in (("rule", "Rule选择"), ("agent", "Agent选择")):
                candidates.append(
                    {
                        "route": route,
                        "title": title,
                        "method": row[f"{route}_method"],
                        "machine_grade": row[f"old_{route}_grade"],
                        "codex_grade": row[f"codex_{route}_grade"],
                        "available": True,
                        "status_text": "请按高清成图独立判断",
                        "image_url": f"/v1/media/focus20/{task_id}/{route}",
                        "native_url": None,
                        "review_rationale": row["codex_reason"],
                        "review": self._review_payload(review_row, route),
                    }
                )
            aigc_available = status["status"] == "success"
            candidates.append(
                {
                    "route": "aigc",
                    "title": "AIGC结果",
                    "method": "aigc",
                    "machine_grade": row["old_aigc_grade"] if aigc_available else "N/A",
                    "codex_grade": row["codex_aigc_grade"] if aigc_available else "N/A",
                    "available": aigc_available,
                    "status_text": (
                        "成功回图；同时提供1536评测图和原生2K图"
                        if aigc_available
                        else f"{status['error_code']}：{status['failure_meaning']}"
                    ),
                    "image_url": (f"/v1/media/focus20/{task_id}/aigc" if aigc_available else None),
                    "native_url": (
                        f"/v1/media/focus20/{task_id}/aigc_native" if aigc_available else None
                    ),
                    "review_rationale": row["codex_reason"],
                    "review": (
                        self._review_payload(review_row, "aigc") if aigc_available else None
                    ),
                }
            )
            tasks.append(
                {
                    "task_id": task_id,
                    "split": row["split"],
                    "scene_category": task_id.split("_")[0],
                    "source_url": f"/v1/media/focus20/{task_id}/source",
                    "comparison_url": f"/v1/media/focus20/{task_id}/comparison",
                    "candidates": candidates,
                }
            )
        return tasks

    @staticmethod
    def _complete(task: dict[str, Any]) -> bool:
        available = [item for item in task["candidates"] if item["available"]]
        return bool(available) and all(item["review"] is not None for item in available)

    def workspace(self, mode: ReviewMode) -> dict[str, Any]:
        tasks = self._all60_tasks() if mode == "all60" else self._focus20_tasks()
        return {
            "mode": mode,
            "task_count": len(tasks),
            "completed_task_count": sum(self._complete(task) for task in tasks),
            "tasks": tasks,
        }

    def save(self, submission: Movie60ReviewSubmission) -> dict[str, Any]:
        workspace = self.workspace(submission.mode)
        task = next(
            (item for item in workspace["tasks"] if item["task_id"] == submission.task_id),
            None,
        )
        if task is None:
            raise Movie60WorkspaceError("task is not part of this review mode")
        expected = {item["route"] for item in task["candidates"] if item["available"]}
        provided = [item.route for item in submission.reviews]
        if len(provided) != len(set(provided)) or set(provided) != expected:
            raise Movie60WorkspaceError("all available routes must be reviewed exactly once")
        timestamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        with self._lock:
            if submission.mode == "all60":
                path = self.all60_dir / "candidate-review.csv"
                rows = _read_csv(path)
                fields = list(rows[0])
                for review in submission.reviews:
                    row = next(
                        item
                        for item in rows
                        if item["task_id"] == submission.task_id and item["method"] == review.route
                    )
                    row.update(
                        {
                            "human_grade": review.grade,
                            "human_reason": review.reason.strip(),
                            "human_issue_codes": ";".join(review.issue_codes),
                            "human_confirmed": "true",
                            "reviewer_id": submission.reviewer_id,
                            "updated_at": timestamp,
                        }
                    )
            else:
                path = self.focus20_dir / "review.csv"
                rows = _read_csv(path)
                fields = list(rows[0])
                for route in ("rule", "agent", "aigc"):
                    field = f"human_{route}_issue_codes"
                    if field not in fields:
                        fields.append(field)
                for field in ("reviewer_id", "updated_at"):
                    if field not in fields:
                        fields.append(field)
                row = next(item for item in rows if item["task_id"] == submission.task_id)
                for review in submission.reviews:
                    row[f"human_{review.route}_grade"] = review.grade
                    row[f"human_{review.route}_reason"] = review.reason.strip()
                    row[f"human_{review.route}_issue_codes"] = ";".join(review.issue_codes)
                row.update(
                    {
                        "human_confirmed": "true",
                        "reviewer_id": submission.reviewer_id,
                        "updated_at": timestamp,
                    }
                )
            _write_csv_atomic(path, rows, fields)
            if submission.mode == "all60":
                self._sync_legacy_top1(submission, timestamp)
        return {
            "status": "saved",
            "mode": submission.mode,
            "task_id": submission.task_id,
            "reviewer_id": submission.reviewer_id,
        }

    def _sync_legacy_top1(
        self,
        submission: Movie60ReviewSubmission,
        timestamp: str,
    ) -> None:
        """Keep the old one-row-per-task Top1 table compatible with candidate review."""

        summary = next(
            row
            for row in _read_csv(self.all60_dir / "summary.csv")
            if row["task_id"] == submission.task_id
        )
        top1 = next(
            review for review in submission.reviews if review.route == summary["final_method"]
        )
        path = self.all60_dir / "review.csv"
        rows = _read_csv(path)
        fields = list(rows[0])
        for field in ("human_issue_codes", "reviewer_id", "updated_at"):
            if field not in fields:
                fields.append(field)
        row = next(item for item in rows if item["task_id"] == submission.task_id)
        row.update(
            {
                "human_grade": top1.grade,
                "human_reason": top1.reason.strip(),
                "human_issue_codes": ";".join(top1.issue_codes),
                "human_confirmed": "true",
                "reviewer_id": submission.reviewer_id,
                "updated_at": timestamp,
            }
        )
        _write_csv_atomic(path, rows, fields)

    def media(self, mode: str, task_id: str, media_id: str) -> Movie60Media:
        record = self._media.get((mode, task_id, media_id))
        if record is None or not record.path.is_file():
            raise Movie60WorkspaceError("media is not part of this review workspace")
        return record


def create_movie60_review_app(workspace_dir: Path) -> FastAPI:
    """Create the local Movie60 review app scoped to one curated workspace."""

    workspace = Movie60ReviewWorkspace(workspace_dir)
    web_root = Path(__file__).with_name("web_movie60")
    app = FastAPI(
        title="Movie60 Human Review API",
        version="1.0.0",
        docs_url="/api/docs",
        redoc_url=None,
    )
    app.state.movie60_workspace = workspace
    app.mount("/assets", StaticFiles(directory=web_root), name="movie60-review-assets")

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
        )
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        return response

    @app.exception_handler(Movie60WorkspaceError)
    async def workspace_error(request: Request, error: Movie60WorkspaceError) -> JSONResponse:
        del request
        return JSONResponse(status_code=422, content={"detail": str(error)})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        del request
        return JSONResponse(status_code=422, content={"detail": error.errors()})

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException) -> JSONResponse:
        del request
        return JSONResponse(status_code=error.status_code, content={"detail": error.detail})

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(web_root / "index.html", media_type="text/html")

    @app.get("/health/ready")
    def ready() -> dict[str, Any]:
        return workspace.ready()

    @app.get("/v1/workspace")
    def load_workspace(
        mode: Annotated[ReviewMode, Query()] = "all60",
    ) -> dict[str, Any]:
        return workspace.workspace(mode)

    @app.post("/v1/reviews")
    def save_reviews(submission: Movie60ReviewSubmission) -> dict[str, Any]:
        return workspace.save(submission)

    @app.get("/v1/media/{mode}/{task_id}/{media_id}", include_in_schema=False)
    def media(mode: str, task_id: str, media_id: str) -> FileResponse:
        record = workspace.media(mode, task_id, media_id)
        return FileResponse(
            record.path,
            media_type=record.media_type,
            headers={
                "Cache-Control": "private, max-age=31536000, immutable",
                "ETag": f'"{record.sha256}"',
                "Content-Disposition": f'inline; filename="{record.path.name}"',
                "X-Content-Type-Options": "nosniff",
            },
        )

    return app


__all__ = ["Movie60ReviewWorkspace", "create_movie60_review_app"]
