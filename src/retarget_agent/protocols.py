"""Replaceable boundaries; the runner depends on these protocols, not implementations."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import numpy as np

from .models import (
    AnalysisArtifact,
    CandidateRecord,
    DecisionRecord,
    ExecutionContext,
    HumanGuidance,
    MethodConfig,
    MetricBundle,
    ProviderCapability,
    RegionRecord,
    ReviewEvent,
    RunManifest,
    TaskSpec,
    TransformRecord,
)


@dataclass(slots=True)
class DatasetValidationResult:
    dataset_id: str
    dataset_fingerprint: str
    tasks: list[TaskSpec]
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors


@dataclass(slots=True)
class AnalysisOutput:
    importance_map: np.ndarray
    tolerance_map: np.ndarray
    regions: tuple[RegionRecord, ...]
    analyzer_ids: tuple[str, ...]
    warnings: tuple[str, ...] = ()


@dataclass(slots=True)
class MethodOutput:
    image: np.ndarray | None
    transform: TransformRecord
    status: str = "SUCCESS"
    warnings: tuple[str, ...] = ()
    failure_type: str | None = None
    error_summary: str | None = None


@runtime_checkable
class DatasetAdapter(Protocol):
    adapter_id: str
    adapter_version: str

    def validate(self, dataset_root: Path) -> DatasetValidationResult: ...


@runtime_checkable
class Analyzer(Protocol):
    analyzer_id: str
    analyzer_version: str

    def analyze(
        self,
        image: np.ndarray,
        task: TaskSpec,
        guidance: HumanGuidance | None = None,
    ) -> AnalysisOutput: ...


@runtime_checkable
class CandidateMethod(Protocol):
    method_id: str
    method_version: str

    def generate(
        self,
        image: np.ndarray,
        task: TaskSpec,
        analysis: AnalysisArtifact,
        importance_map: np.ndarray,
        tolerance_map: np.ndarray,
        guidance: HumanGuidance | None,
        config: MethodConfig,
        context: ExecutionContext,
    ) -> MethodOutput: ...


class Evaluator(Protocol):
    evaluator_id: str
    evaluator_version: str

    def evaluate(self, candidate: CandidateRecord) -> MetricBundle: ...


class SelectorPolicy(Protocol):
    selector_id: str
    selector_version: str

    def select(
        self, task: TaskSpec, candidates: list[CandidateRecord], metrics: list[MetricBundle]
    ) -> DecisionRecord: ...


class ArtifactStore(Protocol):
    def write_json(self, relative_path: str, value: Any) -> Path: ...
    def write_image(self, relative_path: str, image: np.ndarray) -> Path: ...


class EventStore(Protocol):
    def initialize(self) -> None: ...
    def append_review(self, event: ReviewEvent) -> None: ...
    def append_run(self, manifest: RunManifest) -> None: ...


class InstrumentationHook(Protocol):
    def stage(self, name: str, **context: str) -> Any: ...


class AgentPlugin(Protocol):
    agent_id: str
    agent_version: str


class ExternalAIGCProvider(Protocol):
    provider_id: str
    provider_version: str

    def capabilities(self) -> ProviderCapability: ...


class WorkflowBackend(Protocol):
    backend_id: str
    backend_version: str

    def capabilities(self) -> dict[str, Any]: ...


class PostProcessor(Protocol):
    processor_id: str
    processor_version: str

    def process(self, artifact: CandidateRecord, parameters: dict[str, Any]) -> CandidateRecord: ...
