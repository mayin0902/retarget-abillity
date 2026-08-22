from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

CURRENT_RELEASE = Path(__file__).resolve().parents[1] / "CURRENT_RELEASE.json"
SAFE_RELEASE_TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def asset_names(release_version: str) -> tuple[str, str, str]:
    if not release_version.startswith("v") or not release_version[1:].isdigit():
        raise ValueError("release_version must look like v1, v2 or v3")
    if release_version == "v3":
        return (
            "movie60-review-v3-core.zip",
            "movie60-review-v3-evidence.zip",
            "SHA256SUMS.txt",
        )
    return (
        f"movie60-handoff-{release_version}-core.zip",
        f"movie60-handoff-{release_version}-evidence.zip",
        "SHA256SUMS.txt",
    )


def _validate_release_tag(tag: str) -> str:
    normalized = tag.strip()
    if not SAFE_RELEASE_TAG.fullmatch(normalized) or normalized in {".", ".."}:
        raise ValueError("GitHub Release tag must be one safe path segment")
    return normalized


def release_defaults(config_path: Path = CURRENT_RELEASE) -> tuple[str, str]:
    """Return the one supported GitHub tag and asset generation from repository metadata."""

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("CURRENT_RELEASE.json must contain a JSON object")
    tag = payload.get("github_release_tag")
    release_version = payload.get("release_version")
    if not isinstance(tag, str) or not tag.strip():
        raise ValueError("CURRENT_RELEASE.json is missing github_release_tag")
    if not isinstance(release_version, str):
        raise ValueError("CURRENT_RELEASE.json is missing release_version")
    expected_assets = list(asset_names(release_version))
    if payload.get("release_asset_names") != expected_assets:
        raise ValueError("CURRENT_RELEASE.json asset names do not match release_version")
    return _validate_release_tag(tag), release_version


def default_asset_directory(
    tag: str,
    *,
    repository_root: Path | None = None,
) -> Path:
    """Return the fixed browser-download directory for one GitHub Release tag."""

    root = (repository_root or CURRENT_RELEASE.parent).resolve()
    return root / "local_data" / "release_assets" / _validate_release_tag(tag)


def discover_local_assets(
    tag: str,
    release_version: str,
    *,
    repository_root: Path | None = None,
) -> tuple[Path, tuple[str, ...]]:
    """Return the conventional directory and any required assets still missing."""

    directory = default_asset_directory(tag, repository_root=repository_root)
    missing = tuple(
        name for name in asset_names(release_version) if not (directory / name).is_file()
    )
    return directory, missing


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _expected_hashes(path: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, separator, filename = line.partition("  ")
        if not separator or len(digest) != 64 or Path(filename).name != filename:
            raise ValueError(f"invalid SHA256SUMS line: {line!r}")
        hashes[filename] = digest.lower()
    return hashes


def _safe_members(archive: zipfile.ZipFile) -> tuple[zipfile.ZipInfo, ...]:
    members = tuple(archive.infolist())
    for member in members:
        path = PurePosixPath(member.filename.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"unsafe ZIP member: {member.filename}")
        if member.external_attr >> 16 & 0o170000 == 0o120000:
            raise ValueError(f"ZIP symlink is not allowed: {member.filename}")
    return members


def verify_and_materialize(
    asset_dir: Path,
    output_dir: Path,
    *,
    release_version: str = "v1",
) -> Path:
    asset_dir = asset_dir.resolve()
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise FileExistsError(output_dir)
    assets = asset_names(release_version)
    for name in assets:
        if not (asset_dir / name).is_file():
            raise FileNotFoundError(asset_dir / name)
    expected = _expected_hashes(asset_dir / "SHA256SUMS.txt")
    for name in assets[:2]:
        actual = _sha256(asset_dir / name)
        if expected.get(name) != actual:
            raise ValueError(f"SHA-256 mismatch for {name}")

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="movie60-extract-", dir=output_dir.parent) as temp:
        extracted = Path(temp) / "extracted"
        extracted.mkdir()
        for name in assets[:2]:
            with zipfile.ZipFile(asset_dir / name) as archive:
                bad = archive.testzip()
                if bad is not None:
                    raise ValueError(f"ZIP CRC failure in {name}: {bad}")
                archive.extractall(extracted, members=_safe_members(archive))
        roots = [item for item in extracted.iterdir() if item.is_dir()]
        expected_root = "movie60-review-v3" if release_version == "v3" else "movie60-review"
        if len(roots) != 1 or roots[0].name != expected_root:
            raise ValueError(f"release archives must merge into one {expected_root} directory")
        shutil.move(str(roots[0]), output_dir)
    return output_dir


def download_release(
    repo: str,
    tag: str,
    destination: Path,
    *,
    release_version: str = "v1",
) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    command = [
        "gh",
        "release",
        "download",
        tag,
        "--repo",
        repo,
        "--dir",
        str(destination),
    ]
    for name in asset_names(release_version):
        command.extend(("--pattern", name))
    subprocess.run(command, check=True)


def materialize_release(
    repo: str,
    tag: str,
    output_dir: Path,
    *,
    asset_dir: Path | None = None,
    release_version: str = "v1",
) -> Path:
    """Fail before network I/O when the immutable output already exists."""

    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise FileExistsError(output_dir)
    if asset_dir is not None:
        return verify_and_materialize(
            asset_dir,
            output_dir,
            release_version=release_version,
        )
    with tempfile.TemporaryDirectory(prefix="movie60-release-") as temp:
        downloaded = Path(temp) / "assets"
        download_release(repo, tag, downloaded, release_version=release_version)
        return verify_and_materialize(
            downloaded,
            output_dir,
            release_version=release_version,
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download, hash-check and safely materialize the private Movie60 release."
    )
    parser.add_argument("--repo", default="mayin0902/retarget-abillity")
    parser.add_argument(
        "--tag",
        help="GitHub Release tag; defaults to CURRENT_RELEASE.json.",
    )
    parser.add_argument(
        "--release-version",
        help="Movie60 asset generation; defaults to CURRENT_RELEASE.json.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("local_data/movie60-review-current"),
    )
    parser.add_argument(
        "--asset-dir",
        type=Path,
        help="Use already-downloaded assets instead of GitHub (testing/offline handoff).",
    )
    args = parser.parse_args()
    default_tag, default_release_version = release_defaults()
    tag = _validate_release_tag(args.tag or default_tag)
    release_version = args.release_version or default_release_version
    asset_dir = args.asset_dir
    if asset_dir is None:
        conventional, missing = discover_local_assets(tag, release_version)
        if not missing:
            asset_dir = conventional
            print(f"Using verified local Release assets: {conventional}")
        elif conventional.exists():
            print(
                "Local Release asset directory is incomplete; online download will be used. "
                f"Missing: {', '.join(missing)}"
            )
    output = materialize_release(
        args.repo,
        tag,
        args.output_dir,
        asset_dir=asset_dir,
        release_version=release_version,
    )
    print(output)


if __name__ == "__main__":
    main()
