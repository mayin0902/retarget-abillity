from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from retarget_agent.movie60_release import validate_movie60_review_v3

if __package__:
    from .package_movie60_release import Entry, _write_zip
else:
    from package_movie60_release import Entry, _write_zip

PACKAGE_ROOT = Path("movie60-review-v3")
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _entry(path: Path, workspace: Path, group: str) -> Entry:
    return Entry(path, PACKAGE_ROOT / path.relative_to(workspace), group)


def collect_v3(workspace: Path) -> tuple[list[Entry], list[Entry]]:
    """Split one validated workspace into two non-overlapping GitHub assets."""

    workspace = workspace.resolve()
    validate_movie60_review_v3(workspace)
    core: list[Entry] = []
    evidence: list[Entry] = []
    for path in sorted(item for item in workspace.rglob("*") if item.is_file()):
        relative = path.relative_to(workspace)
        parts = relative.parts
        if parts[0] in {".review-venv", ".state"}:
            continue
        is_focus_visual = parts[0] == "focus20" and path.suffix.lower() in IMAGE_SUFFIXES
        is_showcase = parts[0] == "showcase"
        is_evidence_visual = (
            len(parts) >= 4
            and parts[0] == "all60"
            and parts[1] == "tasks"
            and "evidence" in parts
            and path.suffix.lower() in IMAGE_SUFFIXES
        )
        is_comparison = (
            len(parts) == 4
            and parts[0] == "all60"
            and parts[1] == "tasks"
            and parts[-1] == "02_comparison.jpg"
        )
        if is_focus_visual or is_showcase or is_evidence_visual or is_comparison:
            evidence.append(_entry(path, workspace, "visual-evidence"))
        else:
            core.append(_entry(path, workspace, "core"))
    core_paths = {entry.archive_path.as_posix() for entry in core}
    evidence_paths = {entry.archive_path.as_posix() for entry in evidence}
    if core_paths & evidence_paths:
        raise ValueError("v3 core/evidence asset paths overlap")
    return core, evidence


def package_v3(workspace: Path, output_dir: Path) -> list[Path]:
    workspace = workspace.resolve()
    output_dir = output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"output directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    core, evidence = collect_v3(workspace)
    core_zip = output_dir / "movie60-review-v3-core.zip"
    evidence_zip = output_dir / "movie60-review-v3-evidence.zip"
    _write_zip(
        core_zip,
        core,
        {},
        "core-manifest.csv",
        package_root=PACKAGE_ROOT,
    )
    _write_zip(
        evidence_zip,
        evidence,
        {},
        "evidence-manifest.csv",
        package_root=PACKAGE_ROOT,
    )
    assets = [core_zip, evidence_zip]
    sums = "".join(f"{_sha256(path)}  {path.name}\n" for path in assets)
    sums_path = output_dir / "SHA256SUMS.txt"
    sums_path.write_text(sums, encoding="ascii")
    assets.append(sums_path)
    return assets


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Package the clean Movie60 review v3 as two GitHub Release assets."
    )
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    for path in package_v3(args.workspace, args.output_dir):
        print(f"{path.name}\t{path.stat().st_size}\t{_sha256(path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
