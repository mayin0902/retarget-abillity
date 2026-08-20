from __future__ import annotations

import argparse
import json
from pathlib import Path

from retarget_agent.movie_visual60 import materialize_movie_visual60


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source-root",
        type=Path,
        default=Path(r"G:\Projects\movie-visual-dataset-60-20260818"),
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("local_data/datasets/movie_visual_60_v1"),
    )
    args = parser.parse_args()
    result = materialize_movie_visual60(args.source_root, args.output_root)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
