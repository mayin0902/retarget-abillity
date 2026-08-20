from __future__ import annotations

import argparse
from pathlib import Path

from retarget_agent.cn60 import materialize_cn60, summary_json


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Materialize the audited local-only CN60 dataset and verify its denominator."
    )
    parser.add_argument(
        "--selection",
        type=Path,
        default=Path("datasets/retarget_cn60_v1/selection.yaml"),
    )
    parser.add_argument(
        "--candidate-csv",
        type=Path,
        default=Path("local_data/datasets/retarget_cn60_discovery/candidates.csv"),
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("local_data/datasets/retarget_cn60_v1"),
    )
    parser.add_argument(
        "--audit-manifest",
        type=Path,
        default=Path("datasets/retarget_cn60_v1/source_manifest.csv"),
    )
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument(
        "--prefer-discovery-cache",
        action="store_true",
        help="Use the already verified local discovery pixels instead of refetching official URLs.",
    )
    args = parser.parse_args()
    result = materialize_cn60(
        args.selection,
        args.candidate_csv,
        args.output_root,
        args.audit_manifest,
        workers=args.workers,
        prefer_discovery_cache=args.prefer_discovery_cache,
    )
    print(summary_json(result))


if __name__ == "__main__":
    main()
