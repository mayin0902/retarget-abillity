"""Provider-neutral external generation execution and audit recording."""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field

from retarget_agent.plugin_catalog import PluginCatalog, built_in_plugin_catalog
from retarget_agent.providers.base import (
    AIGCGenerationRequest,
    AIGCProviderError,
    AIGCProviderResult,
    AIGCProviderRuntime,
)


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AIGCExecutionStatus(StrEnum):
    PLANNED = "planned"
    SUCCESS = "success"
    FAILED = "failed"


class AIGCExecutionRecord(FrozenModel):
    """Serializable, secret-free result of one generic generation request."""

    schema_version: str = "1.0"
    status: AIGCExecutionStatus
    provider_id: str
    provider_version: str | None = None
    task_id: str
    run_id: str
    request_id: str
    source_sha256: str
    target_width: int = Field(gt=0)
    target_height: int = Field(gt=0)
    target_format: str
    prompt_version: str
    prompt_sha256: str
    executed: bool
    timeout_seconds: float = Field(gt=0)
    maximum_cost_cny: Decimal | None = Field(default=None, ge=0)
    idempotency_key: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    elapsed_seconds: float | None = Field(default=None, ge=0)
    output_relative_path: str | None = None
    output_sha256: str | None = None
    media_type: str | None = None
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    cache_hit: bool | None = None
    estimated_cost_min_cny: Decimal | None = Field(default=None, ge=0)
    estimated_cost_max_cny: Decimal | None = Field(default=None, ge=0)
    actual_cost_cny: Decimal | None = Field(default=None, ge=0)
    error_code: str | None = None
    error_message: str | None = None
    charge_may_have_occurred: bool = False


def execute_generation(
    request: AIGCGenerationRequest,
    provider_id: str,
    output_root: Path,
    *,
    execute: bool,
    timeout_seconds: float = 300.0,
    maximum_cost_cny: Decimal | None = None,
    idempotency_key: str | None = None,
    catalog: PluginCatalog | None = None,
    environ: Mapping[str, str] | None = None,
) -> AIGCExecutionRecord:
    """Plan or execute one request through a registered provider adapter.

    ``execute=False`` is a side-effect-free preflight.  Passing ``execute=True``
    is the caller's explicit authorization to send the source to the selected
    provider.  This module intentionally does not implement a business-specific
    content-egress policy.
    """

    plugins = catalog or built_in_plugin_catalog()
    factory = plugins.generation_providers.get(provider_id)
    runtime = AIGCProviderRuntime(
        output_root=output_root.resolve() / "provider-artifacts" / provider_id,
        cache_root=output_root.resolve() / "provider-cache",
        timeout_seconds=timeout_seconds,
        maximum_cost_cny=maximum_cost_cny,
        idempotency_key=idempotency_key,
        environ=dict(os.environ if environ is None else environ),
    )
    common = _common_record_fields(request, provider_id, runtime)
    if not execute:
        return AIGCExecutionRecord(
            status=AIGCExecutionStatus.PLANNED,
            executed=False,
            **common,
        )

    execution_path = output_root.resolve() / "executions" / f"{request.request_id}.json"
    if execution_path.exists():
        raise FileExistsError(f"generation request_id already exists: {request.request_id}")
    execution_path.parent.mkdir(parents=True, exist_ok=True)
    started = datetime.now(UTC)
    monotonic_start = time.perf_counter()
    try:
        _verify_local_source(request)
        provider = factory(runtime)
        if provider.provider_id != provider_id:
            raise AIGCProviderError(
                "PROVIDER_ID_MISMATCH", "registered provider returned a different provider_id"
            )
        result = provider.generate(request)
        _validate_provider_result(result, request, provider_id, runtime.output_root)
    except AIGCProviderError as error:
        record = AIGCExecutionRecord(
            status=AIGCExecutionStatus.FAILED,
            executed=True,
            provider_version=None,
            started_at=started,
            finished_at=datetime.now(UTC),
            elapsed_seconds=time.perf_counter() - monotonic_start,
            error_code=error.code,
            error_message=_safe_message(error),
            charge_may_have_occurred=error.charge_may_have_occurred,
            **common,
        )
        _write_json_atomic(execution_path, record.model_dump(mode="json"))
        return record
    except (OSError, ValueError) as error:
        record = AIGCExecutionRecord(
            status=AIGCExecutionStatus.FAILED,
            executed=True,
            provider_version=None,
            started_at=started,
            finished_at=datetime.now(UTC),
            elapsed_seconds=time.perf_counter() - monotonic_start,
            error_code="CONFIGURATION_OR_OUTPUT_ERROR",
            error_message=_safe_message(error),
            **common,
        )
        _write_json_atomic(execution_path, record.model_dump(mode="json"))
        return record
    except Exception as error:  # provider adapters must not tear down the caller
        record = AIGCExecutionRecord(
            status=AIGCExecutionStatus.FAILED,
            executed=True,
            provider_version=None,
            started_at=started,
            finished_at=datetime.now(UTC),
            elapsed_seconds=time.perf_counter() - monotonic_start,
            error_code="UNEXPECTED_PROVIDER_ERROR",
            error_message=f"unexpected provider error: {error.__class__.__name__}",
            charge_may_have_occurred=True,
            **common,
        )
        _write_json_atomic(execution_path, record.model_dump(mode="json"))
        return record

    record = AIGCExecutionRecord(
        status=AIGCExecutionStatus.SUCCESS,
        executed=True,
        provider_version=result.provider_version,
        started_at=started,
        finished_at=datetime.now(UTC),
        elapsed_seconds=time.perf_counter() - monotonic_start,
        output_relative_path=result.output_path.resolve().relative_to(output_root.resolve()).as_posix(),
        output_sha256=result.output_sha256,
        media_type=result.media_type,
        width=result.width,
        height=result.height,
        cache_hit=result.cache_hit,
        idempotency_key=result.idempotency_key or idempotency_key,
        estimated_cost_min_cny=result.estimated_cost_min_cny,
        estimated_cost_max_cny=result.estimated_cost_max_cny,
        actual_cost_cny=result.actual_cost_cny,
        **{key: value for key, value in common.items() if key != "idempotency_key"},
    )
    _write_json_atomic(execution_path, record.model_dump(mode="json"))
    return record


