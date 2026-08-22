from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from scripts import materialize_movie60_release
from scripts.materialize_movie60_release import verify_and_materialize


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _assets(tmp_path: Path, release_version: str = "v1") -> Path:
    assets = tmp_path / "assets"
    assets.mkdir()
    core = assets / f"movie60-handoff-{release_version}-core.zip"
    evidence = assets / f"movie60-handoff-{release_version}-evidence.zip"
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


def test_v2_asset_names_are_independent_from_immutable_v1(tmp_path: Path) -> None:
    output = tmp_path / "movie60-v2"

    verify_and_materialize(
        _assets(tmp_path, "v2"),
        output,
        release_version="v2",
    )

    assert (output / "README.md").is_file()
    assert materialize_movie60_release.asset_names("v1") != (
        materialize_movie60_release.asset_names("v2")
    )


def test_v3_root_and_asset_names_are_independent(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    core = assets / "movie60-review-v3-core.zip"
    evidence = assets / "movie60-review-v3-evidence.zip"
    with zipfile.ZipFile(core, "w") as archive:
        archive.writestr("movie60-review-v3/README.md", b"core")
    with zipfile.ZipFile(evidence, "w") as archive:
        archive.writestr("movie60-review-v3/evidence.txt", b"evidence")
    sums = "".join(f"{_digest(path)}  {path.name}\n" for path in (core, evidence))
    (assets / "SHA256SUMS.txt").write_text(sums, encoding="ascii")

    output = tmp_path / "movie60-review-v3"
    verify_and_materialize(assets, output, release_version="v3")

    assert (output / "README.md").read_bytes() == b"core"
    assert (output / "evidence.txt").read_bytes() == b"evidence"


def test_release_defaults_are_loaded_and_asset_names_are_cross_checked(
    tmp_path: Path,
) -> None:
    config = tmp_path / "CURRENT_RELEASE.json"
    config.write_text(
        json.dumps(
            {
                "github_release_tag": "v0.7.1",
                "release_version": "v3",
                "release_asset_names": list(materialize_movie60_release.asset_names("v3")),
            }
        ),
        encoding="utf-8",
    )

    assert materialize_movie60_release.release_defaults(config) == ("v0.7.1", "v3")

    payload = json.loads(config.read_text(encoding="utf-8"))
    payload["release_asset_names"] = ["wrong.zip"]
    config.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="asset names"):
        materialize_movie60_release.release_defaults(config)


def test_repository_current_release_points_to_unified_software_release() -> None:
    assert materialize_movie60_release.release_defaults() == ("v0.7.1", "v3")


def test_default_local_asset_directory_is_namespaced_by_release_tag(tmp_path: Path) -> None:
    directory, missing = materialize_movie60_release.discover_local_assets(
        "v0.7.1",
        "v3",
        repository_root=tmp_path,
    )

    assert directory == tmp_path / "local_data" / "release_assets" / "v0.7.1"
    assert missing == materialize_movie60_release.asset_names("v3")

    with pytest.raises(ValueError, match="safe path segment"):
        materialize_movie60_release.default_asset_directory(
            "../wrong-release",
            repository_root=tmp_path,
        )


def test_complete_local_asset_directory_is_discovered_without_network(tmp_path: Path) -> None:
    directory = tmp_path / "local_data" / "release_assets" / "v0.7.1"
    directory.mkdir(parents=True)
    for name in materialize_movie60_release.asset_names("v3"):
        (directory / name).write_bytes(b"present")

    discovered, missing = materialize_movie60_release.discover_local_assets(
        "v0.7.1",
        "v3",
        repository_root=tmp_path,
    )

    assert discovered == directory
    assert missing == ()


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
        materialize_movie60_release.materialize_release("owner/repo", "tag", output)
