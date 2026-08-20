from __future__ import annotations

from pathlib import Path

from PIL import Image

from retarget_agent.models import (
    AnalysisArtifact,
    ArtifactRef,
    Rect,
    RegionKind,
    RegionRecord,
    SceneProfile,
)
from retarget_agent.strict_review import (
    DimensionReview,
    MachineGrade,
    StrictCandidateReview,
    _apply_evidence_grade_caps,
    _build_strict_review_prompt,
    _normalize_wire_review,
    _StrictCandidateReviewWire,
    build_pairwise_review_sheet,
    decide_strict_top2,
)


def dimension(grade: MachineGrade, *codes: str) -> DimensionReview:
    return DimensionReview(
        applicable=grade is not MachineGrade.NA,
        grade=grade,
        reason_codes=codes,
        reason="fixture reason",
    )


def review(grade: MachineGrade, *, code: str = "") -> StrictCandidateReview:
    item = dimension(grade, *((code,) if code else ()))
    return StrictCandidateReview(
        overall_grade=grade,
        directly_usable=grade in {MachineGrade.A, MachineGrade.B},
        confidence=0.9,
        subject=item,
        face_body=dimension(MachineGrade.NA),
        text=dimension(MachineGrade.NA),
        product_logo=dimension(MachineGrade.NA),
        structure=dimension(MachineGrade.A),
        composition=dimension(MachineGrade.A),
        summary="fixture summary",
    )


def test_top2_selects_better_grade_without_aigc() -> None:
    decision = decide_strict_top2(
        "task-1",
        "overview-1",
        ("candidate-1", "candidate-2"),
        (review(MachineGrade.B), review(MachineGrade.A)),
        12.0,
    )

    assert decision.selected_candidate_id == "candidate-2"
    assert decision.selected_grade is MachineGrade.A
    assert not decision.request_external_aigc


def test_top2_shared_material_b_defect_requests_aigc() -> None:
    decision = decide_strict_top2(
        "task-1",
        "overview-1",
        ("candidate-1", "candidate-2"),
        (
            review(MachineGrade.B, code="critical_text_damage"),
            review(MachineGrade.B, code="critical_text_damage"),
        ),
        12.0,
    )

    assert decision.request_external_aigc
    assert "top2_shared_material_b_defect" in decision.aigc_trigger_reasons


