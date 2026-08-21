from __future__ import annotations

import argparse
import json
from pathlib import Path

from retarget_agent.evaluation import EvaluationConfig, evaluate_run
from retarget_agent.strategy import load_strategy_bundle


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--evaluation-id", required=True)
    parser.add_argument(
        "--strategy",
        type=Path,
        default=Path("strategies/movie60/v3_3/bundle.yaml"),
    )
    args = parser.parse_args()
    manifest = evaluate_run(
        args.run_dir,
        args.evaluation_id,
        EvaluationConfig(rerun_detectors=True),
        strategy_bundle=load_strategy_bundle(args.strategy),
    )
    print(json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
