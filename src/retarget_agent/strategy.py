"""Immutable, self-contained strategy bundles for scoring and Agent selection.

The public seam is deliberately small: load one bundle, pass the returned
``LoadedStrategyBundle`` to a workflow, and snapshot it into every derived
artifact.  Callers do not need to know how individual policy files are
resolved, validated, hashed, or copied.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

import yaml
from pydantic import Field, field_validator, model_validator

from .agent_skill import AgentSkill
from .hashing import sha256_file, sha256_json
from .models import FrozenModel, validate_id
from .prompting import LoadedPromptBundle, load_prompt_bundle


class ScoreWeights(FrozenModel):
    content: float = Field(ge=0.0)
    integrity: float = Field(ge=0.0)
    composition: float = Field(ge=0.0)


class TextWeights(FrozenModel):
    character_recall: float = Field(ge=0.0)
    sequence_similarity: float = Field(ge=0.0)


class ContentWeights(FrozenModel):
    feature: float = Field(ge=0.0)
    text: float = Field(ge=0.0)
    face: float = Field(ge=0.0)
    person: float = Field(ge=0.0)
    product: float = Field(ge=0.0)
    logo: float = Field(ge=0.0)
    object: float = Field(ge=0.0)


class IntegrityWeights(FrozenModel):
    sharpness: float = Field(ge=0.0)
    edge_density: float = Field(ge=0.0)
    color: float = Field(ge=0.0)
    structure_lines: float = Field(ge=0.0)
    transform_safety: float = Field(ge=0.0)


class CompositionWeights(FrozenModel):
    protected_border: float = Field(ge=0.0)
    visual_center: float = Field(ge=0.0)


class TransformPenalties(FrozenModel):
    direct_warp_stretch: float = Field(default=0.65, ge=0.0)
    crop_cut_must_keep: float = Field(default=2.0, ge=0.0)
    seam_importance: float = Field(default=1.5, ge=0.0)
    seam_anisotropy: float = Field(default=0.6, ge=0.0)
    mesh_anisotropy: float = Field(default=0.55, ge=0.0)
    mesh_foldover: float = Field(default=8.0, ge=0.0)


HumanMetricName = Literal[
    "quality_score",
    "content_fidelity_score",
    "visual_integrity_score",
    "composition_score",
    "ocr_character_recall",
    "ocr_sequence_similarity",
    "face_count_preservation",
    "person_count_preservation",
    "product_count_preservation",
    "logo_count_preservation",
    "object_label_f1",
    "structure_line_similarity",
    "transform_safety_score",
    "orb_content_similarity",
    "color_histogram_similarity",
    "direct_warp_d_stretch",
]


class HumanMetricCondition(FrozenModel):
    """One allowlisted numeric condition used by a human-aligned gate."""

    metric: HumanMetricName
    operator: Literal["lt", "lte", "gt", "gte"]
    threshold: float


class HumanScoreAdjustment(FrozenModel):
    """Transparent score offset restricted to declared scenes or methods."""

    adjustment_id: str
    amount: float = Field(ge=-30.0, le=30.0)
    scenes: tuple[str, ...] = ()
    methods: tuple[str, ...] = ()

    _adjustment_id = field_validator("adjustment_id")(validate_id)


class HumanRegressionPenalty(FrozenModel):
    """Turn a legacy critical regression into soft, inspectable evidence."""

    regression_code: str
    amount: float = Field(le=0.0, ge=-30.0)

    _regression_code = field_validator("regression_code")(validate_id)


class HumanAlignedGate(FrozenModel):
    """Declarative AND gate; absent metrics never match."""

    gate_id: str
    outcome: Literal["C", "D"]
    scenes: tuple[str, ...] = ()
    methods: tuple[str, ...] = ()
    conditions: tuple[HumanMetricCondition, ...] = ()

    _gate_id = field_validator("gate_id")(validate_id)

    @model_validator(mode="after")
    def constrained_gate(self) -> HumanAlignedGate:
        if not (self.scenes or self.methods or self.conditions):
            raise ValueError("a human-aligned gate must declare a scene, method, or condition")
        return self


class HumanAlignedScoringPolicy(FrozenModel):
    """Optional post-processor for business-usability scoring.

    The data model is intentionally narrower than a general expression language:
    versions may tune offsets and numeric AND gates, but cannot import code or match
    task IDs and filenames.
    """

    enabled: bool = True
    hard_failure_outcome: Literal["C", "D"] = "D"
    score_adjustments: tuple[HumanScoreAdjustment, ...] = ()
    regression_penalties: tuple[HumanRegressionPenalty, ...] = ()
    gates: tuple[HumanAlignedGate, ...] = ()

    @model_validator(mode="after")
    def unique_rule_ids(self) -> HumanAlignedScoringPolicy:
        adjustment_ids = [item.adjustment_id for item in self.score_adjustments]
        gate_ids = [item.gate_id for item in self.gates]
        regression_codes = [item.regression_code for item in self.regression_penalties]
        if len(adjustment_ids) != len(set(adjustment_ids)):
            raise ValueError("human-aligned score adjustment IDs must be unique")
        if len(gate_ids) != len(set(gate_ids)):
            raise ValueError("human-aligned gate IDs must be unique")
        if len(regression_codes) != len(set(regression_codes)):
            raise ValueError("human-aligned regression penalty codes must be unique")
        return self


class ScoringPolicy(FrozenModel):
    schema_version: str = "1.0"
    policy_id: str
    version: str
    # Resolved through the allowlisted scoring registry.  This is deliberately
    # a validated ID rather than an import path.
    implementation: str = "auto_proxy_v1"
    evaluator_id: str
    evaluator_version: str
    max_analysis_edge: int = Field(ge=256, le=2048)
    proxy_a_threshold: float = Field(ge=0.0, le=100.0)
    proxy_b_threshold: float = Field(ge=0.0, le=100.0)
    proxy_c_threshold: float = Field(default=0.0, ge=0.0, le=100.0)
    critical_text_recall: float = Field(ge=0.0, le=1.0)
    blank_std_threshold: float = Field(ge=0.0)
    prominent_face_confidence: float = Field(default=0.75, ge=0.0, le=1.0)
    prominent_face_area_ratio: float = Field(default=0.015, ge=0.0, le=1.0)
    direct_warp_proxy_a_cap_d_stretch: float | None = Field(default=None, ge=0.0)
    direct_warp_proxy_c_cap_d_stretch: float | None = Field(default=None, ge=0.0)
    total_weights: ScoreWeights
    text_weights: TextWeights
    content_weights: ContentWeights
    integrity_weights: IntegrityWeights
    composition_weights: CompositionWeights
    transform_penalties: TransformPenalties = Field(default_factory=TransformPenalties)
    human_alignment: HumanAlignedScoringPolicy | None = None

    _policy_id = field_validator("policy_id")(validate_id)
    _implementation = field_validator("implementation")(validate_id)

    @model_validator(mode="after")
    def valid_thresholds_and_weights(self) -> ScoringPolicy:
        if not (self.proxy_a_threshold >= self.proxy_b_threshold >= self.proxy_c_threshold):
            raise ValueError(
                "grade thresholds must satisfy A >= B >= C; scores below C are grade D"
            )
        groups = (
            self.total_weights,
            self.text_weights,
            self.content_weights,
            self.integrity_weights,
            self.composition_weights,
        )
        if any(sum(group.model_dump().values()) <= 0 for group in groups):
            raise ValueError("every scoring weight group must have positive total weight")
        return self


RankingKey = Literal[
    "technical_valid_desc",
    "hard_failures_absent_desc",
    "critical_regressions_absent_desc",
    "generation_success_desc",
    "quality_score_desc",
    "method_id_asc",
]


class SelectionPolicy(FrozenModel):
    schema_version: str = "1.0"
    policy_id: str
    version: str
    ranking_order: tuple[RankingKey, ...]
    score_gap_trigger: float = Field(default=6.0, ge=0.0, le=100.0)
    low_score_trigger: float = Field(default=72.0, ge=0.0, le=100.0)
    deterministic_fallback_threshold: float = Field(default=58.0, ge=0.0, le=100.0)

    _policy_id = field_validator("policy_id")(validate_id)

    @model_validator(mode="after")
    def unique_complete_order(self) -> SelectionPolicy:
        if not self.ranking_order or len(set(self.ranking_order)) != len(self.ranking_order):
            raise ValueError("ranking_order must be non-empty and unique")
        return self


class OverridePolicy(FrozenModel):
    schema_version: str = "1.0"
    policy_id: str
    version: str
    rule_usable_grades: tuple[Literal["A", "B", "C", "D"], ...] = ("A", "B")
    protected_metrics: tuple[str, ...] = (
        "person_count_preservation",
        "face_count_preservation",
        "product_count_preservation",
        "logo_count_preservation",
    )
    metric_decline_tolerance: float = Field(default=0.01, ge=0.0, le=1.0)
    minimum_pair_confidence: float = Field(default=0.75, ge=0.0, le=1.0)
    require_clear_visual_evidence: bool = True
    require_consistent_pair_evidence: bool = True
    require_agent_grade_improvement: bool = True
    max_agent_challengers: int = Field(default=1, ge=1, le=2)
    allow_agent_upgrade: bool = True
    allow_agent_downgrade: bool = True
    agent_selection_mode: Literal["challenge", "advisory_only"] = "challenge"
    combined_grade_source: Literal["strict_review", "rule_metric"] = "strict_review"
    request_aigc_grades: tuple[Literal["A", "B", "C", "D"], ...] = ("C", "D")
    soft_review_target_seconds: float = Field(default=120.0, gt=0.0)

    _policy_id = field_validator("policy_id")(validate_id)


class StrategyBundle(FrozenModel):
    schema_version: str = "1.0"
    strategy_id: str
    version: str
    status: Literal["active", "frozen", "deprecated"] = "frozen"
    parent_strategy: str | None = None
    description: str = Field(min_length=1, max_length=500)
    scoring_policy: str
    selection_policy: str
    override_policy: str
    agent_skill: str
    prompt_bundle: str | None = None
    detector_suite_plugin: str = "legacy_opencv_v1"
    reference_scorer_plugin: str = "auto_proxy_v1"
    standalone_scorer_plugin: str = "technical_no_reference_v1"
    selector_plugin: str = "technical_risk_v1"
    rule_selector_plugin: str = "deterministic_rule_ranking_v1"
    agent_backend_plugin: str = "openai_compatible_vision_v1"
    strict_review_backend_plugin: str = "openai_compatible_strict_review_v1"
    pair_review_backend_plugin: str = "rule_anchored_pair_review_v1"
    image_review_backend_plugin: str = "openai_compatible_image_review_v1"

    _strategy_id = field_validator("strategy_id")(validate_id)
    _plugin_ids = field_validator(
        "detector_suite_plugin",
        "reference_scorer_plugin",
        "standalone_scorer_plugin",
        "selector_plugin",
        "rule_selector_plugin",
        "agent_backend_plugin",
        "strict_review_backend_plugin",
        "pair_review_backend_plugin",
        "image_review_backend_plugin",
    )(validate_id)

    @field_validator(
        "scoring_policy",
        "selection_policy",
        "override_policy",
        "agent_skill",
        "prompt_bundle",
    )
    @classmethod
    def safe_relative_reference(cls, value: str | None) -> str | None:
        if value is None:
            return None
        path = PurePosixPath(value.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise ValueError("strategy references must remain inside the version directory")
        return path.as_posix()


@dataclass(frozen=True)
class LoadedStrategyBundle:
    bundle: StrategyBundle
    scoring: ScoringPolicy
    selection: SelectionPolicy
    override: OverridePolicy
    agent_skill: AgentSkill
    prompts: LoadedPromptBundle | None
    root: Path
    source_files: tuple[Path, ...]
    file_hashes: dict[str, str]
    source_sha256: str

    def snapshot_to(self, destination: Path) -> Path:
        """Copy the exact bundle inputs into a new artifact directory."""

        destination = destination.resolve()
        if destination.exists():
            raise FileExistsError(destination)
        destination.mkdir(parents=True)
        for source in self.source_files:
            relative = source.relative_to(self.root)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        manifest = {
            "schema_version": "1.0",
            "strategy_id": self.bundle.strategy_id,
            "strategy_version": self.bundle.version,
            "strategy_sha256": self.source_sha256,
            "files": self.file_hashes,
        }
        (destination / "snapshot.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return destination


def _read_mapping(path: Path) -> dict[str, object]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"strategy file must be a YAML mapping: {path}")
    return raw


def load_strategy_bundle(path: Path) -> LoadedStrategyBundle:
    resolved = path.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    bundle = StrategyBundle.model_validate(_read_mapping(resolved))
    from .plugin_catalog import built_in_plugin_catalog

    plugins = built_in_plugin_catalog()
    plugins.detector_suites.get(bundle.detector_suite_plugin)
    plugins.reference_scorers.get(bundle.reference_scorer_plugin)
    plugins.standalone_scorers.get(bundle.standalone_scorer_plugin)
    plugins.selectors.get(bundle.selector_plugin)
    plugins.selectors.get(bundle.rule_selector_plugin)
    plugins.agent_backends.get(bundle.agent_backend_plugin)
    plugins.agent_backends.get(bundle.strict_review_backend_plugin)
    plugins.agent_backends.get(bundle.pair_review_backend_plugin)
    plugins.agent_backends.get(bundle.image_review_backend_plugin)
    root = resolved.parent

    def referenced(relative: str) -> Path:
        candidate = (root / Path(*PurePosixPath(relative).parts)).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as error:
            raise ValueError("strategy reference escapes version directory") from error
        if not candidate.is_file():
            raise FileNotFoundError(candidate)
        return candidate

    scoring_path = referenced(bundle.scoring_policy)
    selection_path = referenced(bundle.selection_policy)
    override_path = referenced(bundle.override_policy)
    skill_path = referenced(bundle.agent_skill)
    prompts = None
    prompt_files: tuple[Path, ...] = ()
    if bundle.prompt_bundle is not None:
        prompt_manifest = referenced(bundle.prompt_bundle)
        prompts = load_prompt_bundle(prompt_manifest, strategy_root=root)
        prompt_files = prompts.source_files
    source_files = tuple(
        dict.fromkeys(
            (resolved, scoring_path, selection_path, override_path, skill_path, *prompt_files)
        )
    )
    relative_hashes = {
        item.relative_to(root).as_posix(): sha256_file(item) for item in source_files
    }
    return LoadedStrategyBundle(
        bundle=bundle,
        scoring=ScoringPolicy.model_validate(_read_mapping(scoring_path)),
        selection=SelectionPolicy.model_validate(_read_mapping(selection_path)),
        override=OverridePolicy.model_validate(_read_mapping(override_path)),
        agent_skill=AgentSkill.model_validate(_read_mapping(skill_path)),
        prompts=prompts,
        root=root,
        source_files=source_files,
        file_hashes=relative_hashes,
        source_sha256=sha256_json(relative_hashes),
    )


def diff_strategy_bundles(
    left: LoadedStrategyBundle, right: LoadedStrategyBundle
) -> dict[str, object]:
    """Return a deterministic, human-readable field diff for two bundles."""

    sections = {
        "bundle": (left.bundle, right.bundle),
        "scoring": (left.scoring, right.scoring),
        "selection": (left.selection, right.selection),
        "override": (left.override, right.override),
        "agent_skill": (left.agent_skill, right.agent_skill),
    }
    changed: dict[str, dict[str, object]] = {}
    for section, (old, new) in sections.items():
        old_values = old.model_dump(mode="json")
        new_values = new.model_dump(mode="json")
        keys = sorted(set(old_values) | set(new_values))
        section_changes = {
            key: {"from": old_values.get(key), "to": new_values.get(key)}
            for key in keys
            if old_values.get(key) != new_values.get(key)
        }
        if section_changes:
            changed[section] = section_changes
    return {
        "from": f"{left.bundle.strategy_id}@{left.bundle.version}",
        "to": f"{right.bundle.strategy_id}@{right.bundle.version}",
        "from_sha256": left.source_sha256,
        "to_sha256": right.source_sha256,
        "changes": changed,
    }


__all__ = [
    "LoadedStrategyBundle",
    "OverridePolicy",
    "ScoringPolicy",
    "SelectionPolicy",
    "StrategyBundle",
    "diff_strategy_bundles",
    "load_strategy_bundle",
]
