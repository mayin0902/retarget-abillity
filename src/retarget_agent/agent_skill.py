"""Versioned, reviewable policy used by the visual ranking Agent."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import Field, field_validator

from .hashing import sha256_file
from .models import FrozenModel, validate_id


class AgentSkill(FrozenModel):
    schema_version: str = "1.0"
    skill_id: str
    version: str
    purpose: str = Field(min_length=1, max_length=240)
    instructions: tuple[str, ...] = Field(min_length=1, max_length=20)
    ranking_priority: tuple[str, ...] = Field(min_length=1, max_length=12)
    aigc_gate: tuple[str, ...] = Field(min_length=1, max_length=12)
    allowed_reason_codes: tuple[str, ...] = Field(min_length=1, max_length=40)

    @field_validator("skill_id")
    @classmethod
    def valid_skill_id(cls, value: str) -> str:
        return validate_id(value)

    @field_validator("version")
    @classmethod
    def bounded_version(cls, value: str) -> str:
        if not value or len(value) > 40:
            raise ValueError("skill version must be between 1 and 40 characters")
        return value

    def render(self) -> str:
        sections = (
            ("Policy", self.instructions),
            ("Ranking priority", self.ranking_priority),
            ("AIGC gate", self.aigc_gate),
            ("Allowed reason codes", self.allowed_reason_codes),
        )
        lines = [f"Selector skill {self.skill_id}@{self.version}: {self.purpose}"]
        for title, entries in sections:
            lines.append(f"{title}:")
            lines.extend(f"- {entry}" for entry in entries)
        return "\n".join(lines)


class LoadedAgentSkill(FrozenModel):
    skill: AgentSkill
    source_sha256: str


def load_agent_skill(path: Path) -> LoadedAgentSkill:
    resolved = path.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    raw = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Agent skill must be a YAML mapping")
    return LoadedAgentSkill(
        skill=AgentSkill.model_validate(raw),
        source_sha256=sha256_file(resolved),
    )


__all__ = ["AgentSkill", "LoadedAgentSkill", "load_agent_skill"]
