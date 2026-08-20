from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import pytest

from scripts import materialize_movie60_release
from scripts.materialize_movie60_release import verify_and_materialize


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _assets(tmp_path: Path) -> Path:
    assets = tmp_path / "assets"
    assets.mkdir()
    core = assets / "movie60-handoff-v1-core.zip"
    evidence = assets / "movie60-handoff-v1-evidence.zip"
    with zipfile.ZipFile(core, "w") as archive:
        archive.writestr("movie60-review/README.md", "review")
        archive.writestr("movie60-review/all60/source.jpg", b"source")
    with zipfile.ZipFile(evidence, "w") as archive:
        archive.writestr("movie60-review/all60/candidate.jpg", b"candidate")
    (assets / "SHA256SUMS.txt").write_text(
        f"{_digest(core)}  {core.name}\n{_digest(evidence)}  {evidence.name}\n",
        encoding="utf-8",
    )
    return assets


def test_materializer_verifies_and_merges_two_release_archives(tmp_path: Path) -> None:
    output = tmp_path / "movie60"
    verify_and_materialize(_assets(tmp_path), output)
    assert (output / "README.md").read_text(encoding="utf-8") == "review"
    assert (output / "all60" / "source.jpg").read_bytes() == b"source"
    assert (output / "all60" / "candidate.jpg").read_bytes() == b"candidate"
    with pytest.raises(FileExistsError):
        verify_and_materialize(tmp_path / "assets", output)


def test_materializer_rejects_hash_mismatch(tmp_path: Path) -> None:
    assets = _assets(tmp_path)
    (assets / "movie60-handoff-v1-core.zip").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        verify_and_materialize(assets, tmp_path / "output")


def test_materializer_rejects_zip_path_traversal(tmp_path: Path) -> None:
    assets = _assets(tmp_path)
    core = assets / "movie60-handoff-v1-core.zip"
    with zipfile.ZipFile(core, "w") as archive:
        archive.writestr("../escape.txt", "bad")
    evidence = assets / "movie60-handoff-v1-evidence.zip"
    (assets / "SHA256SUMS.txt").write_text(
        f"{_digest(core)}  {core.name}\n{_digest(evidence)}  {evidence.name}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unsafe ZIP member"):
        verify_and_materialize(assets, tmp_path / "output")


def test_existing_output_refuses_before_release_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "existing"
    output.mkdir()

    def unexpected_download(*_args, **_kwargs) -> None:
        raise AssertionError("release download must not start")

    monkeypatch.setattr(
        materialize_movie60_release,
        "download_release",
        unexpected_download,
    )
    with pytest.raises(FileExistsError):
        materialize_movie60_release.materialize_release(
            "owner/repo", "tag", output
        )
