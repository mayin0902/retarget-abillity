from __future__ import annotations

import argparse
import json
from pathlib import Path

from retarget_agent.resource_cost_reporting import build_resource_cost_report


def _arm_observation(value: str) -> tuple[str, Path]:
    arm_id, separator, path = value.partition("=")
    if not separator or not arm_id or not path:
        raise argparse.ArgumentTypeError("expected ARM_ID=RESOURCE_OBSERVATION.json")
    return arm_id, Path(path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build an observed GPU resource and scenario-cost benchmark sidecar."
    )
    parser.add_argument("--benchmark-report", type=Path, required=True)
    parser.add_argument(
        "--observation",
        type=_arm_observation,
        action="append",
        required=True,
        metavar="ARM_ID=JSON",
    )
    parser.add_argument("--report-id", required=True)
    parser.add_argument(
        "--gpu-hour-rate-cny",
        type=float,
        action="append",
        help="Repeat for custom scenarios; defaults to 1, 2, 5, and 10 CNY/GPU-hour.",
    )
    args = parser.parse_args()
    arm_observations: dict[str, Path] = {}
    for arm_id, path in args.observation:
        if arm_id in arm_observations:
            parser.error(f"duplicate --observation arm: {arm_id}")
        arm_observations[arm_id] = path
    report = build_resource_cost_report(
        args.benchmark_report,
        arm_observations,
        args.report_id,
        args.gpu_hour_rate_cny,
    )
    print(
        json.dumps(
            {
                "resource_cost_report_id": report["resource_cost_report_id"],
                "row_count": len(report["rows"]),
                "rows_hash": report["rows_hash"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
