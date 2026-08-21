from __future__ import annotations

import argparse
import json
from pathlib import Path

from retarget_agent.movie60_release import (
    Movie60V3Sources,
    materialize_movie60_review_v3,
    validate_movie60_review_v3,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build or validate the single clean Movie60 review v3 workspace."
    )
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--base-workspace", type=Path)
    parser.add_argument("--run", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--validate-only", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.validate_only is not None:
        print(json.dumps(validate_movie60_review_v3(args.validate_only), ensure_ascii=False))
        return 0
    missing = [
        name for name in ("base_workspace", "run", "output_dir") if getattr(args, name) is None
    ]
    if missing:
        raise SystemExit(f"missing required build arguments: {', '.join(missing)}")
    run = args.run.resolve()
    strict = run / "strict-reviews"
    agent_runs = run / "agent-runs"
    sources = Movie60V3Sources(
        repository=args.repository,
        base_workspace=args.base_workspace,
        run=run,
        evaluation=run / "evaluations" / "movie60-human-aligned-v3-3-20260821",
        development_overview=agent_runs / "movie60-v3-3-agent-overview-dev45-v2-20260821",
        holdout_overview=agent_runs / "movie60-v3-3-agent-overview-holdout15-v2-20260821",
        development_advisory=strict / "movie60-v3-3-agent-strict-dev45-v4-20260821",
        holdout_advisory=strict / "movie60-v3-3-agent-strict-holdout15-v2-20260821",
        development_visual_review=strict / "movie60-v3-3-agent-strict-dev45-v4-20260821",
        holdout_visual_review=strict / "movie60-v3-3-agent-strict-holdout15-v2-20260821",
    )
    result = materialize_movie60_review_v3(sources, args.output_dir)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
