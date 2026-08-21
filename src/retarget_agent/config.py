"""YAML run configuration with stable hashing."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .hashing import sha256_json
from .models import validate_id

CN_SQUARE_METHODS = (
    "direct_warp",
    "crop",
    "seam_full",
    "mesh_full",
    "seam_scale",
)
LEGACY_FOUR_METHODS = ("direct_warp", "crop", "seam", "mesh")
CN_SQUARE_SEVEN_METHODS = (
    "direct_warp",
    "crop",
    "seam",
    "seam_full",
    "mesh",
    "mesh_full",
    "seam_scale",
)
RETARGET_DEFAULT_METHODS = CN_SQUARE_SEVEN_METHODS
METHOD_PROFILES = {
    "cn_square_v1": CN_SQUARE_METHODS,
    "cn_square_v2": CN_SQUARE_SEVEN_METHODS,
    "legacy_four_v1": LEGACY_FOUR_METHODS,
    "retarget_default_v1": CN_SQUARE_SEVEN_METHODS,
}

RETARGET_DEFAULT_METHOD_PARAMETERS: dict[str, dict[str, Any]] = {
    "direct_warp": {"interpolation": "lanczos"},
    "crop": {"grid_step": 48, "scales": [1.0, 0.94, 0.88]},
    "seam": {
        "max_seams_per_axis": 24,
        "protection_weight": 18.0,
        "tolerance_weight": 3.0,
    },
    "seam_full": {
        "proxy_long_edge": 512,
        "protection_weight": 24.0,
        "tolerance_weight": 2.5,
        "unsafe_mean_importance": 0.45,
        "unsafe_peak_importance": 0.90,
    },
    "mesh": {
        "grid_columns": 12,
        "grid_rows": 12,
        "protection_gain": 1.8,
        "minimum_cell_fraction": 0.25,
    },
    "mesh_full": {
        "grid_columns": 12,
        "grid_rows": 12,
        "protection_gain": 5.0,
        "uniform_anchor_weight": 0.18,
        "smoothness_weight": 0.65,
        "unsafe_anisotropy": 4.5,
    },
    "seam_scale": {
        "proxy_long_edge": 512,
        "seam_fraction": 0.45,
        "protection_weight": 24.0,
        "tolerance_weight": 2.5,
        "unsafe_mean_importance": 0.45,
        "unsafe_peak_importance": 0.90,
    },
}


def method_parameters_for_profile(profile: str) -> dict[str, dict[str, Any]]:
    """Return an isolated parameter mapping for one frozen method profile."""
    methods = METHOD_PROFILES.get(profile)
    if methods is None:
        raise ValueError(f"unknown method profile: {profile}")
    if profile != "retarget_default_v1":
        return {}
    return deepcopy(
        {method: RETARGET_DEFAULT_METHOD_PARAMETERS[method] for method in methods}
    )


class AnalysisConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    gradient_weight: float = Field(default=0.45, ge=0.0)
    contrast_weight: float = Field(default=0.35, ge=0.0)
    center_weight: float = Field(default=0.20, ge=0.0)
    region_padding_ratio: float = Field(default=0.02, ge=0.0, le=0.25)
    detector_mode: Literal["disabled", "optional", "required"] = "disabled"
    detector_suite_plugin: str = "legacy_opencv_v1"
    model_root: str = "models/analyzers"
    face_confidence_threshold: float = Field(default=0.55, ge=0.0, le=1.0)
    object_confidence_threshold: float = Field(default=0.35, ge=0.0, le=1.0)
    object_nms_threshold: float = Field(default=0.50, ge=0.0, le=1.0)
    text_binary_threshold: float = Field(default=0.30, ge=0.0, le=1.0)
    text_polygon_threshold: float = Field(default=0.50, ge=0.0, le=1.0)
    text_max_candidates: int = Field(default=200, ge=1, le=1000)
    logo_candidate_limit: int = Field(default=24, ge=0, le=100)

    _detector_suite_plugin = field_validator("detector_suite_plugin")(validate_id)


class SelectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    selector_id: str = "technical_risk_v1"

    _selector_id = field_validator("selector_id")(validate_id)


class RunConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "1.0"
    dataset_root: str
    output_root: str = "runs"
    run_id: str
    seed: int = 20260810
    device: Literal["cpu"] = "cpu"
    method_profile: Literal[
        "cn_square_v1",
        "cn_square_v2",
        "legacy_four_v1",
        "retarget_default_v1",
        "custom",
    ] = "retarget_default_v1"
    methods: tuple[str, ...] = RETARGET_DEFAULT_METHODS
    analysis: AnalysisConfig = Field(default_factory=AnalysisConfig)
    method_parameters: dict[str, dict[str, Any]] = Field(default_factory=dict)
    selector: SelectorConfig = Field(default_factory=SelectorConfig)

    @model_validator(mode="after")
    def methods_match_frozen_profile(self) -> RunConfig:
        active_methods = self.methods
        if not active_methods or len(active_methods) != len(set(active_methods)):
            raise ValueError("methods must be a non-empty list of unique method IDs")
        active_profile = self.method_profile
        if "method_profile" not in self.model_fields_set:
            if "methods" in self.model_fields_set and active_methods == LEGACY_FOUR_METHODS:
                active_profile = "legacy_four_v1"
            elif "methods" in self.model_fields_set and active_methods == CN_SQUARE_METHODS:
                active_profile = "cn_square_v1"
            elif (
                "methods" not in self.model_fields_set
                and self.method_parameters
                and set(self.method_parameters).issubset(LEGACY_FOUR_METHODS)
            ):
                active_profile = "legacy_four_v1"
                active_methods = LEGACY_FOUR_METHODS
        if active_profile != "custom":
            expected = METHOD_PROFILES[active_profile]
            if active_methods != expected:
                raise ValueError(
                    f"method_profile {active_profile!r} requires methods in this order: {expected}"
                )
        unknown = set(self.method_parameters) - set(active_methods)
        if unknown:
            raise ValueError(
                f"method_parameters contains methods not enabled in this run: {sorted(unknown)}"
            )
        if active_profile != self.method_profile or active_methods != self.methods:
            return self.model_copy(
                update={"method_profile": active_profile, "methods": active_methods}
            )
        return self

    @property
    def config_hash(self) -> str:
        return sha256_json(self.model_dump(mode="json"))


def load_run_config(path: Path) -> RunConfig:
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    if not isinstance(raw, dict):
        raise ValueError("run config must be a YAML mapping")
    return RunConfig.model_validate(raw)
