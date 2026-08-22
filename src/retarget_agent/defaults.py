"""Single source of truth for current project defaults."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import METHOD_PROFILES
from .models import validate_id


class ReviewDefaults(BaseModel):
    """Local review defaults shared by every public CLI entry point."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    host: str
    port: int = Field(ge=1, le=65535)
    movie60_workspace: str
    runs_root: str


class PublicWorkflowDefaults(BaseModel):
    """Validated public workflow defaults loaded from ``configs/default.yaml``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str
    strategy: Literal["current"]
    method_profile: str
    analysis_profile: str
    default_target: str
    review: ReviewDefaults

    _analysis_profile = field_validator("analysis_profile")(validate_id)

    @field_validator("method_profile")
    @classmethod
    def known_method_profile(cls, value: str) -> str:
        if value not in METHOD_PROFILES:
            raise ValueError(f"unknown method profile: {value}")
        return value

    @field_validator("default_target")
    @classmethod
    def valid_default_target(cls, value: str) -> str:
        normalized = value.strip().lower()
        parts = normalized.split("x")
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            raise ValueError("default_target must use WIDTHxHEIGHT pixels")
        width, height = (int(part) for part in parts)
        if not (1 <= width <= 16384 and 1 <= height <= 16384):
            raise ValueError("default_target dimensions must be between 1 and 16384")
        return normalized


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


def load_public_defaults(start: Path | None = None) -> tuple[Path, PublicWorkflowDefaults]:
    """Load and validate the one public-workflow defaults document."""

    root, payload = load_default_config(start)
    return root, PublicWorkflowDefaults.model_validate(payload)


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
