from __future__ import annotations

import hashlib
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from retarget_agent.generation_execution import AIGCExecutionStatus, execute_generation
from retarget_agent.models import ProviderCapability
from retarget_agent.plugin_catalog import PluginCatalog, built_in_plugin_catalog
from retarget_agent.providers.base import (
    AIGCGenerationRequest,
    AIGCProviderError,
    AIGCProviderResult,
    AIGCProviderRuntime,
)
from retarget_agent.providers.seedream import SeedDreamAIGCAdapter


def _source(tmp_path: Path) -> tuple[Path, str]:
    path = tmp_path / "source.png"
    Image.new("RGB", (48, 32), (30, 80, 160)).save(path)
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def _request(tmp_path: Path, **changes: object) -> AIGCGenerationRequest:
    source, digest = _source(tmp_path)
    values: dict[str, object] = {
        "task_id": "poster-001",
        "run_id": "provider-smoke",
        "request_id": "request-001",
        "source_path": source,
        "source_sha256": digest,
        "target_width": 1536,
        "target_height": 1536,
        "prompt": "Preserve the subject and important text.",
        "prompt_version": "smoke-v1",
    }
    values.update(changes)
    return AIGCGenerationRequest(**values)


class FakeProvider:
    provider_id = "fake_image_api"
    provider_version = "1.2.3"

    def __init__(self, runtime: AIGCProviderRuntime, calls: list[AIGCGenerationRequest]) -> None:
        self.runtime = runtime
        self.calls = calls

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

    def generate(self, request: AIGCGenerationRequest) -> AIGCProviderResult:
        self.calls.append(request)
        self.runtime.output_root.mkdir(parents=True, exist_ok=True)
        output = self.runtime.output_root / "result.png"
        Image.new("RGB", (64, 64), (100, 20, 50)).save(output)
        return AIGCProviderResult(
            provider_id=self.provider_id,
            provider_version=self.provider_version,
            task_id=request.task_id,
            request_id=request.request_id,
            output_path=output,
            output_sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
            media_type="image/png",
            width=64,
            height=64,
        )


def _fake_catalog(calls: list[AIGCGenerationRequest]) -> PluginCatalog:
    catalog = PluginCatalog.empty()
    catalog.generation_providers.register(
        "fake_image_api", lambda runtime: FakeProvider(runtime, calls)
    )
    return catalog


def test_catalog_exposes_generation_provider_seam() -> None:
    description = built_in_plugin_catalog().describe()

    assert description["generation_providers"] == ("seedream_api",)


def test_preflight_is_side_effect_free_and_needs_no_provider_configuration(
    tmp_path: Path,
) -> None:
    calls: list[AIGCGenerationRequest] = []
    output_root = tmp_path / "generation"

    result = execute_generation(
        _request(tmp_path),
        "fake_image_api",
        output_root,
        execute=False,
        catalog=_fake_catalog(calls),
        environ={},
    )

    assert result.status is AIGCExecutionStatus.PLANNED
    assert result.executed is False
    assert calls == []
    assert not output_root.exists()


def test_execute_records_valid_image_with_optional_controls_unset(tmp_path: Path) -> None:
    calls: list[AIGCGenerationRequest] = []
    output_root = tmp_path / "generation"
    request = _request(tmp_path)

    result = execute_generation(
        request,
        "fake_image_api",
        output_root,
        execute=True,
        catalog=_fake_catalog(calls),
        environ={},
    )

    assert result.status is AIGCExecutionStatus.SUCCESS
    assert result.maximum_cost_cny is None
    assert result.actual_cost_cny is None
    assert result.idempotency_key is None
    assert result.width == result.height == 64
    assert len(calls) == 1
    assert (output_root / "executions" / "request-001.json").is_file()


def test_optional_budget_and_idempotency_are_forwarded_to_factory(tmp_path: Path) -> None:
    observed: list[AIGCProviderRuntime] = []
    catalog = PluginCatalog.empty()

    def factory(runtime: AIGCProviderRuntime) -> FakeProvider:
        observed.append(runtime)
        return FakeProvider(runtime, [])

    catalog.generation_providers.register("fake_image_api", factory)

    result = execute_generation(
        _request(tmp_path),
        "fake_image_api",
        tmp_path / "generation",
        execute=True,
        maximum_cost_cny=Decimal("0.60"),
        idempotency_key="caller-key-1",
        catalog=catalog,
        environ={},
    )

    assert result.status is AIGCExecutionStatus.SUCCESS
    assert observed[0].maximum_cost_cny == Decimal("0.60")
    assert observed[0].idempotency_key == "caller-key-1"
    assert result.idempotency_key == "caller-key-1"


