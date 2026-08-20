from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

ASSETS = (
    "movie60-handoff-v1-core.zip",
    "movie60-handoff-v1-evidence.zip",
    "SHA256SUMS.txt",
)


def asset_names(release_version: str) -> tuple[str, str, str]:
    if not release_version.startswith("v") or not release_version[1:].isdigit():
        raise ValueError("release_version must look like v1 or v2")
    return (
        f"movie60-handoff-{release_version}-core.zip",
        f"movie60-handoff-{release_version}-evidence.zip",
        "SHA256SUMS.txt",
    )


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
        if len(roots) != 1 or roots[0].name != "movie60-review":
            raise ValueError("release archives must merge into one movie60-review directory")
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
    parser.add_argument("--tag", default="movie60-review-v1")
    parser.add_argument("--release-version", default="v1")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("local_data/movie60-review-v1"),
    )
    parser.add_argument(
        "--asset-dir",
        type=Path,
        help="Use already-downloaded assets instead of GitHub (testing/offline handoff).",
    )
    args = parser.parse_args()
    output = materialize_release(
        args.repo,
        args.tag,
        args.output_dir,
        asset_dir=args.asset_dir,
        release_version=args.release_version,
    )
    print(output)


if __name__ == "__main__":
    main()
