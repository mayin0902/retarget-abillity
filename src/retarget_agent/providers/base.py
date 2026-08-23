"""Small, provider-neutral seam for external image generation.

The interface deliberately contains only facts shared by image-to-image
providers.  Authentication, provider request fields, model limits and polling
remain inside each adapter.  Budget and idempotency controls are optional
runtime concerns rather than requirements imposed on every provider.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from retarget_agent.models import SHA256_PATTERN, ProviderCapability, validate_id


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AIGCGenerationRequest(FrozenModel):
    """Provider-neutral image-to-image request.

    Exactly one source transport is supplied.  A local source is convenient for
    application callers; an HTTPS source lets an adapter avoid re-encoding an
    already hosted image.  The hash identifies the bytes the caller intended to
    submit and is verified for local files before any provider is called.
    """

    task_id: str
    run_id: str
    request_id: str
    source_path: Path | None = None
    source_url: str | None = None
    source_sha256: str
    target_width: int = Field(gt=0)
    target_height: int = Field(gt=0)
    target_format: str = "png"
    prompt: str = Field(min_length=1, max_length=20_000)
    prompt_version: str = Field(min_length=1, max_length=128)

    _task_id = field_validator("task_id")(validate_id)
    _run_id = field_validator("run_id")(validate_id)
    _request_id = field_validator("request_id")(validate_id)

    @field_validator("source_sha256")
    @classmethod
    def valid_source_sha256(cls, value: str) -> str:
        if not SHA256_PATTERN.fullmatch(value):
            raise ValueError("source_sha256 must be 64 lowercase hexadecimal characters")
        return value

    @field_validator("target_format")
    @classmethod
    def supported_target_format(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"jpeg", "png", "webp"}:
            raise ValueError("target_format must be jpeg, png or webp")
        return normalized

    @model_validator(mode="after")
    def one_source(self) -> AIGCGenerationRequest:
        if (self.source_path is None) == (self.source_url is None):
            raise ValueError("provide exactly one of source_path or source_url")
        return self

    @property
    def prompt_sha256(self) -> str:
        return hashlib.sha256(self.prompt.encode("utf-8")).hexdigest()


class AIGCProviderResult(FrozenModel):
    """Successful provider output before common execution telemetry is added."""

    provider_id: str
    provider_version: str
    task_id: str
    request_id: str
    output_path: Path
    output_sha256: str
    media_type: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    cache_hit: bool | None = None
    idempotency_key: str | None = None
    estimated_cost_min_cny: Decimal | None = Field(default=None, ge=0)
    estimated_cost_max_cny: Decimal | None = Field(default=None, ge=0)
    actual_cost_cny: Decimal | None = Field(default=None, ge=0)

    @field_validator("output_sha256")
    @classmethod
    def valid_output_sha256(cls, value: str) -> str:
        if not SHA256_PATTERN.fullmatch(value):
            raise ValueError("output_sha256 must be 64 lowercase hexadecimal characters")
        return value


class AIGCProviderError(RuntimeError):
    """Sanitized provider failure understood by the common executor."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        charge_may_have_occurred: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.charge_may_have_occurred = charge_may_have_occurred


class AIGCProvider(Protocol):
    """External seam implemented by each image-generation adapter."""

    provider_id: str
    provider_version: str

    def capabilities(self) -> ProviderCapability: ...

    def generate(self, request: AIGCGenerationRequest) -> AIGCProviderResult: ...


@dataclass(frozen=True, slots=True)
class AIGCProviderRuntime:
    """Non-serializable runtime dependencies passed to a provider factory."""

    output_root: Path
    cache_root: Path
    timeout_seconds: float = 300.0
    maximum_cost_cny: Decimal | None = None
    idempotency_key: str | None = None
    environ: Mapping[str, str] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.maximum_cost_cny is not None and self.maximum_cost_cny < 0:
            raise ValueError("maximum_cost_cny must be non-negative")
        if self.idempotency_key is not None and not self.idempotency_key.strip():
            raise ValueError("idempotency_key must be non-empty when provided")


class AIGCProviderFactory(Protocol):
    def __call__(self, runtime: AIGCProviderRuntime) -> AIGCProvider: ...


__all__ = [
    "AIGCGenerationRequest",
    "AIGCProvider",
    "AIGCProviderError",
    "AIGCProviderFactory",
    "AIGCProviderResult",
    "AIGCProviderRuntime",
]
