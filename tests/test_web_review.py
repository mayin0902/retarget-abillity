from __future__ import annotations

import asyncio
import csv
from pathlib import Path
from typing import Any

import httpx
import yaml

from retarget_agent.config import RunConfig
from retarget_agent.fixtures import materialize_fixture_dataset
from retarget_agent.runner import GenerationRunner
from retarget_agent.web_app import create_review_app


class ASGITestClient:
    """Small synchronous test adapter over httpx's in-process ASGI transport."""

    def __init__(self, app: Any) -> None:
        self.app = app

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        async def send() -> httpx.Response:
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                return await client.request(method, path, **kwargs)

        return asyncio.run(send())

    def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", path, **kwargs)


def _review_run(tmp_path: Path) -> Path:
    dataset = materialize_fixture_dataset(tmp_path / "dataset", source_limit=1)
    tasks_path = dataset / "tasks.csv"
    with tasks_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))[:1]
    with tasks_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    raw = {
        "dataset_root": str(dataset),
        "output_root": str(tmp_path / "runs"),
        "run_id": "web-review-run",
        "method_parameters": {"seam_full": {"proxy_long_edge": 96}},
    }
    config_path = tmp_path / "run.yaml"
    config_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    GenerationRunner.default().run(RunConfig.model_validate(raw), config_path)
    return tmp_path / "runs" / "web-review-run"


def _submission(workspace: dict[str, object]) -> dict[str, object]:
    task = workspace["tasks"][0]
    grades = ["A", "B", "C", "D", "Skip", "A", "B"]
    return {
        "reviewer_id": "web-reviewer",
        "task_id": task["task_id"],
        "reviews": [
            {
                "candidate_id": candidate["candidate_id"],
                "grade": grade,
                "is_best": index == 0,
                "failure_reasons": ["content_cutoff"] if grade in {"B", "C", "D"} else [],
                "dimension_grades": (
                    [
                        {
                            "dimension": item["dimension"],
                            "grade": grade,
                            "reason_codes": [],
                        }
                        for item in workspace["review_dimensions"]
                    ]
                    if grade != "Skip"
                    else []
                ),
                "note": "browser checked",
                "display_order": index,
            }
            for index, (candidate, grade) in enumerate(zip(task["candidates"], grades, strict=True))
        ],
    }


def test_web_app_serves_frontend_health_and_openapi(tmp_path: Path) -> None:
    run_dir = _review_run(tmp_path)
    client = ASGITestClient(create_review_app(run_dir))
    index = client.get("/")
    assert index.status_code == 200
    assert "图片重定向评审" in index.text
    assert "default-src 'self'" in index.headers["content-security-policy"]
    assert client.get("/assets/app.css").status_code == 200
    assert client.get("/assets/app.js").status_code == 200
    assert client.get("/health/live").json() == {"status": "alive"}
    ready = client.get("/health/ready").json()
    assert ready["status"] == "ready"
    assert ready["task_count"] == 1
    schema = client.get("/openapi.json").json()
    assert "/v1/review-workspace" in schema["paths"]
    assert "/v1/reviews" in schema["paths"]


def test_workspace_hides_paths_and_serves_only_indexed_media(tmp_path: Path) -> None:
    run_dir = _review_run(tmp_path)
    client = ASGITestClient(create_review_app(run_dir))
    response = client.get("/v1/review-workspace", params={"reviewer_id": "web-reviewer"})
    assert response.status_code == 200
    workspace = response.json()
    serialized = response.text
    assert str(tmp_path) not in serialized
    assert "source_path" not in serialized
    assert "image_path" not in serialized
    task = workspace["tasks"][0]
    assert task["source_url"].startswith("/v1/media/sources/")
    assert all(
        candidate["image_url"].startswith("/v1/media/candidates/")
        for candidate in task["candidates"]
    )

    source = client.get(task["source_url"])
    assert source.status_code == 200
    assert source.headers["content-type"].startswith("image/")
    assert len(source.headers["etag"]) == 66
    candidate = client.get(task["candidates"][0]["image_url"])
    assert candidate.status_code == 200
    assert candidate.headers["content-disposition"].startswith("inline;")
    download = client.get(task["candidates"][0]["download_url"])
    assert download.headers["content-disposition"].startswith("attachment;")
    assert client.get("/v1/media/candidates/not-in-this-run").status_code == 404
    assert client.get("/v1/media/sources/not-in-this-run").status_code == 404


def test_web_review_save_resume_and_domain_validation(tmp_path: Path) -> None:
    run_dir = _review_run(tmp_path)
    client = ASGITestClient(create_review_app(run_dir))
    workspace = client.get("/v1/review-workspace", params={"reviewer_id": "web-reviewer"}).json()
    submission = _submission(workspace)
    saved = client.post("/v1/reviews", json=submission)
    assert saved.status_code == 200
    assert saved.json()["saved_count"] == 7
    resumed = client.get("/v1/review-workspace", params={"reviewer_id": "web-reviewer"}).json()
    assert resumed["completed_task_count"] == 1
    assert [item["review"]["grade"] for item in resumed["tasks"][0]["candidates"]] == [
        "A",
        "B",
        "C",
        "D",
        "Skip",
        "A",
        "B",
    ]

    invalid = _submission(workspace)
    invalid["reviews"][2]["failure_reasons"] = []
    rejected = client.post("/v1/reviews", json=invalid)
    assert rejected.status_code == 422
    assert "requires at least one issue reason" in rejected.json()["detail"]
    bad_id = client.get("/v1/review-workspace", params={"reviewer_id": "../escape"})
    assert bad_id.status_code == 422
