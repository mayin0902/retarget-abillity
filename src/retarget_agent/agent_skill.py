"""Versioned, reviewable policy used by the visual ranking Agent."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

import yaml
from pydantic import Field, field_validator

from .hashing import sha256_file, sha256_json
from .models import FrozenModel, validate_id


class AgentCaseKnowledge(FrozenModel):
    """General visual precedent; sample IDs and per-image answers are forbidden by policy."""

    case_id: str
    situation: str = Field(min_length=1, max_length=240)
    expected_judgement: str = Field(min_length=1, max_length=240)
    rationale: str = Field(min_length=1, max_length=320)
    counterexample: str | None = Field(default=None, max_length=240)

    @field_validator("case_id")
    @classmethod
    def valid_case_id(cls, value: str) -> str:
        return validate_id(value)


class AgentSkill(FrozenModel):
    schema_version: str = "1.0"
    skill_id: str
    version: str
    purpose: str = Field(min_length=1, max_length=240)
    instructions: tuple[str, ...] = Field(min_length=1, max_length=20)
    ranking_priority: tuple[str, ...] = Field(min_length=1, max_length=12)
    aigc_gate: tuple[str, ...] = Field(min_length=1, max_length=12)
    allowed_reason_codes: tuple[str, ...] = Field(min_length=1, max_length=40)
    case_knowledge: tuple[AgentCaseKnowledge, ...] = Field(default=(), max_length=24)

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
        if self.case_knowledge:
            lines.append("General case knowledge:")
            for case in self.case_knowledge:
                text = (
                    f"- {case.case_id}: situation={case.situation}; "
                    f"judgement={case.expected_judgement}; rationale={case.rationale}"
                )
                if case.counterexample:
                    text += f"; counterexample={case.counterexample}"
                lines.append(text)
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
    knowledge_name = raw.pop("knowledge_file", None)
    source_sha256 = sha256_file(resolved)
    if knowledge_name is not None:
        knowledge_path = PurePosixPath(str(knowledge_name).replace("\\", "/"))
        if knowledge_path.is_absolute() or ".." in knowledge_path.parts:
            raise ValueError("knowledge_file must stay below the Skill directory")
        if raw.get("case_knowledge"):
            raise ValueError("use knowledge_file or inline case_knowledge, not both")
        knowledge_file = resolved.parent / knowledge_path.as_posix()
        knowledge_raw = yaml.safe_load(knowledge_file.read_text(encoding="utf-8"))
        if not isinstance(knowledge_raw, dict) or not isinstance(
            knowledge_raw.get("cases"), list
        ):
            raise ValueError("Agent knowledge must be a mapping containing a cases list")
        unknown = set(knowledge_raw) - {"schema_version", "knowledge_id", "version", "cases"}
        if unknown:
            raise ValueError(f"unknown Agent knowledge fields: {sorted(unknown)}")
        raw["case_knowledge"] = knowledge_raw["cases"]
        source_sha256 = sha256_json(
            {
                "skill_sha256": source_sha256,
                "knowledge_sha256": sha256_file(knowledge_file),
            }
        )
    return LoadedAgentSkill(
        skill=AgentSkill.model_validate(raw),
        source_sha256=source_sha256,
    )


__all__ = [
    "AgentCaseKnowledge",
    "AgentSkill",
    "LoadedAgentSkill",
    "load_agent_skill",
]
