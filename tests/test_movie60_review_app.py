from __future__ import annotations

import asyncio
import csv
from pathlib import Path
from typing import Any

import httpx
from PIL import Image

from retarget_agent.movie60_review_app import create_movie60_review_app


class ASGITestClient:
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


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _image(path: Path, color: tuple[int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 48), color).save(path)


def _workspace(tmp_path: Path) -> Path:
    root = tmp_path / "movie60-review"
    all_task = "poster_001__square-1536"
    _write_csv(
        root / "all60" / "summary.csv",
        [
            {
                "task_id": all_task,
                "phase": "calibration",
                "scene_category": "movie_poster",
                "final_method": "seam",
                "final_grade": "C",
                "passed_ab": "False",
                "agent_overrode_rule": "False",
                "aigc_requested": "True",
                "wall_seconds": "12.5",
            }
        ],
    )
    _write_csv(
        root / "all60" / "review.csv",
        [
            {
                "task_id": all_task,
                "phase": "calibration",
                "scene_category": "movie_poster",
                "machine_method": "seam",
                "machine_grade": "C",
                "human_grade": "",
                "human_reason": "",
                "human_confirmed": "false",
            }
        ],
    )
    methods = [
        "seam",
        "crop",
        "direct_warp",
        "seam_full",
        "seam_scale",
        "mesh",
        "mesh_full",
    ]
    candidate_rows = []
    for rank, method in enumerate(methods, 1):
        candidate_rows.append(
            {
                "task_id": all_task,
                "phase": "calibration",
                "scene_category": "movie_poster",
                "candidate_id": f"{all_task}--{method}--test",
                "method": method,
                "image_sha256": "0" * 64,
                "rule_rank": str(rank),
                "rule_quality": str(90 - rank),
                "rule_grade": "A" if rank == 1 else "B",
                "rule_reason": f"Rule依据：{method} 指标完整",
                "rule_ocr_recall": "0.95",
                "rule_person_preservation": "1.0",
                "rule_face_preservation": "1.0",
                "rule_product_preservation": "",
                "rule_logo_preservation": "",
                "agent_rank": str(8 - rank),
                "agent_role": "Rule Top1" if rank == 1 else "普通候选",
                "agent_review_scope": "高清单图复核" if rank == 1 else "七候选总览排序",
                "agent_grade": "A" if rank == 1 else "",
                "agent_directly_usable": "true" if rank == 1 else "",
                "agent_confidence": "0.9" if rank == 1 else "",
                "agent_reason": f"Agent依据：{method} 视觉判断",
                "agent_reason_codes": "rule_retained_no_clear_gain",
                "agent_reason_codes_zh": "没有明确增益，保留Rule选择",
                "final_selected": str(rank == 1).lower(),
                "model_advice_grade": "B" if rank == 1 else "",
                "model_advice_reason": "大模型建议理由" if rank == 1 else "",
                "model_advice_scope": "高清复核" if rank == 1 else "待高清复核",
                "model_advice_source": "test" if rank == 1 else "",
                "human_grade": "",
                "human_reason": "",
                "human_issue_codes": "",
                "human_confirmed": "false",
                "reviewer_id": "",
                "updated_at": "",
            }
        )
    _write_csv(root / "all60" / "candidate-review.csv", candidate_rows)
    all_dir = root / "all60" / "tasks" / all_task
    _image(all_dir / "00_source.jpg", (200, 50, 50))
    _image(all_dir / "01_final.png", (50, 200, 50))
    _image(all_dir / "02_comparison.jpg", (50, 50, 200))
    for index, method in enumerate(methods):
        _image(all_dir / "candidates" / f"{method}.png", (20 * index, 100, 150))

    success = "still_001__square-1536"
    failed = "video_cover_001__square-1536"
    codex_rows = []
    review_rows = []
    status_rows = []
    for task_id, status in ((success, "success"), (failed, "failed")):
        codex_rows.append(
            {
                "task_id": task_id,
                "split": "validation",
                "rule_method": "seam",
                "old_rule_grade": "C",
                "codex_rule_grade": "B",
                "agent_method": "crop",
                "old_agent_grade": "C",
                "codex_agent_grade": "C",
                "aigc_status": status,
                "old_aigc_grade": "C",
                "codex_aigc_grade": "A" if status == "success" else "N/A",
                "codex_reason": "review suggestion",
            }
        )
        review_rows.append(
            {
                "task_id": task_id,
                "split": "validation",
                "codex_rule_grade": "B",
                "human_rule_grade": "",
                "human_rule_reason": "",
                "codex_agent_grade": "C",
                "human_agent_grade": "",
                "human_agent_reason": "",
                "aigc_status": status,
                "codex_aigc_grade": "A" if status == "success" else "N/A",
                "human_aigc_grade": "",
                "human_aigc_reason": "",
                "human_confirmed": "false",
            }
        )
        status_rows.append(
            {
                "task_id": task_id,
                "status": status,
                "error_code": "" if status == "success" else "TIMEOUT",
                "failure_meaning": "成功返回图片" if status == "success" else "等待API超时",
                "wall_seconds": "100.0",
                "charge_may_have_occurred": "True",
                "estimated_cost_min_cny": "0.30",
                "estimated_cost_max_cny": "0.60",
                "evaluation_image": "03_aigc_seedream.png" if status == "success" else "",
                "native_2k_image": "04_aigc_native_2k.jpg" if status == "success" else "",
            }
        )
        task_dir = root / "focus20" / "tasks" / task_id
        _image(task_dir / "00_source.jpg", (220, 220, 220))
        _image(task_dir / "01_rule_seam.png", (200, 100, 50))
        _image(task_dir / "02_agent_crop.png", (50, 100, 200))
        _image(task_dir / "collage.jpg", (100, 100, 100))
        if status == "success":
            _image(task_dir / "03_aigc_seedream.png", (100, 200, 100))
            _image(task_dir / "04_aigc_native_2k.jpg", (100, 210, 100))
    _write_csv(root / "focus20" / "codex.csv", codex_rows)
    _write_csv(root / "focus20" / "review.csv", review_rows)
    _write_csv(root / "focus20" / "aigc-status.csv", status_rows)
    return root


