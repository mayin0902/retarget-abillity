from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
from PIL import Image

from retarget_agent.hashing import sha256_file
from retarget_agent.models import (
    ArtifactRef,
    CandidateRecord,
    GenerationStatus,
    RunManifest,
    SourceRecord,
    TargetSpec,
    TaskSpec,
)
from retarget_agent.review_workspace import (
    RunReviewAdapter,
    import_review_case,
    latest_completed_run,
)
from retarget_agent.unified_review_app import create_unified_review_app


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

    def get(self, path: str) -> httpx.Response:
        return self.request("GET", path)

    def post(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", path, **kwargs)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _run(root: Path, *, status: str = "COMPLETED") -> Path:
    run = root / "demo-run"
    task_id = "source-1__square-64"
    candidate_id = f"{task_id}--crop--default"
    source_path = run / "sources" / "source-1.png"
    output_path = run / "candidates" / task_id / "crop" / "output.png"
    source_path.parent.mkdir(parents=True)
    output_path.parent.mkdir(parents=True)
    Image.new("RGB", (96, 64), "navy").save(source_path)
    Image.new("RGB", (64, 64), "navy").save(output_path)
    source = SourceRecord(
        source_id="source-1",
        image_path="images/source-1.png",
        width=96,
        height=64,
        sha256=sha256_file(source_path),
        split="validation",
        scene_category="poster",
    )
    task = TaskSpec(
        dataset_id="demo-dataset",
        task_id=task_id,
        source=source,
        target=TargetSpec(target_id="square-64", width=64, height=64),
    )
    _write_json(run / "tasks" / f"{task_id}.json", task.model_dump(mode="json"))
    output = ArtifactRef(
        relative_path=output_path.relative_to(run).as_posix(),
        sha256=sha256_file(output_path),
        media_type="image/png",
        width=64,
        height=64,
    )
    candidate = CandidateRecord(
        candidate_id=candidate_id,
        task_id=task_id,
        method_id="crop",
        method_version="1.0.0",
        variant_id="default",
        run_id="demo-run",
        input_sha256=source.sha256,
        output=output,
        target_width=64,
        target_height=64,
        seed=1,
        config_hash="a" * 64,
        analysis_artifact_id="analysis-1",
        generation_status=GenerationStatus.SUCCESS,
    )
    _write_json(
        output_path.with_name("candidate.json"),
        candidate.model_dump(mode="json"),
    )
    manifest = RunManifest(
        run_id="demo-run",
        dataset_id="demo-dataset",
        dataset_fingerprint="b" * 64,
        status=status,
        methods=("crop",),
        config_hash="a" * 64,
        config_snapshot="config/run.yaml",
        code_version="test",
        python_version="3.13",
        dependency_versions={},
        task_ids=(task_id,),
        candidate_ids=(candidate_id,),
    )
    _write_json(run / "run.json", manifest.model_dump(mode="json"))
    evaluation = run / "evaluations" / "rule-current"
    _write_json(
        evaluation / "evaluation.json",
        {
            "evaluation_id": "rule-current",
            "strategy_version": "3.3.0",
        },
    )
    _write_json(
        evaluation / "metrics" / f"{candidate_id}.json",
        {
            "candidate_id": candidate_id,
            "metrics": {
                "quality_score": 88.5,
                "proxy_grade": "proxy_a",
                "content_fidelity_score": 0.95,
                "visual_integrity_score": None,
                "composition_score": 0.87,
            },
        },
    )
    return run


def test_run_adapter_saves_sidecars_without_mutating_run_manifest(tmp_path: Path) -> None:
    run = _run(tmp_path)
    original_manifest = (run / "run.json").read_bytes()
    adapter = RunReviewAdapter(run)
    assert adapter.ready()["candidate_count"] == 1
    app = create_unified_review_app(run)
    client = ASGITestClient(app)
    workspace = client.get("/v1/workspace?mode=all").json()
    assert workspace["tasks"][0]["candidates"][0]["machine_score"] == 88.5
    assert "视觉完整 未观测" in workspace["tasks"][0]["candidates"][0]["rule_reason"]

    response = client.post(
        "/v1/reviews",
        json={
            "mode": "all",
            "reviewer_id": "developer-1",
            "task_id": "source-1__square-64",
            "reviews": [
                {
                    "route": "crop",
                    "grade": "A",
                    "reason": "主体与文字均自然完整，可直接使用。",
                    "issue_codes": [],
                }
            ],
        },
    )
    assert response.status_code == 200
    assert (run / "reviews" / "human-review-current.csv").is_file()
    assert (run / "reviews" / "human-review-events.jsonl").is_file()
    assert (run / "reviews" / "review-status.json").is_file()
    assert (run / "run.json").read_bytes() == original_manifest


def test_latest_completed_run_uses_status_field(tmp_path: Path) -> None:
    completed = _run(tmp_path)
    assert latest_completed_run(tmp_path) == completed.resolve()


def test_run_adapter_keeps_failed_method_in_denominator(tmp_path: Path) -> None:
    run = _run(tmp_path)
    task_id = "source-1__square-64"
    failed_id = f"{task_id}--seam_full--default"
    failed = CandidateRecord(
        candidate_id=failed_id,
        task_id=task_id,
        method_id="seam_full",
        method_version="1.0.0",
        variant_id="default",
        run_id="demo-run",
        input_sha256="0" * 64,
        output=None,
        target_width=64,
        target_height=64,
        seed=1,
        config_hash="a" * 64,
        analysis_artifact_id="analysis-1",
        generation_status=GenerationStatus.FAILED,
        failure_type="RuntimeError",
        error_summary="fixture failure",
    )
    _write_json(
        run / "candidates" / task_id / "seam_full" / "candidate.json",
        failed.model_dump(mode="json"),
    )
    _write_json(
        run / "evaluations" / "rule-current" / "metrics" / f"{failed_id}.json",
        {
            "candidate_id": failed_id,
            "metrics": {
                "technical_valid": False,
                "hard_failures": "candidate_output_missing",
                "quality_score": None,
                "proxy_grade": "proxy_c",
            },
        },
    )
    manifest = json.loads((run / "run.json").read_text(encoding="utf-8"))
    manifest["methods"] = ["crop", "seam_full"]
    manifest["candidate_ids"].append(failed_id)
    manifest["failed_candidate_ids"] = [failed_id]
    manifest["status"] = "PARTIAL_COMPLETED"
    _write_json(run / "run.json", manifest)

    workspace = RunReviewAdapter(run).workspace("all")
    candidates = workspace["tasks"][0]["candidates"]
    assert len(candidates) == 2
    assert all(candidate["rule_denominator"] == 2 for candidate in candidates)
    failed_view = next(item for item in candidates if item["method"] == "seam_full")
    assert failed_view["available"] is False
    assert failed_view["machine_grade"] == "N/A"
    assert failed_view["machine_score"] is None
    assert failed_view["failure_type"] == "RuntimeError"
    assert "生成失败" in failed_view["status_text"]


def test_imported_case_is_frozen_and_reviewable_without_fake_scores(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "outside-case"
    source = source_dir / "source.jpg"
    crop = source_dir / "candidates" / "crop.png"
    generated = source_dir / "candidates" / "generated.png"
    crop.parent.mkdir(parents=True)
    Image.new("RGB", (96, 64), "red").save(source)
    Image.new("RGB", (64, 64), "blue").save(crop)
    Image.new("RGB", (64, 64), "green").save(generated)
    original_hashes = {path: sha256_file(path) for path in (source, crop, generated)}

    workspace_dir = tmp_path / "imported"
    result = import_review_case(source_dir, workspace_dir)
    assert result["candidate_count"] == 2
    client = ASGITestClient(create_unified_review_app(workspace_dir))
    response = client.get("/v1/workspace?mode=all")
    assert response.status_code == 200
    candidates = response.json()["tasks"][0]["candidates"]
    assert {candidate["method"] for candidate in candidates} == {"crop", "generated"}
    assert all(candidate["machine_grade"] == "N/A" for candidate in candidates)
    assert all("未运行" in candidate["status_text"] for candidate in candidates)
    assert {path: sha256_file(path) for path in original_hashes} == original_hashes