def _common_record_fields(
    request: AIGCGenerationRequest,
    provider_id: str,
    runtime: AIGCProviderRuntime,
) -> dict[str, Any]:
    return {
        "provider_id": provider_id,
        "task_id": request.task_id,
        "run_id": request.run_id,
        "request_id": request.request_id,
        "source_sha256": request.source_sha256,
        "target_width": request.target_width,
        "target_height": request.target_height,
        "target_format": request.target_format,
        "prompt_version": request.prompt_version,
        "prompt_sha256": request.prompt_sha256,
        "timeout_seconds": runtime.timeout_seconds,
        "maximum_cost_cny": runtime.maximum_cost_cny,
        "idempotency_key": runtime.idempotency_key,
    }


def _verify_local_source(request: AIGCGenerationRequest) -> None:
    if request.source_path is None:
        return
    try:
        data = request.source_path.resolve().read_bytes()
    except OSError as error:
        raise AIGCProviderError("INVALID_REQUEST", "local source image is unavailable") from error
    if hashlib.sha256(data).hexdigest() != request.source_sha256:
        raise AIGCProviderError(
            "INVALID_REQUEST", "local source image does not match source_sha256"
        )


def _validate_provider_result(
    result: AIGCProviderResult,
    request: AIGCGenerationRequest,
    provider_id: str,
    artifact_root: Path,
) -> None:
    if (
        result.provider_id != provider_id
        or result.task_id != request.task_id
        or result.request_id != request.request_id
    ):
        raise AIGCProviderError(
            "OUTPUT_INVALID", "provider output identity does not match the request"
        )
    path = result.output_path.resolve()
    try:
        path.relative_to(artifact_root.resolve())
        data = path.read_bytes()
    except (OSError, ValueError) as error:
        raise AIGCProviderError(
            "OUTPUT_INVALID", "provider output is outside the artifact root or unavailable"
        ) from error
    if hashlib.sha256(data).hexdigest() != result.output_sha256:
        raise AIGCProviderError("OUTPUT_INVALID", "provider output hash does not match")
    try:
        with Image.open(path) as image:
            width, height = image.size
            image.verify()
    except (OSError, UnidentifiedImageError) as error:
        raise AIGCProviderError("OUTPUT_INVALID", "provider output is not a valid image") from error
    if (width, height) != (result.width, result.height):
        raise AIGCProviderError("OUTPUT_INVALID", "provider output dimensions do not match")


def _safe_message(error: Exception) -> str:
    value = " ".join(str(error).split())
    return value[:500] or error.__class__.__name__


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


__all__ = [
    "AIGCExecutionRecord",
    "AIGCExecutionStatus",
    "execute_generation",
]
