from __future__ import annotations

import csv
import hashlib
from pathlib import Path
from typing import Any

import pytest
import requests

from scripts import materialize_analyzer_models as materializer


class FakeResponse:
    def __init__(
        self,
        payload: bytes = b"",
        *,
        url: str,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.payload = payload
        self.url = url
        self.status_code = status_code
        self.headers = headers or {}
        self.closed = False

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def close(self) -> None:
        self.closed = True

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def iter_content(self, _chunk_size: int):
        yield self.payload


def _manifest(tmp_path: Path, payload: bytes, **overrides: str) -> Path:
    row = {
        "asset_id": "pinned-model",
        "analyzer_id": "test",
        "source_url": "https://media.githubusercontent.com/model.bin",
        "official_source": "https://github.com/example/model",
        "license": "MIT",
        "license_url": "https://github.com/example/license",
        "access_date": "2026-08-21",
        "sha256": hashlib.sha256(payload).hexdigest(),
        "expected_bytes": str(len(payload)),
        "local_filename": "model.bin",
        "redistribution_status": "allowed",
        **overrides,
    }
    path = tmp_path / "manifest.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    return path


def test_normal_download_uses_tls_verification_and_content_pins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"audited-model"
    calls: list[dict[str, Any]] = []

    def fake_get(url: str, **kwargs: Any) -> FakeResponse:
        calls.append({"url": url, **kwargs})
        return FakeResponse(payload, url=url)

    monkeypatch.setattr(materializer.requests, "get", fake_get)
    output = tmp_path / "models"
    materializer.materialize(_manifest(tmp_path, payload), output)

    assert (output / "model.bin").read_bytes() == payload
    assert [call["verify"] for call in calls] == [True]
    assert calls[0]["allow_redirects"] is False


def test_valid_cached_asset_is_reused_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"audited-model"
    output = tmp_path / "models"
    output.mkdir()
    (output / "model.bin").write_bytes(payload)
    monkeypatch.setattr(
        materializer.requests,
        "get",
        lambda *_args, **_kwargs: pytest.fail("valid cache must not access network"),
    )

    materializer.materialize(_manifest(tmp_path, payload), output)

    assert (output / "model.bin").read_bytes() == payload


def test_ssl_error_alone_uses_warned_unverified_retry_with_mandatory_pins(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = b"audited-model"
    verify_values: list[bool] = []

    def fake_get(url: str, **kwargs: Any) -> FakeResponse:
        verify_values.append(kwargs["verify"])
        if kwargs["verify"]:
            raise requests.exceptions.SSLError("company interception certificate")
        return FakeResponse(payload, url=url)

    monkeypatch.setattr(materializer.requests, "get", fake_get)
    output = tmp_path / "models"
    materializer.materialize(_manifest(tmp_path, payload), output)

    assert verify_values == [True, False]
    assert (output / "model.bin").read_bytes() == payload
    warning = capsys.readouterr().out
    assert "TLS server identity verification disabled" in warning
    assert "SHA-256 and byte-size pins remain mandatory" in warning


def test_non_ssl_network_error_never_disables_tls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    def fake_get(_url: str, **_kwargs: Any) -> FakeResponse:
        nonlocal calls
        calls += 1
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(materializer.requests, "get", fake_get)
    output = tmp_path / "models"
    with pytest.raises(requests.ConnectionError):
        materializer.materialize(_manifest(tmp_path, b"model"), output)

    assert calls == 1
    assert not (output / "model.bin.part").exists()


def test_redirect_host_is_checked_before_following(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def fake_get(url: str, **_kwargs: Any) -> FakeResponse:
        calls.append(url)
        return FakeResponse(
            url=url,
            status_code=302,
            headers={"Location": "https://evil.example/model.bin"},
        )

    monkeypatch.setattr(materializer.requests, "get", fake_get)
    with pytest.raises(ValueError, match="unapproved model source"):
        materializer.materialize(_manifest(tmp_path, b"model"), tmp_path / "models")

    assert calls == ["https://media.githubusercontent.com/model.bin"]


def test_unpinned_manifest_is_rejected_before_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        materializer.requests,
        "get",
        lambda *_args, **_kwargs: pytest.fail("network must not be called"),
    )
    with pytest.raises(ValueError, match="no valid SHA-256 pin"):
        materializer.materialize(
            _manifest(tmp_path, b"model", sha256=""),
            tmp_path / "models",
        )


def test_normal_bootstrap_manifest_contains_only_current_pinned_yunet() -> None:
    path = (
        Path(__file__).parents[1]
        / "datasets"
        / "analyzer_models_company_cpu_v2"
        / "download_manifest.csv"
    )
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert [row["asset_id"] for row in rows] == ["yunet-face-2023mar"]
    assert len(rows[0]["sha256"]) == 64
    assert int(rows[0]["expected_bytes"]) > 0
