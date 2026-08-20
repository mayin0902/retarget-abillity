from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import replace
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
import requests
from PIL import Image

from retarget_agent.costing import BudgetLedger, ReservationState
from retarget_agent.providers.seedream import (
    SeedDreamErrorCode,
    SeedDreamGenerationRequest,
    SeedDreamProvider,
    SeedDreamProviderConfig,
    SeedDreamProviderError,
)


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        *,
        json_data: Any = None,
        content: bytes = b"",
        content_type: str = "application/json",
        content_length: str | None = None,
        iter_error: Exception | None = None,
    ) -> None:
        self.status_code = status_code
        self._json_data = json_data
        self._content = content
        self._iter_error = iter_error
        self.headers = {"Content-Type": content_type}
        if content_length is not None:
            self.headers["Content-Length"] = content_length

    def json(self) -> Any:
        if isinstance(self._json_data, Exception):
            raise self._json_data
        return self._json_data

    def iter_content(self, chunk_size: int) -> Any:
        if self._iter_error is not None:
            raise self._iter_error
        for offset in range(0, len(self._content), chunk_size):
            yield self._content[offset : offset + chunk_size]


class FakeHttpClient:
    def __init__(self, post: FakeResponse | Exception, get: FakeResponse | Exception) -> None:
        self.post_response = post
        self.get_response = get
        self.post_calls: list[tuple[str, dict[str, Any]]] = []
        self.get_calls: list[tuple[str, dict[str, Any]]] = []

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.post_calls.append((url, kwargs))
        if isinstance(self.post_response, Exception):
            raise self.post_response
        return self.post_response

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.get_calls.append((url, kwargs))
        if isinstance(self.get_response, Exception):
            raise self.get_response
        return self.get_response


def _image_bytes(size: tuple[int, int] = (32, 32), image_format: str = "PNG") -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, (32, 96, 180)).save(buffer, format=image_format)
    return buffer.getvalue()


def _request(**changes: Any) -> SeedDreamGenerationRequest:
    values: dict[str, Any] = {
        "task_id": "source-1__square",
        "run_id": "square-round0",
        "request_id": "request-1",
        "source_url": "https://images.example.com/source.png",
        "source_sha256": "a" * 64,
        "source_is_public": True,
        "allow_data_egress": True,
        "target_width": 1536,
        "target_height": 1536,
        "prompt": "Preserve the subject and all important visual information.",
        "prompt_version": "square-v1",
        "max_cost_cny": Decimal("0.60"),
    }
    values.update(changes)
    return SeedDreamGenerationRequest(**values)


def test_explicit_user_authorization_allows_nonpublic_local_research_source(
    tmp_path: Path,
) -> None:
    provider = _provider(tmp_path, client=_client())
    request = _request(
        source_is_public=False,
        allow_data_egress=True,
        egress_authorization_basis="user_explicit_2026-08-18_fixture",
    )

    result = provider.generate(request)

    assert result.width == result.height == 32


def _config(**changes: Any) -> SeedDreamProviderConfig:
    values = {
        "endpoint_url": "https://api.example.com/v3/images/generations",
        "api_key": "test-secret-that-must-not-persist",
        "model": "seedream-test-model",
    }
    values.update(changes)
    return SeedDreamProviderConfig(**values)


def _client(
    *,
    post: FakeResponse | Exception | None = None,
    get: FakeResponse | Exception | None = None,
) -> FakeHttpClient:
    return FakeHttpClient(
        post
        or FakeResponse(
            200,
            json_data={"data": [{"url": "https://cdn.example.com/output.png"}]},
        ),
        get or FakeResponse(200, content=_image_bytes(), content_type="image/png"),
    )


def _provider(
    tmp_path: Path,
    *,
    client: FakeHttpClient,
    budget: BudgetLedger | None = None,
    config: SeedDreamProviderConfig | None = None,
) -> SeedDreamProvider:
    return SeedDreamProvider(
        config or _config(),
        output_root=tmp_path / "outputs",
        cache_path=tmp_path / "cache" / "seedream.json",
        budget=budget or BudgetLedger("1.20"),
        http_client=client,
    )


