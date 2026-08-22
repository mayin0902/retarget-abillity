from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from retarget_agent.movie60_release_v4 import validate_movie60_review_v4

if __package__:
    from .release_packaging import Entry, write_release_zip
else:
    from release_packaging import Entry, write_release_zip

PACKAGE_ROOT = Path("movie60-review-v4")
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
GENERATED_MANIFESTS = {"core-manifest.csv", "evidence-manifest.csv"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _entries(workspace: Path) -> tuple[list[Entry], list[Entry]]:
    validate_movie60_review_v4(workspace)
    core: list[Entry] = []
    evidence: list[Entry] = []
    for path in sorted(item for item in workspace.rglob("*") if item.is_file()):
        relative = path.relative_to(workspace)
        if relative.parts[0] in {".review-venv", ".state"}:
            continue
        if len(relative.parts) == 1 and relative.name in GENERATED_MANIFESTS:
            # write_release_zip creates one authoritative manifest per archive. The v3
            # workspace contains historical root manifests, which must not create duplicate
            # ZIP member names in v4.
            continue
        visual = path.suffix.lower() in IMAGE_SUFFIXES and (
            relative.parts[0] == "showcase"
            or relative.parts[0] == "focus20"
            or "evidence" in relative.parts
            or path.name == "02_comparison.jpg"
        )
        entry = Entry(path, PACKAGE_ROOT / relative, "visual-evidence" if visual else "core")
        (evidence if visual else core).append(entry)
    if {item.archive_path for item in core} & {item.archive_path for item in evidence}:
        raise ValueError("v4 core and evidence paths overlap")
    return core, evidence


def package_v4(workspace: Path, output_dir: Path) -> list[Path]:
    workspace = workspace.resolve()
    output_dir = output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"output directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    core, evidence = _entries(workspace)
    core_zip = output_dir / "movie60-review-v4-core.zip"
    evidence_zip = output_dir / "movie60-review-v4-evidence.zip"
    write_release_zip(core_zip, core, {}, "core-manifest.csv", package_root=PACKAGE_ROOT)
    write_release_zip(
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
    return [*assets, sums_path]


def main() -> int:
    parser = argparse.ArgumentParser(description="Package Movie60 review v4 Release assets.")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    for path in package_v4(args.workspace, args.output_dir):
        print(f"{path.name}\t{path.stat().st_size}\t{_sha256(path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
