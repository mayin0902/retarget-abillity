"""FastAPI adapter for the local human-review web application.

The adapter intentionally owns only HTTP DTOs, safe media lookup and presentation shaping.
Review invariants and append-only persistence remain behind ``RetargetApplicationService``.
"""

from __future__ import annotations

import json
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, Query, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import HTTPException, RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import (
    CandidateRecord,
    ReviewDimensionGrade,
    ReviewGrade,
    RunManifest,
    TaskSpec,
    validate_id,
)
from .service import RetargetApplicationService
from .storage import LocalArtifactStore


class ReviewItemRequest(BaseModel):
    """One candidate judgement in a complete task submission."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    grade: ReviewGrade
    is_best: bool = False
    failure_reasons: tuple[str, ...] = Field(default=(), max_length=10)
    dimension_grades: tuple[ReviewDimensionGrade, ...] = Field(default=(), max_length=6)
    note: str | None = Field(default=None, max_length=2000)
    display_order: int = Field(ge=0)

    _candidate_id = field_validator("candidate_id")(validate_id)


class ReviewSubmissionRequest(BaseModel):
    """A complete, atomic-looking review of all candidates for one task."""

    model_config = ConfigDict(extra="forbid")

    reviewer_id: str
    task_id: str
    reviews: tuple[ReviewItemRequest, ...] = Field(min_length=1, max_length=32)

    _reviewer_id = field_validator("reviewer_id")(validate_id)
    _task_id = field_validator("task_id")(validate_id)


@dataclass(frozen=True)
class MediaRecord:
    path: Path
    media_type: str
    sha256: str
    filename: str


class ReviewResourceNotFound(Exception):
    """Raised when a public resource ID is not part of the configured Run."""


class ReviewWebModule:
    """Deep module that adapts one frozen Run to the small HTTP review interface."""

    def __init__(
        self,
        run_dir: Path,
        service: RetargetApplicationService,
        agent_run_id: str | None = None,
    ) -> None:
        self.run_dir = run_dir.resolve()
        if not (self.run_dir / "run.json").is_file():
            raise ValueError("the configured directory is not a Generation Run")
        self.service = service
        self.store = LocalArtifactStore(self.run_dir)
        self.manifest = RunManifest.model_validate(self.store.read_json("run.json"))
        self.source_media: dict[str, MediaRecord] = {}
        self.candidate_media: dict[str, MediaRecord] = {}
        self.pre_review_run_id: str | None = None
        self.pre_review_decisions: dict[str, dict[str, Any]] = {}
        self._index_media()
        self._index_pre_review(agent_run_id)

    def _index_media(self) -> None:
        for task_id in self.manifest.task_ids:
            task = TaskSpec.model_validate(self.store.read_json(f"tasks/{task_id}.json"))
            suffix = Path(task.source.image_path).suffix.lower() or ".img"
            source_path = self.store.path(f"sources/{task.source.source_id}{suffix}")
            source_type = mimetypes.guess_type(source_path.name)[0] or "application/octet-stream"
            self.source_media[task_id] = MediaRecord(
                path=source_path,
                media_type=source_type,
                sha256=task.source.sha256,
                filename=f"{task.source.source_id}{suffix}",
            )
        for record_path in sorted(self.run_dir.glob("candidates/*/*/candidate.json")):
            candidate = CandidateRecord.model_validate_json(record_path.read_text(encoding="utf-8"))
            if candidate.output is None:
                continue
            self.candidate_media[candidate.candidate_id] = MediaRecord(
                path=self.store.path(candidate.output.relative_path),
                media_type=candidate.output.media_type,
                sha256=candidate.output.sha256,
                filename=f"{candidate.task_id}--{candidate.method_id}.png",
            )

    def _index_pre_review(self, requested_run_id: str | None) -> None:
        if requested_run_id is not None:
            validate_id(requested_run_id)
            candidates = [self.run_dir / "agent-runs" / requested_run_id]
        else:
            candidates = sorted(
                (path.parent for path in self.run_dir.glob("agent-runs/*/agent-run.json")),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        for base in candidates:
            manifest_path = base / "agent-run.json"
            if not manifest_path.is_file():
                if requested_run_id is not None:
                    raise ValueError(f"large-model pre-review does not exist: {requested_run_id}")
                continue
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("source_run_id") != self.manifest.run_id:
                if requested_run_id is not None:
                    raise ValueError("large-model pre-review belongs to a different Run")
                continue
            if set(manifest.get("task_ids", ())) != set(self.manifest.task_ids):
                if requested_run_id is not None:
                    raise ValueError("large-model pre-review denominator is incomplete")
                continue
            decisions: dict[str, dict[str, Any]] = {}
            for task_id in self.manifest.task_ids:
                path = base / "decisions" / f"{task_id}.json"
                if not path.is_file():
                    decisions = {}
                    break
                decision = json.loads(path.read_text(encoding="utf-8"))
                if decision.get("task_id") != task_id:
                    decisions = {}
                    break
                decisions[task_id] = decision
            if decisions:
                self.pre_review_run_id = str(manifest["agent_run_id"])
                self.pre_review_decisions = decisions
                return
        if requested_run_id is not None:
            raise ValueError("large-model pre-review has missing or invalid decisions")

    def ready_state(self) -> dict[str, Any]:
        missing = [
            record.filename
            for record in (*self.source_media.values(), *self.candidate_media.values())
            if not record.path.is_file()
        ]
        return {
            "status": "ready" if not missing else "degraded",
            "run_id": self.manifest.run_id,
            "run_status": self.manifest.status,
            "task_count": len(self.manifest.task_ids),
            "candidate_media_count": len(self.candidate_media),
            "missing_media_count": len(missing),
        }

    def workspace(self, reviewer_id: str) -> dict[str, Any]:
        raw = self.service.load_review_workspace(self.run_dir, reviewer_id)
        tasks: list[dict[str, Any]] = []
        for item in raw["tasks"]:
            task = item["task"]
            source = task["source"]
            target = task["target"]
            decision = item["decision"]
            candidates = []
            for candidate in item["candidates"]:
                candidate_id = candidate["candidate_id"]
                media_exists = candidate_id in self.candidate_media
                candidates.append(
                    {
                        "candidate_id": candidate_id,
                        "method_id": candidate["method_id"],
                        "method_version": candidate["method_version"],
                        "generation_status": candidate["generation_status"],
                        "target_width": candidate["target_width"],
                        "target_height": candidate["target_height"],
                        "error_summary": candidate["error_summary"],
                        "warnings": candidate["warnings"],
                        "image_url": (
                            f"/v1/media/candidates/{candidate_id}" if media_exists else None
                        ),
                        "download_url": (
                            f"/v1/media/candidates/{candidate_id}?download=true"
                            if media_exists
                            else None
                        ),
                        "review": candidate["review"],
                    }
                )
            tasks.append(
                {
                    "task_id": task["task_id"],
                    "source": {
                        "source_id": source["source_id"],
                        "width": source["width"],
                        "height": source["height"],
                        "scene_category": source["scene_category"],
                    },
                    "target": {
                        "target_id": target["target_id"],
                        "width": target["width"],
                        "height": target["height"],
                        "format": target["format"],
                    },
                    "source_url": f"/v1/media/sources/{task['task_id']}",
                    "source_download_url": (f"/v1/media/sources/{task['task_id']}?download=true"),
                    "technical_top_candidate_id": decision["best_candidate_id"],
                    "selector_id": decision["selector_id"],
                    "pre_review": self._pre_review_payload(task["task_id"], candidates),
                    "candidates": candidates,
                }
            )
        return {
            "run_id": raw["run_id"],
            "run_status": self.manifest.status,
            "reviewer_id": raw["reviewer_id"],
            "task_count": raw["task_count"],
            "completed_task_count": raw["completed_task_count"],
            "failure_reasons": raw["failure_reasons"],
            "review_dimensions": raw["review_dimensions"],
            "pre_review_available": bool(self.pre_review_decisions),
            "tasks": tasks,
        }

    def _pre_review_payload(
        self,
        task_id: str,
        candidates: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        decision = self.pre_review_decisions.get(task_id)
        if decision is None:
            return None
        candidate_ids = {item["candidate_id"] for item in candidates}
        selected = decision.get("selected_candidate_id")
        if selected is not None and selected not in candidate_ids:
            return None
        return {
            "selected_candidate_id": selected,
            "candidate_ranking": [
                item for item in decision.get("candidate_ranking", ()) if item in candidate_ids
            ],
            "agent_called": bool(decision.get("agent_called")),
            "changed_top1": bool(decision.get("changed_top1")),
            "route_action": decision.get("route_action"),
            "reason_codes": list(decision.get("reason_codes", ())),
        }

    def save_reviews(self, submission: ReviewSubmissionRequest) -> dict[str, Any]:
        saved = self.service.save_task_reviews(
            self.run_dir,
            submission.reviewer_id,
            submission.task_id,
            [item.model_dump(mode="json") for item in submission.reviews],
        )
        return {
            "run_id": self.manifest.run_id,
            "task_id": submission.task_id,
            "reviewer_id": submission.reviewer_id,
            "saved_count": len(saved),
            "events": saved,
        }

    def media(self, kind: str, resource_id: str) -> MediaRecord:
        records = self.source_media if kind == "source" else self.candidate_media
        record = records.get(resource_id)
        if record is None or not record.path.is_file():
            raise ReviewResourceNotFound(resource_id)
        return record


def _media_response(record: MediaRecord, *, download: bool) -> FileResponse:
    disposition = "attachment" if download else "inline"
    return FileResponse(
        record.path,
        media_type=record.media_type,
        headers={
            "Cache-Control": "private, max-age=31536000, immutable",
            "ETag": f'"{record.sha256}"',
            "Content-Disposition": f'{disposition}; filename="{record.filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


def create_review_app(
    run_dir: Path,
    *,
    service: RetargetApplicationService | None = None,
    agent_run_id: str | None = None,
) -> FastAPI:
    """Create a same-origin local web app scoped to exactly one frozen Run."""
    module = ReviewWebModule(
        run_dir,
        service or RetargetApplicationService.default(),
        agent_run_id=agent_run_id,
    )
    web_root = Path(__file__).with_name("web")
    app = FastAPI(
        title="Retarget Engine Review API",
        version="1.0.0",
        docs_url="/api/docs",
        redoc_url=None,
    )
    app.state.review_module = module
    app.mount("/assets", StaticFiles(directory=web_root), name="review-assets")

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

    @app.exception_handler(ReviewResourceNotFound)
    async def resource_not_found(
        request: Request,
        error: ReviewResourceNotFound,
    ) -> JSONResponse:
        del request, error
        return JSONResponse(status_code=404, content={"detail": "resource not found"})

    @app.exception_handler(ValueError)
    async def domain_validation_error(request: Request, error: ValueError) -> JSONResponse:
        del request
        return JSONResponse(status_code=422, content={"detail": str(error)})

    @app.exception_handler(RequestValidationError)
    async def request_validation_error(
        request: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
        del request
        return JSONResponse(status_code=422, content={"detail": error.errors()})

    @app.exception_handler(HTTPException)
    async def known_http_error(request: Request, error: HTTPException) -> Any:
        return await http_exception_handler(request, error)

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(web_root / "index.html", media_type="text/html")

    @app.get("/health/live")
    def health_live() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/health/ready")
    def health_ready() -> dict[str, Any]:
        return module.ready_state()

    @app.get("/v1/review-workspace")
    def review_workspace(
        reviewer_id: Annotated[str, Query(min_length=1, max_length=80)] = "local-reviewer",
    ) -> dict[str, Any]:
        return module.workspace(reviewer_id)

    @app.post("/v1/reviews")
    def save_reviews(submission: ReviewSubmissionRequest) -> dict[str, Any]:
        return module.save_reviews(submission)

    @app.get("/v1/media/sources/{task_id}", include_in_schema=False)
    def source_media(task_id: str, download: bool = False) -> FileResponse:
        return _media_response(module.media("source", task_id), download=download)

    @app.get("/v1/media/candidates/{candidate_id}", include_in_schema=False)
    def candidate_media(candidate_id: str, download: bool = False) -> FileResponse:
        return _media_response(module.media("candidate", candidate_id), download=download)

    return app
