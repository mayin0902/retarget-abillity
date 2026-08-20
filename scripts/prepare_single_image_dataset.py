from __future__ import annotations

import argparse
import csv
import shutil
from pathlib import Path
from typing import Any

import yaml
from PIL import Image, ImageOps, UnidentifiedImageError

from retarget_agent.hashing import sha256_file
from retarget_agent.models import validate_id

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


def _write_csv(path: Path, fieldnames: tuple[str, ...], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def materialize_single_image_dataset(
    input_path: Path,
    output_dir: Path,
    *,
    source_id: str,
    run_id: str,
    target_size: int = 1536,
    split: str = "calibration",
    scene_category: str = "movie_poster",
    output_root: str = "runs",
) -> dict[str, str]:
    """Freeze one local image into the engine's folder/CSV dataset contract.

    The source pixels are copied byte-for-byte. This helper deliberately refuses
    to merge into or overwrite an existing dataset directory.
    """

    validate_id(source_id)
    validate_id(run_id)
    if split not in {"calibration", "validation", "test", "smoke"}:
        raise ValueError("split must be calibration, validation, test, or smoke")
    if target_size <= 0:
        raise ValueError("target_size must be positive")
    if not scene_category.strip():
        raise ValueError("scene_category must not be empty")

    source = input_path.resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    suffix = source.suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png"}:
        raise ValueError("single-image helper accepts only JPEG or PNG")
    try:
        with Image.open(source) as opened:
            opened.verify()
        with Image.open(source) as opened:
            width, height = ImageOps.exif_transpose(opened).size
    except (OSError, UnidentifiedImageError) as error:
        raise ValueError(f"input is not a decodable image: {source}") from error

    root = output_dir.resolve()
    if root.exists():
        raise FileExistsError(f"output dataset already exists: {root}")
    images = root / "images"
    images.mkdir(parents=True)
    local_filename = f"{source_id}{suffix}"
    frozen_source = images / local_filename
    shutil.copy2(source, frozen_source)
    source_sha256 = sha256_file(frozen_source)
    target_id = f"square-{target_size}"
    task_id = f"{source_id}__{target_id}"
    dataset_id = f"single-{source_id}"

    descriptor = {
        "schema_version": "1.0",
        "dataset_id": dataset_id,
        "version": "1.0.0",
        "description": "One local image for a reproducible retarget-engine walkthrough.",
        "expected_source_count": 1,
        "expected_scene_counts": {scene_category: 1},
        "evaluation_canvas": f"{target_size}x{target_size}",
        "generation_originals_may_be_retained_at_2k": True,
        "silent_upsampling_forbidden": True,
    }
    (root / "dataset.yaml").write_text(
        yaml.safe_dump(descriptor, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    _write_csv(
        root / "sources.csv",
        SOURCE_FIELDS,
        [
            {
                "source_id": source_id,
                "image_path": f"images/{local_filename}",
                "width": width,
                "height": height,
                "sha256": source_sha256,
                "split": split,
                "scene_profile": "balanced",
                "enabled": "true",
                "source_kind": "user_authorized_local_real",
                "license_status": "local_research_not_publicly_redistributable",
                "scene_category": scene_category,
                "fixture_type": "",
                "test_purpose": "",
            }
        ],
    )
    _write_csv(
        root / "targets.csv",
        ("target_id", "width", "height", "format"),
        [
            {
                "target_id": target_id,
                "width": target_size,
                "height": target_size,
                "format": "png",
            }
        ],
    )
    _write_csv(
        root / "tasks.csv",
        ("task_id", "source_id", "target_id", "enabled"),
        [
            {
                "task_id": task_id,
                "source_id": source_id,
                "target_id": target_id,
                "enabled": "true",
            }
        ],
    )

    run_config = {
        "schema_version": "1.0",
        "dataset_root": root.as_posix(),
        "output_root": output_root,
        "run_id": run_id,
        "seed": 20260819,
        "device": "cpu",
        "method_profile": "cn_square_v2",
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
            "detector_mode": "required",
            "model_root": "models/analyzers",
            "face_confidence_threshold": 0.55,
            "object_confidence_threshold": 0.35,
            "object_nms_threshold": 0.50,
            "text_binary_threshold": 0.30,
            "text_polygon_threshold": 0.50,
            "text_max_candidates": 240,
            "logo_candidate_limit": 32,
        },
        "method_parameters": {
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
        },
        "selector": {"selector_id": "technical_risk_v1"},
    }
    config_path = root / "run.yaml"
    config_path.write_text(
        yaml.safe_dump(run_config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return {
        "dataset_root": str(root),
        "run_config": str(config_path),
        "source_id": source_id,
        "task_id": task_id,
        "sha256": source_sha256,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a non-overwriting one-image dataset and seven-method run config."
    )
    parser.add_argument("input_path", type=Path)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("local_data/datasets/single_image_demo"),
    )
    parser.add_argument("--source-id", default="demo_poster")
    parser.add_argument("--run-id", default="single-image-square-v1")
    parser.add_argument("--target-size", type=int, default=1536)
    parser.add_argument(
        "--split",
        choices=("calibration", "validation", "test", "smoke"),
        default="calibration",
    )
    parser.add_argument("--scene-category", default="movie_poster")
    parser.add_argument("--output-root", default="runs")
    args = parser.parse_args()
    result = materialize_single_image_dataset(
        args.input_path,
        args.output_dir,
        source_id=args.source_id,
        run_id=args.run_id,
        target_size=args.target_size,
        split=args.split,
        scene_category=args.scene_category,
        output_root=args.output_root,
    )
    print(yaml.safe_dump(result, allow_unicode=True, sort_keys=False), end="")


if __name__ == "__main__":
    main()
