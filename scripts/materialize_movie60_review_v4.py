from __future__ import annotations

import argparse
import json
from pathlib import Path

from retarget_agent.movie60_release_v4 import (
    materialize_movie60_review_v4,
    validate_movie60_review_v4,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build or validate immutable Movie60 review v4.")
    parser.add_argument("--base-v3", type=Path)
    parser.add_argument("--agent-run", type=Path)
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--skill-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--validate-only", type=Path)
    args = parser.parse_args()
    if args.validate_only is not None:
        result = validate_movie60_review_v4(args.validate_only)
    else:
        required = (args.base_v3, args.agent_run, args.skill_dir, args.output_dir)
        if any(value is None for value in required):
            parser.error("build mode requires --base-v3, --agent-run, --skill-dir and --output-dir")
        result = materialize_movie60_review_v4(
            args.base_v3,
            args.agent_run,
            args.repository,
            args.skill_dir,
            args.output_dir,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
