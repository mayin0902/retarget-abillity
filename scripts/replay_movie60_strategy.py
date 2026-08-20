from __future__ import annotations

import argparse
import json
from pathlib import Path

from retarget_agent.human_aligned_replay import replay_human_aligned_evaluation
from retarget_agent.strategy import load_strategy_bundle


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Replay a frozen Movie60 strategy over an existing metric evaluation."
    )
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--source-evaluation-id", required=True)
    parser.add_argument("--evaluation-id", required=True)
    parser.add_argument("--strategy", type=Path, required=True)
    args = parser.parse_args()
    manifest = replay_human_aligned_evaluation(
        args.run_dir,
        source_evaluation_id=args.source_evaluation_id,
        evaluation_id=args.evaluation_id,
        strategy_bundle=load_strategy_bundle(args.strategy),
    )
    print(json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
