from __future__ import annotations

import argparse
import json
from pathlib import Path

from retarget_agent.strict_review import StrictVisionReviewBackend, run_strict_top2_review


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--evaluation-id", required=True)
    parser.add_argument("--overview-agent-run-id", required=True)
    parser.add_argument("--strict-run-id", required=True)
    parser.add_argument("--backend-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    args = parser.parse_args()
    backend = StrictVisionReviewBackend(
        base_url=args.backend_url,
        model_version=args.model,
        timeout_seconds=args.timeout_seconds,
        cache_path=args.run_dir / "agent-cache" / "strict-pairwise-qwen4.json",
    )
    summary = run_strict_top2_review(
        args.run_dir,
        args.evaluation_id,
        args.overview_agent_run_id,
        args.strict_run_id,
        backend,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