def test_success_freezes_one_output_and_commits_unknown_actual(tmp_path: Path) -> None:
    client = _client()
    budget = BudgetLedger("1.20")
    provider = _provider(tmp_path, client=client, budget=budget)

    result = provider.generate(_request())

    assert result.output_path.is_file()
    assert result.width == result.height == 32
    assert result.media_type == "image/png"
    assert result.estimated_cost_min_cny == Decimal("0.30")
    assert result.estimated_cost_max_cny == Decimal("0.60")
    assert result.actual_cost_cny is None
    assert result.cost_entry.actual_amount is None
    assert len(client.post_calls) == len(client.get_calls) == 1
    _, post_options = client.post_calls[0]
    assert post_options["json"] == {
        "model": "seedream-test-model",
        "prompt": "Preserve the subject and all important visual information.",
        "image": "https://images.example.com/source.png",
        "response_format": "url",
        "size": "2K",
        "stream": False,
        "watermark": True,
    }
    assert post_options["allow_redirects"] is False
    assert client.get_calls[0][1]["allow_redirects"] is False
    reservation = budget.get(result.request_hash)
    assert reservation is not None
    assert reservation.state is ReservationState.COMMITTED
    assert reservation.actual_amount is None
    assert budget.snapshot().unknown_actual_count == 1


def test_base64_source_is_validated_sent_and_not_persisted(tmp_path: Path) -> None:
    source = _image_bytes((24, 48))
    data_uri = "data:image/png;base64," + base64.b64encode(source).decode("ascii")
    client = _client()
    provider = _provider(tmp_path, client=client)

    result = provider.generate(
        _request(
            source_url=None,
            source_data_uri=data_uri,
            source_sha256=hashlib.sha256(source).hexdigest(),
        )
    )

    assert result.output_path.is_file()
    assert client.post_calls[0][1]["json"]["image"] == data_uri
    cache_text = (tmp_path / "cache" / "seedream.json").read_text(encoding="utf-8")
    assert data_uri not in cache_text
    assert "base64_data_uri" in cache_text


def test_base64_source_hash_mismatch_is_rejected_before_http(tmp_path: Path) -> None:
    source = _image_bytes((24, 48))
    data_uri = "data:image/png;base64," + base64.b64encode(source).decode("ascii")
    client = _client()
    provider = _provider(tmp_path, client=client)

    with pytest.raises(SeedDreamProviderError) as caught:
        provider.generate(
            _request(
                source_url=None,
                source_data_uri=data_uri,
                source_sha256="b" * 64,
            )
        )

    assert caught.value.code is SeedDreamErrorCode.INVALID_REQUEST
    assert client.post_calls == client.get_calls == []


def test_persistent_cache_prevents_a_second_http_call_and_contains_no_secrets(
    tmp_path: Path,
) -> None:
    first_client = _client()
    first = _provider(tmp_path, client=first_client)
    request = _request()
    initial = first.generate(request)

    second_client = _client(
        post=AssertionError("POST must not be called"),
        get=AssertionError("GET must not be called"),
    )
    second = _provider(tmp_path, client=second_client, budget=BudgetLedger("0.60"))
    cached = second.generate(request)

    assert cached.cache_hit is True
    assert cached.output_sha256 == initial.output_sha256
    assert second_client.post_calls == second_client.get_calls == []
    cache_text = (tmp_path / "cache" / "seedream.json").read_text(encoding="utf-8")
    assert "test-secret-that-must-not-persist" not in cache_text
    assert "api.example.com" not in cache_text
    assert request.source_url not in cache_text
    assert request.prompt not in cache_text
    payload = json.loads(cache_text)
    record = next(iter(payload["records"].values()))
    assert record["material"]["source_sha256"] == "a" * 64
    assert record["material"]["target"] == "1536x1536:png"
    assert record["material"]["model"] == "seedream-test-model"
    assert record["material"]["prompt_sha256"] == request.prompt_sha256
    assert record["material"]["seed"] is None
    assert len(record["material"]["generation_config_sha256"]) == 64


