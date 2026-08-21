from __future__ import annotations

import argparse
import atexit
import os
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

import uvicorn

from retarget_agent.movie60_review_app import create_movie60_review_app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args()
    state = args.workspace / ".state"
    state.mkdir(exist_ok=True)
    pid_file = state / "review-ui.pid"
    pid_file.write_text(str(os.getpid()), encoding="ascii")
    atexit.register(lambda: pid_file.unlink(missing_ok=True))

    if args.open_browser:
        url = f"http://{args.host}:{args.port}/"

        def open_when_ready() -> None:
            for _ in range(80):
                time.sleep(0.25)
                try:
                    with urllib.request.urlopen(url + "health/ready", timeout=1) as response:
                        if response.status == 200:
                            webbrowser.open(url)
                            return
                except OSError:
                    continue

        threading.Thread(target=open_when_ready, daemon=True).start()
    uvicorn.run(
        create_movie60_review_app(args.workspace),
        host=args.host,
        port=args.port,
        access_log=False,
    )


if __name__ == "__main__":
    main()
