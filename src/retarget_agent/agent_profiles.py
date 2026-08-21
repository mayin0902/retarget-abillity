"""Private runtime settings for explicitly enabled Agent replay."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


class AgentRuntimeProfile(BaseModel):
    """Endpoint settings; credentials are referenced by environment-variable name only."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    profile_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    backend_url: str
    model_id: str = Field(min_length=1, max_length=240)
    api_key_env: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{1,79}$")
    mode: Literal["conditional_agent", "always_on_agent"] = "conditional_agent"
    maximum_calls: int | None = Field(default=None, ge=0)

    @field_validator("backend_url")
    @classmethod
    def valid_backend_url(cls, value: str) -> str:
        normalized = value.rstrip("/")
        if not normalized.startswith(
            ("http://127.0.0.1:", "http://localhost:")
        ) and not normalized.startswith("https://"):
            raise ValueError("Agent backend must use HTTPS or a loopback HTTP address")
        return normalized


def load_agent_runtime_profile(path: Path) -> AgentRuntimeProfile:
    resolved = path.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    payload = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Agent runtime profile must be a YAML mapping")
    return AgentRuntimeProfile.model_validate(payload)


__all__ = ["AgentRuntimeProfile", "load_agent_runtime_profile"]