def test_idempotency_key_changes_for_every_required_factor(tmp_path: Path) -> None:
    provider = _provider(tmp_path, client=_client())
    baseline = _request()
    keys = {
        provider.build_idempotency_key(baseline),
        provider.build_idempotency_key(_request(source_sha256="b" * 64)),
        provider.build_idempotency_key(_request(target_width=1024, target_height=1024)),
        provider.build_idempotency_key(_request(prompt="A different controlled prompt")),
        provider.build_idempotency_key(_request(seed=7)),
    }
    other_model = _provider(tmp_path / "model", client=_client(), config=_config(model="other"))
    keys.add(other_model.build_idempotency_key(baseline))
    other_config = _provider(tmp_path / "config", client=_client(), config=_config(watermark=False))
    keys.add(other_config.build_idempotency_key(baseline))
    assert len(keys) == 7


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"allow_data_egress": False}, SeedDreamErrorCode.DATA_EGRESS_DENIED),
        ({"source_is_public": False}, SeedDreamErrorCode.DATA_EGRESS_DENIED),
        (
            {"source_url": "http://images.example.com/source.png"},
            SeedDreamErrorCode.INVALID_REQUEST,
        ),
        ({"source_url": "https://127.0.0.1/source.png"}, SeedDreamErrorCode.INVALID_REQUEST),
        (
            {
                "source_url": None,
                "source_data_uri": "data:image/png;base64,not-base64",
            },
            SeedDreamErrorCode.INVALID_REQUEST,
        ),
        ({"seed": 9}, SeedDreamErrorCode.INVALID_REQUEST),
        ({"max_cost_cny": Decimal("0.59")}, SeedDreamErrorCode.COST_LIMIT_EXCEEDED),
    ],
)
def test_policy_gates_run_before_budget_or_http(
    tmp_path: Path, changes: dict[str, Any], expected: SeedDreamErrorCode
) -> None:
    client = _client()
    budget = BudgetLedger("1.20")
    provider = _provider(tmp_path, client=client, budget=budget)

    with pytest.raises(SeedDreamProviderError) as caught:
        provider.generate(_request(**changes))

    assert caught.value.code is expected
    assert client.post_calls == client.get_calls == []
    assert budget.snapshot().remaining_amount == Decimal("1.20")


def test_budget_is_reserved_before_http_and_exhaustion_blocks_call(tmp_path: Path) -> None:
    client = _client()
    provider = _provider(tmp_path, client=client, budget=BudgetLedger("0.59"))

    with pytest.raises(SeedDreamProviderError) as caught:
        provider.generate(_request())

    assert caught.value.code is SeedDreamErrorCode.COST_LIMIT_EXCEEDED
    assert client.post_calls == []


@pytest.mark.parametrize(
    ("get_response", "expected"),
    [
        (
            FakeResponse(200, content=b"not an image", content_type="image/png"),
            SeedDreamErrorCode.OUTPUT_INVALID,
        ),
        (
            FakeResponse(200, content=_image_bytes((40, 20)), content_type="image/png"),
            SeedDreamErrorCode.OUTPUT_INVALID,
        ),
        (
            FakeResponse(200, content=_image_bytes(), content_type="text/plain"),
            SeedDreamErrorCode.OUTPUT_INVALID,
        ),
        (
            FakeResponse(200, content=b"", content_type="image/png"),
            SeedDreamErrorCode.OUTPUT_MISSING,
        ),
    ],
)
def test_invalid_download_is_committed_and_cached_without_retry(
    tmp_path: Path, get_response: FakeResponse, expected: SeedDreamErrorCode
) -> None:
    client = _client(get=get_response)
    budget = BudgetLedger("1.20")
    provider = _provider(tmp_path, client=client, budget=budget)
    request = _request()

    with pytest.raises(SeedDreamProviderError) as caught:
        provider.generate(request)

    assert caught.value.code is expected
    key = provider.build_idempotency_key(request)
    assert budget.get(key).state is ReservationState.COMMITTED  # type: ignore[union-attr]
    assert len(client.post_calls) == len(client.get_calls) == 1
    with pytest.raises(SeedDreamProviderError) as cached:
        provider.generate(request)
    assert cached.value.code is SeedDreamErrorCode.CACHED_FAILURE
    assert len(client.post_calls) == len(client.get_calls) == 1


def test_download_limit_is_enforced_while_streaming(tmp_path: Path) -> None:
    client = _client(get=FakeResponse(200, content=_image_bytes(), content_type="image/png"))
    provider = _provider(
        tmp_path,
        client=client,
        config=replace(_config(), max_download_bytes=8),
    )

    with pytest.raises(SeedDreamProviderError) as caught:
        provider.generate(_request())

    assert caught.value.code is SeedDreamErrorCode.OUTPUT_INVALID


