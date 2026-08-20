from __future__ import annotations

import csv
import hashlib
import importlib.util
import re
from collections import Counter
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "materialize_square_public_v2.py"
SPEC = importlib.util.spec_from_file_location("materialize_square_public_v2", SCRIPT)
assert SPEC and SPEC.loader
v2 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(v2)


@pytest.mark.parametrize(
    ("width", "height", "tier", "eligible"),
    [
        (1024, 682, "aspect_hard_1", True),
        (1024, 576, "aspect_hard_1", True),
        (1024, 575, "aspect_hard_1", False),
        (1024, 512, "aspect_hard_2", True),
        (1024, 384, "aspect_hard_2", True),
        (1024, 383, "aspect_hard_2", False),
        (1024, 300, "aspect_extreme", True),
        (1024, 256, "aspect_extreme", True),
        (1024, 255, None, False),
        (1000, 400, "aspect_hard_2", False),
    ],
)
def test_tier_specific_source_resolution_contract(
    width: int, height: int, tier: str | None, eligible: bool
) -> None:
    result = v2.classify_dimensions(width, height)
    assert result.get("tier") == tier
    assert result["eligible"] is eligible


def test_near_square_and_over_extreme_sources_are_rejected() -> None:
    assert not v2.classify_dimensions(1024, 800)["eligible"]
    assert not v2.classify_dimensions(1280, 256)["eligible"]


def _synthetic_pool() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    serial = 0
    for scene, count in v2.POOL_COUNTS.items():
        for _ in range(count):
            serial += 1
            source_id = f"candidate-{serial:04d}"
            rows.append(
                {
                    "source_id": source_id,
                    "proposed_scene": scene,
                    "upstream_dataset": "open_images_v7",
                    "upstream_id": source_id,
                    "official_source": "https://storage.googleapis.com/openimages/web/download_v7.html",
                    "source_url": (
                        f"https://open-images-dataset.s3.amazonaws.com/validation/{source_id}.jpg"
                    ),
                    "review_url": (
                        f"https://open-images-dataset.s3.amazonaws.com/validation/{source_id}.jpg"
                    ),
                    "license_evidence_url": "https://www.flickr.com/photos/example/1",
                    "license": "CC BY 2.0",
                    "license_url": "https://creativecommons.org/licenses/by/2.0/",
                    "author": "Example",
                    "attribution": "Example — CC BY 2.0",
                    "review_local_filename": f"candidate_review/{source_id}.bin",
                    "download_status": "not_downloaded",
                }
            )
    return rows


def test_candidate_pool_requires_exact_three_x_scene_counts() -> None:
    result = v2.validate_pool(_synthetic_pool(), require_pixels=False)
    assert result["rows"] == 900
    assert result["scenes"] == v2.POOL_COUNTS


def test_candidate_pool_rejects_identity_duplicates_and_path_escape() -> None:
    rows = _synthetic_pool()
    rows[1]["upstream_id"] = rows[0]["upstream_id"]
    rows[1]["review_local_filename"] = "../escape.jpg"
    with pytest.raises(v2.V2Error) as captured:
        v2.validate_pool(rows, require_pixels=False)
    assert "upstream identities are not unique" in str(captured.value)
    assert "unsafe review path" in str(captured.value)


def _synthetic_pilot() -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    pool: list[dict[str, str]] = []
    decisions: list[dict[str, str]] = []
    scenes = [scene for scene, count in v2.PILOT_COUNTS.items() for _ in range(count)]
    tiers = [tier for tier, count in v2.PILOT_GLOBAL_TIER_COUNTS.items() for _ in range(count)]
    for serial, (scene, tier) in enumerate(zip(scenes, tiers, strict=True), start=1):
        source_id = f"pilot-{serial:03d}"
        pool.append(
            {
                "source_id": source_id,
                "proposed_scene": scene,
                "resolution_eligible": "true",
                "difficulty_tier": tier,
            }
        )
        decisions.append(
            {
                "source_id": source_id,
                "proposed_scene": scene,
                "decision": "select_pilot",
                "final_scene": scene,
                "scene_confirmed": "true",
                "safety_confirmed": "true",
                "real_world_confirmed": "true",
                "non_fixture_confirmed": "true",
                "license_review_status": "approved",
                "non_copyright_review_status": "approved_for_research_benchmark",
                "api_egress_allowed": "false",
                "reviewer": "codex",
                "review_reason": ("Viewed in pilot overview and meets the frozen scene contract."),
            }
        )
    return pool, decisions


def test_pilot_is_indivisible_and_has_exact_scene_and_tier_counts() -> None:
    pool, decisions = _synthetic_pilot()
    selected = v2.validate_pilot_decisions(decisions, pool)
    assert len(selected) == 60
    assert sum(row["api_egress_allowed"] == "true" for row in selected) == 0


def test_pilot_rejects_partial_denominator_and_unviewed_selection() -> None:
    pool, decisions = _synthetic_pilot()
    decisions.pop()
    decisions[0]["scene_confirmed"] = "false"
    with pytest.raises(v2.V2Error) as captured:
        v2.validate_pilot_decisions(decisions, pool)
    assert "exactly 60" in str(captured.value)
    assert "scene_confirmed must be true" in str(captured.value)