def test_movie60_ui_serves_both_modes_and_indexed_media(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    client = ASGITestClient(create_movie60_review_app(root))
    assert client.get("/").status_code == 200
    assert "人工质量评审" in client.get("/").text
    assert client.get("/assets/app.css").status_code == 200
    ready = client.get("/health/ready").json()
    assert ready["all60_task_count"] == 1
    assert ready["all60_candidate_count"] == 7

    all60 = client.get("/v1/workspace", params={"mode": "all60"})
    assert all60.status_code == 200
    task = all60.json()["tasks"][0]
    assert len(task["candidates"]) == 7
    assert task["candidates"][0]["route"] == "seam"
    assert task["candidates"][0]["machine_score"] == 89.0
    assert task["candidates"][0]["agent_grade"] == "A"
    assert task["candidates"][0]["model_advice_grade"] == "B"
    assert "Rule依据" in task["candidates"][0]["rule_reason"]
    assert "Agent依据" in task["candidates"][0]["agent_reason"]
    assert task["candidates"][0]["agent_reason_codes"] == ["没有明确增益，保留Rule选择"]
    assert str(tmp_path) not in all60.text
    assert client.get(task["source_url"]).headers["content-type"].startswith("image/")
    assert client.get("/v1/media/all60/not-a-task/source").status_code == 422

    focus = client.get("/v1/workspace", params={"mode": "focus20"}).json()
    assert len(focus["tasks"]) == 2
    failed = next(item for item in focus["tasks"] if item["task_id"].startswith("video"))
    aigc = next(item for item in failed["candidates"] if item["route"] == "aigc")
    assert aigc["available"] is False
    assert aigc["machine_grade"] == "N/A"
    assert "TIMEOUT" in aigc["status_text"]


def test_movie60_ui_saves_reasons_and_resumes(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    client = ASGITestClient(create_movie60_review_app(root))
    all_task = client.get("/v1/workspace", params={"mode": "all60"}).json()["tasks"][0]
    submission = {
        "mode": "all60",
        "reviewer_id": "human-reviewer",
        "task_id": all_task["task_id"],
        "reviews": [
            {
                "route": candidate["route"],
                "grade": "A" if candidate["route"] == "seam" else "B",
                "reason": f"{candidate['route']}主体与文字完整，轻微变化不影响上传",
                "issue_codes": ["directly_usable", "minor_nonblocking_issue"],
            }
            for candidate in all_task["candidates"]
        ],
    }
    assert client.post("/v1/reviews", json=submission).status_code == 200
    resumed = client.get("/v1/workspace", params={"mode": "all60"}).json()
    assert resumed["completed_task_count"] == 1
    review = resumed["tasks"][0]["candidates"][0]["review"]
    assert review["grade"] == "A"
    assert "不影响上传" in review["reason"]
    saved = _read_csv(root / "all60" / "candidate-review.csv")[0]
    assert saved["reviewer_id"] == "human-reviewer"
    assert saved["human_issue_codes"] == "directly_usable;minor_nonblocking_issue"

    invalid = {
        **submission,
        "reviews": [{**submission["reviews"][0], "reason": ""}],
    }
    assert client.post("/v1/reviews", json=invalid).status_code == 422


def test_focus20_requires_only_available_routes(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    client = ASGITestClient(create_movie60_review_app(root))
    focus = client.get("/v1/workspace", params={"mode": "focus20"}).json()
    failed = next(item for item in focus["tasks"] if item["task_id"].startswith("video"))
    payload = {
        "mode": "focus20",
        "reviewer_id": "human-reviewer",
        "task_id": failed["task_id"],
        "reviews": [
            {"route": "rule", "grade": "B", "reason": "轻微拉伸但可上传"},
            {"route": "agent", "grade": "C", "reason": "关键标题被裁掉，需要返工"},
        ],
    }
    assert client.post("/v1/reviews", json=payload).status_code == 200
    payload["reviews"].append({"route": "aigc", "grade": "A", "reason": "实际上没有图片，不能评分"})
    rejected = client.post("/v1/reviews", json=payload)
    assert rejected.status_code == 422
    assert "all available routes" in rejected.json()["detail"]
