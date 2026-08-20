from __future__ import annotations

import argparse
import json
from pathlib import Path

from retarget_agent.stratified_reporting import build_stratified_benchmark_report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build a strict declared-profile benchmark report by scene and difficulty tier."
        )
    )
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--evaluation-id", required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--benchmark-id", required=True)
    args = parser.parse_args()
    report = build_stratified_benchmark_report(
        args.run_dir,
        args.evaluation_id,
        args.source_manifest,
        args.benchmark_id,
    )
    print(
        json.dumps(
            {
                "benchmark_id": report["benchmark_id"],
                "task_count": report["task_count"],
                "candidate_count": report["candidate_count"],
                "rows_hash": report["rows_hash"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
