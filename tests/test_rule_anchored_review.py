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
from retarget_agent.rule_anchored_review import (
    PairPreference,
    RuleAgentPairReview,
    build_rule_agent_pair_sheet,
    decide_rule_anchored_candidate,
)
from retarget_agent.strategy import load_strategy_bundle
from retarget_agent.strict_review import DimensionReview, MachineGrade, StrictCandidateReview


def _dimension(grade: MachineGrade, code: str = "") -> DimensionReview:
    return DimensionReview(
        applicable=grade is not MachineGrade.NA,
        grade=grade,
        reason_codes=((code,) if code else ()),
        reason="fixture reason",
    )


def _review(grade: MachineGrade) -> StrictCandidateReview:
    return StrictCandidateReview(
        overall_grade=grade,
        directly_usable=grade in {MachineGrade.A, MachineGrade.B},
        confidence=0.9,
        subject=_dimension(grade),
        face_body=_dimension(MachineGrade.NA),
        text=_dimension(MachineGrade.A),
        product_logo=_dimension(MachineGrade.NA),
        structure=_dimension(MachineGrade.A),
        composition=_dimension(MachineGrade.A),
        summary="fixture summary",
    )


def _pair(
    *,
    preferred: PairPreference = PairPreference.AGENT,
    clear: bool = True,
    consistent: bool = True,
    confidence: float = 0.9,
) -> RuleAgentPairReview:
    return RuleAgentPairReview(
        preferred=preferred,
        clear_visual_evidence=clear,
        evidence_consistent=consistent,
        confidence=confidence,
        summary="fixture pair summary",
    )


def _decision(**overrides: object):
    arguments = {
        "task_id": "task-1",
        "phase": "calibration",
        "rule_ranking": ("rule", "agent", "other"),
        "agent_candidate_id": "agent",
        "rule_review": _review(MachineGrade.B),
        "agent_review": _review(MachineGrade.A),
        "pair_review": _pair(),
        "agent_core_content_preserved": True,
        "rule_metrics": {
            "ocr_character_recall": 0.9,
            "person_count_preservation": 1.0,
        },
        "agent_metrics": {
            "ocr_character_recall": 0.9,
            "person_count_preservation": 1.0,
        },
        "wall_seconds": 12.0,
    }
    arguments.update(overrides)
    return decide_rule_anchored_candidate(**arguments)


def test_clear_b_to_a_improvement_can_override_rule() -> None:
    decision = _decision()

    assert decision.agent_overrode_rule
    assert decision.selected_candidate_id == "agent"
    assert decision.rule_complete_ranking == ("rule", "agent", "other")


def test_core_content_false_blocks_override_of_usable_rule() -> None:
    decision = _decision(agent_core_content_preserved=False)

    assert not decision.agent_overrode_rule
    assert decision.selected_candidate_id == "rule"
    assert "agent_core_content_not_preserved" in decision.override_block_reasons


def test_critical_text_or_subject_count_decline_blocks_usable_rule_override() -> None:
    decision = _decision(
        agent_metrics={
            "ocr_character_recall": 0.6,
            "person_count_preservation": 0.5,
        }
    )

    assert not decision.agent_overrode_rule
    assert "critical_text_recall_declined" in decision.override_block_reasons
    assert "person_count_preservation_declined" in decision.override_block_reasons


def test_override_metric_tolerance_is_loaded_from_strategy() -> None:
    policy = load_strategy_bundle(
        Path(__file__).resolve().parents[1] / "strategies/movie60/v2/bundle.yaml"
    ).override.model_copy(update={"metric_decline_tolerance": 0.5})
    decision = _decision(
        agent_metrics={
            "ocr_character_recall": 0.6,
            "person_count_preservation": 0.6,
        },
        override_policy=policy,
    )
    assert decision.agent_overrode_rule


def test_conflicting_or_non_decisive_pair_falls_back_to_rule() -> None:
    conflict = _decision(pair_review=_pair(consistent=False))
    tie = _decision(pair_review=_pair(preferred=PairPreference.TIE, clear=False))

    assert not conflict.agent_overrode_rule
    assert "pair_evidence_conflict" in conflict.override_block_reasons
    assert not tie.agent_overrode_rule
    assert "pair_did_not_prefer_agent" in tie.override_block_reasons


def test_same_candidate_is_reviewed_but_never_counted_as_override() -> None:
    decision = _decision(
        rule_ranking=("rule", "other"),
        agent_candidate_id="rule",
        rule_review=_review(MachineGrade.A),
        agent_review=_review(MachineGrade.A),
        pair_review=_pair(preferred=PairPreference.TIE, clear=False),
    )

    assert decision.reviewed_candidate_ids == ("rule",)
    assert not decision.agent_overrode_rule


def test_pair_sheet_includes_eight_balanced_text_person_product_crops(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    rule = tmp_path / "rule.png"
    agent = tmp_path / "agent.png"
    output = tmp_path / "pair.png"
    for path, color in ((source, "red"), (rule, "green"), (agent, "blue")):
        Image.new("RGB", (1200, 700), color).save(path)
    semantics = ("text", "text", "text", "face", "person", "person", "product", "logo_candidate")
    regions = tuple(
        RegionRecord(
            region_id=f"region-{index}",
            kind=RegionKind.MUST_KEEP,
            rect=Rect(x1=20 + index * 40, y1=40, x2=160 + index * 40, y2=220),
            importance=1.0 - index * 0.05,
            tolerance=0.0,
            confidence=0.9,
            source="fixture",
            label=semantic,
            attributes={"semantic_type": semantic},
        )
        for index, semantic in enumerate(semantics)
    )
    analysis = AnalysisArtifact(
        artifact_id="analysis-fixture",
        analysis_version="1.0",
        task_id="task-1",
        source_id="source-1",
        target_id="square-1536",
        source_width=1200,
        source_height=700,
        scene_profile=SceneProfile.BALANCED,
        regions=regions,
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

    metadata = build_rule_agent_pair_sheet(
        source,
        rule,
        agent,
        analysis,
        output,
        critical_crop_limit=8,
    )

    assert output.is_file()
    assert metadata["sheet_width"] == 2304
    assert metadata["critical_crop_limit"] == 8
    observed = {item["semantic_type"] for item in metadata["critical_crops"]}
    assert {"text", "person", "product", "logo_candidate"} <= observed