def test_policy_freezes_1024_canvas_and_forbids_silent_upsampling() -> None:
    policy_path = ROOT / "datasets" / "retarget_square_public_v2" / "selection_policy.yaml"
    policy = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    assert policy["evaluation_canvas"]["target_id"] == "square-1024x1024"
    assert policy["evaluation_canvas"]["silent_upsampling_forbidden"] is True
    assert sum(scene["pool_minimum"] for scene in policy["scenes"].values()) == 900


def test_materialized_pilot_manifest_is_an_indivisible_audited_denominator() -> None:
    manifest_path = ROOT / "datasets" / "retarget_square_public_v2" / "source_manifest.csv"
    with manifest_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 60
    assert len({row["source_id"] for row in rows}) == 60
    assert Counter(row["scene_category"] for row in rows) == Counter(v2.PILOT_COUNTS)
    assert Counter(row["difficulty_tier"] for row in rows) == Counter(v2.PILOT_GLOBAL_TIER_COUNTS)
    for row in rows:
        assert row["split"] == "pilot60"
        assert row["review_status"] == "approved"
        assert row["duplicate_review_status"] == "approved"
        assert row["public_release_eligible"] == "true"
        assert row["source_url"].startswith(
            ("https://upload.wikimedia.org/", "https://open-images-dataset.s3.amazonaws.com/")
        )
        assert re.fullmatch(r"[0-9a-f]{64}", row["raw_sha256"])
        assert re.fullmatch(r"[0-9a-f]{64}", row["materialized_sha256"])
        assert row["license_evidence_url"].startswith("https://")
        assert row["author"]
        assert row["attribution"]


def _synthetic_heldout() -> tuple[list[dict[str, str]], list[dict[str, str]], dict[str, int]]:
    pool: list[dict[str, str]] = []
    decisions: list[dict[str, str]] = []
    tiers = v2.FROZEN_HELDOUT_TIER_COUNTS
    scenes = [scene for scene, count in v2.HELDOUT_COUNTS.items() for _ in range(count)]
    tier_values = [tier for tier, count in tiers.items() for _ in range(count)]
    for serial, (scene, tier) in enumerate(zip(scenes, tier_values, strict=True), start=1):
        source_id = f"heldout-{serial:03d}"
        pool.append(
            {
                "source_id": source_id,
                "proposed_scene": scene,
                "resolution_eligible": "true",
                "difficulty_tier": tier,
            }
        )
        decisions.append(
            {
                "source_id": source_id,
                "proposed_scene": scene,
                "decision": "select_heldout",
                "final_scene": scene,
                "scene_confirmed": "true",
                "safety_confirmed": "true",
                "real_world_confirmed": "true",
                "non_fixture_confirmed": "true",
                "license_review_status": "approved",
                "non_copyright_review_status": "approved_for_research_benchmark",
                "api_egress_allowed": "false",
                "reviewer": "codex",
                "review_reason": "Viewed in the held-out scene and pressure overview.",
            }
        )
    return pool, decisions, tiers


def test_heldout_validation_is_indivisible_and_pressure_versioned() -> None:
    pool, decisions, tiers = _synthetic_heldout()
    selected = v2.validate_heldout_decisions(decisions, pool, tiers)
    assert len(selected) == 240
    decisions.pop()
    with pytest.raises(v2.V2Error, match="exactly 240"):
        v2.validate_heldout_decisions(decisions, pool, tiers)


def test_pilot_manifest_is_byte_frozen_before_heldout_materialization() -> None:
    manifest = ROOT / "datasets" / "retarget_square_public_v2" / "source_manifest.csv"
    assert hashlib.sha256(manifest.read_bytes().replace(b"\r\n", b"\n")).hexdigest() == (
        v2.FROZEN_PILOT_MANIFEST_SHA256
    )


def _read_manifest(name: str) -> list[dict[str, str]]:
    path = ROOT / "datasets" / "retarget_square_public_v2" / name
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_materialized_heldout_is_complete_reviewed_and_disjoint_from_pilot() -> None:
    pilot = _read_manifest("source_manifest.csv")
    heldout = _read_manifest("heldout240_source_manifest.csv")
    assert len(heldout) == 240
    assert len({row["source_id"] for row in heldout}) == 240
    assert {row["source_id"] for row in pilot}.isdisjoint(row["source_id"] for row in heldout)
    assert Counter(row["scene_category"] for row in heldout) == Counter(v2.HELDOUT_COUNTS)
    assert Counter(row["difficulty_tier"] for row in heldout) == Counter(
        v2.FROZEN_HELDOUT_TIER_COUNTS
    )
    assert all(row["split"] == "heldout240" for row in heldout)
    assert all(row["review_status"] == "approved" for row in heldout)
    assert all(row["duplicate_review_status"] == "approved" for row in heldout)
    assert all(row["api_egress_allowed"] == "false" for row in heldout)


def test_materialized_full300_has_exact_frozen_scene_and_pressure_contract() -> None:
    pilot = _read_manifest("source_manifest.csv")
    heldout = _read_manifest("heldout240_source_manifest.csv")
    full = _read_manifest("full300_source_manifest.csv")
    assert len(full) == 300
    assert len({row["source_id"] for row in full}) == 300
    assert {row["source_id"] for row in full} == {row["source_id"] for row in [*pilot, *heldout]}
    assert Counter(row["scene_category"] for row in full) == Counter(v2.SCENE_COUNTS)
    assert Counter(row["difficulty_tier"] for row in full) == Counter(v2.FULL_GLOBAL_TIER_COUNTS)
