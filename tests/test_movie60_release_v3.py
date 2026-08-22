from __future__ import annotations

import csv
import json
import zipfile
from pathlib import Path

from retarget_agent.movie60_release import (
    CURRENT_EVIDENCE_DIR,
    HUMAN_REVIEW_FIELDS,
    METHODS,
    _human_review_snapshot,
    validate_movie60_review_v3,
)
from scripts.materialize_movie60_release import asset_names
from scripts.release_packaging import Entry, write_release_zip


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_validate_v3_checks_exact_current_denominators(tmp_path: Path) -> None:
    root = tmp_path / "movie60-review-v3"
    version = {
        "release_id": "movie60-review-v3",
        "dataset_version": "1.0.0",
        "schema_version": "1.1",
        "evaluation_id": "movie60-human-aligned-v3-3-20260821",
        "strategy_version": "3.3.0",
        "strategy_sha256": "b09353f51bd65fd376269bbbe3196269f0276a2445a7f01f3ea71915d9fa8792",
        "task_count": 60,
        "candidate_count": 420,
    }
    root.mkdir()
    (root / "VERSION.json").write_text(json.dumps(version), encoding="utf-8")
    summary = []
    candidates = []
    for index in range(60):
        task_id = f"poster_{index:03d}__square-1536"
        summary.append({"task_id": task_id})
        task = root / "all60" / "tasks" / task_id
        (task / "candidates").mkdir(parents=True)
        for name in ("00_source.jpg", "01_final.png", "02_comparison.jpg"):
            (task / name).write_bytes(b"fixture")
        evidence = task / "evidence" / CURRENT_EVIDENCE_DIR
        evidence.mkdir(parents=True)
        for name in ("decision.json", "overview-decision.json", "rule-ranking.json"):
            (evidence / name).write_text("{}", encoding="utf-8")
        for method in METHODS:
            (task / "candidates" / f"{method}.png").write_bytes(b"fixture")
            candidates.append(
                {
                    "task_id": task_id,
                    "method": method,
                    "candidate_id": f"{task_id}--{method}--v1",
                    "image_sha256": f"sha-{index}-{method}",
                    "strategy_version": "movie60@3.3.0",
                    "evaluation_id": "movie60-human-aligned-v3-3-20260821",
                    "human_grade": "A" if index == 0 else "",
                    "human_reason": "人工确认可用" if index == 0 else "",
                    "human_issue_codes": "" if index == 0 else "",
                    "human_confirmed": "true" if index == 0 else "",
                    "reviewer_id": "human-reviewer" if index == 0 else "",
                    "updated_at": "2026-08-21T12:00:00+08:00" if index == 0 else "",
                }
            )
    human_review = _human_review_snapshot(candidates)
    version.update(
        {
            "human_reviewed_candidate_count": human_review["candidate_count"],
            "human_reviewed_task_count": human_review["task_count"],
            "human_review_grade_counts": human_review["grade_counts"],
            "human_review_sha256": human_review["sha256"],
        }
    )
    (root / "VERSION.json").write_text(json.dumps(version), encoding="utf-8")
    _write_csv(root / "all60" / "summary.csv", summary)
    _write_csv(root / "all60" / "candidate-review.csv", candidates)
    human_rows = [
        {field: row.get(field, "") for field in HUMAN_REVIEW_FIELDS}
        for row in candidates
        if row.get("human_grade") in {"A", "B", "C", "D"}
    ]
    _write_csv(root / "all60" / "human-review-current.csv", human_rows)
    (root / "all60" / "human-review-status.json").write_text(
        json.dumps(
            {
                "reviewed_candidate_count": human_review["candidate_count"],
                "reviewed_task_count": human_review["task_count"],
                "pending_candidate_count": 420 - human_review["candidate_count"],
                "grade_counts": human_review["grade_counts"],
                "sha256": human_review["sha256"],
            }
        ),
        encoding="utf-8",
    )
    for name in (
        "START_HERE.html",
        "01_CONFIGURE_PIP_MIRROR_FIRST.md",
        "INSTALL_WINDOWS.bat",
        "START_REVIEW.bat",
        "STOP_REVIEW.bat",
        "OPEN_RESULTS.bat",
        "_runtime/install_windows.ps1",
        "_runtime/stop_review.ps1",
        "_runtime/run_review_ui.py",
        "_runtime/src/retarget_agent/movie60_review_app.py",
    ):
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text("fixture", encoding="ascii")

    result = validate_movie60_review_v3(root)

    assert result == {
        "status": "valid",
        "release_id": "movie60-review-v3",
        "task_count": 60,
        "candidate_count": 420,
        "human_reviewed_candidate_count": 7,
        "human_reviewed_task_count": 1,
        "human_review_sha256": human_review["sha256"],
    }
    candidates[0]["human_reason"] = "被改写的理由"
    _write_csv(root / "all60" / "candidate-review.csv", candidates)
    try:
        validate_movie60_review_v3(root)
    except ValueError as error:
        assert "human review hash" in str(error)
    else:
        raise AssertionError("human-review tampering must fail validation")


def test_v3_uses_unambiguous_asset_names() -> None:
    assert asset_names("v3") == (
        "movie60-review-v3-core.zip",
        "movie60-review-v3-evidence.zip",
        "SHA256SUMS.txt",
    )


def test_windows_release_launcher_is_package_relative() -> None:
    template = Path("release_templates/movie60-review-v3")
    start = (template / "START_REVIEW.bat").read_text(encoding="utf-8")
    runner = (template / "_runtime/run_review_ui.py").read_text(encoding="utf-8")
    mirror = (template / "PIP_MIRROR.ini").read_text(encoding="utf-8")

    assert 'set "REVIEW_ROOT=%REVIEW_ROOT:~0,-1%"' in start
    assert "%REVIEW_ROOT%\\_runtime\\run_review_ui.py" in start
    assert "deliverables\\movie60-review" not in start
    assert "%~dp0" in start
    assert "--open-browser" in start
    assert "health/ready" in runner
    assert "INDEX_URL=\n" in mirror


def test_zip_writer_uses_explicit_v3_package_root(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("payload", encoding="utf-8")
    archive_path = tmp_path / "asset.zip"
    write_release_zip(
        archive_path,
        [Entry(source, Path("movie60-review-v3/source.txt"), "core")],
        {},
        "core-manifest.csv",
        package_root=Path("movie60-review-v3"),
    )

    with zipfile.ZipFile(archive_path) as archive:
        roots = {name.split("/")[0] for name in archive.namelist()}

    assert roots == {"movie60-review-v3"}
