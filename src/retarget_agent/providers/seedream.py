"""Policy-gated SeedDream image-to-image adapter with durable idempotency.

The adapter intentionally exposes one synchronous ``generate`` operation.  It
owns provider request mapping, output freezing and duplicate-charge protection;
it does not decide when a task deserves external generation and it does not
evaluate business quality.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import ipaddress
import json
import os
import threading
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any, Protocol
from urllib.parse import urlsplit

import requests
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from retarget_agent.costing import (
    BudgetExceededError,
    BudgetLedger,
    CostEntry,
    CostType,
    IdempotencyConflictError,
    InvalidBudgetTransitionError,
)
from retarget_agent.models import SHA256_PATTERN, ProviderCapability, validate_id

_ESTIMATED_COST_MIN_CNY = Decimal("0.30")
_ESTIMATED_COST_MAX_CNY = Decimal("0.60")
_CACHE_SCHEMA_VERSION = "1.0"
_PROVIDER_ID = "seedream_api"
_PROVIDER_VERSION = "1.0.0"
_ALLOWED_IMAGE_MIME = frozenset({"image/jpeg", "image/png", "image/webp"})
_FORMAT_TO_MIME = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SeedDreamErrorCode(StrEnum):
    AUTH_ERROR = "AUTH_ERROR"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    RATE_LIMITED = "RATE_LIMITED"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    DATA_EGRESS_DENIED = "DATA_EGRESS_DENIED"
    INVALID_REQUEST = "INVALID_REQUEST"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    TIMEOUT = "TIMEOUT"
    OUTPUT_MISSING = "OUTPUT_MISSING"
    OUTPUT_INVALID = "OUTPUT_INVALID"
    COST_LIMIT_EXCEEDED = "COST_LIMIT_EXCEEDED"
    CACHED_FAILURE = "CACHED_FAILURE"
    CACHE_CORRUPT = "CACHE_CORRUPT"
    UNKNOWN_PROVIDER_ERROR = "UNKNOWN_PROVIDER_ERROR"


class SeedDreamProviderError(RuntimeError):
    """A normalized, deliberately sanitized provider failure."""

    def __init__(
        self,
        code: SeedDreamErrorCode,
        message: str,
        *,
        charge_may_have_occurred: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.charge_may_have_occurred = charge_may_have_occurred


@dataclass(frozen=True, slots=True)
class SeedDreamProviderConfig:
    """Private runtime configuration; endpoint and credential never serialize."""

    endpoint_url: str = field(repr=False)
    api_key: str = field(repr=False)
    model: str = field(repr=False)
    size: str = "2K"
    watermark: bool = True
    connect_timeout_seconds: float = 10.0
    read_timeout_seconds: float = 180.0
    max_download_bytes: int = 25 * 1024 * 1024
    max_output_pixels: int = 40_000_000

    def __post_init__(self) -> None:
        _validate_public_https_url(self.endpoint_url, purpose="provider endpoint")
        if not self.api_key.strip() or not self.model.strip() or not self.size.strip():
            raise ValueError("api_key, model and size must be non-empty")
        if self.connect_timeout_seconds <= 0 or self.read_timeout_seconds <= 0:
            raise ValueError("provider timeouts must be positive")
        if self.max_download_bytes <= 0 or self.max_output_pixels <= 0:
            raise ValueError("download limits must be positive")

    @classmethod
    def from_env(
        cls,
        *,
        endpoint_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        endpoint_env: str = "SEEDREAM_BASE_URL",
        api_key_env: str = "SEEDREAM_API_KEY",
        model_env: str = "SEEDREAM_MODEL",
        environ: Mapping[str, str] | None = None,
        **kwargs: Any,
    ) -> SeedDreamProviderConfig:
        """Resolve secrets only at runtime from arguments or named environment values."""

        source = os.environ if environ is None else environ
        resolved_endpoint = endpoint_url or source.get(endpoint_env)
        resolved_key = api_key or source.get(api_key_env)
        resolved_model = model or source.get(model_env)
        if not resolved_endpoint or not resolved_key or not resolved_model:
            raise ValueError("SeedDream endpoint, API key and model must be configured at runtime")
        return cls(
            endpoint_url=resolved_endpoint,
            api_key=resolved_key,
            model=resolved_model,
            **kwargs,
        )

    @property
    def generation_config_hash(self) -> str:
        return _sha256_json(
            {
                "response_format": "url",
                "size": self.size,
                "stream": False,
                "watermark": self.watermark,
            }
        )


class SeedDreamGenerationRequest(FrozenModel):
    task_id: str
    run_id: str
    request_id: str
    source_url: str | None = None
    source_data_uri: str | None = Field(default=None, exclude=True, repr=False)
    source_sha256: str
    source_is_public: bool = False
    allow_data_egress: bool = False
    egress_authorization_basis: str | None = Field(default=None, max_length=100)
    target_width: int = Field(gt=0)
    target_height: int = Field(gt=0)
    target_format: str = "png"
    prompt: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    seed: int | None = Field(default=None, ge=0)
    output_count: int = Field(default=1, ge=1, le=1)
    max_cost_cny: Decimal = Field(default=_ESTIMATED_COST_MAX_CNY, ge=0)

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
    def valid_request_shape(self) -> SeedDreamGenerationRequest:
        if self.target_width != self.target_height:
            raise ValueError("this SeedDream experiment adapter accepts square targets only")
        if (self.source_url is None) == (self.source_data_uri is None):
            raise ValueError("provide exactly one of source_url or source_data_uri")
        return self

    @property
    def prompt_sha256(self) -> str:
        return hashlib.sha256(self.prompt.encode("utf-8")).hexdigest()


class SeedDreamGenerationResult(FrozenModel):
    provider_id: str = _PROVIDER_ID
    provider_version: str = _PROVIDER_VERSION
    task_id: str
    request_hash: str
    output_path: Path
    output_sha256: str
    media_type: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    cache_hit: bool
    estimated_cost_min_cny: Decimal = _ESTIMATED_COST_MIN_CNY
    estimated_cost_max_cny: Decimal = _ESTIMATED_COST_MAX_CNY
    actual_cost_cny: Decimal | None = None
    cost_entry: CostEntry


class _IdempotencyMaterial(FrozenModel):
    source_sha256: str
    target: str
    model: str
    prompt_sha256: str
    seed: int | None
    generation_config_sha256: str
    source_transport: str = "url"


class _CacheRecord(FrozenModel):
    idempotency_key: str
    material: _IdempotencyMaterial
    status: str
    task_id: str
    output_relative_path: str | None = None
    output_sha256: str | None = None
    media_type: str | None = None
    width: int | None = None
    height: int | None = None
    error_code: str | None = None
    charge_state: str
    estimated_cost_min_cny: Decimal = _ESTIMATED_COST_MIN_CNY
    estimated_cost_max_cny: Decimal = _ESTIMATED_COST_MAX_CNY
    actual_cost_cny: Decimal | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class _HttpResponse(Protocol):
    status_code: int
    headers: dict[str, str]

    def json(self) -> Any: ...

    def iter_content(self, chunk_size: int) -> Any: ...


class _HttpClient(Protocol):
    def post(self, url: str, **kwargs: Any) -> _HttpResponse: ...

    def get(self, url: str, **kwargs: Any) -> _HttpResponse: ...


class SeedDreamProvider:
    """One-output SeedDream adapter with policy, budget and durable replay guards."""

    provider_id = _PROVIDER_ID
    provider_version = _PROVIDER_VERSION

    def __init__(
        self,
        config: SeedDreamProviderConfig,
        *,
        output_root: Path,
        cache_path: Path,
        budget: BudgetLedger,
        http_client: _HttpClient | None = None,
    ) -> None:
        self._config = config
        self._output_root = output_root.resolve()
        self._cache_path = cache_path.resolve()
        self._budget = budget
        self._http = http_client or requests.Session()
        self._lock = threading.RLock()
        self._output_root.mkdir(parents=True, exist_ok=True)
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)

    def capabilities(self) -> ProviderCapability:
        return ProviderCapability(
            provider_id=self.provider_id,
            provider_version=self.provider_version,
            supports_async=False,
            supports_cancel=False,
            supports_seed=False,
            supports_mask=False,
            max_outputs=1,
        )

    def build_idempotency_key(self, request: SeedDreamGenerationRequest) -> str:
        """Hash every output-affecting field without storing the source URL or prompt."""

        material = self._idempotency_material(request)
        return f"seedream-v1-{_sha256_json(material.model_dump(mode='json'))}"

    def generate(self, request: SeedDreamGenerationRequest) -> SeedDreamGenerationResult:
        """Generate and freeze exactly one image, or raise a normalized safe error."""

        self._enforce_policy(request)
        material = self._idempotency_material(request)
        key = f"seedream-v1-{_sha256_json(material.model_dump(mode='json'))}"

        with self._lock:
            records = self._load_cache()
            cached = records.get(key)
            if cached is not None:
                return self._read_cached_result(cached)

            with self._exclusive_claim(key):
                if self._budget.get(key) is not None:
                    raise SeedDreamProviderError(
                        SeedDreamErrorCode.CACHED_FAILURE,
                        "an existing budget record suppresses a duplicate external call",
                        charge_may_have_occurred=True,
                    )
                try:
                    self._budget.reserve(key, _ESTIMATED_COST_MAX_CNY)
                except (
                    BudgetExceededError,
                    IdempotencyConflictError,
                    InvalidBudgetTransitionError,
                ) as error:
                    raise SeedDreamProviderError(
                        SeedDreamErrorCode.COST_LIMIT_EXCEEDED,
                        "external generation budget is unavailable",
                    ) from error

                # A durable pending record is written before network I/O.  If this
                # process dies after submission, another process will fail closed
                # instead of creating a second billable request.
                records[key] = _CacheRecord(
                    idempotency_key=key,
                    material=material,
                    status="pending",
                    task_id=request.task_id,
                    charge_state="reserved_or_unknown",
                )
                try:
                    self._save_cache(records)
                except SeedDreamProviderError:
                    self._budget.release(key)
                    raise

                try:
                    response = self._submit_once(request)
                except Exception as error:
                    self._budget.commit(key, actual_amount=None)
                    self._persist_failure(
                        records,
                        key,
                        material,
                        request.task_id,
                        SeedDreamErrorCode.TIMEOUT
                        if isinstance(error, requests.Timeout)
                        else SeedDreamErrorCode.PROVIDER_UNAVAILABLE,
                        charge_state="unknown_after_submit",
                    )
                    code = (
                        SeedDreamErrorCode.TIMEOUT
                        if isinstance(error, requests.Timeout)
                        else SeedDreamErrorCode.PROVIDER_UNAVAILABLE
                    )
                    raise SeedDreamProviderError(
                        code,
                        "provider submission failed; duplicate external call is suppressed",
                        charge_may_have_occurred=True,
                    ) from error

                if response.status_code != 200:
                    code = _http_error_code(response.status_code)
                    charge_unknown = _http_status_charge_unknown(response.status_code)
                    if charge_unknown:
                        self._budget.commit(key, actual_amount=None)
                    else:
                        self._budget.release(key)
                    self._persist_failure(
                        records,
                        key,
                        material,
                        request.task_id,
                        code,
                        charge_state="unknown_after_submit" if charge_unknown else "not_charged",
                    )
                    raise SeedDreamProviderError(
                        code,
                        "provider rejected the generation request",
                        charge_may_have_occurred=charge_unknown,
                    )

                self._budget.commit(key, actual_amount=None)
                try:
                    download_url = self._extract_single_output_url(response)
                    image_bytes, media_type, width, height = self._download_and_validate(
                        download_url
                    )
                    output_path, output_sha256 = self._freeze_output(key, image_bytes, media_type)
                except SeedDreamProviderError as error:
                    self._persist_failure(
                        records,
                        key,
                        material,
                        request.task_id,
                        error.code,
                        charge_state="committed_unknown_actual",
                    )
                    raise
                except Exception as error:
                    self._persist_failure(
                        records,
                        key,
                        material,
                        request.task_id,
                        SeedDreamErrorCode.UNKNOWN_PROVIDER_ERROR,
                        charge_state="committed_unknown_actual",
                    )
                    raise SeedDreamProviderError(
                        SeedDreamErrorCode.UNKNOWN_PROVIDER_ERROR,
                        "provider output could not be frozen",
                        charge_may_have_occurred=True,
                    ) from error

                record = _CacheRecord(
                    idempotency_key=key,
                    material=material,
                    status="success",
                    task_id=request.task_id,
                    output_relative_path=output_path.relative_to(self._output_root).as_posix(),
                    output_sha256=output_sha256,
                    media_type=media_type,
                    width=width,
                    height=height,
                    charge_state="committed_unknown_actual",
                )
                records[key] = record
                self._save_cache(records)
                return self._result_from_record(record, cache_hit=False)

    def _enforce_policy(self, request: SeedDreamGenerationRequest) -> None:
        explicit_user_authorization = bool(
            request.egress_authorization_basis
            and request.egress_authorization_basis.startswith("user_explicit_")
        )
        if not request.allow_data_egress or not (
            request.source_is_public or explicit_user_authorization
        ):
            raise SeedDreamProviderError(
                SeedDreamErrorCode.DATA_EGRESS_DENIED,
                "public-source or explicit user authorization and data-egress approval is required",
            )
        if request.source_url is not None:
            try:
                _validate_public_https_url(request.source_url, purpose="source image")
            except ValueError as error:
                raise SeedDreamProviderError(
                    SeedDreamErrorCode.INVALID_REQUEST, "source image URL is not allowed"
                ) from error
        else:
            try:
                decoded = _decode_data_image(request.source_data_uri or "")
            except ValueError as error:
                raise SeedDreamProviderError(
                    SeedDreamErrorCode.INVALID_REQUEST,
                    "source image Base64 data is not allowed",
                ) from error
            if hashlib.sha256(decoded).hexdigest() != request.source_sha256:
                raise SeedDreamProviderError(
                    SeedDreamErrorCode.INVALID_REQUEST,
                    "source image Base64 data does not match source_sha256",
                )
        if request.output_count != 1:
            raise SeedDreamProviderError(
                SeedDreamErrorCode.INVALID_REQUEST, "exactly one output is allowed per task"
            )
        if request.seed is not None:
            raise SeedDreamProviderError(
                SeedDreamErrorCode.INVALID_REQUEST,
                "the verified provider contract does not declare seed support",
            )
        if request.max_cost_cny < _ESTIMATED_COST_MAX_CNY:
            raise SeedDreamProviderError(
                SeedDreamErrorCode.COST_LIMIT_EXCEEDED,
                "per-task maximum cost is below the conservative estimate",
            )

    def _idempotency_material(self, request: SeedDreamGenerationRequest) -> _IdempotencyMaterial:
        return _IdempotencyMaterial(
            source_sha256=request.source_sha256,
            target=f"{request.target_width}x{request.target_height}:{request.target_format}",
            model=self._config.model,
            prompt_sha256=request.prompt_sha256,
            seed=request.seed,
            generation_config_sha256=self._config.generation_config_hash,
            source_transport="base64_data_uri" if request.source_data_uri else "url",
        )

    def _submit_once(self, request: SeedDreamGenerationRequest) -> _HttpResponse:
        payload = {
            "model": self._config.model,
            "prompt": request.prompt,
            "image": request.source_data_uri or request.source_url,
            "response_format": "url",
            "size": self._config.size,
            "stream": False,
            "watermark": self._config.watermark,
        }
        return self._http.post(
            self._config.endpoint_url,
            headers={
                "Authorization": f"Bearer {self._config.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=(
                self._config.connect_timeout_seconds,
                self._config.read_timeout_seconds,
            ),
            allow_redirects=False,
        )

    def _extract_single_output_url(self, response: _HttpResponse) -> str:
        try:
            payload = response.json()
            outputs = payload["data"]
            if not isinstance(outputs, list) or len(outputs) != 1:
                raise ValueError("expected one output")
            output_url = outputs[0]["url"]
            if not isinstance(output_url, str):
                raise ValueError("missing output URL")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise SeedDreamProviderError(
                SeedDreamErrorCode.OUTPUT_MISSING,
                "provider response did not contain exactly one allowed output",
                charge_may_have_occurred=True,
            ) from error
        try:
            _validate_public_https_url(output_url, purpose="provider output")
        except ValueError as error:
            raise SeedDreamProviderError(
                SeedDreamErrorCode.OUTPUT_INVALID,
                "provider output URL is not allowed",
                charge_may_have_occurred=True,
            ) from error
        return output_url

    def _download_and_validate(self, output_url: str) -> tuple[bytes, str, int, int]:
        try:
            response = self._http.get(
                output_url,
                timeout=(
                    self._config.connect_timeout_seconds,
                    self._config.read_timeout_seconds,
                ),
                allow_redirects=False,
                stream=True,
            )
        except Exception as error:
            code = (
                SeedDreamErrorCode.TIMEOUT
                if isinstance(error, requests.Timeout)
                else SeedDreamErrorCode.PROVIDER_UNAVAILABLE
            )
            raise SeedDreamProviderError(
                code,
                "provider output download failed",
                charge_may_have_occurred=True,
            ) from error
        if response.status_code != 200:
            raise SeedDreamProviderError(
                SeedDreamErrorCode.OUTPUT_MISSING,
                "provider output download was not successful",
                charge_may_have_occurred=True,
            )
        media_type = _normalized_media_type(response.headers.get("Content-Type", ""))
        if media_type not in _ALLOWED_IMAGE_MIME:
            raise SeedDreamProviderError(
                SeedDreamErrorCode.OUTPUT_INVALID,
                "provider output MIME type is not allowed",
                charge_may_have_occurred=True,
            )
        content_length = response.headers.get("Content-Length")
        if content_length:
            try:
                if int(content_length) > self._config.max_download_bytes:
                    raise SeedDreamProviderError(
                        SeedDreamErrorCode.OUTPUT_INVALID,
                        "provider output exceeds the download limit",
                        charge_may_have_occurred=True,
                    )
            except ValueError as error:
                raise SeedDreamProviderError(
                    SeedDreamErrorCode.OUTPUT_INVALID,
                    "provider output has an invalid content length",
                    charge_may_have_occurred=True,
                ) from error

        chunks: list[bytes] = []
        total = 0
        try:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > self._config.max_download_bytes:
                    raise SeedDreamProviderError(
                        SeedDreamErrorCode.OUTPUT_INVALID,
                        "provider output exceeds the download limit",
                        charge_may_have_occurred=True,
                    )
                chunks.append(chunk)
        except SeedDreamProviderError:
            raise
        except Exception as error:
            code = (
                SeedDreamErrorCode.TIMEOUT
                if isinstance(error, requests.Timeout)
                else SeedDreamErrorCode.PROVIDER_UNAVAILABLE
            )
            raise SeedDreamProviderError(
                code,
                "provider output stream failed",
                charge_may_have_occurred=True,
            ) from error
        data = b"".join(chunks)
        if not data:
            raise SeedDreamProviderError(
                SeedDreamErrorCode.OUTPUT_MISSING,
                "provider output was empty",
                charge_may_have_occurred=True,
            )
        width, height, detected_mime = _decode_image(data, self._config.max_output_pixels)
        if detected_mime != media_type:
            raise SeedDreamProviderError(
                SeedDreamErrorCode.OUTPUT_INVALID,
                "provider output MIME does not match decoded image",
                charge_may_have_occurred=True,
            )
        if width != height:
            raise SeedDreamProviderError(
                SeedDreamErrorCode.OUTPUT_INVALID,
                "provider output is not square",
                charge_may_have_occurred=True,
            )
        return data, media_type, width, height

    def _freeze_output(self, key: str, data: bytes, media_type: str) -> tuple[Path, str]:
        extension = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}[media_type]
        output_path = self._output_root / f"{key}{extension}"
        temporary = self._output_root / f".{key}.{uuid.uuid4().hex}.tmp"
        try:
            with temporary.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, output_path)
        finally:
            temporary.unlink(missing_ok=True)
        return output_path, hashlib.sha256(data).hexdigest()

    def _read_cached_result(self, record: _CacheRecord) -> SeedDreamGenerationResult:
        if record.status != "success":
            raise SeedDreamProviderError(
                SeedDreamErrorCode.CACHED_FAILURE,
                "an earlier identical attempt failed; duplicate external call is suppressed",
                charge_may_have_occurred=record.charge_state != "not_charged",
            )
        return self._result_from_record(record, cache_hit=True)

    def _result_from_record(
        self, record: _CacheRecord, *, cache_hit: bool
    ) -> SeedDreamGenerationResult:
        if not all(
            (
                record.output_relative_path,
                record.output_sha256,
                record.media_type,
                record.width,
                record.height,
            )
        ):
            raise SeedDreamProviderError(
                SeedDreamErrorCode.CACHE_CORRUPT, "cached output metadata is incomplete"
            )
        relative = PurePosixPath(record.output_relative_path or "")
        if relative.is_absolute() or ".." in relative.parts:
            raise SeedDreamProviderError(
                SeedDreamErrorCode.CACHE_CORRUPT, "cached output path is invalid"
            )
        output_path = (self._output_root / Path(*relative.parts)).resolve()
        try:
            output_path.relative_to(self._output_root)
        except ValueError as error:
            raise SeedDreamProviderError(
                SeedDreamErrorCode.CACHE_CORRUPT, "cached output escaped its artifact root"
            ) from error
        try:
            data = output_path.read_bytes()
        except OSError as error:
            raise SeedDreamProviderError(
                SeedDreamErrorCode.CACHE_CORRUPT, "cached output is unavailable"
            ) from error
        if hashlib.sha256(data).hexdigest() != record.output_sha256:
            raise SeedDreamProviderError(
                SeedDreamErrorCode.CACHE_CORRUPT, "cached output hash does not match"
            )
        width, height, media_type = _decode_image(data, self._config.max_output_pixels)
        if (
            width != record.width
            or height != record.height
            or width != height
            or media_type != record.media_type
        ):
            raise SeedDreamProviderError(
                SeedDreamErrorCode.CACHE_CORRUPT, "cached output validation failed"
            )
        call_id = f"seedream-{record.idempotency_key.removeprefix('seedream-v1-')[:16]}"
        cost_entry = CostEntry(
            entry_id=f"cost-{call_id}",
            task_id=record.task_id,
            call_id=call_id,
            cost_type=CostType.DIRECT,
            estimated_amount=record.estimated_cost_max_cny,
            actual_amount=record.actual_cost_cny,
            currency="CNY",
            pricing_source="user_provided_seedream_range",
            quantity=Decimal(1),
            unit="image",
        )
        return SeedDreamGenerationResult(
            task_id=record.task_id,
            request_hash=record.idempotency_key,
            output_path=output_path,
            output_sha256=record.output_sha256 or "",
            media_type=record.media_type or "",
            width=record.width or 0,
            height=record.height or 0,
            cache_hit=cache_hit,
            estimated_cost_min_cny=record.estimated_cost_min_cny,
            estimated_cost_max_cny=record.estimated_cost_max_cny,
            actual_cost_cny=record.actual_cost_cny,
            cost_entry=cost_entry,
        )

    def _persist_failure(
        self,
        records: dict[str, _CacheRecord],
        key: str,
        material: _IdempotencyMaterial,
        task_id: str,
        error_code: SeedDreamErrorCode,
        *,
        charge_state: str,
    ) -> None:
        records[key] = _CacheRecord(
            idempotency_key=key,
            material=material,
            status="failure",
            task_id=task_id,
            error_code=error_code.value,
            charge_state=charge_state,
        )
        self._save_cache(records)

    @contextmanager
    def _exclusive_claim(self, key: str) -> Iterator[None]:
        """Claim one logical request across provider instances and processes."""

        claim_path = self._cache_path.parent / f".{self._cache_path.name}.{key}.claim"
        try:
            with claim_path.open("x", encoding="ascii") as handle:
                handle.write("external-call-claim\n")
        except FileExistsError as error:
            raise SeedDreamProviderError(
                SeedDreamErrorCode.CACHED_FAILURE,
                "an identical external request is already claimed; duplicate call is suppressed",
                charge_may_have_occurred=True,
            ) from error
        try:
            yield
        finally:
            claim_path.unlink(missing_ok=True)

    def _load_cache(self) -> dict[str, _CacheRecord]:
        if not self._cache_path.exists():
            return {}
        try:
            if self._cache_path.stat().st_size > 5 * 1024 * 1024:
                raise ValueError("cache file is too large")
            payload = json.loads(self._cache_path.read_text(encoding="utf-8"))
            if payload.get("schema_version") != _CACHE_SCHEMA_VERSION:
                raise ValueError("unsupported cache schema")
            raw_records = payload.get("records")
            if not isinstance(raw_records, dict):
                raise ValueError("invalid cache records")
            records = {
                key: _CacheRecord.model_validate(value) for key, value in raw_records.items()
            }
            if any(key != record.idempotency_key for key, record in records.items()):
                raise ValueError("cache key mismatch")
            return records
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise SeedDreamProviderError(
                SeedDreamErrorCode.CACHE_CORRUPT, "idempotency cache could not be read"
            ) from error

    def _save_cache(self, records: dict[str, _CacheRecord]) -> None:
        payload = {
            "schema_version": _CACHE_SCHEMA_VERSION,
            "records": {
                key: value.model_dump(mode="json") for key, value in sorted(records.items())
            },
        }
        temporary = self._cache_path.with_name(f".{self._cache_path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, ensure_ascii=True, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._cache_path)
        except OSError as error:
            raise SeedDreamProviderError(
                SeedDreamErrorCode.CACHE_CORRUPT, "idempotency cache could not be persisted"
            ) from error
        finally:
            temporary.unlink(missing_ok=True)


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _normalized_media_type(value: str) -> str:
    normalized = value.split(";", 1)[0].strip().lower()
    return "image/jpeg" if normalized == "image/jpg" else normalized


def _decode_data_image(value: str) -> bytes:
    """Validate and decode an official image Base64 data URI without persisting it."""

    prefix, separator, encoded = value.partition(",")
    if separator != "," or prefix.lower() not in {
        "data:image/jpeg;base64",
        "data:image/png;base64",
        "data:image/webp;base64",
    }:
        raise ValueError("unsupported image data URI")
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError, TypeError) as error:
        raise ValueError("invalid Base64 image") from error
    if not decoded or len(decoded) > 10 * 1024 * 1024:
        raise ValueError("Base64 image exceeds the 10 MiB input safety bound")
    declared_mime = prefix[5:].split(";", 1)[0].lower()
    try:
        with Image.open(BytesIO(decoded)) as image:
            width, height = image.size
            decoded_mime = _FORMAT_TO_MIME.get(image.format or "")
            if (
                decoded_mime != declared_mime
                or width <= 0
                or height <= 0
                or width * height > 40_000_000
            ):
                raise ValueError("Base64 image type or dimensions are invalid")
            image.verify()
    except (OSError, UnidentifiedImageError, ValueError) as error:
        raise ValueError("Base64 source is not a valid bounded image") from error
    return decoded


def _decode_image(data: bytes, max_pixels: int) -> tuple[int, int, str]:
    try:
        with Image.open(BytesIO(data)) as image:
            width, height = image.size
            image_format = image.format
            if width <= 0 or height <= 0 or width * height > max_pixels:
                raise ValueError("decoded image exceeds pixel limits")
            image.verify()
        with Image.open(BytesIO(data)) as image:
            image.load()
    except (OSError, UnidentifiedImageError, ValueError) as error:
        raise SeedDreamProviderError(
            SeedDreamErrorCode.OUTPUT_INVALID,
            "provider output is not a valid bounded image",
            charge_may_have_occurred=True,
        ) from error
    media_type = _FORMAT_TO_MIME.get(image_format or "")
    if media_type is None:
        raise SeedDreamProviderError(
            SeedDreamErrorCode.OUTPUT_INVALID,
            "decoded provider image format is not allowed",
            charge_may_have_occurred=True,
        )
    return width, height, media_type


def _validate_public_https_url(value: str, *, purpose: str) -> None:
    parsed = urlsplit(value)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError(f"{purpose} must use HTTPS")
    if parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise ValueError(f"{purpose} URL contains forbidden components")
    try:
        if parsed.port not in (None, 443):
            raise ValueError(f"{purpose} must use the default HTTPS port")
    except ValueError as error:
        raise ValueError(f"{purpose} contains an invalid port") from error
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(
        (".localhost", ".local", ".internal")
    ):
        raise ValueError(f"{purpose} must use a public host")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        if "." not in hostname:
            raise ValueError(f"{purpose} must use a qualified public host") from None
    else:
        if not address.is_global:
            raise ValueError(f"{purpose} must not use a private or reserved address")


def _http_error_code(status_code: int) -> SeedDreamErrorCode:
    if status_code == 401:
        return SeedDreamErrorCode.AUTH_ERROR
    if status_code == 402:
        return SeedDreamErrorCode.QUOTA_EXCEEDED
    if status_code == 403:
        return SeedDreamErrorCode.POLICY_BLOCKED
    if status_code == 429:
        return SeedDreamErrorCode.RATE_LIMITED
    if status_code in {408, 504}:
        return SeedDreamErrorCode.TIMEOUT
    if 400 <= status_code < 500:
        return SeedDreamErrorCode.INVALID_REQUEST
    if status_code >= 500:
        return SeedDreamErrorCode.PROVIDER_UNAVAILABLE
    return SeedDreamErrorCode.UNKNOWN_PROVIDER_ERROR


def _http_status_charge_unknown(status_code: int) -> bool:
    """Treat timeout and server failures as possibly billed after accepting work."""

    return status_code in {408, 504} or status_code >= 500
