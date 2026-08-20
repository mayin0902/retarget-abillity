from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from retarget_agent.agents import deterministic_ranking
from retarget_agent.evaluation import compute_proxy_metrics
from retarget_agent.image_scoring import (
    OpenAICompatibleImageReviewBackend,
    compute_no_reference_metrics,
    score_image,
)
from retarget_agent.models import Rect, RegionKind, RegionRecord
from retarget_agent.plugin_catalog import PluginCatalog, built_in_plugin_catalog
from retarget_agent.selector import select_by_technical_risk
from retarget_agent.strategy import load_strategy_bundle

ROOT = Path(__file__).resolve().parents[1]
V2 = ROOT / "strategies/movie60/v2/bundle.yaml"
V2_1 = ROOT / "strategies/movie60/v2_1/bundle.yaml"


class EmptyDetectorSuite:
    analyzer_ids = ("empty-detector-suite:test",)

    def __init__(self, _config: object) -> None:
        pass

    def detect(self, _image: np.ndarray, _padding: float):
        return (
            RegionRecord(
                region_id="text-000",
                kind=RegionKind.MUST_KEEP,
                rect=Rect(x1=4, y1=4, x2=24, y2=18),
                importance=1.0,
                tolerance=0.0,
                confidence=0.95,
                source="fixture",
                label="text",
                attributes={"semantic_type": "text", "recognized_text": "测试"},
            ),
        )


def _test_catalog() -> PluginCatalog:
    catalog = PluginCatalog.empty()
    catalog.detector_suites.register("legacy_opencv_v1", EmptyDetectorSuite)
    catalog.reference_scorers.register("auto_proxy_v1", compute_proxy_metrics)
    catalog.standalone_scorers.register(
        "technical_no_reference_v1", compute_no_reference_metrics
    )
    catalog.selectors.register("technical_risk_v1", select_by_technical_risk)
    catalog.selectors.register("deterministic_rule_ranking_v1", deterministic_ranking)
    catalog.agent_backends.register("openai_compatible_vision_v1", object)
    return catalog


def test_current_strategy_changes_architecture_not_scoring_values(tmp_path: Path) -> None:
    old = load_strategy_bundle(V2)
    current = load_strategy_bundle(V2_1)

    old_values = old.scoring.model_dump(exclude={"policy_id", "version"})
    current_values = current.scoring.model_dump(exclude={"policy_id", "version"})
    assert current_values == old_values
    assert current.prompts is not None
    assert current.prompts.aigc_generation is not None
    assert "square 1:1" in current.prompts.aigc_generation.render()
    rendered = current.prompts.overview.render(
        skill_instruction="skill",
        task_id="task",
        rule_top1_alias="C0",
        rule_ranking_json='["C0", "C1"]',
        candidate_payload_json="[]",
    )
    assert "Rule Top1: C0" in rendered

    destination = current.snapshot_to(tmp_path / "snapshot")
    assert (destination / "prompts/overview.txt").is_file()
    assert (destination / "prompts/seedream-generation.txt").is_file()
    assert (destination / "snapshot.json").is_file()


def test_builtin_catalog_exposes_only_allowlisted_ids() -> None:
    description = built_in_plugin_catalog().describe()
    assert description["detector_suites"] == ("company_cpu_v2", "legacy_opencv_v1")
    assert "auto_proxy_v1" in description["reference_scorers"]
    assert "technical_no_reference_v1" in description["standalone_scorers"]


def test_no_reference_metrics_do_not_claim_preservation_or_grade() -> None:
    image = np.full((64, 96, 3), 127, dtype=np.uint8)
    metrics = compute_no_reference_metrics(image=image, regions=())
    assert metrics["content_preservation_score"] is None
    assert metrics["grade"] is None
    assert metrics["content_preservation_status"] == "not_available_without_source"


def test_standalone_scoring_writes_report_overlay_inputs_and_strategy(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.png"
    values = np.zeros((80, 120, 3), dtype=np.uint8)
    values[:, :60] = (230, 50, 50)
    Image.fromarray(values).save(candidate)
    strategy = load_strategy_bundle(V2)
    output = tmp_path / "score"

    result = score_image(
        candidate_path=candidate,
        output_dir=output,
        strategy=strategy,
        plugin_catalog=_test_catalog(),
    )

    assert result["mode"] == "standalone"
    assert result["metrics"]["content_preservation_score"] is None
    assert result["candidate_regions"][0]["rect"] == {
        "x1": 4,
        "y1": 4,
        "x2": 24,
        "y2": 18,
    }
    assert result["source_regions"] is None
    for relative in (
        "report.json",
        "report.md",
        "overlay.png",
        "inputs/candidate.png",
        "strategy/snapshot.json",
    ):
        assert (output / relative).is_file()


def test_reference_scoring_compares_source_and_candidate(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    values = np.zeros((96, 128, 3), dtype=np.uint8)
    values[20:75, 30:105] = (240, 180, 40)
    Image.fromarray(values).save(source)
    output = tmp_path / "reference-score"

    result = score_image(
        source_path=source,
        candidate_path=source,
        output_dir=output,
        strategy=load_strategy_bundle(V2),
        plugin_catalog=_test_catalog(),
    )

    assert result["mode"] == "reference"
    assert result["metrics"]["quality_score"] > 90
    assert result["candidate_regions"][0]["attributes"]["recognized_text"] == "测试"
    assert result["source_regions"][0]["confidence"] == 0.95
    assert result["metrics"]["calibration_status"] == "uncalibrated_no_human_ground_truth"
    assert (output / "inputs/source.png").is_file()


def test_optional_standalone_agent_review_uses_external_prompt_and_no_preservation(
    tmp_path: Path, monkeypatch
) -> None:
    loaded = load_strategy_bundle(V2_1)
    assert loaded.prompts is not None and loaded.prompts.standalone_image is not None
    candidate = tmp_path / "candidate.jpg"
    Image.new("RGB", (32, 32), "white").save(candidate)
    captured = {}

    class Response:
        def raise_for_status(self) -> None:
            pass

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"grade":"A","directly_usable":true,'
                                '"content_preserved":null,"visible_defects":[],'
                                '"confidence":0.9,"summary":"画面自然清晰"}'
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["payload"] = kwargs["json"]
        return Response()

    monkeypatch.setattr("retarget_agent.image_scoring.requests.post", fake_post)
    backend = OpenAICompatibleImageReviewBackend(
        base_url="http://127.0.0.1:8000/v1",
        model_version="local-model",
        prompt_template=loaded.prompts.standalone_image,
    )

    result = backend.review(source_path=None, candidate_path=candidate, evidence={"x": 1})

    assert result["review"]["content_preserved"] is None
    assert captured["url"].endswith("/chat/completions")
    assert "模式为 standalone" in captured["payload"]["messages"][0]["content"][0]["text"]