def test_pairwise_sheet_contains_whole_views_and_critical_crops(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    candidate = tmp_path / "candidate.png"
    output = tmp_path / "sheet.png"
    Image.new("RGB", (600, 900), (200, 30, 30)).save(source)
    Image.new("RGB", (800, 800), (30, 200, 30)).save(candidate)
    artifact = AnalysisArtifact(
        artifact_id="analysis-fixture",
        analysis_version="1.0",
        task_id="task-1",
        source_id="source-1",
        target_id="square-1536",
        source_width=600,
        source_height=900,
        scene_profile=SceneProfile.BALANCED,
        regions=(
            RegionRecord(
                region_id="text-1",
                kind=RegionKind.MUST_KEEP,
                rect=Rect(x1=100, y1=100, x2=500, y2=250),
                importance=1.0,
                tolerance=0.0,
                confidence=0.9,
                source="fixture",
                label="text",
                attributes={"semantic_type": "text"},
            ),
        ),
        importance_map=ArtifactRef(
            relative_path="maps/importance.png",
            sha256="a" * 64,
            media_type="image/png",
        ),
        tolerance_map=ArtifactRef(
            relative_path="maps/tolerance.png",
            sha256="b" * 64,
            media_type="image/png",
        ),
        analyzer_ids=("fixture",),
        config_hash="c" * 64,
    )

    metadata = build_pairwise_review_sheet(source, candidate, artifact, output)

    assert output.is_file()
    assert metadata["sheet_width"] == 1792
    assert metadata["critical_crops"][0]["semantic_type"] == "text"


def test_generative_sheet_uses_candidate_localized_regions_not_source_coordinates(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.png"
    candidate = tmp_path / "candidate.png"
    output = tmp_path / "sheet.png"
    Image.new("RGB", (1000, 500), (200, 30, 30)).save(source)
    Image.new("RGB", (1000, 1000), (30, 200, 30)).save(candidate)
    source_region = RegionRecord(
        region_id="source-text",
        kind=RegionKind.MUST_KEEP,
        rect=Rect(x1=20, y1=20, x2=220, y2=100),
        importance=1.0,
        tolerance=0.0,
        confidence=0.9,
        source="fixture",
        label="text",
        attributes={"semantic_type": "text"},
    )
    candidate_region = RegionRecord(
        region_id="candidate-text",
        kind=RegionKind.MUST_KEEP,
        rect=Rect(x1=700, y1=800, x2=980, y2=960),
        importance=1.0,
        tolerance=0.0,
        confidence=0.9,
        source="fixture",
        label="text",
        attributes={"semantic_type": "text"},
    )
    artifact = AnalysisArtifact(
        artifact_id="analysis-generative",
        analysis_version="1.0",
        task_id="task-generative",
        source_id="source-1",
        target_id="square-1536",
        source_width=1000,
        source_height=500,
        scene_profile=SceneProfile.BALANCED,
        regions=(source_region,),
        importance_map=ArtifactRef(
            relative_path="maps/importance.png",
            sha256="a" * 64,
            media_type="image/png",
        ),
        tolerance_map=ArtifactRef(
            relative_path="maps/tolerance.png",
            sha256="b" * 64,
            media_type="image/png",
        ),
        analyzer_ids=("fixture",),
        config_hash="c" * 64,
    )

    metadata = build_pairwise_review_sheet(
        source,
        candidate,
        artifact,
        output,
        candidate_regions=(candidate_region,),
        spatially_aligned=False,
    )

    assert metadata["spatially_aligned"] is False
    assert metadata["critical_crops"][0]["source_box"] == [20, 20, 220, 100]
    assert metadata["critical_crops"][0]["candidate_box"] == [700, 800, 980, 960]
    assert "independently localized" in metadata["note"]


def test_generative_review_prompt_allows_recomposition_and_distrusts_ocr_counts() -> None:
    prompt = _build_strict_review_prompt(
        "task-generative",
        "candidate-generative",
        {
            "method_id": "seedream",
            "candidate_kind": "generative_recomposition",
            "ocr_and_detector_counts_are_advisory": True,
        },
    )

    assert "canvas size, element scale, spacing, and layout are expected" in prompt
    assert "not ground truth" in prompt
    assert "visibly absent or unreadable" in prompt
    assert "A is valid" in prompt


def test_review_prompt_never_penalizes_target_canvas_size_alone() -> None:
    prompt = _build_strict_review_prompt(
        "task-traditional",
        "candidate-traditional",
        {"method_id": "seam_scale"},
    )

    assert "Never penalize a candidate merely because" in prompt
    assert "canvas dimensions differ" in prompt
    assert "require visible deformation" in prompt


def test_generative_sheet_uses_whole_view_when_candidate_detector_misses(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.png"
    candidate = tmp_path / "candidate.png"
    output = tmp_path / "sheet.png"
    Image.new("RGB", (400, 200), (200, 30, 30)).save(source)
    Image.new("RGB", (400, 400), (30, 200, 30)).save(candidate)
    artifact = AnalysisArtifact(
        artifact_id="analysis-detector-miss",
        analysis_version="1.0",
        task_id="task-detector-miss",
        source_id="source-1",
        target_id="square-1536",
        source_width=400,
        source_height=200,
        scene_profile=SceneProfile.BALANCED,
        regions=(
            RegionRecord(
                region_id="source-face",
                kind=RegionKind.MUST_KEEP,
                rect=Rect(x1=20, y1=20, x2=120, y2=160),
                importance=1.0,
                tolerance=0.0,
                confidence=0.9,
                source="fixture",
                label="face",
                attributes={"semantic_type": "face"},
            ),
        ),
        importance_map=ArtifactRef(
            relative_path="maps/importance.png",
            sha256="a" * 64,
            media_type="image/png",
        ),
        tolerance_map=ArtifactRef(
            relative_path="maps/tolerance.png",
            sha256="b" * 64,
            media_type="image/png",
        ),
        analyzer_ids=("fixture",),
        config_hash="c" * 64,
    )

    metadata = build_pairwise_review_sheet(
        source,
        candidate,
        artifact,
        output,
        candidate_regions=(),
        spatially_aligned=False,
    )

    assert metadata["critical_crops"][0]["candidate_box"] is None
    assert (
        metadata["critical_crops"][0]["candidate_crop_mode"]
        == "whole_view_fallback_no_detector_box"
    )


def test_wire_overall_a_is_strictly_downgraded_by_critical_b() -> None:
    wire = _StrictCandidateReviewWire(
        overall_grade=MachineGrade.A,
        directly_usable=True,
        confidence=0.9,
        subject=dimension(MachineGrade.B, "visible_stretch"),
        face_body=dimension(MachineGrade.NA),
        text=dimension(MachineGrade.A),
        product_logo=dimension(MachineGrade.NA),
        structure=dimension(MachineGrade.A),
        composition=dimension(MachineGrade.A),
        summary="fixture summary",
    )

    normalized = _normalize_wire_review(wire)

    assert normalized.overall_grade is MachineGrade.B
    assert normalized.directly_usable


def test_wire_absent_dimension_c_is_normalized_to_na() -> None:
    wire = _StrictCandidateReviewWire(
        overall_grade=MachineGrade.A,
        directly_usable=True,
        confidence=0.9,
        subject=dimension(MachineGrade.A),
        face_body=dimension(MachineGrade.NA),
        text=dimension(MachineGrade.A),
        product_logo=DimensionReview(
            applicable=False,
            grade=MachineGrade.C,
            reason="source has no product or logo",
        ),
        structure=dimension(MachineGrade.A),
        composition=dimension(MachineGrade.A),
        summary="fixture summary",
    )

    normalized = _normalize_wire_review(wire)

    assert normalized.product_logo.grade is MachineGrade.NA
    assert not normalized.product_logo.applicable
    assert normalized.overall_grade is MachineGrade.A


def test_wire_defect_without_code_receives_dimension_default() -> None:
    wire = _StrictCandidateReviewWire(
        overall_grade=MachineGrade.C,
        directly_usable=False,
        confidence=0.9,
        subject=dimension(MachineGrade.A),
        face_body=DimensionReview(
            applicable=True,
            grade=MachineGrade.C,
            reason="face geometry is visibly damaged",
        ),
        text=dimension(MachineGrade.NA),
        product_logo=dimension(MachineGrade.NA),
        structure=dimension(MachineGrade.A),
        composition=dimension(MachineGrade.A),
        summary="fixture summary",
    )

    normalized = _normalize_wire_review(wire)

    assert normalized.face_body.reason_codes == ("face_body_deformation",)


def test_direct_warp_evidence_caps_visual_under_call_at_c() -> None:
    visual = review(MachineGrade.A)

    capped = _apply_evidence_grade_caps(
        visual,
        {"method_id": "direct_warp", "direct_warp_d_stretch": 0.87},
    )

    assert capped.overall_grade is MachineGrade.C
    assert not capped.directly_usable
    assert capped.subject.grade is MachineGrade.C
    assert "global_stretch" in capped.subject.reason_codes


def test_direct_warp_evidence_makes_na_subject_applicable_instead_of_crashing() -> None:
    visual = review(MachineGrade.A).model_copy(
        update={"subject": dimension(MachineGrade.NA)}
    )

    capped = _apply_evidence_grade_caps(
        visual,
        {"method_id": "direct_warp", "direct_warp_d_stretch": 0.2},
    )

    assert capped.overall_grade is MachineGrade.B
    assert capped.subject.applicable
    assert capped.subject.grade is MachineGrade.B
