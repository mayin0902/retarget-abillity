from __future__ import annotations

import argparse
from pathlib import Path

from retarget_agent.real_smoke import HD_TARGETS, materialize_real_smoke


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download and verify the audited high-resolution real Smoke images."
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("datasets/retarget_smoke_real_hd_v1/source_manifest.csv"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("local_data/retarget_smoke_real_hd_v1"),
    )
    args = parser.parse_args()
    result = materialize_real_smoke(
        args.manifest,
        args.output,
        dataset_id="retarget_smoke_real_hd_v1",
        targets=HD_TARGETS,
        description=(
            "Twelve audited real-world public images with HD source renditions and review targets."
        ),
    )
    print(result.resolve())


if __name__ == "__main__":
    main()
