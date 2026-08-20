from __future__ import annotations

import argparse
import csv
import hashlib
from pathlib import Path
from urllib.parse import urlparse

import requests

ALLOWED_HOSTS = {"media.githubusercontent.com", "raw.githubusercontent.com"}
MAXIMUM_BYTES = 100 * 1024 * 1024


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify(path: Path, row: dict[str, str]) -> None:
    if path.stat().st_size != int(row["expected_bytes"]):
        raise ValueError(f"byte size mismatch: {path}")
    if _sha256(path) != row["sha256"]:
        raise ValueError(f"SHA-256 mismatch: {path}")


def materialize(manifest: Path, output: Path) -> None:
    with manifest.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    output.mkdir(parents=True, exist_ok=True)
    for row in rows:
        url = row["source_url"]
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
            raise ValueError(f"unapproved model source: {url}")
        destination = output / row["local_filename"]
        if destination.exists():
            _verify(destination, row)
            print(f"verified  {row['asset_id']}: {destination}")
            continue
        temporary = destination.with_suffix(destination.suffix + ".part")
        digest = hashlib.sha256()
        size = 0
        try:
            with requests.get(
                url,
                headers={"User-Agent": "retarget-agent/0.1 analyzer-materializer"},
                stream=True,
                timeout=(10, 300),
            ) as response:
                response.raise_for_status()
                final_host = urlparse(response.url).hostname
                if final_host not in ALLOWED_HOSTS:
                    raise ValueError(f"model redirected to unapproved host: {response.url}")
                with temporary.open("wb") as handle:
                    for chunk in response.iter_content(1024 * 1024):
                        if not chunk:
                            continue
                        size += len(chunk)
                        if size > MAXIMUM_BYTES:
                            raise ValueError(f"model exceeds 100 MiB: {row['asset_id']}")
                        digest.update(chunk)
                        handle.write(chunk)
            if size != int(row["expected_bytes"]) or digest.hexdigest() != row["sha256"]:
                raise ValueError(f"downloaded model failed pin verification: {row['asset_id']}")
            temporary.replace(destination)
        finally:
            if temporary.exists():
                temporary.unlink()
        print(f"downloaded {row['asset_id']}: {destination}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Materialize audited local analyzer models.")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("datasets/analyzer_models_v1/model_manifest.csv"),
    )
    parser.add_argument("--output", type=Path, default=Path("models/analyzers"))
    args = parser.parse_args()
    materialize(args.manifest, args.output)


if __name__ == "__main__":
    main()
