from __future__ import annotations

import argparse
import json
from pathlib import Path

from retarget_agent.agent_human_gate import evaluate_agent_human_gate


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Gate one complete Agent replay against confirmed seven-candidate human labels."
    )
    parser.add_argument("--human-review", type=Path, required=True)
    parser.add_argument("--agent-run", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = evaluate_agent_human_gate(args.human_review, args.agent_run).as_dict()
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if payload["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
