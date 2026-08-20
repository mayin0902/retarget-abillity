from __future__ import annotations

import csv
import importlib.util
import io
from pathlib import Path

import numpy as np
import pytest
import yaml
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "materialize_square_public_v1.py"
SPEC = importlib.util.spec_from_file_location("materialize_square_public_v1", SCRIPT)
assert SPEC and SPEC.loader
square = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(square)


def _candidate_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    serial = 0
    for scene, source_counts in square.SOURCE_COUNTS.items():
        pilot_remaining = square.PILOT_COUNTS[scene]
        for upstream, count in source_counts.items():
            for _ in range(count):
                serial += 1
                source_id = f"test-{serial:03d}"
                split = "pilot60" if pilot_remaining else "held-out240"
                pilot_remaining = max(0, pilot_remaining - 1)
                rows.append(
                    {
                        "source_id": source_id,
                        "split": split,
                        "scene_category": scene,
                        "secondary_tags": "",
                        "difficulty_tier": "pending_pixel_download",
                        "upstream_dataset": upstream,
                        "upstream_id": source_id,
                        "upstream_split": "validation",
                        "official_source": "https://storage.googleapis.com/openimages/web/download_v7.html",
                        "source_url": (
                            "https://open-images-dataset.s3.amazonaws.com/validation/"
                            f"{source_id}.jpg"
                        ),
                        "license_evidence_url": "https://www.flickr.com/photos/example/1",
                        "license": "CC BY 2.0",
                        "license_url": "https://creativecommons.org/licenses/by/2.0/",
                        "author": "Example Author",
                        "attribution": "Example Author — test — CC BY 2.0",
                        "access_date": square.ACCESS_DATE,
                        "upstream_revision_timestamp": "2026-08-11",
                        "upstream_hash": "",
                        "raw_sha256": "",
                        "materialized_sha256": "",
                        "expected_width": "",
                        "expected_height": "",
                        "source_aspect": "",
                        "orientation": "",
                        "local_filename": f"images/{source_id}.png",
                        "redistribution_status": "pending",
                        "modification_notice": "pending",
                        "personality_rights_status": "unknown",
                        "trademark_status": "unknown",
                        "non_copyright_restrictions": "pending",
                        "source_resolution_limited": "false",
                        "resolution_review_status": "pending_pixel_download",
                        "public_release_eligible": "false",
                        "api_egress_allowed": "false",
                        "license_review_status": "pending",
                        "scene_review_status": "pending",
                        "content_safety_status": "pending",
                        "duplicate_review_status": "pending",
                        "review_status": "pending",
                        "download_status": "not_downloaded",
                        "phash": "",
                        "review_notes": "test candidate",
                    }
                )
    return rows


def test_candidate_contract_accepts_exact_pending_freeze() -> None:
    result = square.validate_manifest(
        _candidate_rows(), require_approved=False, require_pixel_fields=False
    )
    assert result["sources"] == 300
    assert result["splits"] == {"pilot60": 60, "held-out240": 240}
    assert result["upstreams"] == {
        "wikimedia_commons": 125,
        "open_images_v7": 175,
    }


def test_existing_candidate_freeze_is_reused_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset_dir = tmp_path / "dataset"
    rows = _candidate_rows()
    square._write_csv(dataset_dir / "source_manifest.csv", square.MANIFEST_FIELDS, rows)

    def unexpected_network(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("an existing v1 freeze must not query mutable discovery results")

    monkeypatch.setattr(square, "_download_metadata", unexpected_network)
    assert square.freeze_candidates(dataset_dir, tmp_path / "local") == rows


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("local_filename", "../escape.png", "unsafe local_filename"),
        ("source_url", "http://storage.googleapis.com/file.jpg", "allowlisted HTTPS"),
        ("source_url", "https://example.com/file.jpg", "allowlisted HTTPS"),
        ("license", "CC BY-NC 4.0", "disallowed license"),
        ("api_egress_allowed", "true", "deny API egress"),
    ],
)
def test_candidate_contract_fails_closed(field: str, value: str, message: str) -> None:
    rows = _candidate_rows()
    rows[0][field] = value
    with pytest.raises(square.DatasetError, match=message):
        square.validate_manifest(rows, require_approved=False, require_pixel_fields=False)


