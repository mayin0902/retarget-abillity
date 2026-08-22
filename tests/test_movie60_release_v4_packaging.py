from pathlib import Path

from PIL import Image

from scripts import package_movie60_review_v4 as packaging


def test_v4_packaging_replaces_historical_root_manifests(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "movie60-review-v4"
    workspace.mkdir()
    (workspace / "core-manifest.csv").write_text("old core\n", encoding="utf-8")
    (workspace / "evidence-manifest.csv").write_text("old evidence\n", encoding="utf-8")
    (workspace / "VERSION.json").write_text("{}\n", encoding="utf-8")
    showcase = workspace / "showcase"
    showcase.mkdir()
    Image.new("RGB", (8, 8), "red").save(showcase / "example.jpg")
    monkeypatch.setattr(packaging, "validate_movie60_review_v4", lambda _path: {})

    core, evidence = packaging._entries(workspace)

    archive_paths = {item.archive_path for item in (*core, *evidence)}
    assert Path("movie60-review-v4/core-manifest.csv") not in archive_paths
    assert Path("movie60-review-v4/evidence-manifest.csv") not in archive_paths
    assert Path("movie60-review-v4/VERSION.json") in archive_paths
    assert Path("movie60-review-v4/showcase/example.jpg") in archive_paths
