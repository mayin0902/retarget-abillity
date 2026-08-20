from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from retarget_agent.agent_proxy_reporting import build_agent_proxy_report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--ratings", type=Path, required=True)
    parser.add_argument("--split-manifest", type=Path, required=True)
    parser.add_argument("--partition", choices=("development", "proxy_holdout"), required=True)
    parser.add_argument("--evaluation-id", required=True)
    parser.add_argument("--overview-agent-run-id", required=True)
    parser.add_argument("--review-run-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError(args.output_dir)
    report = build_agent_proxy_report(
        args.run_dir,
        ratings_csv=args.ratings,
        split_manifest=args.split_manifest,
        partition=args.partition,
        evaluation_id=args.evaluation_id,
        overview_agent_run_id=args.overview_agent_run_id,
        review_run_id=args.review_run_id,
    )
    args.output_dir.mkdir(parents=True)
    (args.output_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with (args.output_dir / "tasks.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        rows = report["rows"]
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary = {key: value for key, value in report.items() if key != "rows"}
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
