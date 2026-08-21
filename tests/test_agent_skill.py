from __future__ import annotations

from pathlib import Path

import pytest

from retarget_agent.agent_skill import load_agent_skill

ROOT = Path(__file__).resolve().parents[1]


def test_versioned_qwen4_skill_loads_and_renders() -> None:
    loaded = load_agent_skill(ROOT / "agent_skills/qwen4-selector/v1/skill.yaml")
    assert loaded.skill.skill_id == "qwen4-selector"
    assert loaded.skill.version == "1.0.0"
    assert len(loaded.source_sha256) == 64
    rendered = loaded.skill.render()
    assert "Compare every candidate with the source" in rendered
    assert "all_traditional_unusable" in rendered


def test_v3_skill_supports_dynamic_candidates_and_advisory_unsafe() -> None:
    loaded = load_agent_skill(ROOT / "agent_skills/qwen4-selector/v3/skill.yaml")
    rendered = loaded.skill.render()

    assert loaded.skill.version == "1.2.0"
    assert "every C-number alias" in rendered
    assert "UNSAFE is advisory risk evidence" in rendered
    assert "local_text_distortion" in rendered


def test_skill_schema_rejects_unknown_fields(tmp_path: Path) -> None:
    path = tmp_path / "skill.yaml"
    path.write_text("skill_id: x\nversion: v1\nunknown: true\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_agent_skill(path)


def test_v8_skill_loads_separate_chinese_knowledge() -> None:
    loaded = load_agent_skill(ROOT / "agent_skills/qwen4-selector/v8/skill.yaml")
    rendered = loaded.skill.render()

    assert loaded.skill.version == "2.4.0"
    assert len(loaded.skill.case_knowledge) == 8
    assert "目标比例变化本身不是缺陷" in rendered
    assert len(loaded.source_sha256) == 64
