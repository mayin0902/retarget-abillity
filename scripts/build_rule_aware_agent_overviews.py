from __future__ import annotations

import argparse
import json
import textwrap
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

from retarget_agent.agents import deterministic_ranking, evidence_from_metrics
from retarget_agent.models import CandidateRecord, DecisionRecord, RunManifest, TaskSpec
from retarget_agent.storage import LocalArtifactStore
from retarget_agent.strategy import load_strategy_bundle
from retarget_agent.visualization import comparison_grid


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        Path("C:/Windows/Fonts/msyhbd.ttc") if bold else Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/arialbd.ttf") if bold else Path("C:/Windows/Fonts/arial.ttf"),
    )
    for path in candidates:
        if path.is_file():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def _metric(store: LocalArtifactStore, evaluation_id: str, candidate_id: str) -> dict:
    return store.read_json(f"evaluations/{evaluation_id}/metrics/{candidate_id}.json")["metrics"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--evaluation-id", required=True)
    parser.add_argument("--input-id", required=True)
    parser.add_argument(
        "--strategy",
        type=Path,
        default=Path("strategies/movie60/v3_2_2/bundle.yaml"),
    )
    args = parser.parse_args()
    strategy = load_strategy_bundle(args.strategy)
    run_dir = args.run_dir.resolve()
    store = LocalArtifactStore(run_dir)
    run = RunManifest.model_validate(store.read_json("run.json"))
    output = run_dir / "agent-inputs" / args.input_id
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    ranking_rows: list[dict] = []
    for task_id in run.task_ids:
        task = TaskSpec.model_validate(store.read_json(f"tasks/{task_id}.json"))
        source_ref = store.read_json(f"sources/{task.source.source_id}.json")
        with Image.open(store.path(source_ref["relative_path"])) as opened:
            source = np.asarray(ImageOps.exif_transpose(opened).convert("RGB")).copy()
        records = [
            CandidateRecord.model_validate_json(path.read_text(encoding="utf-8"))
            for path in sorted((run_dir / "candidates" / task_id).glob("*/candidate.json"))
        ]
        by_id = {item.candidate_id: item for item in records}
        evidence = tuple(
            evidence_from_metrics(
                item.candidate_id,
                item.method_id,
                _metric(store, args.evaluation_id, item.candidate_id),
                item.model_dump(mode="json"),
            )
            for item in records
        )
        ranking = deterministic_ranking(evidence, strategy.selection)
        generation_decision = DecisionRecord.model_validate(
            store.read_json(f"decisions/{task_id}.json")
        )
        rule_decision = generation_decision.model_copy(update={"best_candidate_id": ranking[0]})
        grid = Image.fromarray(
            comparison_grid(
                source,
                task,
                records,
                rule_decision,
                run_dir,
                show_top1_marker=True,
            ),
            mode="RGB",
        )
        score_by_id = {item.candidate_id: item.quality_score for item in evidence}
        ranking_text = " > ".join(
            f"{index}.{by_id[candidate_id].method_id} {float(score_by_id[candidate_id] or 0):.2f}"
            for index, candidate_id in enumerate(ranking, start=1)
        )
        header_height = 150
        canvas = Image.new("RGB", (grid.width, grid.height + header_height), "#f3f4f6")
        draw = ImageDraw.Draw(canvas)
        draw.text(
            (24, 16),
            f"RULE TOP1｜{by_id[ranking[0]].method_id}",
            font=_font(30, bold=True),
            fill="#b0000a",
        )
        for line_index, line in enumerate(textwrap.wrap("RULE 完整排名｜" + ranking_text, 92)):
            draw.text(
                (24, 62 + line_index * 30),
                line,
                font=_font(20),
                fill="#17202a",
            )
        canvas.paste(grid, (0, header_height))
        target = output / f"{task_id}.png"
        canvas.save(target, optimize=True)
        ranking_rows.append(
            {
                "task_id": task_id,
                "split": task.source.split,
                "rule_top1_candidate_id": ranking[0],
                "rule_ranking": list(ranking),
                "rule_ranking_methods": [by_id[item].method_id for item in ranking],
            }
        )
    summary = {
        "schema_version": "1.0",
        "input_id": args.input_id,
        "source_run_id": run.run_id,
        "evaluation_id": args.evaluation_id,
        "task_count": len(ranking_rows),
        "source_aspect_preserved": True,
        "rule_top1_marker_visible": True,
        "complete_rule_ranking_visible": True,
        "strategy_id": strategy.bundle.strategy_id,
        "strategy_version": strategy.bundle.version,
        "strategy_sha256": strategy.source_sha256,
        "rankings": ranking_rows,
    }
    strategy.snapshot_to(output / "strategy")
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary | {"rankings": "see summary.json"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
