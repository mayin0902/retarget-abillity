from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from retarget_agent.config import CN_SQUARE_SEVEN_METHODS, RunConfig, load_run_config


def test_cn_square_v2_freezes_all_seven_methods() -> None:
    config = RunConfig(
        dataset_root="local_data/datasets/fixture",
        run_id="fixture-seven",
        method_profile="cn_square_v2",
        methods=CN_SQUARE_SEVEN_METHODS,
    )

    assert config.methods == CN_SQUARE_SEVEN_METHODS


def test_cn_square_v2_rejects_missing_legacy_candidate() -> None:
    with pytest.raises(ValidationError, match="requires methods in this order"):
        RunConfig(
            dataset_root="local_data/datasets/fixture",
            run_id="fixture-seven",
            method_profile="cn_square_v2",
            methods=("direct_warp", "crop", "seam_full", "mesh_full", "seam_scale"),
        )


def test_movie60_config_loads_with_legacy_method_variants() -> None:
    path = Path(__file__).parents[1] / "configs" / "movie_visual60_square_v1.yaml"
    config = load_run_config(path)

    assert config.methods == CN_SQUARE_SEVEN_METHODS
    assert config.method_parameters["seam"]["max_seams_per_axis"] == 24
    assert config.method_parameters["mesh"]["protection_gain"] == 1.8
