from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from retarget_agent.agents import CandidateEvidence, deterministic_ranking
from retarget_agent.models import ProxyGrade
from retarget_agent.strategy import (
    ScoringPolicy,
    diff_strategy_bundles,
    load_strategy_bundle,
)

ROOT = Path(__file__).resolve().parents[1]
V1 = ROOT / "strategies" / "movie60" / "v1" / "bundle.yaml"
V2 = ROOT / "strategies" / "movie60" / "v2" / "bundle.yaml"


def test_historical_and_current_strategy_versions_load_and_diff() -> None:
    old = load_strategy_bundle(V1)
    current = load_strategy_bundle(V2)

    assert old.bundle.version == "1.0.0"
    assert current.bundle.parent_strategy == "movie60@1.0.0"
    assert old.scoring.proxy_a_threshold == 80.0
    assert current.scoring.proxy_a_threshold == 90.0
    assert old.source_sha256 != current.source_sha256
    changes = diff_strategy_bundles(old, current)
    assert changes["changes"]["scoring"]["proxy_a_threshold"] == {
        "from": 80.0,
        "to": 90.0,
    }
    assert changes["changes"]["agent_skill"]["version"] == {
        "from": "1.0.0",
        "to": "2.3.0",
    }


def test_registry_pins_immutable_strategy_hashes() -> None:
    registry = yaml.safe_load((ROOT / "strategies" / "registry.yaml").read_text(encoding="utf-8"))
    for row in registry["strategies"]:
        loaded = load_strategy_bundle(ROOT / "strategies" / row["bundle"])
        assert loaded.source_sha256 == row["sha256"], row["strategy"]


def test_strategy_snapshot_is_self_contained_and_refuses_overwrite(tmp_path: Path) -> None:
    loaded = load_strategy_bundle(V2)
    destination = tmp_path / "strategy"

    loaded.snapshot_to(destination)

    snapshot = json.loads((destination / "snapshot.json").read_text(encoding="utf-8"))
    assert snapshot["strategy_sha256"] == loaded.source_sha256
    assert set(snapshot["files"]) == {
        "bundle.yaml",
        "scoring.yaml",
        "selection.yaml",
        "override.yaml",
        "agent-skill.yaml",
    }
    with pytest.raises(FileExistsError):
        loaded.snapshot_to(destination)


def test_abcd_thresholds_are_configurable_and_ordered() -> None:
    current = load_strategy_bundle(V2).scoring
    relaxed = current.model_copy(
        update={
            "proxy_a_threshold": 80.0,
            "proxy_b_threshold": 70.0,
            "proxy_c_threshold": 60.0,
        }
    )
    assert ScoringPolicy.model_validate(relaxed.model_dump()).proxy_c_threshold == 60.0

    invalid = relaxed.model_dump(mode="json")
    invalid["proxy_b_threshold"] = 79.0
    invalid["proxy_c_threshold"] = 81.0
    with pytest.raises(ValueError, match="A >= B >= C"):
        ScoringPolicy.model_validate(invalid)


def test_selection_order_is_a_real_swappable_seam(tmp_path: Path) -> None:
    loaded = load_strategy_bundle(V2)
    high_unsafe = CandidateEvidence(
        candidate_id="task--warp--one",
        method_id="direct_warp",
        quality_score=95.0,
        proxy_grade=ProxyGrade.A,
        technical_valid=False,
        hard_failures=("stretch",),
    )
    lower_safe = CandidateEvidence(
        candidate_id="task--crop--one",
        method_id="crop",
        quality_score=80.0,
        proxy_grade=ProxyGrade.A,
        technical_valid=True,
    )
    assert deterministic_ranking((high_unsafe, lower_safe), loaded.selection)[0] == (
        lower_safe.candidate_id
    )

    version_dir = tmp_path / "v3"
    version_dir.mkdir()
    for source in loaded.source_files:
        (version_dir / source.name).write_bytes(source.read_bytes())
    selection_path = version_dir / "selection.yaml"
    selection = yaml.safe_load(selection_path.read_text(encoding="utf-8"))
    selection["ranking_order"] = ["quality_score_desc", "method_id_asc"]
    selection_path.write_text(
        yaml.safe_dump(selection, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    quality_first = load_strategy_bundle(version_dir / "bundle.yaml")
    assert deterministic_ranking((high_unsafe, lower_safe), quality_first.selection)[0] == (
        high_unsafe.candidate_id
    )


def test_strategy_snapshots_separate_agent_knowledge(tmp_path: Path) -> None:
    version_dir = tmp_path / "v-next"
    shutil.copytree(ROOT / "strategies/movie60/v3_3", version_dir)
    shutil.copy2(
        ROOT / "agent_skills/qwen4-selector/v8/skill.yaml",
        version_dir / "agent-skill.yaml",
    )
    shutil.copy2(
        ROOT / "agent_skills/qwen4-selector/v8/agent-knowledge.yaml",
        version_dir / "agent-knowledge.yaml",
    )

    loaded = load_strategy_bundle(version_dir / "bundle.yaml")
    assert len(loaded.agent_skill.case_knowledge) == 12
    assert "agent-knowledge.yaml" in loaded.file_hashes
    snapshot = loaded.snapshot_to(tmp_path / "snapshot")
    assert (snapshot / "agent-knowledge.yaml").is_file()
    reloaded = load_strategy_bundle(snapshot / "bundle.yaml")
    assert reloaded.agent_skill.render() == loaded.agent_skill.render()
    assert reloaded.agent_skill_sha256 == loaded.agent_skill_sha256


def test_strategy_references_cannot_escape_version_directory(tmp_path: Path) -> None:
    bundle = yaml.safe_load(V2.read_text(encoding="utf-8"))
    bundle["scoring_policy"] = "../secret.yaml"
    path = tmp_path / "bundle.yaml"
    path.write_text(yaml.safe_dump(bundle, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="inside the version directory"):
        load_strategy_bundle(path)
