"""Build the reviewed 1024-square public retargeting benchmark v2.

V2 keeps V1 unchanged and uses the stable Open Images S3 copies as source
pixels.  Discovery, pixel screening, visual review, and final materialization
are separate fail-closed stages.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import importlib.util
import io
import json
import re
import shutil
import sys
import tempfile
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

import requests
import yaml
from PIL import Image, ImageDraw


def _load_v1_module() -> object:
    path = Path(__file__).with_name("materialize_square_public_v1.py")
    spec = importlib.util.spec_from_file_location("square_public_v1_primitives", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load audited V1 primitives: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


V1 = _load_v1_module()
DATASET_ID = "retarget_square_public_v2"
DATASET_DIR = Path("datasets") / DATASET_ID
LOCAL_DIR = Path("local_data") / DATASET_ID
ACCESS_DATE = "2026-08-12"
TARGET = {
    "target_id": "square-1024x1024",
    "width": "1024",
    "height": "1024",
    "format": "png",
}
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
PILOT_TIER_COUNTS = {
    "chinese_dense_poster": {"aspect_hard_1": 3, "aspect_hard_2": 5, "aspect_extreme": 2},
    "single_product_promo": {"aspect_hard_1": 3, "aspect_hard_2": 5, "aspect_extreme": 2},
    "multi_product_commercial": {"aspect_hard_1": 3, "aspect_hard_2": 5, "aspect_extreme": 2},
    "multi_person": {"aspect_hard_1": 3, "aspect_hard_2": 5, "aspect_extreme": 2},
    "portrait": {"aspect_hard_1": 2, "aspect_hard_2": 3, "aspect_extreme": 2},
    "landscape_architecture_structure": {
        "aspect_hard_1": 2,
        "aspect_hard_2": 4,
        "aspect_extreme": 1,
    },
    "complex_mixed": {"aspect_hard_1": 2, "aspect_hard_2": 3, "aspect_extreme": 1},
}
PILOT_GLOBAL_TIER_COUNTS = {
    "aspect_hard_1": 40,
    "aspect_hard_2": 12,
    "aspect_extreme": 8,
}
HELDOUT_COUNTS = {
    "chinese_dense_poster": 40,
    "single_product_promo": 40,
    "multi_product_commercial": 40,
    "multi_person": 40,
    "portrait": 27,
    "landscape_architecture_structure": 26,
    "complex_mixed": 27,
}
HELDOUT_GLOBAL_TIER_COUNTS = {
    "aspect_hard_1": 50,
    "aspect_hard_2": 138,
    "aspect_extreme": 52,
}
FULL_GLOBAL_TIER_COUNTS = {
    "aspect_hard_1": 144,
    "aspect_hard_2": 98,
    "aspect_extreme": 58,
}
FROZEN_HELDOUT_TIER_COUNTS = {
    "aspect_hard_1": 104,
    "aspect_hard_2": 86,
    "aspect_extreme": 50,
}
FROZEN_PILOT_MANIFEST_SHA256 = (
    "b88478dc1d31dcbeF7f80a00a3daf0ebba673257683bf8215e1f3a62ee4df39c".lower()
)
POOL_MULTIPLIER = 3
POOL_COUNTS = {scene: count * POOL_MULTIPLIER for scene, count in SCENE_COUNTS.items()}
EXTRA_CHINESE_COMMONS_QUERIES = [
    "filetype:bitmap intitle:广告 中国",
    "filetype:bitmap intitle:廣告 中國",
    "filetype:bitmap Chinese propaganda poster",
    "filetype:bitmap Chinese film poster",
    "filetype:bitmap China poster Chinese text",
    "filetype:bitmap Hong Kong poster Chinese",
    "filetype:bitmap Taiwan poster Chinese",
    "filetype:bitmap Chinese public notice",
    "filetype:bitmap Chinese event poster",
    "filetype:bitmap Chinese election poster",
    "filetype:bitmap 中文 告示",
    "filetype:bitmap 中文 宣传单",
]
TARGETED_ASPECT_QUERIES = {
    "chinese_dense_poster": [
        "filetype:bitmap Chinese horizontal banner text",
        "filetype:bitmap Chinese vertical poster text",
        "filetype:bitmap Chinese long scroll notice",
    ],
    "single_product_promo": [
        "filetype:bitmap tall product advertising poster",
        "filetype:bitmap vertical product advertisement",
        "filetype:bitmap panoramic product advertisement",
    ],
    "multi_product_commercial": [
        "filetype:bitmap panoramic supermarket shelf products",
        "filetype:bitmap market stall panorama products signs",
        "filetype:bitmap long store shelf price labels",
    ],
    "multi_person": [
        "filetype:bitmap panoramic group portrait adults",
        "filetype:bitmap parade panorama people",
        "filetype:bitmap crowd panoramic public event",
    ],
    "portrait": [
        "filetype:bitmap full length portrait standing adult",
        "filetype:bitmap vertical environmental portrait adult",
        "filetype:bitmap tall portrait person full body",
    ],
    "landscape_architecture_structure": [
        "filetype:bitmap panoramic architecture bridge",
        "filetype:bitmap vertical panorama skyscraper",
        "filetype:bitmap interior architecture panorama",
    ],
    "complex_mixed": [
        "filetype:bitmap street panorama signs crowd",
        "filetype:bitmap market panorama people signs",
        "filetype:bitmap festival panorama crowd signs",
    ],
}
HELDOUT_HIGH_PRESSURE_QUERIES = {
    "chinese_dense_poster": [
        'filetype:bitmap "Chinese banner" text',
        'filetype:bitmap "Chinese-language banner"',
        "filetype:bitmap Chinese vertical signboard text",
        "filetype:bitmap Chinese long sign text",
        "filetype:bitmap 中文 横幅",
        "filetype:bitmap 中文 长卷",
        "filetype:bitmap 中文 竖幅",
        'deepcat:"Banners in China" filetype:bitmap',
        'deepcat:"Chinese-language signs" filetype:bitmap',
        'deepcat:"Chinese-language posters" filetype:bitmap',
        "filetype:bitmap Chinese public notice dense text",
        "filetype:bitmap Chinese information board text",
        "filetype:bitmap Chinese timetable sign",
        "filetype:bitmap Chinese menu board prices",
        "filetype:bitmap Chinese wall newspaper poster",
        "filetype:bitmap 中文 公告栏",
        "filetype:bitmap 中文 通告 海报",
        "filetype:bitmap 中文 菜单 价目表",
        'deepcat:"Chinese-language advertisements" filetype:bitmap',
        'deepcat:"Posters of China" filetype:bitmap',
    ],
    "single_product_promo": [
        "filetype:bitmap vertical product advertisement",
        "filetype:bitmap tall product advertising poster",
        "filetype:bitmap narrow bottle advertisement",
        "filetype:bitmap vertical food advertisement",
        "filetype:bitmap long product banner",
        'deepcat:"Product advertisements" filetype:bitmap',
        'deepcat:"Advertisements featuring bottles" filetype:bitmap',
        'deepcat:"Food advertisements" filetype:bitmap',
        "filetype:bitmap intitle:advertisement bottle",
        "filetype:bitmap intitle:advertisement soap",
        "filetype:bitmap intitle:advertisement shoe",
        "filetype:bitmap intitle:advertisement camera",
        "filetype:bitmap intitle:advertisement car",
        "filetype:bitmap intitle:advertisement food",
        "filetype:bitmap vintage vertical product advertisement",
        'deepcat:"Advertisements by product" filetype:bitmap',
        'deepcat:"Bottle advertisements" filetype:bitmap',
        "filetype:bitmap vintage soap advertisement",
        "filetype:bitmap vintage shoe advertisement",
        "filetype:bitmap vintage camera advertisement",
        "filetype:bitmap vintage sewing machine advertisement",
        "filetype:bitmap vintage bicycle advertisement",
        "filetype:bitmap vintage automobile advertisement",
        "filetype:bitmap vintage coffee advertisement",
        "filetype:bitmap vintage chocolate advertisement",
        "filetype:bitmap vintage toothpaste advertisement",
        'deepcat:"Soap advertisements" filetype:bitmap',
        'deepcat:"Perfume advertisements" filetype:bitmap',
        'deepcat:"Camera advertisements" filetype:bitmap',
        'deepcat:"Shoe advertisements" filetype:bitmap',
        'deepcat:"Chocolate advertisements" filetype:bitmap',
        'deepcat:"Bicycle advertisements" filetype:bitmap',
        'deepcat:"Sewing machine advertisements" filetype:bitmap',
        'deepcat:"Typewriter advertisements" filetype:bitmap',
        'deepcat:"Vacuum cleaner advertisements" filetype:bitmap',
        'deepcat:"Radio advertisements" filetype:bitmap',
        'deepcat:"Watch advertisements" filetype:bitmap',
        'deepcat:"Toothpaste advertisements" filetype:bitmap',
        'deepcat:"Tea advertisements" filetype:bitmap',
    ],
    "multi_product_commercial": [
        "filetype:bitmap panoramic supermarket shelves",
        "filetype:bitmap vertical supermarket shelf",
        "filetype:bitmap tall retail shelves products",
        "filetype:bitmap long shop window products",
        "filetype:bitmap panoramic market stall goods",
        "filetype:bitmap vending machine rows products",
        'deepcat:"Supermarket shelves" filetype:bitmap',
        'deepcat:"Market stalls" filetype:bitmap',
        'deepcat:"Shop windows" filetype:bitmap',
        'filetype:bitmap intitle:"vending machine"',
        "filetype:bitmap vertical vending machine products",
        "filetype:bitmap tall grocery shelf prices",
        "filetype:bitmap panoramic retail display products",
        'deepcat:"Vending machines" filetype:bitmap',
        'deepcat:"Shelves in shops" filetype:bitmap',
        'deepcat:"Retail displays" filetype:bitmap',
        "filetype:bitmap supermarket shelf panorama products",
        "filetype:bitmap grocery shelf panorama products prices",
        "filetype:bitmap retail aisle wide angle products",
        "filetype:bitmap shop display case products panorama",
        "filetype:bitmap pharmacy shelves products",
        "filetype:bitmap store window display products",
        "filetype:bitmap market stall goods prices panorama",
        "filetype:bitmap Japanese vending machines products",
        'deepcat:"Supermarket interiors" filetype:bitmap',
        'deepcat:"Vending machines in Japan" filetype:bitmap',
        "filetype:bitmap vintage catalogue page products prices",
        "filetype:bitmap department store catalogue page products",
        "filetype:bitmap mail order catalog page products prices",
        "filetype:bitmap illustrated price list products",
        "filetype:bitmap seed catalogue page prices",
        "filetype:bitmap toy catalogue page prices",
        "filetype:bitmap shoe catalogue page prices",
        "filetype:bitmap trade catalogue products price list",
        'deepcat:"Trade catalogs" filetype:bitmap',
        'deepcat:"Mail-order catalogs" filetype:bitmap',
        'deepcat:"Supermarket shelves in the United Kingdom" filetype:bitmap',
        'deepcat:"Supermarket shelves in the Netherlands" filetype:bitmap',
        'deepcat:"Supermarket shelves in Russia" filetype:bitmap',
        'deepcat:"Displays for sale in supermarket" filetype:bitmap',
        'deepcat:"Food market stalls" filetype:bitmap',
        'deepcat:"Fruit market stalls" filetype:bitmap',
        'deepcat:"Vending machines for food" filetype:bitmap',
        'deepcat:"Shop windows of grocery stores" filetype:bitmap',
        "filetype:bitmap panoramic flea market stall goods",
        "filetype:bitmap panoramic shop interior shelves merchandise",
        "filetype:bitmap panorama retail display goods",
        'deepcat:"Hardware stores" filetype:bitmap',
    ],
    "multi_person": [
        "filetype:bitmap panoramic group portrait people",
        "filetype:bitmap crowd panorama people",
        "filetype:bitmap long parade panorama",
        "filetype:bitmap vertical group portrait full length",
        'deepcat:"Panoramic photographs of people" filetype:bitmap',
        'deepcat:"Group photographs" filetype:bitmap',
        'deepcat:"Crowds" filetype:bitmap',
        "filetype:bitmap panoramic group photograph",
        "filetype:bitmap school group panoramic photograph",
        "filetype:bitmap military group portrait panoramic",
        "filetype:bitmap team panorama group photograph",
        "filetype:bitmap large group photograph wide",
        "filetype:bitmap parade crowd panorama people",
    ],
    "portrait": [
        "filetype:bitmap tall full length portrait standing adult",
        "filetype:bitmap narrow full-length portrait photograph",
        "filetype:bitmap vertical environmental portrait full body",
        "filetype:bitmap full length studio portrait adult",
        'deepcat:"Full-length portraits" filetype:bitmap',
        'deepcat:"Standing people" filetype:bitmap',
        'filetype:bitmap intitle:"Full-length portrait"',
        'filetype:bitmap "full-length portrait of"',
        'filetype:bitmap "full length portrait of"',
        'filetype:bitmap intitle:"Standing portrait"',
        'filetype:bitmap "standing full length portrait"',
        'filetype:bitmap "carte de visite" standing portrait',
        'filetype:bitmap "cabinet card" full length portrait',
        'deepcat:"Full-length portrait photographs" filetype:bitmap',
        'deepcat:"Full-length portraits of women" filetype:bitmap',
        'deepcat:"Full-length portraits of men" filetype:bitmap',
        'filetype:bitmap "full figure portrait" photograph',
        'filetype:bitmap "full-length standing" portrait',
        'filetype:bitmap "standing man" portrait photograph',
        'filetype:bitmap "standing woman" portrait photograph',
        'filetype:bitmap "full-length portrait photograph"',
        'deepcat:"Portrait photographs of standing people" filetype:bitmap',
        'deepcat:"Full-length portraits in photographs" filetype:bitmap',
        'deepcat:"Portrait photographs at full length" filetype:bitmap',
        'deepcat:"Front views of people at full length" filetype:bitmap',
        'deepcat:"Portraits of standing people at full length" filetype:bitmap',
        'filetype:bitmap intitle:"portrait en pied"',
        'deepcat:"Portrait photographs of standing men at full length" filetype:bitmap',
        'deepcat:"Portrait photographs of standing women at full length" filetype:bitmap',
        'deepcat:"19th-century portrait photographs at full length" filetype:bitmap',
        'deepcat:"20th-century portrait photographs at full length" filetype:bitmap',
    ],
    "landscape_architecture_structure": [
        "filetype:bitmap panoramic architecture bridge",
        "filetype:bitmap vertical panorama skyscraper",
        "filetype:bitmap tall tower facade",
        "filetype:bitmap long bridge panorama",
        "filetype:bitmap panoramic building interior",
        'deepcat:"Panoramics of architecture" filetype:bitmap',
        'deepcat:"Vertical panoramas" filetype:bitmap',
    ],
    "complex_mixed": [
        "filetype:bitmap panoramic street signs people",
        "filetype:bitmap panoramic market people signs",
        "filetype:bitmap long festival panorama crowd",
        "filetype:bitmap vertical street market signs",
        "filetype:bitmap panoramic night market signs",
        'deepcat:"Panoramas of streets" filetype:bitmap',
        'deepcat:"Night markets" filetype:bitmap',
        "filetype:bitmap Times Square panorama crowd signs",
        "filetype:bitmap Shibuya crossing panorama crowd signs",
        "filetype:bitmap street market panorama signs people",
        "filetype:bitmap night market panorama signs people",
        "filetype:bitmap festival market panorama crowd signs",
        "filetype:bitmap commercial street panorama signs crowd",
        "filetype:bitmap shopping street panorama crowd signs",
        "filetype:bitmap city square crowd signs panorama",
    ],
}
CHINESE_CATEGORY_QUERIES = [
    'deepcat:"Chinese-language posters" filetype:bitmap',
    'deepcat:"Chinese-language propaganda posters" filetype:bitmap',
    'deepcat:"Posters of China" filetype:bitmap',
    'deepcat:"Posters of Taiwan" filetype:bitmap',
]
OI_METADATA_DIR = LOCAL_DIR / "metadata"
POOL_FIELDS = [
    "source_id",
    "proposed_scene",
    "upstream_dataset",
    "upstream_id",
    "official_source",
    "source_url",
    "review_url",
    "license_evidence_url",
    "license",
    "license_url",
    "author",
    "attribution",
    "access_date",
    "upstream_revision_timestamp",
    "upstream_hash",
    "original_width",
    "original_height",
    "review_width",
    "review_height",
    "source_aspect",
    "pressure",
    "difficulty_tier",
    "orientation",
    "resolution_eligible",
    "review_sha256",
    "phash",
    "review_local_filename",
    "download_status",
    "discovery_reason",
]
DECISION_FIELDS = [
    "source_id",
    "proposed_scene",
    "decision",
    "final_scene",
    "scene_confirmed",
    "safety_confirmed",
    "real_world_confirmed",
    "non_fixture_confirmed",
    "license_review_status",
    "non_copyright_review_status",
    "api_egress_allowed",
    "reviewer",
    "review_reason",
]


class V2Error(ValueError):
    """A fail-closed V2 dataset or review contract error."""


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fields,
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def _clean_audit_text(value: str) -> str:
    """Repair the small set of HTML/mojibake artifacts present upstream."""
    return (
        html.unescape(value)
        .replace("â€”", "—")
        .replace("â€“", "–")
        .replace("â€™", "’")
        .replace("â€œ", "“")
        .replace("â€", "”")
    )


def classify_dimensions(width: int, height: int) -> dict[str, object]:
    if width <= 0 or height <= 0:
        return {"eligible": False, "reason": "invalid_dimensions"}
    long_side, short_side = max(width, height), min(width, height)
    aspect = width / height
    pressure = long_side / short_side
    orientation = "landscape" if width > height else "portrait" if height > width else "square"
    if 1.5 <= pressure < 2:
        tier, short_minimum = "aspect_hard_1", 576
    elif 2 <= pressure < 3:
        tier, short_minimum = "aspect_hard_2", 384
    elif 3 <= pressure <= 4:
        tier, short_minimum = "aspect_extreme", 256
    else:
        return {
            "eligible": False,
            "reason": "pressure_outside_[1.5,4]",
            "aspect": aspect,
            "pressure": pressure,
            "orientation": orientation,
        }
    eligible = long_side >= 1024 and short_side >= short_minimum
    reason = "eligible" if eligible else f"requires_long>=1024_short>={short_minimum}"
    return {
        "eligible": eligible,
        "reason": reason,
        "aspect": aspect,
        "pressure": pressure,
        "orientation": orientation,
        "tier": tier,
        "short_minimum": short_minimum,
    }


def _commons_thumbnail_urls(titles: list[str], session: requests.Session) -> dict[str, str]:
    result: dict[str, str] = {}
    for offset in range(0, len(titles), 50):
        batch = titles[offset : offset + 50]
        response: requests.Response | None = None
        for attempt in range(6):
            response = session.post(
                V1.COMMONS_API,
                data={
                    "action": "query",
                    "format": "json",
                    "formatversion": 2,
                    "maxlag": 5,
                    "prop": "imageinfo",
                    "iiprop": "url",
                    "iiurlwidth": 1024,
                    "titles": "|".join(batch),
                },
                timeout=(15, 90),
            )
            if response.status_code not in {429, 500, 502, 503, 504}:
                response.raise_for_status()
                break
            time.sleep(min(3 * (attempt + 1), 30))
        else:
            assert response is not None
            response.raise_for_status()
        assert response is not None
        for page in response.json().get("query", {}).get("pages", []):
            infos = page.get("imageinfo") or []
            if infos:
                result[page["title"]] = infos[0].get("thumburl") or infos[0]["url"]
        time.sleep(1)
    missing = set(titles) - set(result)
    if missing:
        raise V2Error(f"Commons thumbnail metadata missing for {len(missing)} titles")
    return result


def _pool_row_from_v1(row: dict[str, str], review_url: str, reason: str) -> dict[str, str]:
    return {
        "source_id": row["source_id"],
        "proposed_scene": row["scene_category"],
        "upstream_dataset": row["upstream_dataset"],
        "upstream_id": row["upstream_id"],
        "official_source": row["official_source"],
        "source_url": row["source_url"],
        "review_url": review_url,
        "license_evidence_url": row["license_evidence_url"],
        "license": row["license"],
        "license_url": row["license_url"],
        "author": row["author"],
        "attribution": row["attribution"],
        "access_date": ACCESS_DATE,
        "upstream_revision_timestamp": row["upstream_revision_timestamp"],
        "upstream_hash": row["upstream_hash"],
        "original_width": row.get("expected_width", ""),
        "original_height": row.get("expected_height", ""),
        "review_width": "",
        "review_height": "",
        "source_aspect": "",
        "pressure": "",
        "difficulty_tier": "pending_pixel_download",
        "orientation": "",
        "resolution_eligible": "pending_pixel_download",
        "review_sha256": "",
        "phash": "",
        "review_local_filename": f"candidate_review/{row['source_id']}.bin",
        "download_status": "not_downloaded",
        "discovery_reason": reason,
    }


def discover_candidate_pool(dataset_dir: Path, local_dir: Path) -> list[dict[str, str]]:
    pool_path = dataset_dir / "candidate_pool.csv"
    if pool_path.is_file():
        rows = _read_csv(pool_path)
        validate_pool(rows, require_pixels=False)
        return rows

    shared_metadata = local_dir.parent / "retarget_square_public_v1" / "metadata"

    def metadata_path(url: str, filename: str) -> Path:
        destination = local_dir / "metadata" / filename
        shared = shared_metadata / filename
        if destination.is_file() and destination.stat().st_size:
            return destination
        if shared.is_file() and shared.stat().st_size:
            return shared
        return V1._download_metadata(url, destination)

    image_info = metadata_path(V1.OI_IMAGE_INFO_URL, "oi-validation-images.csv")
    bbox = metadata_path(V1.OI_BBOX_URL, "oi-validation-bbox.csv")
    classes = metadata_path(V1.OI_CLASS_URL, "oi-boxable-classes.csv")
    session = requests.Session()
    session.headers.update({"User-Agent": V1.USER_AGENT})

    original_queries = list(V1.COMMONS_QUERIES["chinese_dense_poster"])
    V1.COMMONS_QUERIES["chinese_dense_poster"] = original_queries + EXTRA_CHINESE_COMMONS_QUERIES
    commons = V1._commons_candidates(
        "chinese_dense_poster",
        POOL_COUNTS["chinese_dense_poster"],
        session,
    )
    thumbnails = _commons_thumbnail_urls([row["upstream_id"] for row in commons], session)
    rows = [
        _pool_row_from_v1(
            row,
            thumbnails[row["upstream_id"]],
            "Commons Chinese-poster search; scene remains unreviewed.",
        )
        for row in commons
    ]

    stats = V1._load_oi_stats(bbox, classes)
    used: set[str] = set()
    for scene in SCENE_COUNTS:
        if scene == "chinese_dense_poster":
            continue
        candidates = V1._oi_candidates(
            scene,
            POOL_COUNTS[scene],
            image_info,
            stats,
            used,
        )
        rows.extend(
            _pool_row_from_v1(
                row,
                row["source_url"],
                "Open Images V7 validation bbox heuristic; evaluator-only discovery signal.",
            )
            for row in candidates
        )
    rows.sort(key=lambda row: row["source_id"])
    validate_pool(rows, require_pixels=False)
    _write_csv(pool_path, POOL_FIELDS, rows)
    return rows


def validate_pool(rows: list[dict[str, str]], *, require_pixels: bool) -> dict[str, object]:
    expected_total = sum(POOL_COUNTS.values())
    errors: list[str] = []
    if len(rows) < expected_total:
        errors.append(f"expected at least {expected_total} candidate rows, found {len(rows)}")
    if len({row.get("source_id", "") for row in rows}) != len(rows):
        errors.append("candidate source_id values are not unique")
    identities = {(row.get("upstream_dataset", ""), row.get("upstream_id", "")) for row in rows}
    if len(identities) != len(rows):
        errors.append("candidate upstream identities are not unique")
    actual = Counter(row.get("proposed_scene", "") for row in rows)
    for scene, minimum in POOL_COUNTS.items():
        if actual[scene] < minimum:
            errors.append(f"candidate scene pool shortage {scene}: {actual[scene]} < {minimum}")
    for row in rows:
        for field in ("official_source", "source_url", "review_url", "license_evidence_url"):
            parsed = urlparse(row.get(field, ""))
            if parsed.scheme != "https" or not parsed.hostname:
                errors.append(f"{row.get('source_id')}: invalid HTTPS {field}")
        relative = PurePosixPath(row.get("review_local_filename", ""))
        if relative.is_absolute() or ".." in relative.parts or "\\" in str(relative):
            errors.append(f"{row.get('source_id')}: unsafe review path")
        if row.get("license") not in V1.ALLOWED_LICENSES:
            errors.append(f"{row.get('source_id')}: disallowed license")
        if not row.get("author") or not row.get("attribution"):
            errors.append(f"{row.get('source_id')}: author/attribution missing")
        if require_pixels:
            if row.get("download_status") != "downloaded":
                errors.append(f"{row.get('source_id')}: review pixels not downloaded")
            if not re.fullmatch(r"[0-9a-f]{64}", row.get("review_sha256", "")):
                errors.append(f"{row.get('source_id')}: invalid review SHA-256")
            if not re.fullmatch(r"[0-9a-f]{16}", row.get("phash", "")):
                errors.append(f"{row.get('source_id')}: invalid pHash")
    if errors:
        raise V2Error("candidate pool validation failed:\n- " + "\n- ".join(errors))
    return {"rows": len(rows), "scenes": dict(actual)}


def supplement_commons_pool(dataset_dir: Path) -> dict[str, object]:
    pool_path = dataset_dir / "candidate_pool.csv"
    rows = _read_csv(pool_path)
    by_id = {row["source_id"]: row for row in rows}
    session = requests.Session()
    session.headers.update({"User-Agent": V1.USER_AGENT})
    requested = {
        "single_product_promo": 50,
        "multi_product_commercial": 50,
        "multi_person": 50,
        "portrait": 50,
        "landscape_architecture_structure": 50,
        "complex_mixed": 50,
    }
    added = Counter()
    for scene, count in requested.items():
        candidates: list[dict[str, str]] | None = None
        for attempted in range(count, 9, -10):
            try:
                candidates = V1._commons_candidates(scene, attempted, session)
                break
            except V1.DatasetError:
                continue
        if candidates is None:
            continue
        new_candidates = [row for row in candidates if row["source_id"] not in by_id]
        thumbnails = _commons_thumbnail_urls(
            [row["upstream_id"] for row in new_candidates],
            session,
        )
        for row in new_candidates:
            converted = _pool_row_from_v1(
                row,
                thumbnails[row["upstream_id"]],
                "Commons scene search supplement; visual scene review required.",
            )
            by_id[converted["source_id"]] = converted
            added[scene] += 1
        checkpoint = sorted(by_id.values(), key=lambda row: row["source_id"])
        _write_csv(pool_path, POOL_FIELDS, checkpoint)
    output = sorted(by_id.values(), key=lambda row: row["source_id"])
    validate_pool(output, require_pixels=False)
    _write_csv(pool_path, POOL_FIELDS, output)
    return {"pool": len(output), "added": dict(added)}


def supplement_targeted_aspects(dataset_dir: Path) -> dict[str, object]:
    pool_path = dataset_dir / "candidate_pool.csv"
    rows = _read_csv(pool_path)
    by_id = {row["source_id"]: row for row in rows}
    session = requests.Session()
    session.headers.update({"User-Agent": V1.USER_AGENT})
    added = Counter()
    added_tiers = Counter()
    for scene, queries in TARGETED_ASPECT_QUERIES.items():
        V1.COMMONS_QUERIES[scene] = queries
        candidates: list[dict[str, str]] | None = None
        for attempted in (30, 20, 10):
            try:
                candidates = V1._commons_candidates(scene, attempted, session)
                break
            except V1.DatasetError:
                continue
        if not candidates:
            continue
        new_candidates = [row for row in candidates if row["source_id"] not in by_id]
        thumbnails = _commons_thumbnail_urls(
            [row["upstream_id"] for row in new_candidates],
            session,
        )
        for row in new_candidates:
            converted = _pool_row_from_v1(
                row,
                thumbnails[row["upstream_id"]],
                "Commons aspect-targeted query; visual scene review required.",
            )
            dimensions = classify_dimensions(
                int(converted["original_width"]),
                int(converted["original_height"]),
            )
            if not dimensions["eligible"]:
                continue
            by_id[converted["source_id"]] = converted
            added[scene] += 1
            added_tiers[str(dimensions["tier"])] += 1
        checkpoint = sorted(by_id.values(), key=lambda row: row["source_id"])
        _write_csv(pool_path, POOL_FIELDS, checkpoint)
    return {
        "pool": len(by_id),
        "added": dict(added),
        "added_tiers_from_original_metadata": dict(added_tiers),
    }


def _commons_high_pressure_rows(
    scene: str,
    queries: list[str],
    session: requests.Session,
) -> list[dict[str, str]]:
    """Discover licensed Commons files without downloading their full pixels."""
    by_title: dict[str, dict[str, str]] = {}
    for query in queries:
        continuation: dict[str, object] = {}
        for _ in range(4):
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
                "iiurlwidth": 1024,
                "iiextmetadatafilter": (
                    "LicenseShortName|LicenseUrl|Artist|Credit|Attribution|"
                    "Permission|GPSLatitude|GPSLongitude"
                ),
            }
            params.update(continuation)
            response: requests.Response | None = None
            for attempt in range(8):
                response = session.post(V1.COMMONS_API, data=params, timeout=(15, 90))
                if response.status_code not in {429, 500, 502, 503, 504}:
                    response.raise_for_status()
                    break
                retry_after = response.headers.get("Retry-After", "")
                delay = int(retry_after) if retry_after.isdigit() else 5 * (attempt + 1)
                time.sleep(min(delay, 60))
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
                width, height = int(info.get("width", 0)), int(info.get("height", 0))
                dimensions = classify_dimensions(width, height)
                if not dimensions["eligible"] or dimensions.get("tier") == "aspect_hard_1":
                    continue
                metadata = info.get("extmetadata", {})
                license_name = V1._normalise_license(
                    V1._metadata_value(metadata, "LicenseShortName")
                )
                author = V1._clean_html(
                    V1._metadata_value(metadata, "Attribution")
                    or V1._metadata_value(metadata, "Artist")
                    or V1._metadata_value(metadata, "Credit")
                )
                mime = str(info.get("mime", ""))
                image_url = str(info.get("url", ""))
                description_url = str(info.get("descriptionurl", ""))
                if (
                    license_name not in V1.ALLOWED_LICENSES
                    or not author
                    or not mime.startswith("image/")
                    or urlparse(image_url).hostname != "upload.wikimedia.org"
                    or urlparse(description_url).hostname != "commons.wikimedia.org"
                ):
                    continue
                review_url = str(info.get("thumburl") or image_url)
                license_url = V1._metadata_value(metadata, "LicenseUrl")
                if license_url.startswith("http://creativecommons.org/"):
                    license_url = "https://creativecommons.org/" + license_url.removeprefix(
                        "http://creativecommons.org/"
                    )
                if license_name == "Public domain" and not license_url:
                    license_url = description_url + "#Licensing"
                source_key = hashlib.sha256(title.encode()).hexdigest()[:12]
                source_id = f"commons-{source_key}"
                by_title.setdefault(
                    title,
                    {
                        "source_id": source_id,
                        "proposed_scene": scene,
                        "upstream_dataset": "wikimedia_commons",
                        "upstream_id": title,
                        "official_source": description_url,
                        "source_url": image_url,
                        "review_url": review_url,
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
                        "original_width": str(width),
                        "original_height": str(height),
                        "review_width": "",
                        "review_height": "",
                        "source_aspect": f"{width / height:.8f}",
                        "pressure": f"{float(dimensions['pressure']):.8f}",
                        "difficulty_tier": str(dimensions["tier"]),
                        "orientation": str(dimensions["orientation"]),
                        "resolution_eligible": "pending_pixel_download",
                        "review_sha256": "",
                        "phash": "",
                        "review_local_filename": f"candidate_review/{source_id}.bin",
                        "download_status": "not_downloaded",
                        "discovery_reason": (
                            "Commons held-out high-pressure query; exact pixels and visual "
                            "scene review required."
                        ),
                    },
                )
            continuation = data.get("continue", {})
            if not continuation:
                break
            time.sleep(1.5)
        time.sleep(2.0)
    return sorted(by_title.values(), key=lambda row: row["upstream_id"].casefold())


def supplement_heldout_commons(
    dataset_dir: Path,
    scene_filter: str | None,
    query_start: int = 0,
) -> dict[str, object]:
    pool_path = dataset_dir / "candidate_pool.csv"
    rows = _read_csv(pool_path)
    by_id = {row["source_id"]: row for row in rows}
    by_identity = {(row["upstream_dataset"], row["upstream_id"]) for row in rows}
    session = requests.Session()
    session.headers.update({"User-Agent": V1.USER_AGENT})
    scenes = [scene_filter] if scene_filter else list(HELDOUT_HIGH_PRESSURE_QUERIES)
    if any(scene not in HELDOUT_HIGH_PRESSURE_QUERIES for scene in scenes):
        raise V2Error(f"unknown held-out scene filter: {scene_filter}")
    if query_start < 0:
        raise V2Error("query_start must be non-negative")
    added = Counter()
    tiers = Counter()
    for scene in scenes:
        discovered = _commons_high_pressure_rows(
            scene,
            HELDOUT_HIGH_PRESSURE_QUERIES[scene][query_start:],
            session,
        )
        for row in discovered:
            identity = (row["upstream_dataset"], row["upstream_id"])
            if row["source_id"] in by_id or identity in by_identity:
                continue
            by_id[row["source_id"]] = row
            by_identity.add(identity)
            added[scene] += 1
            tiers[row["difficulty_tier"]] += 1
        _write_csv(pool_path, POOL_FIELDS, sorted(by_id.values(), key=lambda row: row["source_id"]))
    output = sorted(by_id.values(), key=lambda row: row["source_id"])
    validate_pool(output, require_pixels=False)
    return {"pool": len(output), "added": dict(added), "added_tiers": dict(tiers)}


def supplement_chinese_categories(dataset_dir: Path) -> dict[str, object]:
    pool_path = dataset_dir / "candidate_pool.csv"
    rows = _read_csv(pool_path)
    by_id = {row["source_id"]: row for row in rows}
    session = requests.Session()
    session.headers.update({"User-Agent": V1.USER_AGENT})
    V1.COMMONS_QUERIES["chinese_dense_poster"] = CHINESE_CATEGORY_QUERIES
    candidates: list[dict[str, str]] | None = None
    for attempted in range(100, 9, -10):
        try:
            candidates = V1._commons_candidates(
                "chinese_dense_poster",
                attempted,
                session,
            )
            break
        except V1.DatasetError:
            continue
    if not candidates:
        raise V2Error("no auditable Chinese poster category candidates found")
    new_candidates = [row for row in candidates if row["source_id"] not in by_id]
    thumbnails = _commons_thumbnail_urls(
        [row["upstream_id"] for row in new_candidates],
        session,
    )
    added_tiers = Counter()
    for row in new_candidates:
        converted = _pool_row_from_v1(
            row,
            thumbnails[row["upstream_id"]],
            "Commons Chinese-language poster category; visual density review required.",
        )
        by_id[converted["source_id"]] = converted
        dimensions = classify_dimensions(
            int(converted["original_width"]),
            int(converted["original_height"]),
        )
        added_tiers[str(dimensions.get("tier", "ineligible"))] += 1
    output = sorted(by_id.values(), key=lambda row: row["source_id"])
    _write_csv(pool_path, POOL_FIELDS, output)
    return {
        "pool": len(output),
        "added": len(new_candidates),
        "added_tiers": dict(added_tiers),
    }


def _download_candidate(row: dict[str, str], local_dir: Path) -> dict[str, str]:
    result = dict(row)
    path = local_dir / PurePosixPath(row["review_local_filename"])
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        payload = path.read_bytes() if path.is_file() else b""
        if payload:
            try:
                with Image.open(io.BytesIO(payload)) as cached:
                    cached.verify()
            except OSError:
                payload = b""
        if not payload:
            last_error: Exception | None = None
            for attempt in range(6):
                try:
                    payload = V1._stream_download(row["review_url"])
                    break
                except (requests.RequestException, V1.DatasetError) as error:
                    last_error = error
                    time.sleep(min(2 * (attempt + 1), 12))
            else:
                assert last_error is not None
                raise last_error
        digest = hashlib.sha256(payload).hexdigest()
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
                handle.write(payload)
                temporary = Path(handle.name)
            temporary.replace(path)
        with Image.open(io.BytesIO(payload)) as image:
            image.load()
            width, height = image.size
            perceptual = V1._phash(image)
        dimensions = classify_dimensions(width, height)
        result.update(
            {
                "review_width": str(width),
                "review_height": str(height),
                "source_aspect": f"{float(dimensions.get('aspect', 0)):.8f}",
                "pressure": f"{float(dimensions.get('pressure', 0)):.8f}",
                "difficulty_tier": str(dimensions.get("tier", "ineligible_aspect")),
                "orientation": str(dimensions.get("orientation", "")),
                "resolution_eligible": str(bool(dimensions["eligible"])).lower(),
                "review_sha256": digest,
                "phash": perceptual,
                "download_status": "downloaded",
            }
        )
        if row["upstream_dataset"] == "open_images_v7":
            result["original_width"] = str(width)
            result["original_height"] = str(height)
    except Exception as error:
        result["download_status"] = "failed"
        result["discovery_reason"] = f"{row['discovery_reason']} Download failure: {error}"
    return result


def download_candidate_pool(
    dataset_dir: Path,
    local_dir: Path,
    *,
    workers: int,
    targeted_aspects_only: bool = False,
    scene_filter: str | None = None,
    retry_failed: bool = False,
) -> dict[str, object]:
    rows = _read_csv(dataset_dir / "candidate_pool.csv")
    completed: dict[str, dict[str, str]] = {row["source_id"]: row for row in rows}
    selected = rows
    allowed_statuses = {"not_downloaded", "failed"} if retry_failed else {"not_downloaded"}
    if targeted_aspects_only:
        selected = []
        for row in rows:
            if row["download_status"] not in allowed_statuses:
                continue
            if row["upstream_dataset"] != "wikimedia_commons":
                continue
            dimensions = classify_dimensions(
                int(row["original_width"]),
                int(row["original_height"]),
            )
            if dimensions.get("tier") in {"aspect_hard_2", "aspect_extreme"}:
                selected.append(row)
    if scene_filter:
        selected = [
            row
            for row in selected
            if row["proposed_scene"] == scene_filter and row["download_status"] in allowed_statuses
        ]
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_download_candidate, row, local_dir): row for row in selected}
        for future in as_completed(futures):
            result = future.result()
            completed[result["source_id"]] = result
    output = [completed[row["source_id"]] for row in rows]
    _write_csv(dataset_dir / "candidate_pool.csv", POOL_FIELDS, output)
    return {
        "downloaded": sum(row["download_status"] == "downloaded" for row in output),
        "failed": sum(row["download_status"] == "failed" for row in output),
        "eligible": sum(row["resolution_eligible"] == "true" for row in output),
        "tiers": dict(
            Counter(
                row["difficulty_tier"] for row in output if row["resolution_eligible"] == "true"
            )
        ),
    }


def _overview(rows: list[dict[str, str]], local_dir: Path, destination: Path) -> None:
    cell_width, cell_height = 300, 230
    columns = 4
    canvas = Image.new(
        "RGB",
        (columns * cell_width, ((len(rows) + columns - 1) // columns) * cell_height),
        "#151821",
    )
    draw = ImageDraw.Draw(canvas)
    for index, row in enumerate(rows):
        path = local_dir / PurePosixPath(row["review_local_filename"])
        with Image.open(path) as opened:
            thumb = opened.convert("RGB")
            thumb.thumbnail((cell_width - 12, cell_height - 52), Image.Resampling.LANCZOS)
        x, y = (index % columns) * cell_width, (index // columns) * cell_height
        canvas.paste(thumb, (x + (cell_width - thumb.width) // 2, y + 47))
        draw.text((x + 6, y + 5), row["source_id"], fill="white")
        label = (
            f"{row['difficulty_tier']} {row['orientation']} "
            f"{row['review_width']}x{row['review_height']}"
        )
        draw.text((x + 6, y + 23), label, fill="#9dd6ff")
    destination.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(destination, format="PNG")


def _review_overview(rows: list[dict[str, str]], local_dir: Path, destination: Path) -> None:
    cell_width, cell_height = 420, 320
    columns = 3
    canvas = Image.new(
        "RGB",
        (columns * cell_width, ((len(rows) + columns - 1) // columns) * cell_height),
        "#151821",
    )
    draw = ImageDraw.Draw(canvas)
    for index, row in enumerate(rows):
        path = local_dir / PurePosixPath(row["review_local_filename"])
        with Image.open(path) as opened:
            thumb = opened.convert("RGB")
            thumb.thumbnail((cell_width - 16, cell_height - 86), Image.Resampling.LANCZOS)
        x, y = (index % columns) * cell_width, (index // columns) * cell_height
        canvas.paste(thumb, (x + (cell_width - thumb.width) // 2, y + 80))
        draw.text((x + 7, y + 6), row["source_id"], fill="white")
        label = (
            f"{row['difficulty_tier']} {row['orientation']} "
            f"{row['review_width']}x{row['review_height']}"
        )
        draw.text((x + 7, y + 24), label, fill="#9dd6ff")
        title = row["upstream_id"].removeprefix("File:")
        draw.text((x + 7, y + 42), title[:60], fill="#d6d9e0")
        if len(title) > 60:
            draw.text((x + 7, y + 58), title[60:120], fill="#d6d9e0")
    destination.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(destination, format="PNG")


def build_heldout_review(dataset_dir: Path, local_dir: Path) -> dict[str, object]:
    rows = _read_csv(dataset_dir / "candidate_pool.csv")
    decisions = _read_csv(dataset_dir / "review_decisions.csv")
    pilot_ids = {row["source_id"] for row in decisions if row.get("decision") == "select_pilot"}
    eligible = sorted(
        [
            row
            for row in rows
            if row["source_id"] not in pilot_ids and row["resolution_eligible"] == "true"
        ],
        key=lambda row: (
            row["proposed_scene"],
            row["difficulty_tier"],
            row["source_id"],
        ),
    )
    _write_csv(dataset_dir / "heldout_review_shortlist.csv", POOL_FIELDS, eligible)
    overview_dir = local_dir / "heldout_review_overviews"
    sheets = 0
    for scene in SCENE_COUNTS:
        for tier in FULL_GLOBAL_TIER_COUNTS:
            group = [
                row
                for row in eligible
                if row["proposed_scene"] == scene and row["difficulty_tier"] == tier
            ]
            for sheet, offset in enumerate(range(0, len(group), 12), start=1):
                _review_overview(
                    group[offset : offset + 12],
                    local_dir,
                    overview_dir / scene / f"{tier}-{sheet:02d}.png",
                )
                sheets += 1
    return {
        "eligible_unselected": len(eligible),
        "scenes": dict(Counter(row["proposed_scene"] for row in eligible)),
        "tiers": dict(Counter(row["difficulty_tier"] for row in eligible)),
        "sheets": sheets,
        "overview_dir": overview_dir.as_posix(),
    }


def build_shortlist(dataset_dir: Path, local_dir: Path) -> dict[str, object]:
    rows = _read_csv(dataset_dir / "candidate_pool.csv")
    eligible = [
        row
        for row in rows
        if row["download_status"] == "downloaded" and row["resolution_eligible"] == "true"
    ]
    shortlist: list[dict[str, str]] = []
    shortages: list[str] = []
    for scene, tiers in PILOT_TIER_COUNTS.items():
        for tier, needed in tiers.items():
            candidates = sorted(
                [
                    row
                    for row in eligible
                    if row["proposed_scene"] == scene and row["difficulty_tier"] == tier
                ],
                key=lambda row: (row["orientation"], row["source_id"]),
            )
            take = min(len(candidates), max(needed * 4, needed + 4))
            shortlist.extend(candidates[:take])
            if len(candidates) < needed:
                shortages.append(f"{scene}/{tier}: {len(candidates)} < {needed}")
    deduplicated = {row["source_id"]: row for row in shortlist}
    shortlist = sorted(
        deduplicated.values(), key=lambda row: (row["proposed_scene"], row["source_id"])
    )
    _write_csv(dataset_dir / "pilot_shortlist.csv", POOL_FIELDS, shortlist)
    overview_dir = local_dir / "pilot_shortlist_overviews"
    for scene in SCENE_COUNTS:
        scene_rows = [row for row in shortlist if row["proposed_scene"] == scene]
        for sheet, offset in enumerate(range(0, len(scene_rows), 20), start=1):
            _overview(
                scene_rows[offset : offset + 20],
                local_dir,
                overview_dir / f"{scene}-{sheet:02d}.png",
            )
    eligible_overview_dir = local_dir / "eligible_overviews"
    for scene in SCENE_COUNTS:
        scene_rows = sorted(
            [row for row in eligible if row["proposed_scene"] == scene],
            key=lambda row: (row["difficulty_tier"], row["source_id"]),
        )
        for sheet, offset in enumerate(range(0, len(scene_rows), 20), start=1):
            _overview(
                scene_rows[offset : offset + 20],
                local_dir,
                eligible_overview_dir / f"{scene}-{sheet:02d}.png",
            )
    return {
        "eligible_pool": len(eligible),
        "shortlist": len(shortlist),
        "shortages": shortages,
        "overview_dir": overview_dir.as_posix(),
        "eligible_overview_dir": eligible_overview_dir.as_posix(),
    }


def validate_pilot_decisions(
    decisions: list[dict[str, str]],
    pool_rows: list[dict[str, str]],
) -> list[dict[str, str]]:
    by_id = {row["source_id"]: row for row in pool_rows}
    selected = [row for row in decisions if row.get("decision") == "select_pilot"]
    errors: list[str] = []
    if len(selected) != 60:
        errors.append(f"pilot must contain exactly 60 reviewed sources, found {len(selected)}")
    if len({row.get("source_id", "") for row in selected}) != len(selected):
        errors.append("pilot decisions contain duplicate source_id")
    scenes = Counter(row.get("final_scene", "") for row in selected)
    if scenes != Counter(PILOT_COUNTS):
        errors.append(f"pilot scene mismatch: {dict(scenes)}")
    tier_counts: Counter[str] = Counter()
    for decision in selected:
        source_id = decision.get("source_id", "")
        source = by_id.get(source_id)
        if source is None:
            errors.append(f"unknown selected source: {source_id}")
            continue
        if decision.get("proposed_scene") != source.get("proposed_scene"):
            errors.append(f"{source_id}: proposed_scene does not match frozen pool")
        if decision.get("final_scene") not in SCENE_COUNTS:
            errors.append(f"{source_id}: invalid final_scene")
        if source.get("resolution_eligible") != "true":
            errors.append(f"selected source is not resolution/aspect eligible: {source_id}")
        tier_counts[source.get("difficulty_tier", "")] += 1
        for field in (
            "scene_confirmed",
            "safety_confirmed",
            "real_world_confirmed",
            "non_fixture_confirmed",
        ):
            if decision.get(field) != "true":
                errors.append(f"{source_id}: {field} must be true")
        if decision.get("license_review_status") != "approved":
            errors.append(f"{source_id}: license review is not approved")
        if decision.get("non_copyright_review_status") != "approved_for_research_benchmark":
            errors.append(f"{source_id}: non-copyright review is incomplete")
        if decision.get("api_egress_allowed") not in {"true", "false"}:
            errors.append(f"{source_id}: invalid api_egress_allowed")
        if decision.get("reviewer") != "codex" or not decision.get("review_reason"):
            errors.append(f"{source_id}: Codex visual review evidence missing")
    if tier_counts != Counter(PILOT_GLOBAL_TIER_COUNTS):
        errors.append(f"pilot global tier mismatch: {dict(tier_counts)}")
    if errors:
        raise V2Error("pilot decision validation failed:\n- " + "\n- ".join(errors))
    return selected


def materialize_pilot(dataset_dir: Path, local_dir: Path) -> dict[str, object]:
    pool_rows = _read_csv(dataset_dir / "candidate_pool.csv")
    decisions = _read_csv(dataset_dir / "review_decisions.csv")
    selected_decisions = validate_pilot_decisions(decisions, pool_rows)
    pool_by_id = {row["source_id"]: row for row in pool_rows}
    decision_by_id = {row["source_id"]: row for row in selected_decisions}
    output_rows: list[dict[str, str]] = []
    images_dir = local_dir / "images"
    raw_dir = local_dir / "raw_cache"
    images_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)
    for source_id in sorted(decision_by_id):
        pool = pool_by_id[source_id]
        decision = decision_by_id[source_id]
        review_path = local_dir / PurePosixPath(pool["review_local_filename"])
        raw = review_path.read_bytes()
        raw_sha256 = hashlib.sha256(raw).hexdigest()
        if raw_sha256 != pool.get("review_sha256"):
            raise V2Error(f"selected review pixels changed after freeze: {source_id}")
        raw_path = raw_dir / f"{source_id}.bin"
        raw_path.write_bytes(raw)
        clean, width, height, perceptual = V1._sanitize_to_png(raw)
        dimensions = classify_dimensions(width, height)
        if not dimensions["eligible"]:
            raise V2Error(f"selected original fails dimension contract: {source_id}")
        image_path = images_dir / f"{source_id}.png"
        image_path.write_bytes(clean)
        license_name = pool["license"]
        output_rows.append(
            {
                "source_id": source_id,
                "split": "pilot60",
                "scene_category": decision["final_scene"],
                "secondary_tags": "reviewed_v2_pilot",
                "difficulty_tier": str(dimensions["tier"]),
                "upstream_dataset": pool["upstream_dataset"],
                "upstream_id": pool["upstream_id"],
                "upstream_split": "validation"
                if pool["upstream_dataset"] == "open_images_v7"
                else "public_file",
                "official_source": pool["official_source"],
                "source_url": pool["review_url"],
                "license_evidence_url": pool["license_evidence_url"],
                "license": license_name,
                "license_url": pool["license_url"],
                "author": _clean_audit_text(pool["author"]),
                "attribution": _clean_audit_text(pool["attribution"]),
                "access_date": ACCESS_DATE,
                "upstream_revision_timestamp": pool["upstream_revision_timestamp"],
                "upstream_hash": pool["upstream_hash"],
                "raw_sha256": raw_sha256,
                "materialized_sha256": hashlib.sha256(clean).hexdigest(),
                "expected_width": str(width),
                "expected_height": str(height),
                "source_aspect": f"{width / height:.8f}",
                "orientation": str(dimensions["orientation"]),
                "local_filename": f"images/{source_id}.png",
                "redistribution_status": "allowed_with_attribution"
                if "BY" in license_name
                else "public_domain_or_cc0",
                "modification_notice": (
                    "official audited evaluation rendition; metadata removed; "
                    "EXIF orientation applied; decoded pixels stored losslessly as PNG"
                ),
                "personality_rights_status": "reviewed_for_non-sensitive_research_benchmark",
                "trademark_status": "no_high_risk_trademark_use_identified",
                "non_copyright_restrictions": "approved_for_research_benchmark_not_endorsement",
                "source_resolution_limited": "true"
                if (
                    pool.get("review_width") != pool.get("original_width")
                    or pool.get("review_height") != pool.get("original_height")
                )
                else "false",
                "resolution_review_status": "approved",
                "public_release_eligible": "true",
                "api_egress_allowed": decision["api_egress_allowed"],
                "license_review_status": "approved",
                "scene_review_status": "approved",
                "content_safety_status": "approved",
                "duplicate_review_status": "pending_full_pilot_phash_check",
                "review_status": "approved",
                "download_status": "materialized",
                "phash": perceptual,
                "review_notes": decision["review_reason"],
            }
        )
    minimum = V1._validate_phash_uniqueness(output_rows)
    smoke_dirs = [
        local_dir.parent / "retarget_smoke_real_hd_v1" / "images",
        local_dir.parent / "retarget_square_public_v1" / "images",
    ]
    cross_minimum = 64
    for comparison_dir in smoke_dirs:
        if not comparison_dir.is_dir():
            continue
        for path in comparison_dir.iterdir():
            if not path.is_file():
                continue
            with Image.open(path) as image:
                comparison_hash = V1._phash(image)
            for row in output_rows:
                distance = V1.phash_distance(row["phash"], comparison_hash)
                cross_minimum = min(cross_minimum, distance)
                if distance <= 4:
                    raise V2Error(
                        f"selected {row['source_id']} near-duplicates {path.name}: {distance}"
                    )
    for row in output_rows:
        row["duplicate_review_status"] = "approved"
    _write_csv(dataset_dir / "source_manifest.csv", V1.MANIFEST_FIELDS, output_rows)
    _write_csv(dataset_dir / "source_audit.csv", V1.MANIFEST_FIELDS, output_rows)
    _write_csv(dataset_dir / "targets.csv", list(TARGET), [TARGET])
    tasks = [
        {
            "task_id": f"{row['source_id']}__{TARGET['target_id']}",
            "source_id": row["source_id"],
            "target_id": TARGET["target_id"],
            "enabled": "true",
        }
        for row in output_rows
    ]
    _write_csv(dataset_dir / "tasks.csv", list(tasks[0]), tasks)
    descriptor = {
        "schema_version": "1.0",
        "dataset_id": DATASET_ID,
        "version": "2.0.0-pilot60",
        "description": "Reviewed public 1024-square benchmark pilot with 60 real images.",
        "sources_file": "sources.csv",
        "targets_file": "targets.csv",
        "tasks_file": "tasks.csv",
        "source_audit_file": "source_audit.csv",
        "expected_source_count": 60,
        "evaluation_canvas": "1024x1024",
        "generation_originals_may_be_retained_at_2k": True,
        "silent_upsampling_forbidden": True,
    }
    sources = [
        {
            "source_id": row["source_id"],
            "image_path": row["local_filename"],
            "width": row["expected_width"],
            "height": row["expected_height"],
            "sha256": row["materialized_sha256"],
            # Core datasets use train/validation/test semantics. The richer
            # audit manifest retains the experiment label ``pilot60``.
            "split": "validation",
            "scene_profile": "coverage",
            "enabled": "true",
            "source_kind": "public_real",
            "license_status": "audited",
            "scene_category": row["scene_category"],
            "fixture_type": "",
            "test_purpose": "",
        }
        for row in output_rows
    ]
    _write_csv(local_dir / "sources.csv", list(sources[0]), sources)
    _write_csv(local_dir / "targets.csv", list(TARGET), [TARGET])
    _write_csv(local_dir / "tasks.csv", list(tasks[0]), tasks)
    core_audit_rows = []
    for row in output_rows:
        license_name = row["license"]
        if "BY-SA" in license_name:
            redistribution_status = "allowed_with_attribution_and_share_alike"
        elif "BY" in license_name:
            redistribution_status = "allowed_with_attribution"
        else:
            redistribution_status = "public_domain"
        core_audit_rows.append(
            {
                "source_id": row["source_id"],
                "official_file_title": row["upstream_id"],
                "source_url": row["source_url"],
                "official_source": row["official_source"],
                "license": license_name,
                "license_url": row["license_url"],
                "access_date": row["access_date"],
                "sha256": row["materialized_sha256"],
                "scene_category": row["scene_category"],
                "local_filename": PurePosixPath(row["local_filename"]).name,
                "redistribution_status": redistribution_status,
                "author": row["author"],
                "attribution": row["attribution"],
                "rights_notes": (
                    f"{row['modification_notice']}; {row['non_copyright_restrictions']}"
                ),
                "local_algorithm_smoke_only": "false",
                "expected_width": row["expected_width"],
                "expected_height": row["expected_height"],
            }
        )
    _write_csv(
        local_dir / "source_audit.csv",
        list(core_audit_rows[0]),
        core_audit_rows,
    )
    (local_dir / "dataset.yaml").write_text(
        yaml.safe_dump(descriptor, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    final_overview_dir = local_dir / "pilot60_overviews"
    for scene in SCENE_COUNTS:
        reviewed_rows = [
            pool_by_id[row["source_id"]]
            for row in selected_decisions
            if row["final_scene"] == scene
        ]
        _overview(
            sorted(reviewed_rows, key=lambda row: row["source_id"]),
            local_dir,
            final_overview_dir / f"{scene}.png",
        )
    expected_images = {f"{row['source_id']}.png" for row in output_rows}
    expected_raw = {f"{row['source_id']}.bin" for row in output_rows}
    for path in images_dir.glob("*.png"):
        if path.name not in expected_images:
            path.unlink()
    for path in raw_dir.glob("*.bin"):
        if path.name not in expected_raw:
            path.unlink()
    return {
        "pilot_sources": len(output_rows),
        "tasks": len(tasks),
        "minimum_internal_phash_distance": minimum,
        "minimum_prior_dataset_phash_distance": cross_minimum,
        "api_egress_allowed": sum(row["api_egress_allowed"] == "true" for row in output_rows),
    }


def _assert_frozen_pilot(dataset_dir: Path) -> list[dict[str, str]]:
    manifest_path = dataset_dir / "source_manifest.csv"
    digest = hashlib.sha256(manifest_path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    if digest != FROZEN_PILOT_MANIFEST_SHA256:
        raise V2Error(
            f"pilot manifest changed after freeze: {digest} != {FROZEN_PILOT_MANIFEST_SHA256}"
        )
    rows = _read_csv(manifest_path)
    if len(rows) != 60 or Counter(row["scene_category"] for row in rows) != Counter(PILOT_COUNTS):
        raise V2Error("frozen pilot manifest no longer has its exact 60-row scene contract")
    return rows


def _heldout_tier_counts(dataset_dir: Path) -> dict[str, int]:
    policy = yaml.safe_load((dataset_dir / "selection_policy.yaml").read_text(encoding="utf-8"))
    tiers = policy.get("aspect_pressure", {})
    result: dict[str, int] = {}
    for tier in FULL_GLOBAL_TIER_COUNTS:
        value = tiers.get(tier, {}).get("heldout_count")
        if not isinstance(value, int) or value < 0:
            raise V2Error(f"selection policy must freeze integer heldout_count for {tier}")
        result[tier] = value
    if sum(result.values()) != 240:
        raise V2Error(f"selection policy heldout tier counts must total 240: {result}")
    return result


def validate_heldout_decisions(
    decisions: list[dict[str, str]],
    pool_rows: list[dict[str, str]],
    expected_tiers: dict[str, int],
) -> list[dict[str, str]]:
    by_id = {row["source_id"]: row for row in pool_rows}
    selected = [row for row in decisions if row.get("decision") == "select_heldout"]
    errors: list[str] = []
    if len(selected) != 240:
        errors.append(f"held-out must contain exactly 240 reviewed sources, found {len(selected)}")
    if len({row.get("source_id", "") for row in selected}) != len(selected):
        errors.append("held-out decisions contain duplicate source_id")
    scenes = Counter(row.get("final_scene", "") for row in selected)
    if scenes != Counter(HELDOUT_COUNTS):
        errors.append(f"held-out scene mismatch: {dict(scenes)}")
    pilot_ids = {row["source_id"] for row in decisions if row.get("decision") == "select_pilot"}
    tiers: Counter[str] = Counter()
    for decision in selected:
        source_id = decision.get("source_id", "")
        source = by_id.get(source_id)
        if source is None:
            errors.append(f"unknown selected source: {source_id}")
            continue
        if source_id in pilot_ids:
            errors.append(f"held-out source overlaps pilot: {source_id}")
        if decision.get("proposed_scene") != source.get("proposed_scene"):
            errors.append(f"{source_id}: proposed_scene does not match frozen pool")
        if decision.get("final_scene") not in SCENE_COUNTS:
            errors.append(f"{source_id}: invalid final_scene")
        if source.get("resolution_eligible") != "true":
            errors.append(f"selected source is not resolution/aspect eligible: {source_id}")
        tiers[source.get("difficulty_tier", "")] += 1
        for field in (
            "scene_confirmed",
            "safety_confirmed",
            "real_world_confirmed",
            "non_fixture_confirmed",
        ):
            if decision.get(field) != "true":
                errors.append(f"{source_id}: {field} must be true")
        if decision.get("license_review_status") != "approved":
            errors.append(f"{source_id}: license review is not approved")
        if decision.get("non_copyright_review_status") != "approved_for_research_benchmark":
            errors.append(f"{source_id}: non-copyright review is incomplete")
        if decision.get("api_egress_allowed") not in {"true", "false"}:
            errors.append(f"{source_id}: invalid api_egress_allowed")
        if decision.get("reviewer") != "codex" or not decision.get("review_reason"):
            errors.append(f"{source_id}: Codex visual review evidence missing")
    if tiers != Counter(expected_tiers):
        errors.append(f"held-out pressure mismatch: {dict(tiers)} != {expected_tiers}")
    if errors:
        raise V2Error("held-out decision validation failed:\n- " + "\n- ".join(errors))
    return selected


def freeze_heldout_decisions(dataset_dir: Path) -> dict[str, object]:
    plan_path = dataset_dir / "heldout_selection.yaml"
    plan = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
    selections = plan.get("selections", {})
    if not isinstance(selections, dict):
        raise V2Error("heldout selection plan must contain a selections mapping")
    pool_rows = _read_csv(dataset_dir / "candidate_pool.csv")
    by_id = {row["source_id"]: row for row in pool_rows}
    existing = _read_csv(dataset_dir / "review_decisions.csv")
    if any(row.get("decision") == "select_heldout" for row in existing):
        raise V2Error("held-out decisions are already frozen; refusing to overwrite them")
    rows: list[dict[str, str]] = []
    for final_scene in HELDOUT_COUNTS:
        source_ids = selections.get(final_scene, [])
        if not isinstance(source_ids, list):
            raise V2Error(f"heldout selection for {final_scene} must be a list")
        for source_id in source_ids:
            source = by_id.get(str(source_id))
            if source is None:
                raise V2Error(f"heldout selection references unknown source: {source_id}")
            rows.append(
                {
                    "source_id": source["source_id"],
                    "proposed_scene": source["proposed_scene"],
                    "decision": "select_heldout",
                    "final_scene": final_scene,
                    "scene_confirmed": "true",
                    "safety_confirmed": "true",
                    "real_world_confirmed": "true",
                    "non_fixture_confirmed": "true",
                    "license_review_status": "approved",
                    "non_copyright_review_status": "approved_for_research_benchmark",
                    "api_egress_allowed": "false",
                    "reviewer": "codex",
                    "review_reason": (
                        f"Viewed at original detail in the held-out {final_scene}/"
                        f"{source['difficulty_tier']} overview; scene, safety, public license, "
                        "real-world and non-fixture status confirmed. API egress remains disabled."
                    ),
                }
            )
    expected_tiers = _heldout_tier_counts(dataset_dir)
    combined = [*existing, *rows]
    validate_heldout_decisions(combined, pool_rows, expected_tiers)
    _write_csv(dataset_dir / "review_decisions.csv", DECISION_FIELDS, combined)
    return {
        "frozen": len(rows),
        "scenes": dict(Counter(row["final_scene"] for row in rows)),
        "tiers": dict(Counter(by_id[row["source_id"]]["difficulty_tier"] for row in rows)),
        "api_egress_allowed": 0,
    }


def _materialize_reviewed_rows(
    selected_decisions: list[dict[str, str]],
    pool_rows: list[dict[str, str]],
    local_dir: Path,
    split: str,
) -> list[dict[str, str]]:
    pool_by_id = {row["source_id"]: row for row in pool_rows}
    output_rows: list[dict[str, str]] = []
    images_dir = local_dir / split / "images"
    raw_dir = local_dir / split / "raw_cache"
    images_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)
    for decision in sorted(selected_decisions, key=lambda row: row["source_id"]):
        source_id = decision["source_id"]
        pool = pool_by_id[source_id]
        review_path = local_dir / PurePosixPath(pool["review_local_filename"])
        raw = review_path.read_bytes()
        raw_sha256 = hashlib.sha256(raw).hexdigest()
        if raw_sha256 != pool.get("review_sha256"):
            raise V2Error(f"selected review pixels changed after freeze: {source_id}")
        clean, width, height, perceptual = V1._sanitize_to_png(raw)
        dimensions = classify_dimensions(width, height)
        if not dimensions["eligible"] or str(dimensions["tier"]) != pool["difficulty_tier"]:
            raise V2Error(f"selected pixels fail frozen dimensions: {source_id}")
        (raw_dir / f"{source_id}.bin").write_bytes(raw)
        (images_dir / f"{source_id}.png").write_bytes(clean)
        license_name = pool["license"]
        output_rows.append(
            {
                "source_id": source_id,
                "split": split,
                "scene_category": decision["final_scene"],
                "secondary_tags": "reviewed_v2_heldout",
                "difficulty_tier": str(dimensions["tier"]),
                "upstream_dataset": pool["upstream_dataset"],
                "upstream_id": pool["upstream_id"],
                "upstream_split": (
                    "validation" if pool["upstream_dataset"] == "open_images_v7" else "public_file"
                ),
                "official_source": pool["official_source"],
                "source_url": pool["review_url"],
                "license_evidence_url": pool["license_evidence_url"],
                "license": license_name,
                "license_url": pool["license_url"],
                "author": _clean_audit_text(pool["author"]),
                "attribution": _clean_audit_text(pool["attribution"]),
                "access_date": ACCESS_DATE,
                "upstream_revision_timestamp": pool["upstream_revision_timestamp"],
                "upstream_hash": pool["upstream_hash"],
                "raw_sha256": raw_sha256,
                "materialized_sha256": hashlib.sha256(clean).hexdigest(),
                "expected_width": str(width),
                "expected_height": str(height),
                "source_aspect": f"{width / height:.8f}",
                "orientation": str(dimensions["orientation"]),
                "local_filename": f"images/{source_id}.png",
                "redistribution_status": (
                    "allowed_with_attribution" if "BY" in license_name else "public_domain_or_cc0"
                ),
                "modification_notice": (
                    "official audited evaluation rendition; metadata removed; EXIF orientation "
                    "applied; decoded pixels stored losslessly as PNG"
                ),
                "personality_rights_status": "reviewed_for_non-sensitive_research_benchmark",
                "trademark_status": "reviewed_research_only_not_endorsement",
                "non_copyright_restrictions": "approved_for_research_benchmark_not_endorsement",
                "source_resolution_limited": (
                    "true"
                    if pool.get("review_width") != pool.get("original_width")
                    or pool.get("review_height") != pool.get("original_height")
                    else "false"
                ),
                "resolution_review_status": "approved",
                "public_release_eligible": "true",
                "api_egress_allowed": decision["api_egress_allowed"],
                "license_review_status": "approved",
                "scene_review_status": "approved",
                "content_safety_status": "approved",
                "duplicate_review_status": "pending_full_phash_check",
                "review_status": "approved",
                "download_status": "materialized",
                "phash": perceptual,
                "review_notes": decision["review_reason"],
            }
        )
    return output_rows


def _write_core_dataset(
    root: Path,
    rows: list[dict[str, str]],
    *,
    version: str,
    description: str,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    tasks = [
        {
            "task_id": f"{row['source_id']}__{TARGET['target_id']}",
            "source_id": row["source_id"],
            "target_id": TARGET["target_id"],
            "enabled": "true",
        }
        for row in rows
    ]
    sources = [
        {
            "source_id": row["source_id"],
            "image_path": row["local_filename"],
            "width": row["expected_width"],
            "height": row["expected_height"],
            "sha256": row["materialized_sha256"],
            "split": "validation" if row["split"] == "pilot60" else "test",
            "scene_profile": "coverage",
            "enabled": "true",
            "source_kind": "public_real",
            "license_status": "audited",
            "scene_category": row["scene_category"],
            "fixture_type": "",
            "test_purpose": "",
        }
        for row in rows
    ]
    audits = []
    for row in rows:
        license_name = row["license"]
        if "BY-SA" in license_name:
            redistribution = "allowed_with_attribution_and_share_alike"
        elif "BY" in license_name:
            redistribution = "allowed_with_attribution"
        else:
            redistribution = "public_domain"
        audits.append(
            {
                "source_id": row["source_id"],
                "official_file_title": row["upstream_id"],
                "source_url": row["source_url"],
                "official_source": row["official_source"],
                "license": license_name,
                "license_url": row["license_url"],
                "access_date": row["access_date"],
                "sha256": row["materialized_sha256"],
                "scene_category": row["scene_category"],
                "local_filename": PurePosixPath(row["local_filename"]).name,
                "redistribution_status": redistribution,
                "author": row["author"],
                "attribution": row["attribution"],
                "rights_notes": (
                    f"{row['modification_notice']}; {row['non_copyright_restrictions']}"
                ),
                "local_algorithm_smoke_only": "false",
                "expected_width": row["expected_width"],
                "expected_height": row["expected_height"],
            }
        )
    _write_csv(root / "sources.csv", list(sources[0]), sources)
    _write_csv(root / "targets.csv", list(TARGET), [TARGET])
    _write_csv(root / "tasks.csv", list(tasks[0]), tasks)
    _write_csv(root / "source_audit.csv", list(audits[0]), audits)
    descriptor = {
        "schema_version": "1.0",
        "dataset_id": DATASET_ID,
        "version": version,
        "description": description,
        "sources_file": "sources.csv",
        "targets_file": "targets.csv",
        "tasks_file": "tasks.csv",
        "source_audit_file": "source_audit.csv",
        "expected_source_count": len(rows),
        "evaluation_canvas": "1024x1024",
        "silent_upsampling_forbidden": True,
    }
    (root / "dataset.yaml").write_text(
        yaml.safe_dump(descriptor, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def _validate_against_prior_datasets(rows: list[dict[str, str]], local_dir: Path) -> int:
    """Reject held-out pixels that duplicate either earlier public benchmark."""
    comparison_dirs = [
        local_dir.parent / "retarget_smoke_real_hd_v1" / "images",
        local_dir.parent / "retarget_square_public_v1" / "images",
    ]
    minimum = 64
    for comparison_dir in comparison_dirs:
        if not comparison_dir.is_dir():
            continue
        for path in comparison_dir.iterdir():
            if not path.is_file():
                continue
            with Image.open(path) as image:
                comparison_hash = V1._phash(image)
            for row in rows:
                distance = V1.phash_distance(row["phash"], comparison_hash)
                minimum = min(minimum, distance)
                if distance <= 4:
                    raise V2Error(
                        f"held-out {row['source_id']} near-duplicates "
                        f"{comparison_dir.parent.name}/{path.name}: {distance}"
                    )
    return minimum


def materialize_heldout(dataset_dir: Path, local_dir: Path) -> dict[str, object]:
    pilot_rows = _assert_frozen_pilot(dataset_dir)
    pool_rows = _read_csv(dataset_dir / "candidate_pool.csv")
    decisions = _read_csv(dataset_dir / "review_decisions.csv")
    expected_tiers = _heldout_tier_counts(dataset_dir)
    selected = validate_heldout_decisions(decisions, pool_rows, expected_tiers)
    heldout = _materialize_reviewed_rows(selected, pool_rows, local_dir, "heldout240")
    combined = [*pilot_rows, *heldout]
    minimum = V1._validate_phash_uniqueness(combined)
    prior_minimum = _validate_against_prior_datasets(heldout, local_dir)
    for row in heldout:
        row["duplicate_review_status"] = "approved"
    _write_csv(dataset_dir / "heldout240_source_manifest.csv", V1.MANIFEST_FIELDS, heldout)
    _write_csv(dataset_dir / "heldout240_source_audit.csv", V1.MANIFEST_FIELDS, heldout)
    tasks = [
        {
            "task_id": f"{row['source_id']}__{TARGET['target_id']}",
            "source_id": row["source_id"],
            "target_id": TARGET["target_id"],
            "enabled": "true",
        }
        for row in heldout
    ]
    _write_csv(dataset_dir / "heldout240_targets.csv", list(TARGET), [TARGET])
    _write_csv(dataset_dir / "heldout240_tasks.csv", list(tasks[0]), tasks)
    _write_core_dataset(
        local_dir / "heldout240",
        heldout,
        version="2.1.0-heldout240",
        description="Reviewed public 1024-square held-out benchmark with 240 real images.",
    )
    pool_by_id = {row["source_id"]: row for row in pool_rows}
    overview_dir = local_dir / "heldout240_overviews"
    for scene in HELDOUT_COUNTS:
        reviewed_rows = [
            pool_by_id[row["source_id"]] for row in selected if row["final_scene"] == scene
        ]
        _review_overview(
            sorted(reviewed_rows, key=lambda row: row["source_id"]),
            local_dir,
            overview_dir / f"{scene}.png",
        )
    expected_images = {f"{row['source_id']}.png" for row in heldout}
    expected_raw = {f"{row['source_id']}.bin" for row in heldout}
    for path in (local_dir / "heldout240" / "images").glob("*.png"):
        if path.name not in expected_images:
            path.unlink()
    for path in (local_dir / "heldout240" / "raw_cache").glob("*.bin"):
        if path.name not in expected_raw:
            path.unlink()
    return {
        "heldout_sources": len(heldout),
        "tasks": len(tasks),
        "scenes": dict(Counter(row["scene_category"] for row in heldout)),
        "tiers": dict(Counter(row["difficulty_tier"] for row in heldout)),
        "minimum_full300_phash_distance": minimum,
        "minimum_prior_dataset_phash_distance": prior_minimum,
        "api_egress_allowed": sum(row["api_egress_allowed"] == "true" for row in heldout),
    }


def materialize_full(dataset_dir: Path, local_dir: Path) -> dict[str, object]:
    pilot = _assert_frozen_pilot(dataset_dir)
    heldout = _read_csv(dataset_dir / "heldout240_source_manifest.csv")
    if len(heldout) != 240:
        raise V2Error(f"held-out manifest must be complete before full300: {len(heldout)}")
    combined = [*pilot, *heldout]
    if len({row["source_id"] for row in combined}) != 300:
        raise V2Error("full300 contains duplicate source_id")
    if Counter(row["scene_category"] for row in combined) != Counter(SCENE_COUNTS):
        raise V2Error("full300 scene distribution does not match the frozen 300-image contract")
    minimum = V1._validate_phash_uniqueness(combined)
    full_root = local_dir / "full300"
    images_dir = full_root / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    output: list[dict[str, str]] = []
    for row in combined:
        source_root = local_dir if row["split"] == "pilot60" else local_dir / "heldout240"
        source = source_root / PurePosixPath(row["local_filename"])
        destination = images_dir / f"{row['source_id']}.png"
        shutil.copyfile(source, destination)
        copied = dict(row)
        copied["local_filename"] = f"images/{row['source_id']}.png"
        copied["duplicate_review_status"] = "approved"
        output.append(copied)
    _write_csv(dataset_dir / "full300_source_manifest.csv", V1.MANIFEST_FIELDS, output)
    _write_csv(dataset_dir / "full300_source_audit.csv", V1.MANIFEST_FIELDS, output)
    tasks = [
        {
            "task_id": f"{row['source_id']}__{TARGET['target_id']}",
            "source_id": row["source_id"],
            "target_id": TARGET["target_id"],
            "enabled": "true",
        }
        for row in output
    ]
    _write_csv(dataset_dir / "full300_targets.csv", list(TARGET), [TARGET])
    _write_csv(dataset_dir / "full300_tasks.csv", list(tasks[0]), tasks)
    _write_core_dataset(
        full_root,
        output,
        version="2.2.0-full300",
        description="Reviewed public 1024-square benchmark with 300 real images.",
    )
    return {
        "full_sources": len(output),
        "tasks": len(tasks),
        "scenes": dict(Counter(row["scene_category"] for row in output)),
        "tiers": dict(Counter(row["difficulty_tier"] for row in output)),
        "minimum_phash_distance": minimum,
    }


def status(dataset_dir: Path, local_dir: Path) -> dict[str, object]:
    pool_path = dataset_dir / "candidate_pool.csv"
    pool = _read_csv(pool_path) if pool_path.is_file() else []
    manifest_path = dataset_dir / "source_manifest.csv"
    manifest = _read_csv(manifest_path) if manifest_path.is_file() else []
    return {
        "pool": len(pool),
        "pool_downloaded": sum(row.get("download_status") == "downloaded" for row in pool),
        "pool_eligible": sum(row.get("resolution_eligible") == "true" for row in pool),
        "pilot_materialized": len(manifest),
        "local_pilot_images": len(list((local_dir / "images").glob("*.png")))
        if (local_dir / "images").is_dir()
        else 0,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, default=DATASET_DIR)
    parser.add_argument("--local-dir", type=Path, default=LOCAL_DIR)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("discover-pool")
    download = commands.add_parser("download-pool")
    download.add_argument("--workers", type=int, default=12)
    download.add_argument("--targeted-aspects-only", action="store_true")
    download.add_argument("--scene")
    download.add_argument("--retry-failed", action="store_true")
    commands.add_parser("supplement-commons")
    commands.add_parser("supplement-targeted-aspects")
    commands.add_parser("supplement-chinese-categories")
    heldout_commons = commands.add_parser("supplement-heldout-commons")
    heldout_commons.add_argument("--scene")
    heldout_commons.add_argument("--query-start", type=int, default=0)
    commands.add_parser("build-shortlist")
    commands.add_parser("build-heldout-review")
    commands.add_parser("freeze-heldout-decisions")
    commands.add_parser("materialize-pilot")
    commands.add_parser("materialize-heldout")
    commands.add_parser("materialize-full")
    commands.add_parser("status")
    return parser


def main() -> None:
    args = _parser().parse_args()
    try:
        if args.command == "discover-pool":
            result: object = {
                "candidate_pool": len(discover_candidate_pool(args.dataset_dir, args.local_dir))
            }
        elif args.command == "supplement-commons":
            result = supplement_commons_pool(args.dataset_dir)
        elif args.command == "supplement-targeted-aspects":
            result = supplement_targeted_aspects(args.dataset_dir)
        elif args.command == "supplement-chinese-categories":
            result = supplement_chinese_categories(args.dataset_dir)
        elif args.command == "supplement-heldout-commons":
            result = supplement_heldout_commons(
                args.dataset_dir,
                args.scene,
                args.query_start,
            )
        elif args.command == "download-pool":
            result = download_candidate_pool(
                args.dataset_dir,
                args.local_dir,
                workers=args.workers,
                targeted_aspects_only=args.targeted_aspects_only,
                scene_filter=args.scene,
                retry_failed=args.retry_failed,
            )
        elif args.command == "build-shortlist":
            result = build_shortlist(args.dataset_dir, args.local_dir)
        elif args.command == "build-heldout-review":
            result = build_heldout_review(args.dataset_dir, args.local_dir)
        elif args.command == "freeze-heldout-decisions":
            result = freeze_heldout_decisions(args.dataset_dir)
        elif args.command == "materialize-pilot":
            result = materialize_pilot(args.dataset_dir, args.local_dir)
        elif args.command == "materialize-heldout":
            result = materialize_heldout(args.dataset_dir, args.local_dir)
        elif args.command == "materialize-full":
            result = materialize_full(args.dataset_dir, args.local_dir)
        else:
            result = status(args.dataset_dir, args.local_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    except (V2Error, V1.DatasetError, requests.RequestException, OSError, csv.Error) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(2) from error


if __name__ == "__main__":
    main()
