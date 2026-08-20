"""Versioned prompt templates loaded from an immutable strategy directory.

Prompt text is data, not Python control flow.  A strategy may therefore replace
an entire judging instruction while the runtime keeps a small, audited render
interface.  Templates use ``string.Template`` variables (``$task_id``), which
avoids executing expressions from YAML or prompt files.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from string import Template

import yaml
from pydantic import field_validator, model_validator

from .hashing import sha256_file, sha256_json
from .models import FrozenModel, validate_id


def _safe_reference(value: str) -> str:
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("prompt references must remain inside the strategy directory")
    return path.as_posix()


class PromptTemplateSpec(FrozenModel):
    template_id: str
    version: str
    body_file: str
    required_variables: tuple[str, ...] = ()

    _template_id = field_validator("template_id")(validate_id)
    _body_file = field_validator("body_file")(_safe_reference)

    @model_validator(mode="after")
    def unique_variables(self) -> PromptTemplateSpec:
        if len(self.required_variables) != len(set(self.required_variables)):
            raise ValueError("required_variables must be unique")
        if any(not item.isidentifier() for item in self.required_variables):
            raise ValueError("prompt variables must be valid identifiers")
        return self


class PromptBundle(FrozenModel):
    schema_version: str = "1.0"
    bundle_id: str
    version: str
    overview: PromptTemplateSpec
    strict_candidate: PromptTemplateSpec
    rule_agent_pair: PromptTemplateSpec
    standalone_image: PromptTemplateSpec | None = None
    aigc_generation: PromptTemplateSpec | None = None

    _bundle_id = field_validator("bundle_id")(validate_id)


@dataclass(frozen=True)
class LoadedPromptTemplate:
    spec: PromptTemplateSpec
    body: str
    source_path: Path
    source_sha256: str

    def render(self, **values: object) -> str:
        missing = sorted(set(self.spec.required_variables) - set(values))
        if missing:
            raise ValueError(
                f"prompt {self.spec.template_id!r} missing variables: {', '.join(missing)}"
            )
        unexpected = sorted(set(values) - set(self.spec.required_variables))
        if unexpected:
            raise ValueError(
                f"prompt {self.spec.template_id!r} got unexpected variables: "
                + ", ".join(unexpected)
            )
        return Template(self.body).substitute(
            {key: str(values[key]) for key in self.spec.required_variables}
        )


@dataclass(frozen=True)
class LoadedPromptBundle:
    bundle: PromptBundle
    overview: LoadedPromptTemplate
    strict_candidate: LoadedPromptTemplate
    rule_agent_pair: LoadedPromptTemplate
    standalone_image: LoadedPromptTemplate | None
    aigc_generation: LoadedPromptTemplate | None
    manifest_path: Path
    source_files: tuple[Path, ...]
    source_sha256: str


def _read_mapping(path: Path) -> dict[str, object]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"prompt bundle must be a YAML mapping: {path}")
    return raw


def load_prompt_bundle(path: Path, *, strategy_root: Path | None = None) -> LoadedPromptBundle:
    manifest_path = path.resolve()
    if not manifest_path.is_file():
        raise FileNotFoundError(manifest_path)
    root = (strategy_root or manifest_path.parent).resolve()
    try:
        manifest_path.relative_to(root)
    except ValueError as error:
        raise ValueError("prompt bundle escapes the strategy directory") from error
    bundle = PromptBundle.model_validate(_read_mapping(manifest_path))

    def load(spec: PromptTemplateSpec) -> LoadedPromptTemplate:
        source = (manifest_path.parent / Path(*PurePosixPath(spec.body_file).parts)).resolve()
        try:
            source.relative_to(root)
        except ValueError as error:
            raise ValueError("prompt body escapes the strategy directory") from error
        if not source.is_file():
            raise FileNotFoundError(source)
        body = source.read_text(encoding="utf-8").strip()
        if not body:
            raise ValueError(f"prompt body is empty: {source}")
        template = Template(body)
        discovered = {
            match.group("named") or match.group("braced")
            for match in template.pattern.finditer(body)
            if match.group("named") or match.group("braced")
        }
        if discovered != set(spec.required_variables):
            raise ValueError(
                f"prompt variable declaration mismatch for {spec.template_id}: "
                f"declared={sorted(spec.required_variables)}, body={sorted(discovered)}"
            )
        return LoadedPromptTemplate(
            spec=spec,
            body=body,
            source_path=source,
            source_sha256=sha256_file(source),
        )

    overview = load(bundle.overview)
    strict = load(bundle.strict_candidate)
    pair = load(bundle.rule_agent_pair)
    standalone = load(bundle.standalone_image) if bundle.standalone_image is not None else None
    aigc = load(bundle.aigc_generation) if bundle.aigc_generation is not None else None
    files = (
        manifest_path,
        overview.source_path,
        strict.source_path,
        pair.source_path,
        *((standalone.source_path,) if standalone is not None else ()),
        *((aigc.source_path,) if aigc is not None else ()),
    )
    hashes = {
        item.relative_to(root).as_posix(): sha256_file(item)
        for item in files
    }
    return LoadedPromptBundle(
        bundle=bundle,
        overview=overview,
        strict_candidate=strict,
        rule_agent_pair=pair,
        standalone_image=standalone,
        aigc_generation=aigc,
        manifest_path=manifest_path,
        source_files=files,
        source_sha256=sha256_json(hashes),
    )


__all__ = [
    "LoadedPromptBundle",
    "LoadedPromptTemplate",
    "PromptBundle",
    "PromptTemplateSpec",
    "load_prompt_bundle",
]
