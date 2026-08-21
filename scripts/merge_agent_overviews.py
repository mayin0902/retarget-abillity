from __future__ import annotations

import argparse
import json
from pathlib import Path

from retarget_agent.review_merge import merge_agent_overview_shards


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--source-agent-run-id", action="append", required=True)
    parser.add_argument("--agent-run-id", required=True)
    args = parser.parse_args()
    result = merge_agent_overview_shards(
        args.run_dir,
        source_agent_run_ids=tuple(args.source_agent_run_id),
        agent_run_id=args.agent_run_id,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
