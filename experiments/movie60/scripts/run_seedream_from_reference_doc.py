"""Load SeedDream runtime credentials from a user-owned reference document.

The endpoint, model and bearer token are kept in process memory only.  This script never prints or
persists them and delegates durable result/cost recording to the provider adapter.
"""

from __future__ import annotations

import argparse
import os
import re
from decimal import Decimal
from pathlib import Path

from retarget_agent.aigc_experiment import run_seedream_plan


def _runtime_values(path: Path) -> tuple[str, str, str]:
    text = path.read_text(encoding="utf-8")
    endpoints = re.findall(r"curl\s+-X\s+POST\s+(https://[^\s\\]+)", text)
    tokens = re.findall(r'Authorization:\s*Bearer\s+([^"\s]+)', text, flags=re.IGNORECASE)
    models = re.findall(r'"model"\s*:\s*"([^"]+)"', text)
    if not endpoints or not tokens or not models:
        raise ValueError("reference document does not contain endpoint, bearer token and model")
    if len(set(endpoints)) != 1 or len(set(tokens)) != 1 or len(set(models)) != 1:
        raise ValueError("reference document contains conflicting provider runtime values")
    return endpoints[0], tokens[0], models[0]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--plan-id", required=True)
    parser.add_argument("--limit", type=int, required=True)
    parser.add_argument("--reference-doc", type=Path, required=True)
    parser.add_argument("--budget-cny", type=Decimal, default=Decimal("12.00"))
    parser.add_argument("--read-timeout-seconds", type=float, default=300.0)
    args = parser.parse_args()
    endpoint, token, model = _runtime_values(args.reference_doc)
    os.environ["SEEDREAM_BASE_URL"] = endpoint
    os.environ["SEEDREAM_API_KEY"] = token
    os.environ["SEEDREAM_MODEL"] = model
    try:
        summary = run_seedream_plan(
            args.run_dir,
            args.plan_id,
            limit=args.limit,
            budget_cny=args.budget_cny,
            read_timeout_seconds=args.read_timeout_seconds,
        )
    finally:
        os.environ.pop("SEEDREAM_BASE_URL", None)
        os.environ.pop("SEEDREAM_API_KEY", None)
        os.environ.pop("SEEDREAM_MODEL", None)
    print(
        "SeedDream execution complete: "
        f"results={summary['result_count']} success={summary['success_count']} "
        f"failure={summary['failure_count']} "
        f"estimated_cny={summary['estimated_cost_min_cny']}-"
        f"{summary['estimated_cost_max_cny']}"
    )


if __name__ == "__main__":
    main()
