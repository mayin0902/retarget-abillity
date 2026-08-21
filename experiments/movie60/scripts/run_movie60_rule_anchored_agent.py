from __future__ import annotations

import argparse
import json
from pathlib import Path

from retarget_agent.agents import (
    AgentMode,
    AgentReplayConfig,
    run_agent_replay,
)
from retarget_agent.models import RunManifest, TaskSpec, validate_id
from retarget_agent.plugin_catalog import built_in_plugin_catalog
from retarget_agent.rule_anchored_review import run_rule_anchored_review
from retarget_agent.storage import LocalArtifactStore
from retarget_agent.strategy import load_strategy_bundle


def _task_ids(run_dir: Path, phase: str, task_ids_file: Path | None) -> tuple[str, ...]:
    store = LocalArtifactStore(run_dir.resolve())
    run = RunManifest.model_validate(store.read_json("run.json"))
    if task_ids_file is not None:
        raw = json.loads(task_ids_file.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and "records" in raw:
            values = [
                item["task_id"]
                for item in raw["records"]
                if item.get("partition") == phase
            ]
        else:
            values = raw["task_ids"] if isinstance(raw, dict) else raw
        task_ids = tuple(str(item) for item in values)
        if not task_ids or len(task_ids) != len(set(task_ids)):
            raise ValueError("task IDs file must contain a non-empty unique list")
        unknown = sorted(set(task_ids) - set(run.task_ids))
        if unknown:
            raise ValueError(f"task IDs file contains unknown IDs: {unknown}")
        return task_ids
    return tuple(
        task_id
        for task_id in run.task_ids
        if TaskSpec.model_validate(store.read_json(f"tasks/{task_id}.json")).source.split == phase
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    overview = subparsers.add_parser("overview")
    review = subparsers.add_parser("review")
    for subparser in (overview, review):
        subparser.add_argument("run_dir", type=Path)
        subparser.add_argument("--evaluation-id", required=True)
        subparser.add_argument(
            "--phase",
            choices=("calibration", "validation", "development", "proxy_holdout"),
            required=True,
        )
        subparser.add_argument("--task-ids-file", type=Path)
        subparser.add_argument("--backend-url", required=True)
        subparser.add_argument("--model", required=True)
        subparser.add_argument("--strategy", type=Path, required=True)
        subparser.add_argument("--timeout-seconds", type=float, default=120.0)
        subparser.add_argument(
            "--cache-namespace",
            help="Optional isolated cache namespace for safe parallel workers.",
        )
    overview.add_argument("--agent-run-id", required=True)
    overview.add_argument("--comparison-dir", type=Path, required=True)
    review.add_argument("--overview-agent-run-id", required=True)
    review.add_argument("--review-run-id", required=True)
    review.add_argument("--calibration-review-run-id")
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    strategy = load_strategy_bundle(args.strategy)
    if args.cache_namespace:
        validate_id(args.cache_namespace)
    plugins = built_in_plugin_catalog()
    tasks = _task_ids(run_dir, args.phase, args.task_ids_file)
    strategy_cache_key = strategy.source_sha256[:12]
    cache_suffix = f"-{args.cache_namespace}" if args.cache_namespace else ""
    if args.command == "overview":
        backend = plugins.agent_backends.get(strategy.bundle.agent_backend_plugin)(
            base_url=args.backend_url,
            model_version=args.model,
            timeout_seconds=args.timeout_seconds,
            cache_path=(
                run_dir
                / "agent-cache"
                / f"overview-rule-aware-{strategy_cache_key}{cache_suffix}.json"
            ),
            skill=strategy.agent_skill,
            skill_sha256=strategy.file_hashes[strategy.bundle.agent_skill],
            prompt_template=(strategy.prompts.overview if strategy.prompts else None),
        )
        result = run_agent_replay(
            run_dir,
            args.evaluation_id,
            args.agent_run_id,
            AgentReplayConfig(
                mode=AgentMode.ALWAYS_ON,
                allow_external_aigc=False,
                max_agent_calls=len(tasks),
                prompt_version="judge-rule-aware-v4",
                score_gap_trigger=strategy.selection.score_gap_trigger,
                low_score_trigger=strategy.selection.low_score_trigger,
                deterministic_fallback_threshold=(
                    strategy.selection.deterministic_fallback_threshold
                ),
            ),
            backend,
            args.comparison_dir,
            task_ids=tasks,
            strategy_bundle=strategy,
        ).model_dump(mode="json")
    else:
        backend = plugins.agent_backends.get(strategy.bundle.pair_review_backend_plugin)(
            base_url=args.backend_url,
            model_version=args.model,
            timeout_seconds=args.timeout_seconds,
            candidate_cache_path=(
                run_dir
                / "agent-cache"
                / f"strict-rule-anchor-{strategy_cache_key}{cache_suffix}.json"
            ),
            pair_cache_path=(
                run_dir
                / "agent-cache"
                / f"pair-rule-anchor-{strategy_cache_key}{cache_suffix}.json"
            ),
            strict_prompt_template=(
                strategy.prompts.strict_candidate if strategy.prompts else None
            ),
            pair_prompt_template=(
                strategy.prompts.rule_agent_pair if strategy.prompts else None
            ),
        )
        result = run_rule_anchored_review(
            run_dir,
            args.evaluation_id,
            args.overview_agent_run_id,
            args.review_run_id,
            args.phase,
            backend,
            task_ids=tasks,
            policy_sha256=strategy.file_hashes[strategy.bundle.agent_skill],
            calibration_review_run_id=args.calibration_review_run_id,
            strategy_bundle=strategy,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
