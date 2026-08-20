"""Materialize the audited 12-image real-world Smoke dataset."""

from __future__ import annotations

import csv
import hashlib
import io
import math
import shutil
from collections import Counter
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests
import yaml
from PIL import Image, ImageDraw, ImageOps
from pydantic import ValidationError

from .datasets import FolderCsvDatasetAdapter
from .models import SourceAuditRecord

EXPECTED_SCENE_COUNTS = {
    "chinese_text_poster": 2,
    "single_product_promo": 2,
    "multi_product_commercial": 2,
    "multi_person": 2,
    "portrait": 1,
    "landscape_architecture": 1,
    "complex_mixed": 2,
}
ALLOWED_DOWNLOAD_HOSTS = {
    "commons.wikimedia.org",
    "upload.wikimedia.org",
    "images.cocodataset.org",
}
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
TARGETS = (
    {"target_id": "square-256x256", "width": 256, "height": 256, "format": "png"},
    {"target_id": "wide-384x200", "width": 384, "height": 200, "format": "png"},
    {"target_id": "portrait-216x384", "width": 216, "height": 384, "format": "png"},
)
HD_TARGETS = (
    {"target_id": "square-1024x1024", "width": 1024, "height": 1024, "format": "png"},
    {"target_id": "wide-1536x800", "width": 1536, "height": 800, "format": "png"},
    {"target_id": "portrait-864x1536", "width": 864, "height": 1536, "format": "png"},
)
SQUARE_BENCHMARK_TARGETS = (
    {"target_id": "square-1536x1536", "width": 1536, "height": 1536, "format": "png"},
)
SCENE_PROFILES = {
    "chinese_text_poster": "coverage",
    "single_product_promo": "precision",
    "multi_product_commercial": "coverage",
    "multi_person": "coverage",
    "portrait": "precision",
    "landscape_architecture": "precision",
    "complex_mixed": "coverage",
}


def _read_manifest(path: Path) -> list[SourceAuditRecord]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    try:
        records = [SourceAuditRecord.model_validate(row) for row in rows]
    except ValidationError as error:
        raise ValueError(f"invalid real Smoke source manifest: {error}") from error
    if len(records) != 12 or len({record.source_id for record in records}) != 12:
        raise ValueError("real Smoke source manifest must contain 12 unique source_id values")
    counts = Counter(record.scene_category for record in records)
    if dict(counts) != EXPECTED_SCENE_COUNTS:
        message = (
            f"real Smoke scene counts mismatch: expected={EXPECTED_SCENE_COUNTS}, "
            f"actual={dict(counts)}"
        )
        raise ValueError(message)
    return records


def _verify_official_metadata(records: list[SourceAuditRecord]) -> None:
    """Recheck official File identity and license immediately before downloading."""
    headers = {"User-Agent": "retarget-agent/0.1 real-smoke-license-audit"}
    titles = "|".join(record.official_file_title for record in records)
    try:
        response = requests.post(
            COMMONS_API,
            headers=headers,
            data={
                "action": "query",
                "format": "json",
                "prop": "imageinfo",
                "iiprop": "url|extmetadata",
                "titles": titles,
            },
            timeout=(10, 60),
        )
        response.raise_for_status()
        pages = response.json()["query"]["pages"].values()
    except (requests.RequestException, KeyError, ValueError) as error:
        raise ConnectionError(
            "official Wikimedia Commons license preflight failed; rerun with network access"
        ) from error
    by_title = {page["title"]: page for page in pages}
    for record in records:
        page = by_title.get(record.official_file_title)
        if not page or "imageinfo" not in page:
            raise ValueError(f"official File page not found: {record.official_file_title}")
        image_info = page["imageinfo"][0]
        metadata = image_info.get("extmetadata", {})
        current_license = metadata.get("LicenseShortName", {}).get("value", "")
        if current_license != record.license:
            raise ValueError(
                f"license changed for {record.source_id}: {current_license!r} != {record.license!r}"
            )
        current_license_url = metadata.get("LicenseUrl", {}).get("value", "")
        public_domain_evidence = (
            record.license == "Public domain"
            and not current_license_url
            and record.license_url == f"{record.official_source}#Licensing"
        )
        if (
            current_license_url.rstrip("/") != record.license_url.rstrip("/")
            and not public_domain_evidence
        ):
            raise ValueError(f"license URL changed for {record.source_id}")
        official_page = image_info.get("descriptionurl", "")
        expected_title = unquote(urlparse(official_page).path.rsplit("/", 1)[-1]).replace("_", " ")
        if expected_title != record.official_file_title:
            raise ValueError(f"official File identity changed for {record.source_id}")
        if official_page.rstrip("/") != record.official_source.rstrip("/"):
            raise ValueError(f"official source URL changed for {record.source_id}")
        if urlparse(image_info.get("url", "")).hostname != "upload.wikimedia.org":
            raise ValueError(
                f"official original is not on Wikimedia upload host: {record.source_id}"
            )


