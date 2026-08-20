from __future__ import annotations

import argparse
from pathlib import Path

from retarget_agent.real_smoke import SQUARE_BENCHMARK_TARGETS, materialize_real_smoke


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Materialize the audited twelve-image 1:1 Round 0 benchmark."
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("datasets/retarget_smoke_real_hd_v1/source_manifest.csv"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("local_data/retarget_smoke_real_square_v1"),
    )
    parser.add_argument(
        "--source-cache",
        type=Path,
        default=Path("local_data/retarget_smoke_real_hd_v1/images"),
        help="Optional hash-verified cache of the same audited source bytes.",
    )
    args = parser.parse_args()
    result = materialize_real_smoke(
        args.manifest,
        args.output,
        dataset_id="retarget_smoke_real_square_v1",
        targets=SQUARE_BENCHMARK_TARGETS,
        target_count=1,
        source_cache=args.source_cache,
        minimum_pressure=0.0,
        description=(
            "Twelve audited public real-world images with one 1536x1536 target each "
            "for the complete Round 0 method and routing comparison."
        ),
    )
    print(result.resolve())


if __name__ == "__main__":
    main()
