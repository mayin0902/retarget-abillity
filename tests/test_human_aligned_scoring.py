from __future__ import annotations

import re
from pathlib import Path

import pytest

from retarget_agent.human_aligned_scoring import apply_human_aligned_policy
from retarget_agent.plugin_catalog import built_in_plugin_catalog
from retarget_agent.strategy import load_strategy_bundle

ROOT = Path(__file__).resolve().parents[1]
V2_1 = ROOT / "strategies" / "movie60" / "v2_1" / "bundle.yaml"
V3 = ROOT / "strategies" / "movie60" / "v3" / "bundle.yaml"
V3_1 = ROOT / "strategies" / "movie60" / "v3_1" / "bundle.yaml"
V3_2 = ROOT / "strategies" / "movie60" / "v3_2" / "bundle.yaml"
V3_3 = ROOT / "strategies" / "movie60" / "v3_3" / "bundle.yaml"


def _metrics(**updates: object) -> dict[str, object]:
    values: dict[str, object] = {
        "quality_score": 78.0,
        "content_fidelity_score": 0.80,
        "visual_integrity_score": 0.80,
        "composition_score": 0.80,
        "technical_valid": True,
        "hard_failures": "",
        "critical_regressions": "critical_text_missing",
        "ocr_character_recall": 0.45,
        "face_count_preservation": 1.0,
        "person_count_preservation": 1.0,
        "product_count_preservation": 1.0,
        "logo_count_preservation": 1.0,
        "transform_safety_score": 0.8,
        "color_histogram_similarity": 0.9,
    }
    values.update(updates)
    return values


def test_all_immutable_human_aligned_versions_load_without_changing_v2_1() -> None:
    historical = load_strategy_bundle(V2_1)
    versions = [load_strategy_bundle(path) for path in (V3, V3_1, V3_2, V3_3)]

    assert (
        historical.source_sha256
        == "c343aa1bab4ebce3cf313d655e822eeee9a7d803f1c7ae4c6100455a4ecbe97d"
    )
    assert [item.bundle.version for item in versions] == [
        "3.0.0",
        "3.1.0",
        "3.2.0",
        "3.3.0",
    ]
    assert all(item.bundle.status == "frozen" for item in versions)
    assert all(item.bundle.reference_scorer_plugin == "human_aligned_proxy_v3" for item in versions)
    assert versions[2].override.max_agent_challengers == 2
    assert len(versions[2].agent_skill.case_knowledge) >= 10


def test_v3_3_has_configurable_thresholds_and_no_positive_score_rewards() -> None:
    policy = load_strategy_bundle(V3_3).scoring

    assert policy.proxy_a_threshold == 89.0
    assert policy.proxy_b_threshold == 52.0
    assert policy.proxy_c_threshold == 42.0
    assert policy.human_alignment is not None
    assert policy.human_alignment.score_adjustments == ()

    result = apply_human_aligned_policy(
        _metrics(
            content_fidelity_score=0.98,
            visual_integrity_score=0.98,
            composition_score=0.98,
            critical_regressions="",
        ),
        scene_category="person",
        method_id="direct_warp",
        scoring_policy=policy,
    )

    assert result["base_quality_score"] == pytest.approx(98.0)
    assert result["quality_score"] == pytest.approx(98.0)
    assert result["human_alignment_adjustments"] == ""
    assert result["proxy_grade"] == "proxy_a"


def test_legacy_text_regression_is_soft_when_high_resolution_content_is_usable() -> None:
    policy = load_strategy_bundle(V3).scoring

    result = apply_human_aligned_policy(
        _metrics(),
        scene_category="movie_poster",
        method_id="seam",
        scoring_policy=policy,
    )

    assert result["proxy_grade"] == "proxy_b"
    assert result["critical_regressions"] == ""
    assert result["base_critical_regressions"] == "critical_text_missing"
    assert result["human_alignment_soft_regressions"] == "critical_text_missing"


def test_missing_main_relation_and_scene_method_gate_cap_grade() -> None:
    policy = load_strategy_bundle(V3_2).scoring

    missing = apply_human_aligned_policy(
        _metrics(person_count_preservation=0.1),
        scene_category="film_still",
        method_id="crop",
        scoring_policy=policy,
    )
    seam_full = apply_human_aligned_policy(
        _metrics(),
        scene_category="movie_poster",
        method_id="seam_full",
        scoring_policy=policy,
    )

    assert missing["proxy_grade"] == "proxy_d"
    assert "main_person_almost_missing" in missing["human_alignment_matched_gates"]
    assert seam_full["proxy_grade"] == "proxy_c"
    assert "poster_or_still_seam_full_visual_risk" in seam_full["human_alignment_matched_gates"]


def test_absent_detector_metric_does_not_match_a_numeric_gate() -> None:
    policy = load_strategy_bundle(V3_1).scoring
    metrics = _metrics()
    metrics["person_count_preservation"] = None
    metrics["face_count_preservation"] = None

    result = apply_human_aligned_policy(
        metrics,
        scene_category="film_still",
        method_id="crop",
        scoring_policy=policy,
    )

    assert "film_still_crop_relationship_loss" not in result["human_alignment_matched_gates"]


def test_catalog_and_strategy_do_not_expose_per_image_answer_paths() -> None:
    assert "human_aligned_proxy_v3" in built_in_plugin_catalog().describe()["reference_scorers"]
    forbidden = re.compile(r"(?:person|poster|still|video_cover)_\d{3}__square|task_id\s*==")
    for root in (V3.parent, V3_1.parent, V3_2.parent, V3_3.parent):
        for path in root.rglob("*"):
            if path.is_file():
                assert not forbidden.search(path.read_text(encoding="utf-8")), path