def test_candidate_contract_rejects_quota_and_duplicate_identity() -> None:
    rows = _candidate_rows()
    rows[0]["scene_category"] = "portrait"
    rows[1]["upstream_id"] = rows[0]["upstream_id"]
    rows[1]["upstream_dataset"] = rows[0]["upstream_dataset"]
    with pytest.raises(square.DatasetError) as captured:
        square.validate_manifest(rows, require_approved=False, require_pixel_fields=False)
    message = str(captured.value)
    assert "scene quota mismatch" in message
    assert "upstream source identities are not unique" in message


def test_strict_validation_never_promotes_pending_candidates() -> None:
    with pytest.raises(square.DatasetError, match="license_review_status is not approved"):
        square.validate_manifest(
            _candidate_rows(), require_approved=True, require_pixel_fields=False
        )


def test_png_materialization_removes_exif_and_preserves_decoded_pixels() -> None:
    source = Image.new("RGB", (80, 160), (20, 80, 140))
    exif = Image.Exif()
    exif[0x010F] = "private-camera-maker"
    raw_buffer = io.BytesIO()
    source.save(raw_buffer, format="JPEG", quality=95, exif=exif)
    raw = raw_buffer.getvalue()

    clean, width, height, phash = square._sanitize_to_png(raw)

    assert (width, height) == (80, 160)
    assert len(phash) == 16
    assert square._sha256_bytes(raw) != square._sha256_bytes(clean)
    with Image.open(io.BytesIO(raw)) as raw_image, Image.open(io.BytesIO(clean)) as clean_image:
        assert raw_image.getexif()
        assert not clean_image.getexif()
        assert np.array_equal(np.asarray(raw_image.convert("RGB")), np.asarray(clean_image))


def test_phash_is_stable_and_distance_detects_large_visual_change() -> None:
    black = Image.new("RGB", (128, 128), "black")
    black_copy = black.copy()
    split = Image.new("RGB", (128, 128), "white")
    for x in range(64):
        for y in range(128):
            split.putpixel((x, y), (0, 0, 0))
    left = square._phash(black)
    assert left == square._phash(black_copy)
    assert square.phash_distance(left, square._phash(split)) > 0


def test_phash_uniqueness_rejects_duplicate_pixels() -> None:
    rows = [
        {"source_id": "left", "phash": "0123456789abcdef"},
        {"source_id": "right", "phash": "0123456789abcdef"},
    ]
    with pytest.raises(square.DatasetError, match="near-duplicate pHash distance 0"):
        square._validate_phash_uniqueness(rows)


def test_safe_local_path_stays_in_dataset_root(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    (root / "images").mkdir(parents=True)
    assert square._safe_local_path(root, "images/source.png") == root / "images/source.png"
    with pytest.raises(square.DatasetError, match="unsafe local_filename"):
        square._safe_local_path(root, "images/../outside.png")


def test_selection_policy_fixes_only_one_square_target_and_expected_quotas() -> None:
    dataset_dir = ROOT / "datasets" / "retarget_square_public_v1"
    policy = yaml.safe_load((dataset_dir / "selection_policy.yaml").read_text(encoding="utf-8"))
    assert policy["target"] == {
        "target_id": "square-1536x1536",
        "width": 1536,
        "height": 1536,
        "format": "png",
    }
    assert sum(item["full"] for item in policy["scenes"].values()) == 300
    assert sum(item["pilot"] for item in policy["scenes"].values()) == 60
    assert policy["upstream_quota"] == {
        "wikimedia_commons": 125,
        "open_images_v7_validation": 175,
    }


def test_frozen_repository_manifest_and_tasks_if_present() -> None:
    dataset_dir = ROOT / "datasets" / "retarget_square_public_v1"
    manifest = dataset_dir / "source_manifest.csv"
    if not manifest.exists():
        pytest.skip("candidate freeze has not run")
    with manifest.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    square.validate_manifest(rows, require_approved=False, require_pixel_fields=False)
    with (dataset_dir / "tasks.csv").open("r", encoding="utf-8", newline="") as handle:
        tasks = list(csv.DictReader(handle))
    assert len(tasks) == 300
    assert {task["target_id"] for task in tasks} == {"square-1536x1536"}
    assert len({task["source_id"] for task in tasks}) == 300
