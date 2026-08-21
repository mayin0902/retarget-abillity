"""Single source of truth for current project defaults."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def project_root(start: Path | None = None) -> Path:
    """Find the checkout root without requiring callers to know repository layout."""
    candidates = [Path(start or Path.cwd()).resolve(), Path(__file__).resolve().parents[2]]
    for candidate in candidates:
        for directory in (candidate, *candidate.parents):
            if (directory / "pyproject.toml").is_file() and (
                directory / "strategies" / "registry.yaml"
            ).is_file():
                return directory
    raise FileNotFoundError("cannot locate retarget-engine project root")


def load_default_config(start: Path | None = None) -> tuple[Path, dict[str, Any]]:
    root = project_root(start)
    path = root / "configs" / "default.yaml"
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("configs/default.yaml must be a mapping")
    return root, payload


def current_strategy_path(start: Path | None = None) -> Path:
    """Resolve exactly one active immutable StrategyBundle from the registry."""
    root = project_root(start)
    registry_path = root / "strategies" / "registry.yaml"
    payload = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    strategies = payload.get("strategies", []) if isinstance(payload, dict) else []
    active = [item for item in strategies if item.get("status") == "active"]
    if len(active) != 1:
        raise ValueError(
            "strategy registry must contain exactly one active entry, "
            f"got {len(active)}"
        )
    path = root / "strategies" / str(active[0]["bundle"])
    if not path.is_file():
        raise FileNotFoundError(f"active strategy bundle does not exist: {path}")
    return path
