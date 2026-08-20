"""Automated evidence for a frozen multi-method Generation Run contract."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from PIL import Image

from .hashing import sha256_file
from .models import CandidateRecord, DecisionRecord, GenerationStatus, RunManifest, TransformRecord


def _cross_task_outputs_are_distinct(
    by_method: dict[str, list[str]],
    declared_methods: tuple[str, ...],
    task_count: int,
) -> tuple[bool, list[str]]:
    passed = True
    evidence: list[str] = []
    for method_id in declared_methods:
        hashes = by_method[method_id]
        unique = len(set(hashes))
        method_ok = len(hashes) == task_count and unique == len(hashes)
        passed &= method_ok
        evidence.append(f"{method_id}:{unique}/{len(hashes)}")
    return passed, evidence


def audit_run_contract(run_dir: Path) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    items: list[dict[str, str]] = []

    def record(name: str, passed: bool, evidence: str) -> None:
        items.append({"check": name, "status": "PASS" if passed else "FAIL", "evidence": evidence})

    manifest_path = run_dir / "run.json"
    if not manifest_path.is_file():
        return {
            "status": "FAIL",
            "run_dir": str(run_dir),
            "checks": [{"check": "run_manifest", "status": "FAIL", "evidence": "missing run.json"}],
        }
    manifest = RunManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    declared_methods = tuple(manifest.methods)
    declared_method_set = set(declared_methods)
    record(
        "declared_method_set",
        bool(declared_methods) and len(declared_methods) == len(declared_method_set),
        repr(declared_methods),
    )
    record(
        "config_snapshot",
        (run_dir / manifest.config_snapshot).is_file(),
        manifest.config_snapshot,
    )
    record("event_store", (run_dir / "events.sqlite").is_file(), "events.sqlite")

    candidates: list[CandidateRecord] = []
    for path in sorted(run_dir.glob("candidates/*/*/candidate.json")):
        candidates.append(CandidateRecord.model_validate_json(path.read_text(encoding="utf-8")))
    expected_count = len(manifest.task_ids) * len(declared_methods)
    record(
        "candidate_budget",
        len(candidates) == expected_count,
        f"{len(candidates)}/{expected_count}",
    )

    grouped: dict[str, list[CandidateRecord]] = defaultdict(list)
    artifact_checks: list[bool] = []
    transform_checks: list[bool] = []
    for candidate in candidates:
        grouped[candidate.task_id].append(candidate)
        if candidate.output is not None:
            output_path = run_dir / candidate.output.relative_path
            valid = output_path.is_file() and sha256_file(output_path) == candidate.output.sha256
            if valid:
                with Image.open(output_path) as image:
                    valid = image.size == (candidate.target_width, candidate.target_height)
            artifact_checks.append(valid)
        else:
            artifact_checks.append(candidate.generation_status == GenerationStatus.FAILED)
        if candidate.transform is not None:
            transform_path = run_dir / candidate.transform.relative_path
            valid_transform = (
                transform_path.is_file()
                and sha256_file(transform_path) == candidate.transform.sha256
            )
            if valid_transform:
                transform = TransformRecord.model_validate_json(
                    transform_path.read_text(encoding="utf-8")
                )
                valid_transform = transform.method_id == candidate.method_id
            transform_checks.append(valid_transform)
        else:
            transform_checks.append(candidate.generation_status == GenerationStatus.FAILED)
    record("artifact_hash_and_dimensions", all(artifact_checks), f"checked={len(artifact_checks)}")
    record("transform_contract", all(transform_checks), f"checked={len(transform_checks)}")

    method_sets_ok = len(grouped) == len(manifest.task_ids) and all(
        len(records) == len(declared_methods)
        and {candidate.method_id for candidate in records} == declared_method_set
        for records in grouped.values()
    )
    record("declared_methods_per_task", method_sets_ok, f"task_groups={len(grouped)}")
    shared_analysis_ok = all(
        len({candidate.analysis_artifact_id for candidate in records}) == 1
        for records in grouped.values()
    )
    record("shared_analysis_per_task", shared_analysis_ok, f"task_groups={len(grouped)}")

    # Different algorithms may legitimately converge to identical pixels for an
    # already-square source.  A placeholder is instead evidenced by one method
    # returning the same pixels for different source tasks.
    by_method: dict[str, list[str]] = defaultdict(list)
    for candidate in candidates:
        if candidate.output is not None:
            by_method[candidate.method_id].append(candidate.output.sha256)
    distinct_output_ok, distinct_evidence = _cross_task_outputs_are_distinct(
        by_method, declared_methods, len(manifest.task_ids)
    )
    record("non_placeholder_outputs", distinct_output_ok, ", ".join(distinct_evidence))

    decision_checks: list[bool] = []
    for task_id, records in grouped.items():
        path = run_dir / "decisions" / f"{task_id}.json"
        if not path.is_file():
            decision_checks.append(False)
            continue
        decision = DecisionRecord.model_validate_json(path.read_text(encoding="utf-8"))
        valid_candidate_ids = {candidate.candidate_id for candidate in records if candidate.output}
        decision_checks.append(
            set(decision.candidate_ids) == valid_candidate_ids
            and (
                decision.best_candidate_id is None
                or decision.best_candidate_id in valid_candidate_ids
            )
        )
    record("decision_references", all(decision_checks), f"checked={len(decision_checks)}")
    overall = "PASS" if all(item["status"] == "PASS" for item in items) else "FAIL"
    return {"status": overall, "run_dir": str(run_dir), "checks": items}
