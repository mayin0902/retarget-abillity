from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy(source: Path, destination: Path, records: list[dict[str, Any]]) -> None:
    if not source.is_file():
        raise FileNotFoundError(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    records.append(
        {
            "relative_path": destination.as_posix(),
            "bytes": destination.stat().st_size,
            "sha256": _sha256(destination),
        }
    )


def _visual_evidence_root(run_dir: Path, review_run_id: str) -> tuple[str, Path]:
    current = review_run_id
    visited: set[str] = set()
    while True:
        if current in visited:
            raise ValueError("strict-review replay ancestry contains a cycle")
        visited.add(current)
        root = run_dir / "strict-reviews" / current
        summary = _read(root / "summary.json")
        if summary.get("complete") is not True:
            raise ValueError(f"{current}: visual evidence run is incomplete")
        parent = summary.get("source_review_run_id")
        if not parent:
            return current, root
        if summary.get("visual_evidence_reused") is not True:
            raise ValueError(f"{current}: replay does not declare visual evidence reuse")
        current = str(parent)


def curate(
    run_dir: Path,
    review_run_ids: tuple[str, ...],
    output_dir: Path,
    *,
    tasks_per_scene: int = 3,
) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise FileExistsError(output_dir)
    if not review_run_ids:
        raise ValueError("at least one review run is required")
    review_by_task: dict[str, Path] = {}
    evidence_by_task: dict[str, Path] = {}
    overview_by_task: dict[str, Path] = {}
    phase_by_task: dict[str, str] = {}
    summaries: list[dict[str, Any]] = []
    for review_run_id in review_run_ids:
        review_root = run_dir / "strict-reviews" / review_run_id
        summary = _read(review_root / "summary.json")
        if not summary.get("complete"):
            raise ValueError(f"{review_run_id}: review is incomplete")
        summaries.append(summary)
        _, evidence_root = _visual_evidence_root(run_dir, review_run_id)
        overview_root = run_dir / "agent-runs" / summary["overview_agent_run_id"]
        for path in sorted((review_root / "decisions").glob("*.json")):
            task_id = path.stem
            if task_id in review_by_task:
                raise ValueError(f"task appears in multiple reviews: {task_id}")
            review_by_task[task_id] = review_root
            evidence_by_task[task_id] = evidence_root
            overview_by_task[task_id] = overview_root
            phase_by_task[task_id] = summary["phase"]
    by_scene_phase: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for task_id in sorted(review_by_task):
        task = _read(run_dir / "tasks" / f"{task_id}.json")
        by_scene_phase[task["source"]["scene_category"]][phase_by_task[task_id]].append(task_id)
    selected: list[str] = []
    for scene in sorted(by_scene_phase):
        phases = by_scene_phase[scene]
        if "proxy_holdout" in phases and tasks_per_scene >= 2:
            selected.append(phases["proxy_holdout"][0])
        holdout_selected = sum(
            task in selected for task in phases.get("proxy_holdout", ())
        )
        remaining = tasks_per_scene - holdout_selected
        pool = sorted(task for values in phases.values() for task in values if task not in selected)
        selected.extend(pool[:remaining])
    output_dir.mkdir(parents=True)
    records: list[dict[str, Any]] = []

    for summary in summaries:
        review_root = run_dir / "strict-reviews" / summary["review_run_id"]
        _copy(
            review_root / "summary.json",
            output_dir / "run-summaries" / f"{summary['review_run_id']}.json",
            records,
        )
        for decision_path in sorted((review_root / "decisions").glob("*.json")):
            _copy(
                decision_path,
                output_dir / "all-decisions" / summary["review_run_id"] / decision_path.name,
                records,
            )

    for task_id in selected:
        review_root = review_by_task[task_id]
        evidence_root = evidence_by_task[task_id]
        overview_root = overview_by_task[task_id]
        task_root = output_dir / "representative-tasks" / task_id
        task = _read(run_dir / "tasks" / f"{task_id}.json")
        source_ref = _read(run_dir / "sources" / f"{task['source']['source_id']}.json")
        source = run_dir / source_ref["relative_path"]
        _copy(source, task_root / f"source{source.suffix.lower()}", records)
        decision_path = review_root / "decisions" / f"{task_id}.json"
        decision = _read(decision_path)
        _copy(decision_path, task_root / "decision.json", records)
        _copy(
            overview_root / "decisions" / f"{task_id}.json",
            task_root / "overview-decision.json",
            records,
        )
        candidate_ids = {
            "rule": decision["rule_top1_candidate_id"],
            "agent": decision["agent_proposed_candidate_id"],
            "combined": decision["selected_candidate_id"],
        }
        candidates = {}
        for path in (run_dir / "candidates" / task_id).glob("*/candidate.json"):
            payload = _read(path)
            candidates[payload["candidate_id"]] = payload
        for role, candidate_id in candidate_ids.items():
            candidate = candidates[candidate_id]
            output = run_dir / candidate["output"]["relative_path"]
            _copy(output, task_root / f"{role}-{candidate['method_id']}.png", records)
        for folder in ("candidate-sheets", "candidate-reviews", "pair-sheets", "pair-reviews"):
            source_root = evidence_root / folder / task_id
            if source_root.is_dir():
                sources = sorted(
                    path for path in source_root.rglob("*") if path.is_file()
                )
                for source_path in sources:
                    _copy(
                        source_path,
                        task_root / folder / source_path.relative_to(source_root),
                        records,
                    )
        chosen_pair = evidence_root / "pair-reviews" / f"{task_id}.json"
        if chosen_pair.is_file():
            _copy(chosen_pair, task_root / "chosen-pair-review.json", records)

    manifest_path = output_dir / "manifest.csv"
    with manifest_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=("relative_path", "bytes", "sha256"))
        writer.writeheader()
        for row in sorted(records, key=lambda item: item["relative_path"]):
            relative = Path(row["relative_path"]).relative_to(output_dir).as_posix()
            row = {**row, "relative_path": relative}
            writer.writerow(row)
    result = {
        "schema_version": "1.0",
        "review_run_ids": list(review_run_ids),
        "representative_task_ids": selected,
        "representative_task_count": len(selected),
        "all_decision_count": len(review_by_task),
        "visual_evidence_review_run_ids": sorted(
            {
                path.name
                for path in evidence_by_task.values()
            }
        ),
        "manifest_sha256": _sha256(manifest_path),
    }
    (output_dir / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--review-run-id", action="append", default=[], required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--tasks-per-scene", type=int, default=3)
    args = parser.parse_args()
    result = curate(
        args.run_dir,
        tuple(args.review_run_id),
        args.output_dir,
        tasks_per_scene=args.tasks_per_scene,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
