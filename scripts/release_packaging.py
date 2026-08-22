"""Deterministic ZIP/SHA helpers shared by current and legacy Release packagers."""

from __future__ import annotations

import csv
import hashlib
import io
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
MAX_RELEASE_ASSET_BYTES = 2 * 1024**3
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


@dataclass(frozen=True)
class Entry:
    source: Path
    archive_path: Path
    group: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest(entries: list[Entry]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(("archive_path", "group", "bytes", "sha256"))
    for entry in sorted(entries, key=lambda item: item.archive_path.as_posix()):
        writer.writerow(
            (
                entry.archive_path.as_posix(),
                entry.group,
                entry.source.stat().st_size,
                _sha256(entry.source),
            )
        )
    return output.getvalue().encode("utf-8-sig")


def _zip_info(path: Path, compress_type: int) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(path.as_posix(), date_time=ZIP_TIMESTAMP)
    info.compress_type = compress_type
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info


def write_release_zip(
    path: Path,
    entries: list[Entry],
    generated: dict[Path, bytes],
    manifest_name: str,
    *,
    package_root: Path,
) -> None:
    """Write one deterministic, Zip64-capable Release asset."""

    if path.exists():
        raise FileExistsError(path)
    with zipfile.ZipFile(path, "w", allowZip64=True) as archive:
        for entry in entries:
            compress = (
                zipfile.ZIP_STORED
                if entry.source.suffix.lower() in IMAGE_SUFFIXES
                else zipfile.ZIP_DEFLATED
            )
            info = _zip_info(entry.archive_path, compress)
            with entry.source.open("rb") as source, archive.open(
                info,
                "w",
                force_zip64=True,
            ) as destination:
                shutil.copyfileobj(source, destination, length=1024 * 1024)
        for archive_path, payload in generated.items():
            archive.writestr(_zip_info(archive_path, zipfile.ZIP_DEFLATED), payload)
        archive.writestr(
            _zip_info(package_root / manifest_name, zipfile.ZIP_DEFLATED),
            _manifest(entries),
        )
    if path.stat().st_size >= MAX_RELEASE_ASSET_BYTES:
        raise ValueError(f"release asset exceeds GitHub 2 GiB limit: {path}")
