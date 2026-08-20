from __future__ import annotations

import argparse
import json
from pathlib import Path

from retarget_agent.proxy_validation import evaluate_proxy_strategy, freeze_proxy_split


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--ratings", type=Path, required=True)
    parser.add_argument("--split-manifest", type=Path, required=True)
    parser.add_argument("--freeze-split", action="store_true")
    parser.add_argument("--evaluation-id", action="append", default=[])
    parser.add_argument(
        "--partition",
        action="append",
        choices=("development", "proxy_holdout"),
        default=[],
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.freeze_split:
        freeze_proxy_split(args.ratings, args.split_manifest)
    if not args.evaluation_id:
        raise ValueError("at least one --evaluation-id is required")
    partitions = args.partition or ["development"]
    reports = [
        evaluate_proxy_strategy(
            args.run_dir,
            evaluation_id=evaluation_id,
            ratings_csv=args.ratings,
            split_manifest=args.split_manifest,
            partitions=partitions,
        )
        for evaluation_id in args.evaluation_id
    ]
    payload = {
        "schema_version": "1.0",
        "reports": reports,
    }
    if args.output.exists():
        raise FileExistsError(args.output)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
