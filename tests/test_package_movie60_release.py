from __future__ import annotations

import os
from pathlib import Path

from scripts.package_movie60_release import Entry, _write_zip


def test_release_zip_is_independent_of_source_mtime(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("same content", encoding="utf-8")
    entries = [Entry(source, Path("movie60-review/source.txt"), "test")]
    generated = {Path("movie60-review/generated.txt"): b"generated"}

    first = tmp_path / "first.zip"
    _write_zip(first, entries, generated, "manifest.csv")
    os.utime(source, (2_000_000_000, 2_000_000_000))
    second = tmp_path / "second.zip"
    _write_zip(second, entries, generated, "manifest.csv")

    assert first.read_bytes() == second.read_bytes()
