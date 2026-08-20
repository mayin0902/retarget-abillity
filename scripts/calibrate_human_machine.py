from __future__ import annotations

import argparse
import json
from pathlib import Path

from retarget_agent.calibration import build_run_calibration


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Calibrate machine Quality against ordinal human A/B/C/D reviews."
    )
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--evaluation-id", required=True)
    parser.add_argument("--reviewer-id", required=True)
    parser.add_argument("--calibration-id", required=True)
    args = parser.parse_args()
    report = build_run_calibration(
        args.run_dir,
        args.evaluation_id,
        args.reviewer_id,
        args.calibration_id,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