def _download(record: SourceAuditRecord, destination: Path) -> None:
    if destination.is_file():
        digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        if digest != record.sha256:
            raise ValueError(f"existing file hash mismatch for {record.source_id}")
        return
    parsed = urlparse(record.source_url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_DOWNLOAD_HOSTS:
        raise ValueError(f"source URL is not on an approved official host: {record.source_url}")
    headers = {"User-Agent": "retarget-agent/0.1 real-smoke-materializer"}
    try:
        response = requests.get(record.source_url, headers=headers, timeout=(10, 60), stream=True)
        response.raise_for_status()
    except requests.RequestException as error:
        raise ConnectionError(
            f"download failed for {record.source_id}; rerun with network access: {error}"
        ) from error
    final_host = urlparse(response.url).hostname
    if final_host not in ALLOWED_DOWNLOAD_HOSTS:
        raise ValueError(f"download redirected to an unapproved host: {response.url}")
    maximum_bytes = 30 * 1024 * 1024
    buffer = io.BytesIO()
    for chunk in response.iter_content(chunk_size=1024 * 1024):
        if not chunk:
            continue
        buffer.write(chunk)
        if buffer.tell() > maximum_bytes:
            raise ValueError(f"download exceeds 30 MiB limit: {record.source_id}")
    payload = buffer.getvalue()
    digest = hashlib.sha256(payload).hexdigest()
    if digest != record.sha256:
        raise ValueError(
            f"download hash mismatch for {record.source_id}: {digest} != {record.sha256}"
        )
    try:
        with Image.open(io.BytesIO(payload)) as image:
            image.verify()
        with Image.open(io.BytesIO(payload)) as image:
            size = ImageOps.exif_transpose(image).size
    except OSError as error:
        raise ValueError(f"download is not a decodable image: {record.source_id}") from error
    if size != (record.expected_width, record.expected_height):
        raise ValueError(
            f"image dimensions changed for {record.source_id}: "
            f"{size} != {(record.expected_width, record.expected_height)}"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    temporary.write_bytes(payload)
    temporary.replace(destination)


def _select_targets(
    width: int,
    height: int,
    targets: tuple[dict[str, object], ...] = TARGETS,
    target_count: int = 2,
    minimum_pressure: float = 0.25,
) -> tuple[dict[str, object], ...]:
    if target_count < 1 or target_count > len(targets):
        raise ValueError("target_count must select at least one available target")
    source_ratio = width / height
    ranked = sorted(
        targets,
        key=lambda target: abs(math.log((target["width"] / target["height"]) / source_ratio)),
        reverse=True,
    )
    selected = tuple(ranked[:target_count])
    pressures = [
        abs(math.log((target["width"] / target["height"]) / source_ratio)) for target in selected
    ]
    if min(pressures) < minimum_pressure:
        raise ValueError(
            f"could not choose two non-trivial target ratios for source ratio {source_ratio:.4f}"
        )
    return selected


def _csv_text(fieldnames: list[str], rows: list[dict[str, object]]) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def _write_immutable(path: Path, content: str) -> None:
    if path.exists():
        if path.read_text(encoding="utf-8") != content:
            raise FileExistsError(f"refusing to overwrite immutable dataset file: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _source_overview(records: list[SourceAuditRecord], dataset_root: Path) -> None:
    output_path = dataset_root / "source_overview.png"
    if output_path.exists():
        return
    cell_width, cell_height = 240, 190
    canvas = Image.new("RGB", (cell_width * 4, cell_height * 3), (28, 28, 28))
    draw = ImageDraw.Draw(canvas)
    for index, record in enumerate(records):
        with Image.open(dataset_root / "images" / record.local_filename) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
            image.thumbnail((cell_width - 12, cell_height - 42), Image.Resampling.LANCZOS)
        column, row = index % 4, index // 4
        x = column * cell_width
        y = row * cell_height
        image_x = x + (cell_width - image.width) // 2
        canvas.paste(image, (image_x, y + 24))
        draw.text((x + 6, y + 5), record.source_id, fill=(245, 245, 245))
        draw.text((x + 6, y + cell_height - 15), record.scene_category, fill=(190, 210, 255))
    canvas.save(output_path, format="PNG")


def materialize_real_smoke(
    manifest_path: Path,
    dataset_root: Path,
    *,
    dataset_id: str = "retarget_smoke_real_v1",
    targets: tuple[dict[str, object], ...] = TARGETS,
    target_count: int = 2,
    source_cache: Path | None = None,
    minimum_pressure: float = 0.25,
    description: str = "Twelve audited real-world public images for retargeting Smoke.",
) -> Path:
    records = _read_manifest(manifest_path)
    _verify_official_metadata(records)
    dataset_root.mkdir(parents=True, exist_ok=True)
    images_dir = dataset_root / "images"
    sources: list[dict[str, object]] = []
    tasks: list[dict[str, object]] = []
    for record in records:
        image_path = images_dir / record.local_filename
        if not image_path.exists() and source_cache is not None:
            cached_path = source_cache / record.local_filename
            if cached_path.is_file():
                cached_digest = hashlib.sha256(cached_path.read_bytes()).hexdigest()
                if cached_digest != record.sha256:
                    raise ValueError(f"cached file hash mismatch for {record.source_id}")
                image_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(cached_path, image_path)
        _download(record, image_path)
        sources.append(
            {
                "source_id": record.source_id,
                "image_path": f"images/{record.local_filename}",
                "width": record.expected_width,
                "height": record.expected_height,
                "sha256": record.sha256,
                "split": "smoke",
                "scene_profile": SCENE_PROFILES[record.scene_category],
                "enabled": "true",
                "source_kind": "public_real",
                "license_status": "audited",
                "scene_category": record.scene_category,
                "fixture_type": "",
                "test_purpose": "",
            }
        )
        for target in _select_targets(
            record.expected_width,
            record.expected_height,
            targets,
            target_count,
            minimum_pressure,
        ):
            tasks.append(
                {
                    "task_id": f"{record.source_id}__{target['target_id']}",
                    "source_id": record.source_id,
                    "target_id": target["target_id"],
                    "enabled": "true",
                }
            )

    descriptor = {
        "schema_version": "1.0",
        "dataset_id": dataset_id,
        "version": "1.0.0",
        "description": description,
        "sources_file": "sources.csv",
        "targets_file": "targets.csv",
        "tasks_file": "tasks.csv",
        "source_audit_file": "source_audit.csv",
        "expected_source_count": 12,
        "expected_scene_counts": EXPECTED_SCENE_COUNTS,
    }
    _write_immutable(
        dataset_root / "dataset.yaml",
        yaml.safe_dump(descriptor, sort_keys=False, allow_unicode=True),
    )
    _write_immutable(dataset_root / "sources.csv", _csv_text(list(sources[0]), sources))
    _write_immutable(dataset_root / "targets.csv", _csv_text(list(targets[0]), list(targets)))
    _write_immutable(dataset_root / "tasks.csv", _csv_text(list(tasks[0]), tasks))
    audit_content = manifest_path.read_text(encoding="utf-8")
    _write_immutable(dataset_root / "source_audit.csv", audit_content)
    result = FolderCsvDatasetAdapter().validate(dataset_root)
    if not result.valid:
        raise ValueError("materialized dataset validation failed: " + "; ".join(result.errors))
    _source_overview(records, dataset_root)
    return dataset_root
