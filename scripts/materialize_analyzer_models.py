from __future__ import annotations

import argparse
import csv
import hashlib
import re
import warnings
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from urllib3.exceptions import InsecureRequestWarning

ALLOWED_HOSTS = {"media.githubusercontent.com", "raw.githubusercontent.com"}
MAXIMUM_BYTES = 100 * 1024 * 1024
MAXIMUM_REDIRECTS = 5
REDIRECT_STATUSES = {301, 302, 303, 307, 308}


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


def _validate_pinned_row(row: dict[str, str]) -> tuple[str, int, str]:
    """Reject any resource that cannot be authenticated by a fixed content pin."""

    asset_id = row.get("asset_id", "").strip()
    expected_sha256 = row.get("sha256", "").strip().lower()
    filename = row.get("local_filename", "").strip()
    if not asset_id:
        raise ValueError("model manifest row is missing asset_id")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ValueError(f"model asset has no valid SHA-256 pin: {asset_id}")
    try:
        expected_bytes = int(row.get("expected_bytes", ""))
    except ValueError as error:
        raise ValueError(f"model asset has no valid byte-size pin: {asset_id}") from error
    if not (1 <= expected_bytes <= MAXIMUM_BYTES):
        raise ValueError(f"model asset byte-size pin is out of range: {asset_id}")
    if not filename or Path(filename).name != filename:
        raise ValueError(f"model asset filename must be a plain file name: {asset_id}")
    return expected_sha256, expected_bytes, filename


def _approved_https_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise ValueError(f"unapproved model source: {url}")


def _download_once(
    row: dict[str, str],
    temporary: Path,
    *,
    verify_tls: bool,
) -> None:
    """Download one pinned asset, validating every redirect before following it."""

    expected_sha256, expected_bytes, _filename = _validate_pinned_row(row)
    current_url = row["source_url"]
    _approved_https_url(current_url)
    response: requests.Response | None = None
    for redirect_count in range(MAXIMUM_REDIRECTS + 1):
        response = requests.get(
            current_url,
            headers={"User-Agent": "retarget-engine/0.7 analyzer-materializer"},
            stream=True,
            timeout=(10, 300),
            verify=verify_tls,
            allow_redirects=False,
        )
        if response.status_code not in REDIRECT_STATUSES:
            break
        location = response.headers.get("Location")
        response.close()
        if not location:
            raise ValueError(f"model redirect has no Location header: {row['asset_id']}")
        if redirect_count >= MAXIMUM_REDIRECTS:
            raise ValueError(f"too many model redirects: {row['asset_id']}")
        current_url = urljoin(current_url, location)
        _approved_https_url(current_url)
    if response is None:  # pragma: no cover - loop always makes one request
        raise RuntimeError("model request was not created")

    digest = hashlib.sha256()
    size = 0
    with response:
        response.raise_for_status()
        _approved_https_url(response.url or current_url)
        with temporary.open("wb") as handle:
            for chunk in response.iter_content(1024 * 1024):
                if not chunk:
                    continue
                size += len(chunk)
                if size > MAXIMUM_BYTES or size > expected_bytes:
                    raise ValueError(f"model exceeds pinned byte size: {row['asset_id']}")
                digest.update(chunk)
                handle.write(chunk)
    if size != expected_bytes or digest.hexdigest() != expected_sha256:
        raise ValueError(f"downloaded model failed pin verification: {row['asset_id']}")


def _download_with_ssl_fallback(row: dict[str, str], temporary: Path) -> bool:
    """Return True only when the audited SSLError-only fallback was required."""

    try:
        _download_once(row, temporary, verify_tls=True)
        return False
    except requests.exceptions.SSLError:
        if temporary.exists():
            temporary.unlink()
        print(
            "WARNING: HTTPS certificate verification failed for pinned model asset "
            f"{row['asset_id']}. Retrying this allowlisted resource with TLS server "
            "identity verification disabled; fixed SHA-256 and byte-size pins remain mandatory."
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", InsecureRequestWarning)
            _download_once(row, temporary, verify_tls=False)
        return True


def materialize(manifest: Path, output: Path) -> None:
    with manifest.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    output.mkdir(parents=True, exist_ok=True)
    for row in rows:
        expected_sha256, expected_bytes, filename = _validate_pinned_row(row)
        row["sha256"] = expected_sha256
        row["expected_bytes"] = str(expected_bytes)
        url = row["source_url"]
        _approved_https_url(url)
        destination = output / filename
        if destination.exists():
            _verify(destination, row)
            print(f"verified  {row['asset_id']}: {destination}")
            continue
        temporary = destination.with_suffix(destination.suffix + ".part")
        try:
            used_fallback = _download_with_ssl_fallback(row, temporary)
            temporary.replace(destination)
        finally:
            if temporary.exists():
                temporary.unlink()
        suffix = " (SSL fallback; content pins verified)" if used_fallback else ""
        print(f"downloaded {row['asset_id']}: {destination}{suffix}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Materialize audited local analyzer models.")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("datasets/analyzer_models_company_cpu_v2/download_manifest.csv"),
    )
    parser.add_argument("--output", type=Path, default=Path("models/analyzers"))
    args = parser.parse_args()
    materialize(args.manifest, args.output)


if __name__ == "__main__":
    main()
