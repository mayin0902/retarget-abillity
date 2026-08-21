"""High-leverage single-image and batch workflows for ordinary developers."""

from __future__ import annotations

import csv
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from PIL import Image, ImageOps, UnidentifiedImageError

from .defaults import current_strategy_path, load_default_config
from .hashing import sha256_file
from .service import RetargetApplicationService

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
SOURCE_FIELDS = (
    "source_id",
    "image_path",
    "width",
    "height",
    "sha256",
    "split",
    "scene_profile",
    "enabled",
    "source_kind",
    "license_status",
    "scene_category",
    "fixture_type",
    "test_purpose",
)


def parse_target(value: str) -> tuple[int, int]:
    """Parse the only accepted target form: WIDTHxHEIGHT in actual pixels."""
    match = re.fullmatch(r"([1-9][0-9]{0,4})[xX]([1-9][0-9]{0,4})", value.strip())
    if not match:
        raise ValueError("target must use WIDTHxHEIGHT pixels, for example 1920x1080")
    width, height = (int(match.group(1)), int(match.group(2)))
    if width > 16384 or height > 16384:
        raise ValueError("target width and height must not exceed 16384 pixels")
    return width, height


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (slug or "image")[:48]


def _write_csv(path: Path, fields: tuple[str, ...], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _image_size(path: Path) -> tuple[int, int]:
    try:
        with Image.open(path) as opened:
            opened.verify()
        with Image.open(path) as opened:
            return ImageOps.exif_transpose(opened).size
    except (OSError, UnidentifiedImageError) as error:
        raise ValueError(f"input is not a decodable image: {path}") from error


def _method_parameters() -> dict[str, dict[str, Any]]:
    return {
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


def materialize_image_dataset(
    images: list[Path],
    dataset_root: Path,
    *,
    run_id: str,
    target: tuple[int, int],
    runs_root: Path,
    detector_mode: str = "required",
) -> Path:
    """Freeze local input images and write one standard Dataset+RunConfig."""
    root = dataset_root.resolve()
    if root.exists():
        raise FileExistsError(f"dataset already exists: {root}")
    root.mkdir(parents=True)
    frozen_dir = root / "images"
    frozen_dir.mkdir()
    width, height = target
    target_id = f"target-{width}x{height}"
    source_rows: list[dict[str, Any]] = []
    task_rows: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for index, original in enumerate(images, 1):
        source = original.resolve()
        if source.suffix.lower() not in IMAGE_SUFFIXES or not source.is_file():
            raise ValueError(f"only JPEG/PNG input images are supported: {source}")
        source_id = _slug(source.stem)
        if source_id in used_ids:
            source_id = f"{source_id}-{index}"
        used_ids.add(source_id)
        source_width, source_height = _image_size(source)
        frozen = frozen_dir / f"{source_id}{source.suffix.lower()}"
        shutil.copy2(source, frozen)
        source_rows.append(
            {
                "source_id": source_id,
                "image_path": frozen.relative_to(root).as_posix(),
                "width": source_width,
                "height": source_height,
                "sha256": sha256_file(frozen),
                "split": "validation",
                "scene_profile": "balanced",
                "enabled": "true",
                "source_kind": "user_authorized_local_real",
                "license_status": "local_research_not_publicly_redistributable",
                "scene_category": "unspecified",
                "fixture_type": "",
                "test_purpose": "",
            }
        )
        task_rows.append(
            {
                "task_id": f"{source_id}__{target_id}",
                "source_id": source_id,
                "target_id": target_id,
                "enabled": "true",
            }
        )
    descriptor = {
        "schema_version": "1.0",
        "dataset_id": f"local-{run_id}",
        "version": "1.0.0",
        "description": "Local developer workflow input; pixels are not redistributable by default.",
        "expected_source_count": len(source_rows),
        "expected_scene_counts": {"unspecified": len(source_rows)},
        "evaluation_canvas": f"{width}x{height}",
        "generation_originals_may_be_retained_at_2k": True,
        "silent_upsampling_forbidden": True,
    }
    (root / "dataset.yaml").write_text(
        yaml.safe_dump(descriptor, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    _write_csv(root / "sources.csv", SOURCE_FIELDS, source_rows)
    _write_csv(
        root / "targets.csv",
        ("target_id", "width", "height", "format"),
        [{"target_id": target_id, "width": width, "height": height, "format": "png"}],
    )
    _write_csv(root / "tasks.csv", ("task_id", "source_id", "target_id", "enabled"), task_rows)
    config = {
        "schema_version": "1.0",
        "dataset_root": root.as_posix(),
        "output_root": runs_root.resolve().as_posix(),
        "run_id": run_id,
        "seed": 20260821,
        "device": "cpu",
        "method_profile": "retarget_default_v1",
        "methods": [
            "direct_warp",
            "crop",
            "seam",
            "seam_full",
            "mesh",
            "mesh_full",
            "seam_scale",
        ],
        "analysis": {
            "gradient_weight": 0.40,
            "contrast_weight": 0.30,
            "center_weight": 0.30,
            "region_padding_ratio": 0.025,
            "detector_mode": detector_mode,
            "detector_suite_plugin": (
                "company_cpu_v2" if detector_mode != "disabled" else "legacy_opencv_v1"
            ),
            "model_root": "models/analyzers",
        },
        "method_parameters": _method_parameters(),
        "selector": {"selector_id": "technical_risk_v1"},
    }
    config_path = root / "run.yaml"
    config_path.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return config_path


def _run_id(stem: str) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    return f"{_slug(stem)}-{timestamp}"


def execute_images(
    images: list[Path],
    *,
    target_text: str,
    name: str,
    runs_root: Path | None = None,
    datasets_root: Path | None = None,
    detector_mode: str = "required",
    agent_profile: Path | None = None,
) -> dict[str, Any]:
    """Generate and Rule-score a Run; Agent executes only with an explicit profile."""
    root, defaults = load_default_config()
    target = parse_target(target_text)
    run_id = _run_id(name)
    run_parent = (runs_root or root / str(defaults["review"]["runs_root"])).resolve()
    dataset_parent = (datasets_root or root / "local_data" / "datasets").resolve()
    dataset = dataset_parent / run_id
    config_path = materialize_image_dataset(
        images,
        dataset,
        run_id=run_id,
        target=target,
        runs_root=run_parent,
        detector_mode=detector_mode,
    )
    service = RetargetApplicationService.default()
    generation = service.generate_from_config(config_path)
    run_dir = run_parent / run_id
    evaluation_id = f"{run_id}-rule-current"
    evaluation = service.evaluate(
        run_dir,
        evaluation_id,
        strategy_path=current_strategy_path(root),
    )
    result: dict[str, Any] = {
        "status": "completed",
        "run_id": run_id,
        "run_dir": str(run_dir),
        "dataset_dir": str(dataset),
        "target": {"width": target[0], "height": target[1]},
        "task_count": len(generation["task_ids"]),
        "candidate_count": len(generation["candidate_ids"]),
        "evaluation_id": evaluation["evaluation_id"],
        "review_command": f"retarget-engine review open \"{run_dir}\"",
        "agent": {"status": "not_requested"},
    }
    if agent_profile is not None:
        from .agent_profiles import load_agent_runtime_profile

        profile = load_agent_runtime_profile(agent_profile)
        agent_run_id = f"{run_id}-agent-{profile.profile_id}"
        agent = service.replay_agent(
            run_dir,
            evaluation_id,
            agent_run_id,
            mode=profile.mode,
            backend_url=profile.backend_url,
            model_version=profile.model_id,
            api_key_env=profile.api_key_env,
            allow_external_aigc=False,
            max_agent_calls=profile.maximum_calls,
            strategy_path=current_strategy_path(root),
        )
        result["agent"] = {
            "status": "completed",
            "agent_run_id": agent["agent_run_id"],
            "profile_id": profile.profile_id,
            "external_aigc_allowed": False,
        }
    return result


def run_image(
    input_path: Path,
    *,
    target: str = "1536x1536",
    agent_profile: Path | None = None,
) -> dict[str, Any]:
    return execute_images(
        [input_path],
        target_text=target,
        name=input_path.stem,
        agent_profile=agent_profile,
    )


def run_batch(
    input_dir: Path,
    *,
    target: str = "1536x1536",
    agent_profile: Path | None = None,
) -> dict[str, Any]:
    directory = input_dir.resolve()
    images = [
        path
        for path in sorted(directory.iterdir())
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    ]
    if not images:
        raise ValueError(f"batch directory contains no JPEG/PNG images: {directory}")
    return execute_images(
        images,
        target_text=target,
        name=directory.name,
        agent_profile=agent_profile,
    )
