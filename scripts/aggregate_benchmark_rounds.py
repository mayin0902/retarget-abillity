from __future__ import annotations

import argparse
import json
from pathlib import Path

from retarget_agent.round_aggregation import aggregate_benchmark_rounds


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Aggregate aligned, complete benchmark arms across dataset rounds."
    )
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--report-id", required=True)
    args = parser.parse_args()
    report = aggregate_benchmark_rounds(args.spec, args.output_dir, args.report_id)
    print(
        json.dumps(
            {
                "report_id": report["report_id"],
                "round_count": report["round_count"],
                "task_count": report["task_count"],
                "rows_hash": report["rows_hash"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
