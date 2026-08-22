from __future__ import annotations

import os
from pathlib import Path

from scripts.release_packaging import Entry, write_release_zip


def test_release_zip_is_independent_of_source_mtime(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("same content", encoding="utf-8")
    entries = [Entry(source, Path("movie60-review/source.txt"), "test")]
    generated = {Path("movie60-review/generated.txt"): b"generated"}

    first = tmp_path / "first.zip"
    write_release_zip(
        first,
        entries,
        generated,
        "manifest.csv",
        package_root=Path("movie60-review"),
    )
    os.utime(source, (2_000_000_000, 2_000_000_000))
    second = tmp_path / "second.zip"
    write_release_zip(
        second,
        entries,
        generated,
        "manifest.csv",
        package_root=Path("movie60-review"),
    )

    assert first.read_bytes() == second.read_bytes()
