"""Evaluation Replay over frozen candidates; no generation code is called."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from .hashing import sha256_json
from .models import (
    CandidateRecord,
    ReplayManifest,
    RunManifest,
    TaskSpec,
    TransformRecord,
    validate_id,
)
from .selector import select_by_technical_risk
from .storage import LocalArtifactStore


def run_evaluation_replay(
    run_dir: Path,
    replay_id: str,
    selector_id: str = "technical_risk_v1",
) -> ReplayManifest:
    validate_id(replay_id)
    if selector_id != "technical_risk_v1":
        raise ValueError(f"unsupported selector: {selector_id}")
    run_dir = run_dir.resolve()
    store = LocalArtifactStore(run_dir)
    replay_record_path = f"replays/{replay_id}/replay.json"
    if store.path(replay_record_path).exists():
        raise FileExistsError(f"replay_id already exists: {replay_id}")
    source_run = RunManifest.model_validate(store.read_json("run.json"))
    grouped: dict[str, list[CandidateRecord]] = defaultdict(list)
    for path in sorted(run_dir.glob("candidates/*/*/candidate.json")):
        candidate = CandidateRecord.model_validate_json(path.read_text(encoding="utf-8"))
        grouped[candidate.task_id].append(candidate)

    all_candidate_ids: list[str] = []
    for task_id in source_run.task_ids:
        task = TaskSpec.model_validate(store.read_json(f"tasks/{task_id}.json"))
        candidates = grouped[task_id]
        transforms: dict[str, TransformRecord] = {}
        for candidate in candidates:
            all_candidate_ids.append(candidate.candidate_id)
            if candidate.transform is not None:
                transforms[candidate.candidate_id] = TransformRecord.model_validate(
                    store.read_json(candidate.transform.relative_path)
                )
        decision = select_by_technical_risk(task, source_run.run_id, candidates, transforms)
        store.write_json(f"replays/{replay_id}/decisions/{task_id}.json", decision)

    manifest = ReplayManifest(
        replay_id=replay_id,
        source_run_id=source_run.run_id,
        evaluator_id="frozen-transform-reader-v1",
        selector_id=selector_id,
        config_hash=sha256_json({"selector_id": selector_id, "selector_version": "1.0.0"}),
        candidate_ids=tuple(all_candidate_ids),
    )
    store.write_json(replay_record_path, manifest)
    return manifest
