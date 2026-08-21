from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from retarget_agent.movie60_review_app import create_movie60_review_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Launch the local Movie60 human-review UI")
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path("deliverables/movie60-review"),
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    uvicorn.run(
        create_movie60_review_app(args.workspace),
        host=args.host,
        port=args.port,
        access_log=False,
    )


if __name__ == "__main__":
    main()
