from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from retarget_agent.config import (
    RETARGET_DEFAULT_METHODS,
    RunConfig,
    load_run_config,
)
from retarget_agent.defaults import current_strategy_path
from retarget_agent.rule_selection import materialize_selected_results
from retarget_agent.service import RetargetApplicationService
from retarget_agent.simple_workflow import materialize_image_dataset, parse_target

ASPECT_CASES = (
    ("1536x1536", (96, 96)),
    ("1920x1080", (160, 90)),
    ("1080x1920", (90, 160)),
    ("1200x900", (120, 90)),
    ("900x1200", (90, 120)),
)


def test_run_config_defaults_to_seven_method_profile() -> None:
    config = RunConfig(dataset_root="dataset", run_id="default-seven")
    assert config.method_profile == "retarget_default_v1"
    assert config.methods == RETARGET_DEFAULT_METHODS


@pytest.mark.parametrize(("target_text", "_smoke_size"), ASPECT_CASES)
def test_documented_targets_parse_exact_pixels(
    target_text: str, _smoke_size: tuple[int, int]
) -> None:
    width, height = (int(item) for item in target_text.split("x"))
    assert parse_target(target_text) == (width, height)


@pytest.mark.parametrize(("target_text", "smoke_size"), ASPECT_CASES)
def test_default_seven_methods_generate_for_every_supported_aspect(
    tmp_path: Path,
    target_text: str,
    smoke_size: tuple[int, int],
) -> None:
    source = tmp_path / f"source-{target_text}.png"
    image = Image.new("RGB", (192, 128), "#c7d9ef")
    draw = ImageDraw.Draw(image)
    draw.rectangle((24, 18, 168, 110), outline="#1a1a1a", width=4)
    draw.text((40, 50), "POSTER 2026", fill="#111111")
    image.save(source)
    run_id = f"aspect-{target_text.replace('x', '-')}"
    config_path = materialize_image_dataset(
        [source],
        tmp_path / f"dataset-{target_text}",
        run_id=run_id,
        target=smoke_size,
        runs_root=tmp_path / "runs",
        detector_mode="disabled",
    )
    config = load_run_config(config_path)
    assert config.methods == RETARGET_DEFAULT_METHODS
    assert set(config.method_parameters) == set(RETARGET_DEFAULT_METHODS)

    result = RetargetApplicationService.default().generate_from_config(config_path)
    assert len(result["candidate_ids"]) == len(RETARGET_DEFAULT_METHODS)
    run = tmp_path / "runs" / run_id
    records = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in run.glob("candidates/*/*/candidate.json")
    ]
    assert {record["method_id"] for record in records} == set(RETARGET_DEFAULT_METHODS)
    assert all(record["target_width"] == smoke_size[0] for record in records)
    assert all(record["target_height"] == smoke_size[1] for record in records)


def test_single_image_evaluation_emits_formal_result(tmp_path: Path) -> None:
    source = tmp_path / "single.png"
    Image.new("RGB", (160, 96), "#b7cde8").save(source)
    config_path = materialize_image_dataset(
        [source],
        tmp_path / "dataset",
        run_id="single-formal-result",
        target=(96, 96),
        runs_root=tmp_path / "runs",
        detector_mode="disabled",
    )
    service = RetargetApplicationService.default()
    service.generate_from_config(config_path)
    run = tmp_path / "runs" / "single-formal-result"
    evaluation = service.evaluate(
        run,
        "rule-current",
        rerun_detectors=False,
        strategy_path=current_strategy_path(),
    )
    result = materialize_selected_results(run, evaluation["evaluation_id"])

    assert (run / "result.png").is_file()
    assert (run / "result.json").is_file()
    assert len(result["results"]) == 1
    payload = json.loads((run / "result.json").read_text(encoding="utf-8"))
    assert payload["selected_candidate_id"] in payload["candidate_ranking"]
    assert (
        run
        / "evaluations"
        / "rule-current"
        / "rule-decisions"
        / f"{payload['task_id']}.json"
    ).is_file()