def test_private_provider_output_url_is_rejected_before_download(tmp_path: Path) -> None:
    client = _client(
        post=FakeResponse(
            200,
            json_data={"data": [{"url": "https://127.0.0.1/private.png"}]},
        )
    )
    provider = _provider(tmp_path, client=client)

    with pytest.raises(SeedDreamProviderError) as caught:
        provider.generate(_request())

    assert caught.value.code is SeedDreamErrorCode.OUTPUT_INVALID
    assert client.get_calls == []


def test_download_stream_timeout_is_normalized_without_retry(tmp_path: Path) -> None:
    response = FakeResponse(
        200,
        content_type="image/png",
        iter_error=requests.Timeout("do not expose transport details"),
    )
    client = _client(get=response)
    provider = _provider(tmp_path, client=client)

    with pytest.raises(SeedDreamProviderError) as caught:
        provider.generate(_request())

    assert caught.value.code is SeedDreamErrorCode.TIMEOUT
    assert "transport details" not in str(caught.value)
    assert len(client.post_calls) == len(client.get_calls) == 1


def test_submission_timeout_is_not_retried_and_blocks_duplicate_charge(tmp_path: Path) -> None:
    client = _client(post=requests.Timeout("private details must not escape"))
    budget = BudgetLedger("1.20")
    provider = _provider(tmp_path, client=client, budget=budget)
    request = _request()

    with pytest.raises(SeedDreamProviderError) as caught:
        provider.generate(request)
    assert caught.value.code is SeedDreamErrorCode.TIMEOUT
    assert caught.value.charge_may_have_occurred is True
    assert "private details" not in str(caught.value)
    assert len(client.post_calls) == 1
    assert budget.get(provider.build_idempotency_key(request)).state is ReservationState.COMMITTED  # type: ignore[union-attr]

    with pytest.raises(SeedDreamProviderError) as cached:
        provider.generate(request)
    assert cached.value.code is SeedDreamErrorCode.CACHED_FAILURE
    assert len(client.post_calls) == 1


def test_rate_limit_is_released_but_cached_and_never_automatically_retried(
    tmp_path: Path,
) -> None:
    client = _client(post=FakeResponse(429))
    budget = BudgetLedger("0.60")
    provider = _provider(tmp_path, client=client, budget=budget)
    request = _request()

    with pytest.raises(SeedDreamProviderError) as caught:
        provider.generate(request)
    assert caught.value.code is SeedDreamErrorCode.RATE_LIMITED
    assert budget.get(provider.build_idempotency_key(request)).state is ReservationState.RELEASED  # type: ignore[union-attr]
    assert len(client.post_calls) == 1

    with pytest.raises(SeedDreamProviderError) as cached:
        provider.generate(request)
    assert cached.value.code is SeedDreamErrorCode.CACHED_FAILURE
    assert len(client.post_calls) == 1


def test_server_error_is_conservatively_committed_as_unknown_cost(tmp_path: Path) -> None:
    client = _client(post=FakeResponse(503))
    budget = BudgetLedger("0.60")
    provider = _provider(tmp_path, client=client, budget=budget)
    request = _request()

    with pytest.raises(SeedDreamProviderError) as caught:
        provider.generate(request)

    assert caught.value.code is SeedDreamErrorCode.PROVIDER_UNAVAILABLE
    assert caught.value.charge_may_have_occurred is True
    reservation = budget.get(provider.build_idempotency_key(request))
    assert reservation is not None
    assert reservation.state is ReservationState.COMMITTED
    assert reservation.actual_amount is None


def test_environment_loading_masks_private_values_in_repr() -> None:
    config = SeedDreamProviderConfig.from_env(
        environ={
            "SEEDREAM_BASE_URL": "https://api.example.com/v3/images/generations",
            "SEEDREAM_API_KEY": "never-print-this",
            "SEEDREAM_MODEL": "runtime-model",
        }
    )

    assert "never-print-this" not in repr(config)
    assert "api.example.com" not in repr(config)
    assert "runtime-model" not in repr(config)
