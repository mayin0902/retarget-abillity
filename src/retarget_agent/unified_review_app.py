"""One FastAPI frontend for every supported review workspace adapter."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import HTTPException, RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .review_workspace import ReviewSubmission, ReviewWorkspaceAdapter, open_review_workspace


def _media_response(
    adapter: ReviewWorkspaceAdapter,
    mode: str,
    task_id: str,
    media_id: str,
) -> FileResponse:
    try:
        media = adapter.media(mode, task_id, media_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return FileResponse(
        media.path,
        media_type=media.media_type,
        headers={"ETag": f'"{media.sha256}"', "Cache-Control": "private, max-age=3600"},
    )


def create_unified_review_app(
    workspace_dir: Path,
    *,
    evaluation_id: str | None = None,
    agent_run_id: str | None = None,
) -> FastAPI:
    """Create the unchanged Movie60 frontend over an auto-detected workspace adapter."""
    adapter = open_review_workspace(
        workspace_dir,
        evaluation_id=evaluation_id,
        agent_run_id=agent_run_id,
    )
    static_dir = Path(__file__).with_name("web_movie60")
    app = FastAPI(
        title="Retarget Engine Human Review",
        version="1.0",
        docs_url="/api/docs",
        redoc_url=None,
    )
    app.mount("/assets", StaticFiles(directory=static_dir), name="assets")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
            "base-uri 'none'; frame-ancestors 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.exception_handler(ValueError)
    async def value_error(_request: Request, error: ValueError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(error)})

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, error: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": error.errors()})

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(static_dir / "index.html", media_type="text/html")

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/health/ready")
    def ready() -> dict[str, object]:
        return adapter.ready()

    @app.get("/v1/workspace")
    def workspace(
        mode: Annotated[str, Query(pattern=r"^[a-z][a-z0-9_-]{0,31}$")],
    ) -> dict[str, object]:
        return adapter.workspace(mode)

    @app.post("/v1/reviews")
    def save(submission: ReviewSubmission) -> dict[str, object]:
        return adapter.save(submission)

    @app.get("/v1/media/{mode}/{task_id}/{media_id}", include_in_schema=False)
    def media(
        mode: str,
        task_id: str,
        media_id: str,
    ) -> FileResponse:
        return _media_response(adapter, mode, task_id, media_id)

    return app
