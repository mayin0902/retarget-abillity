from __future__ import annotations

import pytest

from retarget_agent.registry import Registry


def test_registry_rejects_duplicate_ids() -> None:
    registry: Registry[object] = Registry("method")
    registry.register("crop", object())
    with pytest.raises(ValueError, match="duplicate"):
        registry.register("crop", object())


def test_registry_error_lists_available_plugins() -> None:
    registry: Registry[object] = Registry("method")
    registry.register("crop", object())
    with pytest.raises(KeyError, match="crop"):
        registry.get("mesh")