def test_invalid_provider_image_is_audited_as_failure(tmp_path: Path) -> None:
    class InvalidImageProvider(FakeProvider):
        def generate(self, request: AIGCGenerationRequest) -> AIGCProviderResult:
            self.runtime.output_root.mkdir(parents=True, exist_ok=True)
            output = self.runtime.output_root / "bad.png"
            output.write_text("not an image", encoding="utf-8")
            return AIGCProviderResult(
                provider_id=self.provider_id,
                provider_version=self.provider_version,
                task_id=request.task_id,
                request_id=request.request_id,
                output_path=output,
                output_sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
                media_type="image/png",
                width=1,
                height=1,
            )

    catalog = PluginCatalog.empty()
    catalog.generation_providers.register(
        "fake_image_api", lambda runtime: InvalidImageProvider(runtime, [])
    )

    result = execute_generation(
        _request(tmp_path),
        "fake_image_api",
        tmp_path / "generation",
        execute=True,
        catalog=catalog,
        environ={},
    )

    assert result.status is AIGCExecutionStatus.FAILED
    assert result.error_code == "OUTPUT_INVALID"


def test_local_source_hash_mismatch_is_audited_before_provider_call(tmp_path: Path) -> None:
    calls: list[AIGCGenerationRequest] = []
    output_root = tmp_path / "generation"

    result = execute_generation(
        _request(tmp_path, source_sha256="f" * 64),
        "fake_image_api",
        output_root,
        execute=True,
        catalog=_fake_catalog(calls),
        environ={},
    )

    assert result.status is AIGCExecutionStatus.FAILED
    assert result.error_code == "INVALID_REQUEST"
    assert calls == []
    assert (output_root / "executions" / "request-001.json").is_file()


def test_duplicate_request_id_is_rejected_without_a_second_provider_call(
    tmp_path: Path,
) -> None:
    calls: list[AIGCGenerationRequest] = []
    output_root = tmp_path / "generation"
    catalog = _fake_catalog(calls)
    request = _request(tmp_path)

    first = execute_generation(
        request,
        "fake_image_api",
        output_root,
        execute=True,
        catalog=catalog,
        environ={},
    )
    assert first.status is AIGCExecutionStatus.SUCCESS

    try:
        execute_generation(
            request,
            "fake_image_api",
            output_root,
            execute=True,
            catalog=catalog,
            environ={},
        )
    except FileExistsError:
        pass
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("expected duplicate request_id to fail closed")

    assert len(calls) == 1


def test_seedream_adapter_maps_generic_request_without_business_egress_input(
    tmp_path: Path,
) -> None:
    captured: list[object] = []
    output = tmp_path / "provider-output.png"
    Image.new("RGB", (32, 32), (20, 120, 60)).save(output)

    class LegacyProvider:
        def capabilities(self) -> ProviderCapability:
            return ProviderCapability(
                provider_id="seedream_api",
                provider_version="1.0.0",
                supports_async=False,
                supports_cancel=False,
                supports_seed=False,
                supports_mask=False,
                max_outputs=1,
            )

        def generate(self, request: object) -> object:
            captured.append(request)
            return SimpleNamespace(
                output_path=output,
                output_sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
                media_type="image/png",
                width=32,
                height=32,
                cache_hit=False,
                request_hash="seedream-v1-test",
                estimated_cost_min_cny=Decimal("0.30"),
                estimated_cost_max_cny=Decimal("0.60"),
                actual_cost_cny=None,
            )

    runtime = AIGCProviderRuntime(
        output_root=tmp_path,
        cache_root=tmp_path / "cache",
        maximum_cost_cny=Decimal("0.60"),
    )
    adapter = SeedDreamAIGCAdapter(runtime, legacy_provider=LegacyProvider())  # type: ignore[arg-type]

    result = adapter.generate(_request(tmp_path))

    legacy_request = captured[0]
    assert legacy_request.allow_data_egress is True  # type: ignore[attr-defined]
    assert legacy_request.egress_authorization_basis.startswith(  # type: ignore[attr-defined]
        "user_explicit_"
    )
    assert result.provider_id == "seedream_api"


def test_adapter_normalizes_seedream_failure(tmp_path: Path) -> None:
    class FailingLegacyProvider:
        def capabilities(self) -> ProviderCapability:
            raise AssertionError

        def generate(self, request: object) -> object:
            from retarget_agent.providers.seedream import (
                SeedDreamErrorCode,
                SeedDreamProviderError,
            )

            raise SeedDreamProviderError(
                SeedDreamErrorCode.RATE_LIMITED,
                "provider rejected the generation request",
            )

    adapter = SeedDreamAIGCAdapter(
        AIGCProviderRuntime(output_root=tmp_path, cache_root=tmp_path / "cache"),
        legacy_provider=FailingLegacyProvider(),  # type: ignore[arg-type]
    )

    try:
        adapter.generate(_request(tmp_path))
    except AIGCProviderError as error:
        assert error.code == "RATE_LIMITED"
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("expected normalized provider failure")
