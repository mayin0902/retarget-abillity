"""Download a bounded candidate pool from configured official public pages.

This is discovery only. It never marks an asset redistributable or API-egress eligible.
The exact 60-image dataset is frozen by a separate reviewed selection manifest.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
import yaml
from PIL import Image, ImageOps

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"
)
ALLOWED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
EXCLUDED_TOKENS = (
    "logo",
    "icon",
    "qrcode",
    "code.jpg",
    "wechat",
    "weibo",
    "android",
    "ios",
    "partner",
    "btn-",
)


class _ImageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() not in {"img", "a", "source"}:
            return
        values = dict(attrs)
        for key in ("src", "data-src", "data-original", "href"):
            value = values.get(key)
            if value:
                self.urls.append(value.strip())


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    page_id: str
    scene_hint: str
    official_source_name: str
    official_page_url: str
    asset_url: str
    local_path: str
    sha256: str
    width: int
    height: int
    mode: str
    redistribution_status: str = "local_only"
    api_egress_allowed: bool = False


def _asset_urls(page_url: str, html: str) -> list[str]:
    parser = _ImageParser()
    parser.feed(html)
    output: set[str] = set()
    for raw in parser.urls:
        absolute = urljoin(page_url, raw)
        parsed = urlparse(absolute)
        suffix = Path(parsed.path).suffix.lower()
        lowered = absolute.lower()
        if parsed.scheme not in {"http", "https"} or suffix not in ALLOWED_SUFFIXES:
            continue
        if any(token in lowered for token in EXCLUDED_TOKENS):
            continue
        output.add(absolute)
    return sorted(output)


def _safe_id(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _download_candidate(
    page: dict[str, object], index: int, asset_url: str, assets_root: Path
) -> Candidate | None:
    try:
        asset = requests.get(
            asset_url,
            headers={"User-Agent": USER_AGENT, "Referer": str(page["url"])},
            timeout=15,
        )
        asset.raise_for_status()
        if len(asset.content) > 30 * 1024 * 1024:
            return None
        with Image.open(BytesIO(asset.content)) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
            width, height = image.size
            if width < 600 or height < 600:
                return None
            digest = hashlib.sha256(asset.content).hexdigest()
            candidate_id = f"{_safe_id(str(page['page_id']))}-{index:03d}-{digest[:8]}"
            filename = f"{candidate_id}.jpg"
            image.save(assets_root / filename, format="JPEG", quality=95, optimize=True)
        return Candidate(
            candidate_id=candidate_id,
            page_id=str(page["page_id"]),
            scene_hint=str(page["scene_hint"]),
            official_source_name=str(page["official_source_name"]),
            official_page_url=str(page["url"]),
            asset_url=asset_url,
            local_path=f"assets/{filename}",
            sha256=digest,
            width=width,
            height=height,
            mode="RGB",
        )
    except (OSError, requests.RequestException):
        return None


def discover(
    config_path: Path,
    output_root: Path,
    page_ids: set[str] | None = None,
) -> list[Candidate]:
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    pages = raw.get("pages") if isinstance(raw, dict) else None
    if not isinstance(pages, list):
        raise ValueError("discovery config must contain a pages list")
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    assets_root = output_root / "assets"
    assets_root.mkdir(parents=True, exist_ok=True)
    candidates: list[Candidate] = []
    seen_hashes: set[str] = set()
    selected_pages = [
        page for page in pages if page_ids is None or str(page.get("page_id")) in page_ids
    ]
    if page_ids is not None:
        missing = page_ids - {str(page.get("page_id")) for page in selected_pages}
        if missing:
            raise ValueError(f"unknown page IDs: {sorted(missing)}")
    for page in selected_pages:
        try:
            response = session.get(str(page["url"]), timeout=30)
            response.raise_for_status()
        except requests.RequestException as error:
            print(f"page_failed={page['page_id']} error={type(error).__name__}")
            continue
        response.encoding = response.apparent_encoding or response.encoding
        urls = _asset_urls(str(page["url"]), response.text)[:80]
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {
                executor.submit(_download_candidate, page, index, asset_url, assets_root): asset_url
                for index, asset_url in enumerate(urls, start=1)
            }
            for future in as_completed(futures):
                item = future.result()
                if item is None or item.sha256 in seen_hashes:
                    continue
                seen_hashes.add(item.sha256)
                candidates.append(item)
        print(f"page={page['page_id']} candidates={len(candidates)}", flush=True)
    return candidates


def write_csv(path: Path, candidates: list[Candidate]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(Candidate.__dataclass_fields__)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for item in candidates:
            writer.writerow(item.__dict__)


def read_csv(path: Path) -> list[Candidate]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return [
        Candidate(
            candidate_id=row["candidate_id"],
            page_id=row["page_id"],
            scene_hint=row["scene_hint"],
            official_source_name=row["official_source_name"],
            official_page_url=row["official_page_url"],
            asset_url=row["asset_url"],
            local_path=row["local_path"],
            sha256=row["sha256"],
            width=int(row["width"]),
            height=int(row["height"]),
            mode=row["mode"],
            redistribution_status=row["redistribution_status"],
            api_egress_allowed=row["api_egress_allowed"].strip().lower() == "true",
        )
        for row in rows
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("datasets/retarget_cn60_v1/discovery_pages.yaml"),
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("local_data/datasets/retarget_cn60_discovery"),
    )
    parser.add_argument(
        "--page-id",
        action="append",
        dest="page_ids",
        help="Discover only this configured page ID; may be repeated.",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Append newly discovered unique pixels to an existing candidates.csv.",
    )
    args = parser.parse_args()
    output_root = args.output_root.resolve()
    csv_path = output_root / "candidates.csv"
    candidates = discover(
        args.config.resolve(),
        output_root,
        set(args.page_ids) if args.page_ids else None,
    )
    if args.append:
        existing = read_csv(csv_path)
        known_hashes = {item.sha256 for item in existing}
        candidates = existing + [item for item in candidates if item.sha256 not in known_hashes]
    write_csv(csv_path, candidates)
    print(f"discovered={len(candidates)} output={args.output_root.resolve()}")
    return 0 if candidates else 2


if __name__ == "__main__":
    raise SystemExit(main())
