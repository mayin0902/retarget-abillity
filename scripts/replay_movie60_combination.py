"""Replay a grading-only StrategyBundle patch over completed Movie60 Agent evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from retarget_agent.combination_replay import replay_combination_policy
from retarget_agent.strategy import load_strategy_bundle


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--source-review-run-id", required=True)
    parser.add_argument("--overview-agent-run-id", required=True)
    parser.add_argument("--evaluation-id", required=True)
    parser.add_argument("--review-run-id", required=True)
    parser.add_argument("--strategy", required=True, type=Path)
    args = parser.parse_args()
    result = replay_combination_policy(
        args.run_dir,
        source_review_run_id=args.source_review_run_id,
        overview_agent_run_id=args.overview_agent_run_id,
        evaluation_id=args.evaluation_id,
        review_run_id=args.review_run_id,
        strategy=load_strategy_bundle(args.strategy),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
