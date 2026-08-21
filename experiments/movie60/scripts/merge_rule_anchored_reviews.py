from __future__ import annotations

import argparse
import json
from pathlib import Path

from retarget_agent.review_merge import merge_rule_anchored_review_shards


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--source-review-run-id", action="append", required=True)
    parser.add_argument("--overview-agent-run-id", required=True)
    parser.add_argument("--calibration-review-run-id")
    parser.add_argument("--review-run-id", required=True)
    args = parser.parse_args()
    result = merge_rule_anchored_review_shards(
        args.run_dir,
        source_review_run_ids=tuple(args.source_review_run_id),
        overview_agent_run_id=args.overview_agent_run_id,
        review_run_id=args.review_run_id,
        calibration_review_run_id=args.calibration_review_run_id,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
