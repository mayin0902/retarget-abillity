"""Versioned domain contracts shared by CLI, runner, replay and review."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")


def utc_now() -> datetime:
    return datetime.now(UTC)


def validate_id(value: str) -> str:
    if not ID_PATTERN.fullmatch(value):
        raise ValueError("IDs must contain only lowercase ASCII letters, digits, '_' and '-'")
    return value


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SceneProfile(StrEnum):
    PRECISION = "precision"
    COVERAGE = "coverage"
    BALANCED = "balanced"


class GenerationStatus(StrEnum):
    SUCCESS = "SUCCESS"
    UNSAFE = "UNSAFE"
    FAILED = "FAILED"
    NEEDS_MANUAL_REVIEW = "NEEDS_MANUAL_REVIEW"


class ReviewGrade(StrEnum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    SKIP = "Skip"


class ReviewDimension(StrEnum):
    SUBJECT = "subject_integrity"
    FACE_BODY = "face_body_integrity"
    TEXT = "text_readability"
    PRODUCT_LOGO = "product_logo_integrity"
    COMPOSITION = "composition_balance"
    ARTIFACT = "artifact_control"


class ReviewDimensionGrade(FrozenModel):
    dimension: ReviewDimension
    grade: ReviewGrade
    reason_codes: tuple[str, ...] = Field(default=(), max_length=10)

    @model_validator(mode="after")
    def dimension_cannot_be_skipped(self) -> ReviewDimensionGrade:
        if self.grade is ReviewGrade.SKIP:
            raise ValueError("individual review dimensions cannot use Skip")
        return self


class ProxyGrade(StrEnum):
    """Uncalibrated automatic quality tier; never a substitute for human ReviewGrade."""

    A = "proxy_a"
    B = "proxy_b"
    C = "proxy_c"
    D = "proxy_d"
    UNKNOWN = "unknown"


class RegionKind(StrEnum):
    MUST_KEEP = "must_keep"
    PREFER_KEEP = "prefer_keep"
    REMOVABLE = "removable"
    RIGID = "rigid_region"


class Rect(FrozenModel):
    """Half-open rectangle in source-image pixel coordinates: [x1, x2) × [y1, y2)."""

    x1: int = Field(ge=0)
    y1: int = Field(ge=0)
    x2: int = Field(gt=0)
    y2: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_extent(self) -> Rect:
        if self.x2 <= self.x1 or self.y2 <= self.y1:
            raise ValueError("rectangle must have positive width and height")
        return self

    @property
    def width(self) -> int:
        return self.x2 - self.x1

    @property
    def height(self) -> int:
        return self.y2 - self.y1


class ArtifactRef(FrozenModel):
    relative_path: str
    sha256: str
    media_type: str
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)

    @field_validator("relative_path")
    @classmethod
    def relative_posix_path(cls, value: str) -> str:
        path = PurePosixPath(value.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts or not value:
            raise ValueError("artifact paths must be non-empty relative POSIX paths")
        return path.as_posix()

    @field_validator("sha256")
    @classmethod
    def valid_sha256(cls, value: str) -> str:
        if not SHA256_PATTERN.fullmatch(value):
            raise ValueError("sha256 must be 64 lowercase hexadecimal characters")
        return value


class DatasetDescriptor(FrozenModel):
    schema_version: str = "1.0"
    dataset_id: str
    version: str
    description: str = ""
    sources_file: str = "sources.csv"
    targets_file: str = "targets.csv"
    tasks_file: str = "tasks.csv"
    source_audit_file: str | None = None
    expected_source_count: int | None = Field(default=None, gt=0)
    expected_scene_counts: dict[str, int] = Field(default_factory=dict)
    evaluation_canvas: str | None = None
    generation_originals_may_be_retained_at_2k: bool = False
    silent_upsampling_forbidden: bool = False

    _dataset_id = field_validator("dataset_id")(validate_id)

    @field_validator("evaluation_canvas")
    @classmethod
    def valid_evaluation_canvas(cls, value: str | None) -> str | None:
        if value is None:
            return None
        match = re.fullmatch(r"([1-9][0-9]*)x([1-9][0-9]*)", value)
        if match is None:
            raise ValueError("evaluation_canvas must be formatted as positive WIDTHxHEIGHT")
        return value


class SourceRecord(FrozenModel):
    source_id: str
    image_path: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    sha256: str
    split: Literal["train", "calibration", "validation", "test", "smoke"] = "smoke"
    scene_profile: SceneProfile = SceneProfile.BALANCED
    enabled: bool = True
    source_kind: str = "unknown"
    license_status: str = "unknown"
    scene_category: str = "unknown"
    fixture_type: str | None = None
    test_purpose: str | None = None

    _source_id = field_validator("source_id")(validate_id)

    @field_validator("image_path")
    @classmethod
    def safe_image_path(cls, value: str) -> str:
        path = PurePosixPath(value.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts or not value:
            raise ValueError("image_path must stay below the dataset root")
        return path.as_posix()

    @field_validator("sha256")
    @classmethod
    def valid_sha256(cls, value: str) -> str:
        if not SHA256_PATTERN.fullmatch(value):
            raise ValueError("sha256 must be 64 lowercase hexadecimal characters")
        return value

    @model_validator(mode="after")
    def fixture_metadata_is_explicit(self) -> SourceRecord:
        if self.source_kind == "programmatic_fixture" and (
            not self.fixture_type or not self.test_purpose
        ):
            raise ValueError("programmatic fixtures require fixture_type and test_purpose")
        return self


class SourceAuditRecord(FrozenModel):
    source_id: str
    official_file_title: str
    source_url: str
    official_source: str
    license: str
    license_url: str
    access_date: date
    sha256: str
    scene_category: str
    local_filename: str
    redistribution_status: Literal[
        "public_domain",
        "allowed_with_attribution",
        "allowed_with_attribution_and_share_alike",
        "not_redistributable_local_only",
    ]
    author: str
    attribution: str
    rights_notes: str
    api_egress_allowed: bool = False
    local_algorithm_smoke_only: bool = True
    expected_width: int = Field(gt=0)
    expected_height: int = Field(gt=0)

    _source_id = field_validator("source_id")(validate_id)

    @field_validator("source_url", "official_source", "license_url")
    @classmethod
    def official_https_urls(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("source and license URLs must use HTTPS")
        return value

    @field_validator("sha256")
    @classmethod
    def audit_sha256(cls, value: str) -> str:
        if not SHA256_PATTERN.fullmatch(value):
            raise ValueError("sha256 must be 64 lowercase hexadecimal characters")
        return value

    @field_validator("local_filename")
    @classmethod
    def simple_local_filename(cls, value: str) -> str:
        path = PurePosixPath(value)
        if len(path.parts) != 1 or value != path.name or not value:
            raise ValueError("local_filename must be a single relative file name")
        if any(ord(character) > 127 or character.isspace() for character in value):
            raise ValueError("local_filename must contain no spaces or non-ASCII characters")
        return value


class TargetSpec(FrozenModel):
    target_id: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    format: Literal["png", "webp", "jpeg"] = "png"

    _target_id = field_validator("target_id")(validate_id)

    @property
    def aspect_ratio(self) -> float:
        return self.width / self.height


class TaskSpec(FrozenModel):
    dataset_id: str
    task_id: str
    source: SourceRecord
    target: TargetSpec
    enabled: bool = True

    _dataset_id = field_validator("dataset_id")(validate_id)
    _task_id = field_validator("task_id")(validate_id)

    @model_validator(mode="after")
    def stable_task_id(self) -> TaskSpec:
        expected = f"{self.source.source_id}__{self.target.target_id}"
        if self.task_id != expected:
            raise ValueError(f"task_id must be {expected!r}")
        return self


class RegionRecord(FrozenModel):
    region_id: str
    kind: RegionKind
    rect: Rect
    importance: float = Field(ge=0.0, le=1.0)
    tolerance: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    source: str
    label: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)

    _region_id = field_validator("region_id")(validate_id)


class AnalysisArtifact(FrozenModel):
    schema_version: str = "1.0"
    artifact_id: str
    analysis_version: str
    task_id: str
    source_id: str
    target_id: str
    source_width: int = Field(gt=0)
    source_height: int = Field(gt=0)
    scene_profile: SceneProfile
    regions: tuple[RegionRecord, ...] = ()
    importance_map: ArtifactRef
    tolerance_map: ArtifactRef
    analyzer_ids: tuple[str, ...]
    config_hash: str
    warnings: tuple[str, ...] = ()

    _artifact_id = field_validator("artifact_id")(validate_id)
    _task_id = field_validator("task_id")(validate_id)
    _source_id = field_validator("source_id")(validate_id)
    _target_id = field_validator("target_id")(validate_id)


class HumanGuidance(FrozenModel):
    guidance_id: str
    source_id: str
    source_sha256: str
    version: str
    created_by: str
    created_at: datetime = Field(default_factory=utc_now)
    must_keep: tuple[Rect, ...] = ()
    prefer_keep: tuple[Rect, ...] = ()
    removable: tuple[Rect, ...] = ()
    target_anchor: tuple[float, float] | None = None
    time_spent_seconds: float | None = Field(default=None, ge=0.0)

    _guidance_id = field_validator("guidance_id")(validate_id)
    _source_id = field_validator("source_id")(validate_id)


class MethodConfig(FrozenModel):
    method_id: str
    method_version: str = "1.0.0"
    variant_id: str = "default"
    seed: int = 0
    candidate_budget: int = Field(default=1, ge=1, le=32)
    timeout_seconds: float = Field(default=120.0, gt=0.0)
    parameters: dict[str, Any] = Field(default_factory=dict)

    _method_id = field_validator("method_id")(validate_id)
    _variant_id = field_validator("variant_id")(validate_id)


class ExecutionContext(FrozenModel):
    run_id: str
    run_root: str
    device: Literal["cpu", "cuda"] = "cpu"
    overwrite: bool = False

    _run_id = field_validator("run_id")(validate_id)


class TransformRecord(FrozenModel):
    schema_version: str = "1.0"
    method_id: str
    method_version: str
    operations: tuple[dict[str, Any], ...]
    risk_features: dict[str, float | int | bool | str | None]
    warnings: tuple[str, ...] = ()

    _method_id = field_validator("method_id")(validate_id)


class StageEvent(FrozenModel):
    event_id: str
    run_id: str
    task_id: str | None = None
    candidate_id: str | None = None
    stage: str
    status: Literal["STARTED", "COMPLETED", "FAILED"]
    started_at: datetime
    finished_at: datetime | None = None
    wall_seconds: float | None = Field(default=None, ge=0.0)
    cpu_seconds: float | None = Field(default=None, ge=0.0)
    rss_start_bytes: int | None = Field(default=None, ge=0)
    rss_end_bytes: int | None = Field(default=None, ge=0)
    peak_rss_bytes: int | None = Field(default=None, ge=0)
    error_type: str | None = None
    error_summary: str | None = None

    _event_id = field_validator("event_id")(validate_id)
    _run_id = field_validator("run_id")(validate_id)


class CandidateRecord(FrozenModel):
    schema_version: str = "1.0"
    candidate_id: str
    task_id: str
    method_id: str
    method_version: str
    variant_id: str
    run_id: str
    input_sha256: str
    output: ArtifactRef | None = None
    target_width: int = Field(gt=0)
    target_height: int = Field(gt=0)
    seed: int
    config_hash: str
    analysis_artifact_id: str
    transform: ArtifactRef | None = None
    generation_status: GenerationStatus
    failure_type: str | None = None
    error_summary: str | None = None
    used_human_guidance: bool = False
    external_api_called: bool = False
    performance: StageEvent | None = None
    warnings: tuple[str, ...] = ()

    _candidate_id = field_validator("candidate_id")(validate_id)
    _task_id = field_validator("task_id")(validate_id)
    _method_id = field_validator("method_id")(validate_id)
    _run_id = field_validator("run_id")(validate_id)
    _analysis_id = field_validator("analysis_artifact_id")(validate_id)


class MetricBundle(FrozenModel):
    metric_bundle_id: str
    candidate_id: str
    evaluator_id: str
    evaluator_version: str
    metrics: dict[str, float | int | bool | str | None]

    _metric_id = field_validator("metric_bundle_id")(validate_id)


class DecisionRecord(FrozenModel):
    decision_id: str
    run_id: str
    task_id: str
    selector_id: str
    selector_version: str
    best_candidate_id: str | None
    candidate_ids: tuple[str, ...]
    failed_candidate_ids: tuple[str, ...] = ()
    selection_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    reason_codes: tuple[str, ...] = ()
    created_at: datetime = Field(default_factory=utc_now)

    _decision_id = field_validator("decision_id")(validate_id)


class ReviewEvent(FrozenModel):
    event_id: str
    run_id: str
    reviewer_id: str
    task_id: str
    candidate_id: str
    method_id: str
    grade: ReviewGrade
    is_best: bool = False
    failure_reasons: tuple[str, ...] = ()
    dimension_grades: tuple[ReviewDimensionGrade, ...] = ()
    note: str | None = None
    display_order: int = Field(ge=0)
    method_name_visible: bool = True
    rubric_version: str = "cn-review-v2"
    created_at: datetime = Field(default_factory=utc_now)
    supersedes_event_id: str | None = None

    _event_id = field_validator("event_id")(validate_id)


class RunManifest(FrozenModel):
    schema_version: str = "1.0"
    run_id: str
    run_type: Literal["standard_profile", "standard_four", "guided", "parameter_search"] = (
        "standard_profile"
    )
    dataset_id: str
    dataset_fingerprint: str
    created_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None
    status: Literal["RUNNING", "COMPLETED", "PARTIAL_COMPLETED", "FAILED"]
    methods: tuple[str, ...]
    config_hash: str
    config_snapshot: str
    code_version: str
    python_version: str
    dependency_versions: dict[str, str]
    task_ids: tuple[str, ...]
    candidate_ids: tuple[str, ...] = ()
    failed_candidate_ids: tuple[str, ...] = ()

    _run_id = field_validator("run_id")(validate_id)


class ReplayManifest(FrozenModel):
    replay_id: str
    source_run_id: str
    evaluator_id: str
    selector_id: str
    config_hash: str
    candidate_ids: tuple[str, ...]
    created_at: datetime = Field(default_factory=utc_now)

    _replay_id = field_validator("replay_id")(validate_id)


class EvaluationManifest(FrozenModel):
    evaluation_id: str
    source_run_id: str
    evaluator_id: str
    evaluator_version: str
    config_hash: str
    strategy_id: str | None = None
    strategy_version: str | None = None
    strategy_sha256: str | None = None
    strategy_snapshot: str | None = None
    task_ids: tuple[str, ...]
    candidate_ids: tuple[str, ...]
    metric_bundle_ids: tuple[str, ...]
    created_at: datetime = Field(default_factory=utc_now)

    _evaluation_id = field_validator("evaluation_id")(validate_id)
    _source_run_id = field_validator("source_run_id")(validate_id)
    _evaluator_id = field_validator("evaluator_id")(validate_id)


class RuleDecisionRecord(FrozenModel):
    """Frozen Rule ranking derived from one immutable Evaluation."""

    schema_version: str = "1.0"
    decision_id: str
    source_run_id: str
    evaluation_id: str
    task_id: str
    selector_id: str
    selector_version: str
    strategy_id: str | None = None
    strategy_version: str | None = None
    strategy_sha256: str | None = None
    candidate_ranking: tuple[str, ...]
    selected_candidate_id: str | None
    failed_candidate_ids: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()
    decision_source: Literal["evaluation", "legacy_reconstructed"] = "evaluation"
    created_at: datetime = Field(default_factory=utc_now)

    _decision_id = field_validator("decision_id")(validate_id)
    _source_run_id = field_validator("source_run_id")(validate_id)
    _evaluation_id = field_validator("evaluation_id")(validate_id)
    _task_id = field_validator("task_id")(validate_id)
    _selector_id = field_validator("selector_id")(validate_id)


# Future-boundary records. They deliberately carry no provider-specific fields.
class ProtectionDecision(FrozenModel):
    decision_id: str
    task_id: str
    region_priorities: dict[str, str]
    unresolved_region_ids: tuple[str, ...] = ()
    confidence: float = Field(ge=0.0, le=1.0)


class AgentDecision(FrozenModel):
    decision_id: str
    task_id: str
    candidate_ranking: tuple[str, ...]
    best_candidate_id: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    reason_codes: tuple[str, ...] = ()


class FallbackDecision(FrozenModel):
    decision_id: str
    task_id: str
    action: Literal[
        "USE_BEST_TRADITIONAL", "CALL_EXTERNAL_AIGC", "REQUEST_MANUAL_REVIEW", "RETURN_FAILURE"
    ]
    reason_codes: tuple[str, ...] = ()


class AgentCallRecord(FrozenModel):
    agent_call_id: str
    task_id: str
    insertion_point: Literal["protection_resolution", "candidate_judging", "fallback_decision"]
    agent_id: str
    agent_version: str
    model_version: str | None = None
    prompt_version: str
    input_hash: str
    parsed_output: dict[str, Any] | None = None
    success: bool
    error_type: str | None = None
    latency_seconds: float | None = Field(default=None, ge=0.0)
    tokens: int | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    attempt_count: int = Field(default=1, ge=1, le=2)
    cache_hit: bool = False
    estimated_cost: float | None = Field(default=None, ge=0.0)
    changed_top1: bool = False
    fallback_strategy: str | None = None


class ExternalCallRecord(FrozenModel):
    call_id: str
    provider_id: str
    provider_version: str
    task_id: str
    request_hash: str
    technical_success: bool
    business_grade: ReviewGrade | None = None
    data_egress_allowed: bool
    latency_seconds: float | None = Field(default=None, ge=0.0)
    actual_cost: float | None = Field(default=None, ge=0.0)
    currency: str | None = None


class WorkflowInvocationRecord(FrozenModel):
    invocation_id: str
    backend_id: str
    backend_version: str
    workflow_id: str
    workflow_version: str
    task_id: str
    template_hash: str
    technical_success: bool
    latency_seconds: float | None = Field(default=None, ge=0.0)


class ServiceJobRecord(FrozenModel):
    job_id: str
    request_id: str
    status: Literal["QUEUED", "RUNNING", "COMPLETED", "FAILED", "CANCELLED"]
    task_id: str | None = None
    run_id: str | None = None
    result_ref: str | None = None
    error_type: str | None = None


class QualityDimensionRecord(FrozenModel):
    record_id: str
    candidate_id: str
    dimension: str
    status: Literal["PASS", "MINOR", "MAJOR", "FAIL", "UNKNOWN"]
    reason_codes: tuple[str, ...] = ()


class ProviderCapability(FrozenModel):
    provider_id: str
    provider_version: str
    supports_async: bool
    supports_cancel: bool
    supports_seed: bool
    supports_mask: bool
    max_outputs: int = Field(ge=1)
