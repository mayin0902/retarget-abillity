from __future__ import annotations

import argparse
from pathlib import Path

from retarget_agent.fixtures import materialize_fixture_dataset


def main() -> None:
    parser = argparse.ArgumentParser(description="Materialize deterministic retarget Smoke data.")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("tests/fixtures/generated/retarget_fixture_v1"),
        help="Dataset root to create or refresh.",
    )
    parser.add_argument(
        "--limit-sources",
        type=int,
        default=None,
        help="Materialize only the first N sources for contract audits.",
    )
    parser.add_argument(
        "--dataset-id",
        default="retarget-fixture-v1",
        help="Stable lowercase dataset identifier.",
    )
    args = parser.parse_args()
    root = materialize_fixture_dataset(
        args.output, source_limit=args.limit_sources, dataset_id=args.dataset_id
    )
    print(root.resolve())


if __name__ == "__main__":
    main()
