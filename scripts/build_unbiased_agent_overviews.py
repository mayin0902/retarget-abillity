from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from retarget_agent.models import CandidateRecord, DecisionRecord, RunManifest, TaskSpec
from retarget_agent.storage import LocalArtifactStore
from retarget_agent.visualization import comparison_grid


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--input-id", required=True)
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    store = LocalArtifactStore(run_dir)
    run = RunManifest.model_validate(store.read_json("run.json"))
    output = run_dir / "agent-inputs" / args.input_id
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    for task_id in run.task_ids:
        task = TaskSpec.model_validate(store.read_json(f"tasks/{task_id}.json"))
        source_ref = store.read_json(f"sources/{task.source.source_id}.json")
        with Image.open(store.path(source_ref["relative_path"])) as opened:
            source = np.asarray(ImageOps.exif_transpose(opened).convert("RGB")).copy()
        candidates = [
            CandidateRecord.model_validate_json(path.read_text(encoding="utf-8"))
            for path in sorted((run_dir / "candidates" / task_id).glob("*/candidate.json"))
        ]
        decision = DecisionRecord.model_validate(store.read_json(f"decisions/{task_id}.json"))
        grid = comparison_grid(
            source,
            task,
            candidates,
            decision,
            run_dir,
            show_top1_marker=False,
        )
        Image.fromarray(grid, mode="RGB").save(output / f"{task_id}.png", optimize=True)
    summary = {
        "schema_version": "1.0",
        "input_id": args.input_id,
        "source_run_id": run.run_id,
        "task_count": len(run.task_ids),
        "source_aspect_preserved": True,
        "technical_top1_marker_visible": False,
        "candidate_method_labels_visible": True,
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
