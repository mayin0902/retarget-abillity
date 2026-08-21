from __future__ import annotations

from pathlib import Path

import pytest

from experiments.movie60.scripts.run_seedream_from_reference_doc import _runtime_values


def test_runtime_values_extract_one_consistent_private_contract(tmp_path: Path) -> None:
    path = tmp_path / "provider.md"
    path.write_text(
        'curl -X POST https://api.example.test/v1/images \\\n'
        '-H "Authorization: Bearer private-fixture-token" \\\n'
        "-d '{\"model\": \"fixture-model\"}'\n",
        encoding="utf-8",
    )

    assert _runtime_values(path) == (
        "https://api.example.test/v1/images",
        "private-fixture-token",
        "fixture-model",
    )


def test_runtime_values_reject_conflicting_models(tmp_path: Path) -> None:
    path = tmp_path / "provider.md"
    path.write_text(
        'curl -X POST https://api.example.test/v1/images\n'
        '-H "Authorization: Bearer private-fixture-token"\n'
        '{"model":"first"}\n{"model":"second"}\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="conflicting"):
        _runtime_values(path)
