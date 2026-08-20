from __future__ import annotations

import argparse
from pathlib import Path

from retarget_agent.real_smoke import materialize_real_smoke


def main() -> None:
    parser = argparse.ArgumentParser(description="Download and verify audited real Smoke images.")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("datasets/retarget_smoke_real_v1/source_manifest.csv"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("local_data/retarget_smoke_real_v1"),
    )
    args = parser.parse_args()
    print(materialize_real_smoke(args.manifest, args.output).resolve())


if __name__ == "__main__":
    main()
