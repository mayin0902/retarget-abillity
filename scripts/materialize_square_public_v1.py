"""Freeze, audit, and materialize the public 1:1 retargeting benchmark.

The script deliberately separates a reproducible *candidate freeze* from an
approved publication manifest.  Discovery metadata is never treated as proof
that a file passed the required per-image license, scene, safety, duplicate,
and non-copyright review.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import io
import json
import re
import shutil
import sys
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

import cv2
import numpy as np
import requests
import yaml
from PIL import Image, ImageDraw, ImageOps

DATASET_ID = "retarget_square_public_v1"
DATASET_DIR = Path("datasets") / DATASET_ID
LOCAL_DIR = Path("local_data") / DATASET_ID
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
OI_DOWNLOAD_PAGE = "https://storage.googleapis.com/openimages/web/download_v7.html"
OI_IMAGE_INFO_URL = (
    "https://storage.googleapis.com/openimages/2018_04/validation/"
    "validation-images-with-rotation.csv"
)
OI_BBOX_URL = "https://storage.googleapis.com/openimages/v5/validation-annotations-bbox.csv"
OI_CLASS_URL = "https://storage.googleapis.com/openimages/v7/oidv7-class-descriptions-boxable.csv"

TARGET = {"target_id": "square-1536x1536", "width": "1536", "height": "1536", "format": "png"}
ACCESS_DATE = "2026-08-11"
USER_AGENT = (
    "retarget-agent/0.1 square-public-benchmark license-audit "
    "(https://github.com/mayin0902/retarget-agent)"
)
MAX_DOWNLOAD_BYTES = 80 * 1024 * 1024

SCENE_COUNTS = {
    "chinese_dense_poster": 50,
    "single_product_promo": 50,
    "multi_product_commercial": 50,
    "multi_person": 50,
    "portrait": 34,
    "landscape_architecture_structure": 33,
    "complex_mixed": 33,
}
PILOT_COUNTS = {
    "chinese_dense_poster": 10,
    "single_product_promo": 10,
    "multi_product_commercial": 10,
    "multi_person": 10,
    "portrait": 7,
    "landscape_architecture_structure": 7,
    "complex_mixed": 6,
}
SOURCE_COUNTS = {
    "chinese_dense_poster": {"wikimedia_commons": 50, "open_images_v7": 0},
    "single_product_promo": {"wikimedia_commons": 10, "open_images_v7": 40},
    "multi_product_commercial": {"wikimedia_commons": 30, "open_images_v7": 20},
    "multi_person": {"wikimedia_commons": 10, "open_images_v7": 40},
    "portrait": {"wikimedia_commons": 4, "open_images_v7": 30},
    "landscape_architecture_structure": {"wikimedia_commons": 8, "open_images_v7": 25},
    "complex_mixed": {"wikimedia_commons": 13, "open_images_v7": 20},
}
DIFFICULTY_COUNTS = {
    "aspect_hard_1": 90,
    "aspect_hard_2": 150,
    "aspect_extreme": 60,
}
ALLOWED_LICENSES = {
    "Public domain",
    "CC0",
    "CC BY 2.0",
    "CC BY 3.0",
    "CC BY 4.0",
    "CC BY-SA 2.0",
    "CC BY-SA 3.0",
    "CC BY-SA 4.0",
}
ALLOWED_URL_HOSTS = {
    "commons.wikimedia.org",
    "upload.wikimedia.org",
    "storage.googleapis.com",
    "open-images-dataset.s3.amazonaws.com",
    "creativecommons.org",
    "www.flickr.com",
    "flickr.com",
}

MANIFEST_FIELDS = [
    "source_id",
    "split",
    "scene_category",
    "secondary_tags",
    "difficulty_tier",
    "upstream_dataset",
    "upstream_id",
    "upstream_split",
    "official_source",
    "source_url",
    "license_evidence_url",
    "license",
    "license_url",
    "author",
    "attribution",
    "access_date",
    "upstream_revision_timestamp",
    "upstream_hash",
    "raw_sha256",
    "materialized_sha256",
    "expected_width",
    "expected_height",
    "source_aspect",
    "orientation",
    "local_filename",
    "redistribution_status",
    "modification_notice",
    "personality_rights_status",
    "trademark_status",
    "non_copyright_restrictions",
    "source_resolution_limited",
    "resolution_review_status",
    "public_release_eligible",
    "api_egress_allowed",
    "license_review_status",
    "scene_review_status",
    "content_safety_status",
    "duplicate_review_status",
    "review_status",
    "download_status",
    "phash",
    "review_notes",
]

COMMONS_QUERIES = {
    "chinese_dense_poster": [
        "filetype:bitmap intitle:海报",
        "filetype:bitmap intitle:海報",
        "filetype:bitmap 中国 电影 海报",
        "filetype:bitmap 中文 宣传 海报",
        "filetype:bitmap Chinese poster text",
    ],
    "single_product_promo": [
        "filetype:bitmap product advertising poster",
        "filetype:bitmap product advertisement bottle",
        "filetype:bitmap commercial product poster",
    ],
    "multi_product_commercial": [
        "filetype:bitmap supermarket shelf price tags",
        "filetype:bitmap products sale advertisement",
        "filetype:bitmap grocery shelves products",
        "filetype:bitmap market stall signs products",
    ],
    "multi_person": [
        "filetype:bitmap group people public event",
        "filetype:bitmap crowd adults event",
        "filetype:bitmap group portrait adults",
    ],
    "portrait": [
        "filetype:bitmap portrait adult person hand",
        "filetype:bitmap environmental portrait adult",
    ],
    "landscape_architecture_structure": [
        "filetype:bitmap architecture facade perspective",
        "filetype:bitmap bridge structure lines",
        "filetype:bitmap interior architecture perspective",
    ],
    "complex_mixed": [
        "filetype:bitmap busy street market signs people",
        "filetype:bitmap festival crowd signs street",
        "filetype:bitmap night market signs people",
    ],
}

PRODUCT_LABELS = {
    "Bottle",
    "Box",
    "Can",
    "Cosmetics",
    "Drink",
    "Food",
    "Footwear",
    "Handbag",
    "Mobile phone",
    "Packaged goods",
    "Shoe",
    "Snack",
    "Toy",
    "Wine",
}
STRUCTURE_LABELS = {
    "Arch",
    "Building",
    "Bridge",
    "Door",
    "House",
    "Skyscraper",
    "Stairs",
    "Tower",
    "Window",
}


class DatasetError(ValueError):
    """Raised when a candidate or materialized dataset violates a contract."""


def _clean_html(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html.unescape(value or ""))
    return " ".join(text.split())


def _metadata_value(metadata: dict[str, object], key: str) -> str:
    value = metadata.get(key, {})
    return str(value.get("value", "")) if isinstance(value, dict) else ""


def _normalise_license(value: str) -> str:
    cleaned = _clean_html(value).strip()
    aliases = {
        "CC BY-SA 4.0": "CC BY-SA 4.0",
        "CC BY-SA 3.0": "CC BY-SA 3.0",
        "CC BY-SA 2.0": "CC BY-SA 2.0",
        "CC BY 4.0": "CC BY 4.0",
        "CC BY 3.0": "CC BY 3.0",
        "CC BY 2.0": "CC BY 2.0",
        "CC0 1.0": "CC0",
        "CC0": "CC0",
        "Public domain": "Public domain",
        "Public domain mark": "Public domain",
    }
    return aliases.get(cleaned, cleaned)


def _license_from_url(value: str) -> str:
    path = urlparse(value).path.lower().rstrip("/")
    if path.endswith("/licenses/by/2.0"):
        return "CC BY 2.0"
    if path.endswith("/licenses/by/3.0"):
        return "CC BY 3.0"
    if path.endswith("/licenses/by/4.0"):
        return "CC BY 4.0"
    if path.endswith("/licenses/by-sa/2.0"):
        return "CC BY-SA 2.0"
    if path.endswith("/licenses/by-sa/3.0"):
        return "CC BY-SA 3.0"
    if path.endswith("/licenses/by-sa/4.0"):
        return "CC BY-SA 4.0"
    if path.endswith("/publicdomain/zero/1.0"):
        return "CC0"
    return ""


def _validate_https_url(value: str, field: str, *, allow_empty: bool = False) -> None:
    if allow_empty and not value:
        return
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_URL_HOSTS:
        raise DatasetError(f"{field} is not an allowlisted HTTPS URL: {value!r}")
    if parsed.username or parsed.password:
        raise DatasetError(f"{field} contains credentials")


def _validate_relative_posix(value: str) -> None:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\\" in value:
        raise DatasetError(f"unsafe local_filename: {value!r}")
    if path.parts[0] != "images" or path.suffix.lower() != ".png":
        raise DatasetError(f"local_filename must be images/*.png: {value!r}")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _phash(image: Image.Image) -> str:
    gray = np.asarray(
        image.convert("L").resize((32, 32), Image.Resampling.LANCZOS), dtype=np.float32
    )
    transformed = cv2.dct(gray)
    block = transformed[:8, :8]
    median = float(np.median(block[1:, :]))
    bits = (block >= median).flatten()
    return f"{int(''.join('1' if bit else '0' for bit in bits), 2):016x}"


def phash_distance(left: str, right: str) -> int:
    return (int(left, 16) ^ int(right, 16)).bit_count()


def _validate_phash_uniqueness(rows: list[dict[str, str]], threshold: int = 4) -> int:
    hashes: list[tuple[str, str]] = []
    for row in rows:
        value = row.get("phash", "")
        if not re.fullmatch(r"[0-9a-f]{16}", value):
            raise DatasetError(f"invalid pHash for {row.get('source_id', '<missing>')}")
        hashes.append((row["source_id"], value))
    minimum = 64
    for index, (left_id, left_hash) in enumerate(hashes):
        for right_id, right_hash in hashes[index + 1 :]:
            distance = phash_distance(left_hash, right_hash)
            minimum = min(minimum, distance)
            if distance <= threshold:
                raise DatasetError(
                    f"near-duplicate pHash distance {distance}: {left_id} and {right_id}"
                )
    return minimum


def _safe_local_path(root: Path, relative: str) -> Path:
    _validate_relative_posix(relative)
    resolved_root = root.resolve()
    candidate = root / PurePosixPath(relative)
    current = candidate.parent
    while current != root.parent and current != current.parent:
        if current.exists() and current.is_symlink():
            raise DatasetError(f"local path traverses a symbolic link: {relative}")
        if current == root:
            break
        current = current.parent
    resolved_parent = candidate.parent.resolve()
    if not resolved_parent.is_relative_to(resolved_root):
        raise DatasetError(f"local path escapes dataset root: {relative}")
    return candidate


def _dimensions(width: int, height: int) -> tuple[str, str, str]:
    if width <= 0 or height <= 0:
        return "", "", ""
    aspect = width / height
    pressure = max(aspect, 1 / aspect)
    orientation = "landscape" if width > height else "portrait" if height > width else "square"
    if 1.5 <= pressure < 2:
        difficulty = "aspect_hard_1"
    elif 2 <= pressure < 3:
        difficulty = "aspect_hard_2"
    elif 3 <= pressure <= 4:
        difficulty = "aspect_extreme"
    else:
        difficulty = "ineligible_aspect"
    return f"{aspect:.8f}", orientation, difficulty


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=fieldnames, lineterminator="\n", extrasaction="ignore"
        )
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def _download_metadata(url: str, destination: Path) -> Path:
    if destination.is_file() and destination.stat().st_size:
        return destination
    _validate_https_url(url, "metadata URL")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    headers = {"User-Agent": USER_AGENT}
    with requests.get(url, headers=headers, timeout=(15, 180), stream=True) as response:
        response.raise_for_status()
        final = urlparse(response.url)
        if final.scheme != "https" or final.hostname not in ALLOWED_URL_HOSTS:
            raise DatasetError(f"metadata redirected to an unapproved host: {response.url}")
        size = 0
        with temporary.open("wb") as handle:
            for chunk in response.iter_content(1024 * 1024):
                if not chunk:
                    continue
                size += len(chunk)
                if size > 100 * 1024 * 1024:
                    raise DatasetError(f"metadata file exceeds 100 MiB: {url}")
                handle.write(chunk)
    temporary.replace(destination)
    return destination


def _commons_candidates(scene: str, needed: int, session: requests.Session) -> list[dict[str, str]]:
    by_title: dict[str, dict[str, str]] = {}
    for query in COMMONS_QUERIES[scene]:
        continuation: dict[str, object] = {}
        for _ in range(3):
            params: dict[str, object] = {
                "action": "query",
                "format": "json",
                "formatversion": 2,
                "maxlag": 5,
                "generator": "search",
                "gsrsearch": query,
                "gsrnamespace": 6,
                "gsrlimit": 50,
                "prop": "imageinfo",
                "iiprop": "timestamp|url|size|sha1|mime|extmetadata",
                "iiextmetadatafilter": (
                    "LicenseShortName|LicenseUrl|Artist|Credit|Attribution|"
                    "Permission|GPSLatitude|GPSLongitude"
                ),
            }
            params.update(continuation)
            response: requests.Response | None = None
            for attempt in range(6):
                response = session.post(COMMONS_API, data=params, timeout=(15, 90))
                if response.status_code not in {429, 500, 502, 503, 504}:
                    response.raise_for_status()
                    break
                retry_after = response.headers.get("Retry-After", "")
                delay = int(retry_after) if retry_after.isdigit() else 3 * (attempt + 1)
                time.sleep(min(delay, 30))
            else:
                assert response is not None
                response.raise_for_status()
            assert response is not None
            data = response.json()
            pages = data.get("query", {}).get("pages", [])
            for page in pages if isinstance(pages, list) else pages.values():
                title = str(page.get("title", ""))
                infos = page.get("imageinfo") or []
                if not title.startswith("File:") or not infos:
                    continue
                info = infos[0]
                metadata = info.get("extmetadata", {})
                license_name = _normalise_license(_metadata_value(metadata, "LicenseShortName"))
                author = _clean_html(
                    _metadata_value(metadata, "Attribution")
                    or _metadata_value(metadata, "Artist")
                    or _metadata_value(metadata, "Credit")
                )
                mime = str(info.get("mime", ""))
                width, height = int(info.get("width", 0)), int(info.get("height", 0))
                aspect, orientation, difficulty = _dimensions(width, height)
                if (
                    license_name not in ALLOWED_LICENSES
                    or not author
                    or not mime.startswith("image/")
                    or difficulty == "ineligible_aspect"
                ):
                    continue
                image_url = str(info.get("url", ""))
                description_url = str(info.get("descriptionurl", ""))
                if urlparse(image_url).hostname != "upload.wikimedia.org":
                    continue
                license_url = _metadata_value(metadata, "LicenseUrl")
                if license_url.startswith("http://creativecommons.org/"):
                    license_url = "https://creativecommons.org/" + license_url.removeprefix(
                        "http://creativecommons.org/"
                    )
                if license_name == "Public domain" and not license_url:
                    license_url = description_url + "#Licensing"
                source_key = hashlib.sha256(title.encode()).hexdigest()[:12]
                row = {
                    "source_id": f"commons-{source_key}",
                    "scene_category": scene,
                    "difficulty_tier": difficulty,
                    "upstream_dataset": "wikimedia_commons",
                    "upstream_id": title,
                    "upstream_split": "public_file",
                    "official_source": description_url,
                    "source_url": image_url,
                    "license_evidence_url": description_url + "#Licensing",
                    "license": license_name,
                    "license_url": license_url,
                    "author": author,
                    "attribution": (
                        f"{author} — {title.removeprefix('File:')} — {license_name} — "
                        "via Wikimedia Commons"
                    ),
                    "access_date": ACCESS_DATE,
                    "upstream_revision_timestamp": str(info.get("timestamp", "")),
                    "upstream_hash": "sha1:" + str(info.get("sha1", "")),
                    "expected_width": str(width),
                    "expected_height": str(height),
                    "source_aspect": aspect,
                    "orientation": orientation,
                    "local_filename": f"images/commons-{source_key}.png",
                    "redistribution_status": "pending_per_file_review",
                    "license_review_status": "pending_file_page_review",
                    "scene_review_status": "pending_thumbnail_review",
                    "content_safety_status": "pending_thumbnail_review",
                    "duplicate_review_status": "pending_phash_review",
                    "review_status": "pending",
                    "download_status": "not_downloaded",
                    "resolution_review_status": "pending_pixel_download",
                    "review_notes": (
                        "Auto-discovered candidate; exact File title frozen, "
                        "not publication-approved."
                    ),
                }
                by_title.setdefault(title, row)
            if len(by_title) >= needed * 3:
                break
            continuation = data.get("continue", {})
            if not continuation:
                break
            time.sleep(1.5)
        if len(by_title) >= needed * 3:
            break
    ordered = sorted(by_title.values(), key=lambda row: row["upstream_id"].casefold())
    if len(ordered) < needed:
        raise DatasetError(f"Commons discovery shortage for {scene}: {len(ordered)} < {needed}")
    return ordered[:needed]


def _load_oi_stats(bbox_path: Path, class_path: Path) -> dict[str, dict[str, object]]:
    with class_path.open("r", encoding="utf-8", newline="") as handle:
        class_names = {row[0]: row[1] for row in csv.reader(handle) if len(row) >= 2}
    stats: dict[str, dict[str, object]] = defaultdict(
        lambda: {
            "total": 0,
            "person": 0,
            "person_group": 0,
            "person_max_area": 0.0,
            "product": 0,
            "product_distinct": set(),
            "structure": 0,
            "occluded": 0,
            "truncated": 0,
            "labels": set(),
        }
    )
    with bbox_path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            image_id = row["ImageID"]
            name = class_names.get(row["LabelName"], "")
            item = stats[image_id]
            item["total"] = int(item["total"]) + 1
            cast_labels = item["labels"]
            assert isinstance(cast_labels, set)
            cast_labels.add(name)
            area = (float(row["XMax"]) - float(row["XMin"])) * (
                float(row["YMax"]) - float(row["YMin"])
            )
            if name == "Person":
                item["person"] = int(item["person"]) + 1
                item["person_group"] = int(item["person_group"]) + int(
                    row.get("IsGroupOf", "0") == "1"
                )
                item["person_max_area"] = max(float(item["person_max_area"]), area)
            if name in PRODUCT_LABELS:
                item["product"] = int(item["product"]) + 1
                distinct = item["product_distinct"]
                assert isinstance(distinct, set)
                distinct.add(name)
            if name in STRUCTURE_LABELS:
                item["structure"] = int(item["structure"]) + 1
            item["occluded"] = int(item["occluded"]) + int(row.get("IsOccluded", "0") == "1")
            item["truncated"] = int(item["truncated"]) + int(row.get("IsTruncated", "0") == "1")
    return stats


def _oi_matches(scene: str, stat: dict[str, object]) -> bool:
    total = int(stat["total"])
    people = int(stat["person"])
    products = int(stat["product"])
    structures = int(stat["structure"])
    if scene == "single_product_promo":
        return products == 1 and total <= 5
    if scene == "multi_product_commercial":
        return products >= 4 and len(stat["product_distinct"]) >= 2
    if scene == "multi_person":
        return people >= 3 or int(stat["person_group"]) >= 1
    if scene == "portrait":
        return people == 1 and 0.15 <= float(stat["person_max_area"]) <= 0.8
    if scene == "landscape_architecture_structure":
        return structures >= 1 and people <= 2
    if scene == "complex_mixed":
        hard_signals = sum(
            (
                total >= 8,
                people >= 2,
                products >= 2,
                int(stat["occluded"]) >= 1,
                int(stat["truncated"]) >= 1,
                len(stat["labels"]) >= 5,
            )
        )
        return hard_signals >= 3
    return False


def _oi_candidates(
    scene: str,
    needed: int,
    image_info_path: Path,
    stats: dict[str, dict[str, object]],
    already_used: set[str],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with image_info_path.open("r", encoding="utf-8", newline="") as handle:
        for metadata in csv.DictReader(handle):
            image_id = metadata.get("ImageID", "")
            stat = stats.get(image_id)
            if not image_id or image_id in already_used or not stat or not _oi_matches(scene, stat):
                continue
            license_url = metadata.get("License", "")
            license_name = _license_from_url(license_url)
            author = (metadata.get("Author") or "").strip()
            landing = (metadata.get("OriginalLandingURL") or "").strip()
            if license_name != "CC BY 2.0" or not author or not landing.startswith("https://"):
                continue
            row = {
                "source_id": f"oi-{image_id}",
                "scene_category": scene,
                "difficulty_tier": "pending_pixel_download",
                "upstream_dataset": "open_images_v7",
                "upstream_id": image_id,
                "upstream_split": "validation",
                "official_source": OI_DOWNLOAD_PAGE,
                "source_url": f"https://open-images-dataset.s3.amazonaws.com/validation/{image_id}.jpg",
                "license_evidence_url": landing,
                "license": license_name,
                "license_url": license_url,
                "author": author,
                "attribution": (
                    f"{author} — {metadata.get('Title', '').strip() or image_id} — "
                    "CC BY 2.0 — via Open Images V7"
                ),
                "access_date": ACCESS_DATE,
                "upstream_revision_timestamp": "2018-04 validation metadata; V7 annotations",
                "upstream_hash": "original-md5:" + (metadata.get("OriginalMD5") or ""),
                "expected_width": "",
                "expected_height": "",
                "source_aspect": "",
                "orientation": "",
                "local_filename": f"images/oi-{image_id}.png",
                "redistribution_status": "pending_original_landing_review",
                "license_review_status": "pending_original_landing_review",
                "scene_review_status": "pending_thumbnail_review",
                "content_safety_status": "pending_thumbnail_review",
                "duplicate_review_status": "pending_phash_review",
                "review_status": "pending",
                "download_status": "not_downloaded",
                "resolution_review_status": "pending_pixel_download",
                "review_notes": (
                    "Auto-selected from official validation metadata/bboxes; bbox heuristics are "
                    "evaluator-only discovery evidence, not a reviewed scene label."
                ),
            }
            rows.append(row)
    rows.sort(key=lambda row: row["upstream_id"])
    if len(rows) < needed:
        raise DatasetError(f"Open Images discovery shortage for {scene}: {len(rows)} < {needed}")
    selected = rows[:needed]
    already_used.update(row["upstream_id"] for row in selected)
    return selected


def _complete_candidate_defaults(rows: list[dict[str, str]]) -> None:
    by_scene: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_scene[row["scene_category"]].append(row)
    for scene, group in by_scene.items():
        group.sort(key=lambda row: hashlib.sha256(row["source_id"].encode()).hexdigest())
        for index, row in enumerate(group):
            row["split"] = "pilot60" if index < PILOT_COUNTS[scene] else "held-out240"
            row.setdefault("secondary_tags", "")
            row.setdefault("raw_sha256", "")
            row.setdefault("materialized_sha256", "")
            row.setdefault("modification_notice", "metadata removal pending")
            row.setdefault("personality_rights_status", "unknown_not_granted_by_copyright_license")
            row.setdefault("trademark_status", "unknown_not_granted_by_copyright_license")
            row.setdefault("non_copyright_restrictions", "pending_per_file_review")
            row.setdefault("source_resolution_limited", "false")
            row.setdefault("resolution_review_status", "pending_pixel_download")
            row.setdefault("public_release_eligible", "false")
            row.setdefault("api_egress_allowed", "false")
            row.setdefault("phash", "")


def freeze_candidates(dataset_dir: Path, local_dir: Path) -> list[dict[str, str]]:
    manifest_path = dataset_dir / "source_manifest.csv"
    if manifest_path.is_file():
        existing = _read_csv(manifest_path)
        validate_manifest(existing, require_approved=False, require_pixel_fields=False)
        return existing
    metadata_dir = local_dir / "metadata"
    image_info = _download_metadata(OI_IMAGE_INFO_URL, metadata_dir / "oi-validation-images.csv")
    bbox = _download_metadata(OI_BBOX_URL, metadata_dir / "oi-validation-bbox.csv")
    classes = _download_metadata(OI_CLASS_URL, metadata_dir / "oi-boxable-classes.csv")
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    rows: list[dict[str, str]] = []
    for scene, source_quota in SOURCE_COUNTS.items():
        needed = source_quota["wikimedia_commons"]
        if needed:
            rows.extend(_commons_candidates(scene, needed, session))
    stats = _load_oi_stats(bbox, classes)
    used: set[str] = set()
    for scene, source_quota in SOURCE_COUNTS.items():
        needed = source_quota["open_images_v7"]
        if needed:
            rows.extend(_oi_candidates(scene, needed, image_info, stats, used))
    _complete_candidate_defaults(rows)
    rows.sort(key=lambda row: row["source_id"])
    validate_manifest(rows, require_approved=False, require_pixel_fields=False)
    _write_csv(manifest_path, MANIFEST_FIELDS, rows)
    _write_csv(dataset_dir / "source_audit.csv", MANIFEST_FIELDS, rows)
    _write_csv(dataset_dir / "targets.csv", list(TARGET), [TARGET])
    tasks = [
        {
            "task_id": f"{row['source_id']}__{TARGET['target_id']}",
            "source_id": row["source_id"],
            "target_id": TARGET["target_id"],
            "enabled": "true",
        }
        for row in rows
    ]
    _write_csv(dataset_dir / "tasks.csv", list(tasks[0]), tasks)
    commons_titles = sorted(
        row["upstream_id"] for row in rows if row["upstream_dataset"] == "wikimedia_commons"
    )
    oi_ids = sorted(
        row["upstream_id"] for row in rows if row["upstream_dataset"] == "open_images_v7"
    )
    (dataset_dir / "commons_file_titles.txt").write_text(
        "\n".join(commons_titles) + "\n", encoding="utf-8"
    )
    (dataset_dir / "openimages_validation_ids.txt").write_text(
        "\n".join(f"validation/{image_id}" for image_id in oi_ids) + "\n", encoding="utf-8"
    )
    _write_review_queue(dataset_dir, rows)
    return rows


def _write_review_queue(dataset_dir: Path, rows: list[dict[str, str]]) -> None:
    fields = [
        "source_id",
        "split",
        "scene_category",
        "upstream_dataset",
        "upstream_id",
        "official_source",
        "license_evidence_url",
        "license",
        "author",
        "expected_width",
        "expected_height",
        "difficulty_tier",
        "resolution_review_status",
        "license_review_status",
        "scene_review_status",
        "content_safety_status",
        "duplicate_review_status",
        "review_status",
        "review_notes",
    ]
    _write_csv(dataset_dir / "review_queue.csv", fields, rows)


def _stream_download(url: str) -> bytes:
    _validate_https_url(url, "source_url")
    headers = {"User-Agent": USER_AGENT}
    with requests.get(url, headers=headers, timeout=(15, 180), stream=True) as response:
        response.raise_for_status()
        final = urlparse(response.url)
        if final.scheme != "https" or final.hostname not in ALLOWED_URL_HOSTS:
            raise DatasetError(f"source redirected to unapproved host: {response.url}")
        content_type = response.headers.get("Content-Type", "").lower()
        if not content_type.startswith("image/") and "octet-stream" not in content_type:
            raise DatasetError(f"unexpected source Content-Type: {content_type}")
        buffer = io.BytesIO()
        for chunk in response.iter_content(1024 * 1024):
            if not chunk:
                continue
            buffer.write(chunk)
            if buffer.tell() > MAX_DOWNLOAD_BYTES:
                raise DatasetError("source exceeds 80 MiB")
    return buffer.getvalue()


def _sanitize_to_png(raw: bytes) -> tuple[bytes, int, int, str]:
    try:
        with Image.open(io.BytesIO(raw)) as opened:
            opened.load()
            oriented = ImageOps.exif_transpose(opened)
            if oriented.mode not in {"RGB", "RGBA"}:
                oriented = oriented.convert("RGB")
            clean = oriented.copy()
    except (OSError, Image.DecompressionBombError) as error:
        raise DatasetError("downloaded bytes are not a safe decodable image") from error
    output = io.BytesIO()
    clean.save(output, format="PNG", optimize=False)
    materialized = output.getvalue()
    with Image.open(io.BytesIO(materialized)) as verified:
        verified.load()
        if verified.getexif() or "gps" in {key.lower() for key in verified.info}:
            raise DatasetError("sanitized PNG still exposes EXIF/GPS")
        digest = _phash(verified)
        width, height = verified.size
    return materialized, width, height, digest


def download_review_pixels(
    dataset_dir: Path,
    local_dir: Path,
    *,
    offset: int = 0,
    limit: int | None = None,
) -> tuple[int, int]:
    manifest_path = dataset_dir / "source_manifest.csv"
    rows = _read_csv(manifest_path)
    for row in rows:
        row.setdefault("resolution_review_status", "pending_pixel_download")
    raw_dir = local_dir / "raw_cache"
    image_dir = local_dir / "images"
    raw_dir.mkdir(parents=True, exist_ok=True)
    image_dir.mkdir(parents=True, exist_ok=True)
    downloaded = 0
    failures = 0
    selected = rows[offset:] if limit is None else rows[offset : offset + limit]
    for row in selected:
        source_id = row["source_id"]
        raw_path = raw_dir / f"{source_id}.bin"
        image_path = _safe_local_path(local_dir, row["local_filename"])
        try:
            raw = (
                raw_path.read_bytes() if raw_path.is_file() else _stream_download(row["source_url"])
            )
            raw_digest = _sha256_bytes(raw)
            upstream_hash = row.get("upstream_hash", "")
            if upstream_hash.startswith("sha1:"):
                actual_sha1 = hashlib.sha1(raw, usedforsecurity=False).hexdigest()
                if actual_sha1 != upstream_hash.removeprefix("sha1:"):
                    raise DatasetError(f"official Commons SHA-1 changed for {source_id}")
            if not raw_path.is_file():
                with tempfile.NamedTemporaryFile(dir=raw_dir, delete=False) as handle:
                    handle.write(raw)
                    temporary = Path(handle.name)
                temporary.replace(raw_path)
            clean, width, height, perceptual = _sanitize_to_png(raw)
            image_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_image = image_path.with_suffix(".png.part")
            temporary_image.write_bytes(clean)
            temporary_image.replace(image_path)
            aspect, orientation, difficulty = _dimensions(width, height)
            resolution_passes = min(width, height) >= 1024 and max(width, height) >= 1600
            resolution_status = (
                "auto_dimensions_pass_pending_review"
                if resolution_passes
                else "rejected_below_1024x1600"
            )
            row.update(
                {
                    "raw_sha256": raw_digest,
                    "materialized_sha256": _sha256_bytes(clean),
                    "expected_width": str(width),
                    "expected_height": str(height),
                    "source_aspect": aspect,
                    "orientation": orientation,
                    "difficulty_tier": difficulty,
                    "resolution_review_status": resolution_status,
                    "phash": perceptual,
                    "download_status": "downloaded_review_copy",
                    "modification_notice": (
                        "metadata removed; EXIF orientation applied; decoded pixels "
                        "stored losslessly as PNG"
                    ),
                }
            )
            downloaded += 1
        except Exception as error:  # one failed public source must not erase prior evidence
            row["download_status"] = "download_failed"
            row["review_notes"] = (
                row.get("review_notes", "") + f" Download failure: {error}"
            ).strip()
            failures += 1
        _write_csv(manifest_path, MANIFEST_FIELDS, rows)
        _write_csv(dataset_dir / "source_audit.csv", MANIFEST_FIELDS, rows)
        _write_review_queue(dataset_dir, rows)
    _write_review_overview(rows, local_dir)
    return downloaded, failures


def _write_review_overview(rows: list[dict[str, str]], local_dir: Path) -> None:
    available = [
        row
        for row in rows
        if (local_dir / PurePosixPath(row.get("local_filename", "missing"))).is_file()
    ]
    if not available:
        return
    cell_width, cell_height = 320, 250
    columns = 4
    row_count = (len(available) + columns - 1) // columns
    overview = Image.new("RGB", (columns * cell_width, row_count * cell_height), "#171923")
    draw = ImageDraw.Draw(overview)
    for index, row in enumerate(available):
        image_path = local_dir / PurePosixPath(row["local_filename"])
        with Image.open(image_path) as opened:
            thumbnail = opened.convert("RGB")
            thumbnail.thumbnail((cell_width - 16, cell_height - 62), Image.Resampling.LANCZOS)
        x = (index % columns) * cell_width
        y = (index // columns) * cell_height
        overview.paste(
            thumbnail,
            (x + (cell_width - thumbnail.width) // 2, y + 46),
        )
        draw.text((x + 8, y + 6), row["source_id"], fill="#ffffff")
        label = f"{row['scene_category']} | {row['upstream_dataset']}"
        draw.text((x + 8, y + 24), label[:48], fill="#9dd6ff")
    overview.save(local_dir / "review_overview.png", format="PNG")


def validate_manifest(
    rows: list[dict[str, str]],
    *,
    require_approved: bool,
    require_pixel_fields: bool = True,
) -> dict[str, object]:
    errors: list[str] = []
    if len(rows) != 300:
        errors.append(f"expected 300 rows, found {len(rows)}")
    source_ids = [row.get("source_id", "") for row in rows]
    if len(set(source_ids)) != len(source_ids):
        errors.append("source_id values are not unique")
    upstream_keys = [(row.get("upstream_dataset", ""), row.get("upstream_id", "")) for row in rows]
    if len(set(upstream_keys)) != len(upstream_keys):
        errors.append("upstream source identities are not unique")
    actual_scenes = Counter(row.get("scene_category", "") for row in rows)
    if actual_scenes != Counter(SCENE_COUNTS):
        errors.append(f"scene quota mismatch: {dict(actual_scenes)}")
    actual_splits = Counter(row.get("split", "") for row in rows)
    if actual_splits != Counter({"pilot60": 60, "held-out240": 240}):
        errors.append(f"split quota mismatch: {dict(actual_splits)}")
    actual_sources = Counter(
        (row.get("scene_category", ""), row.get("upstream_dataset", "")) for row in rows
    )
    for scene, expected in SOURCE_COUNTS.items():
        for upstream, count in expected.items():
            if actual_sources[(scene, upstream)] != count:
                errors.append(
                    f"source quota mismatch for {scene}/{upstream}: "
                    f"{actual_sources[(scene, upstream)]} != {count}"
                )
    seen_raw: set[str] = set()
    seen_materialized: set[str] = set()
    for index, row in enumerate(rows, start=2):
        prefix = f"row {index} ({row.get('source_id', '<missing>')})"
        try:
            _validate_relative_posix(row.get("local_filename", ""))
            _validate_https_url(row.get("official_source", ""), "official_source")
            _validate_https_url(row.get("source_url", ""), "source_url")
            _validate_https_url(row.get("license_evidence_url", ""), "license_evidence_url")
            _validate_https_url(row.get("license_url", ""), "license_url")
        except DatasetError as error:
            errors.append(f"{prefix}: {error}")
        if row.get("license") not in ALLOWED_LICENSES:
            errors.append(f"{prefix}: disallowed license {row.get('license')!r}")
        if not row.get("author") or not row.get("attribution"):
            errors.append(f"{prefix}: author/attribution missing")
        if row.get("access_date") != ACCESS_DATE:
            errors.append(f"{prefix}: access_date must be {ACCESS_DATE}")
        if row.get("api_egress_allowed") != "false":
            errors.append(f"{prefix}: candidate freeze must deny API egress")
        if require_pixel_fields:
            for field in ("raw_sha256", "materialized_sha256"):
                value = row.get(field, "")
                if not re.fullmatch(r"[0-9a-f]{64}", value):
                    errors.append(f"{prefix}: invalid {field}")
            if row.get("difficulty_tier") not in DIFFICULTY_COUNTS:
                errors.append(f"{prefix}: ineligible difficulty tier")
            try:
                width = int(row.get("expected_width", ""))
                height = int(row.get("expected_height", ""))
                resolution_low = min(width, height) < 1024 or max(width, height) < 1600
                allowed_exception = (
                    row.get("scene_category") == "chinese_dense_poster"
                    and row.get("source_resolution_limited") == "true"
                )
                if resolution_low and not allowed_exception:
                    errors.append(f"{prefix}: resolution below 1024x1600 minimum")
            except ValueError:
                errors.append(f"{prefix}: missing dimensions")
            if row.get("raw_sha256") in seen_raw:
                errors.append(f"{prefix}: duplicate raw SHA-256")
            seen_raw.add(row.get("raw_sha256", ""))
            if row.get("materialized_sha256") in seen_materialized:
                errors.append(f"{prefix}: duplicate materialized SHA-256")
            seen_materialized.add(row.get("materialized_sha256", ""))
        if require_approved:
            required_statuses = {
                "license_review_status": "approved",
                "scene_review_status": "approved",
                "content_safety_status": "approved",
                "duplicate_review_status": "approved",
                "resolution_review_status": "approved",
                "review_status": "approved",
                "public_release_eligible": "true",
            }
            for field, expected in required_statuses.items():
                if row.get(field) != expected:
                    errors.append(f"{prefix}: {field} is not {expected}")
    if require_pixel_fields:
        actual_difficulty = Counter(row.get("difficulty_tier", "") for row in rows)
        if actual_difficulty != Counter(DIFFICULTY_COUNTS):
            errors.append(f"difficulty quota mismatch: {dict(actual_difficulty)}")
        limited = sum(row.get("source_resolution_limited") == "true" for row in rows)
        if limited > 6:
            errors.append(f"source_resolution_limited exceeds six: {limited}")
        for scene in SCENE_COUNTS:
            orientations = Counter(
                row.get("orientation", "") for row in rows if row.get("scene_category") == scene
            )
            if abs(orientations["landscape"] - orientations["portrait"]) > 1:
                errors.append(f"orientation imbalance for {scene}: {dict(orientations)}")
        try:
            _validate_phash_uniqueness(rows)
        except DatasetError as error:
            errors.append(str(error))
    if errors:
        raise DatasetError("manifest validation failed:\n- " + "\n- ".join(errors))
    return {
        "sources": len(rows),
        "splits": dict(actual_splits),
        "scenes": dict(actual_scenes),
        "upstreams": dict(Counter(row["upstream_dataset"] for row in rows)),
    }


def _fingerprint(rows: list[dict[str, str]], targets_path: Path, tasks_path: Path) -> str:
    digest = hashlib.sha256()
    for row in sorted(rows, key=lambda item: item["source_id"]):
        digest.update(json.dumps(row, sort_keys=True, ensure_ascii=False).encode())
        digest.update(b"\n")
    digest.update(targets_path.read_bytes())
    digest.update(tasks_path.read_bytes())
    return digest.hexdigest()


def finalize_dataset(dataset_dir: Path, local_dir: Path) -> dict[str, object]:
    rows = _read_csv(dataset_dir / "source_manifest.csv")
    summary = validate_manifest(rows, require_approved=True, require_pixel_fields=True)
    smoke_dir = local_dir.parent / "retarget_smoke_real_hd_v1" / "images"
    if smoke_dir.is_dir():
        smoke_hashes: dict[str, str] = {}
        for path in sorted(smoke_dir.iterdir()):
            if not path.is_file():
                continue
            with Image.open(path) as opened:
                smoke_hashes[path.name] = _phash(opened)
        minimum_smoke_distance = 64
        for row in rows:
            for smoke_name, smoke_hash in smoke_hashes.items():
                distance = phash_distance(row["phash"], smoke_hash)
                minimum_smoke_distance = min(minimum_smoke_distance, distance)
                if distance <= 4:
                    raise DatasetError(
                        f"candidate {row['source_id']} duplicates Smoke {smoke_name}: "
                        f"pHash distance {distance}"
                    )
        summary["minimum_smoke_phash_distance"] = minimum_smoke_distance
    targets_path = dataset_dir / "targets.csv"
    tasks_path = dataset_dir / "tasks.csv"
    _write_csv(targets_path, list(TARGET), [TARGET])
    tasks = [
        {
            "task_id": f"{row['source_id']}__{TARGET['target_id']}",
            "source_id": row["source_id"],
            "target_id": TARGET["target_id"],
            "enabled": "true",
        }
        for row in rows
    ]
    _write_csv(tasks_path, list(tasks[0]), tasks)
    fingerprint = _fingerprint(rows, targets_path, tasks_path)
    descriptor = {
        "schema_version": "1.0",
        "dataset_id": DATASET_ID,
        "version": "1.0.0",
        "description": "300 audited public real-world sources, one 1536x1536 task each.",
        "sources_file": "sources.csv",
        "targets_file": "targets.csv",
        "tasks_file": "tasks.csv",
        "source_audit_file": "source_audit.csv",
        "expected_source_count": 300,
        "dataset_fingerprint": fingerprint,
    }
    sources = [
        {
            "source_id": row["source_id"],
            "image_path": row["local_filename"],
            "width": row["expected_width"],
            "height": row["expected_height"],
            "sha256": row["materialized_sha256"],
            "split": row["split"],
            "scene_profile": "coverage" if row["scene_category"] != "portrait" else "precision",
            "enabled": "true",
            "source_kind": "public_real",
            "license_status": "audited",
            "scene_category": row["scene_category"],
            "fixture_type": "",
            "test_purpose": "",
        }
        for row in rows
    ]
    _write_csv(local_dir / "sources.csv", list(sources[0]), sources)
    shutil.copy2(targets_path, local_dir / "targets.csv")
    shutil.copy2(tasks_path, local_dir / "tasks.csv")
    shutil.copy2(dataset_dir / "source_audit.csv", local_dir / "source_audit.csv")
    (local_dir / "dataset.yaml").write_text(
        yaml.safe_dump(descriptor, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    (local_dir / "audit_rows.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    summary["dataset_fingerprint"] = fingerprint
    return summary


def status(dataset_dir: Path, local_dir: Path) -> dict[str, object]:
    manifest = dataset_dir / "source_manifest.csv"
    rows = _read_csv(manifest) if manifest.is_file() else []
    result: dict[str, object] = {
        "candidate_rows": len(rows),
        "download_status": dict(Counter(row.get("download_status", "") for row in rows)),
        "review_status": dict(Counter(row.get("review_status", "") for row in rows)),
        "difficulty": dict(Counter(row.get("difficulty_tier", "") for row in rows)),
        "local_images": len(list((local_dir / "images").glob("*.png")))
        if (local_dir / "images").is_dir()
        else 0,
    }
    try:
        validate_manifest(rows, require_approved=True, require_pixel_fields=True)
        result["final_validation"] = "PASS"
    except DatasetError as error:
        result["final_validation"] = "BLOCKED"
        result["blocker_summary"] = str(error).splitlines()[0]
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, default=DATASET_DIR)
    parser.add_argument("--local-dir", type=Path, default=LOCAL_DIR)
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser(
        "freeze-candidates", help="Freeze exact Commons titles and OI validation IDs."
    )
    download = subcommands.add_parser(
        "download-review", help="Download and sanitize frozen review pixels."
    )
    download.add_argument("--offset", type=int, default=0)
    download.add_argument("--limit", type=int)
    subcommands.add_parser(
        "validate-candidates", help="Validate exact counts/IDs without approval claims."
    )
    subcommands.add_parser(
        "finalize", help="Require every publication gate and create the local dataset."
    )
    subcommands.add_parser("status", help="Print candidate/download/review state.")
    return parser


def main() -> None:
    args = _parser().parse_args()
    try:
        if args.command == "freeze-candidates":
            rows = freeze_candidates(args.dataset_dir, args.local_dir)
            result: object = {"frozen_candidates": len(rows), "review_required": len(rows)}
        elif args.command == "download-review":
            downloaded, failures = download_review_pixels(
                args.dataset_dir,
                args.local_dir,
                offset=args.offset,
                limit=args.limit,
            )
            result = {"downloaded": downloaded, "failures": failures}
        elif args.command == "validate-candidates":
            rows = _read_csv(args.dataset_dir / "source_manifest.csv")
            result = validate_manifest(rows, require_approved=False, require_pixel_fields=False)
        elif args.command == "finalize":
            result = finalize_dataset(args.dataset_dir, args.local_dir)
        else:
            result = status(args.dataset_dir, args.local_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    except (DatasetError, requests.RequestException, OSError, csv.Error) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(2) from error


if __name__ == "__main__":
    main()
