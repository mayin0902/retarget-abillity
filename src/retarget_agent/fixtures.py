"""Deterministic, non-sensitive images used to exercise the complete Smoke flow."""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path

import yaml
from PIL import Image, ImageDraw

from .hashing import sha256_file


@dataclass(frozen=True)
class FixtureSpec:
    source_id: str
    width: int
    height: int
    scene_profile: str
    style: str
    scene_category: str
    test_purpose: str


FIXTURES = (
    FixtureSpec(
        "poster-text-dense",
        240,
        320,
        "coverage",
        "poster",
        "synthetic_text_layout",
        "保护区域密集时的 Crop 可行性和文字框契约",
    ),
    FixtureSpec(
        "poster-price-logo",
        240,
        320,
        "coverage",
        "price",
        "synthetic_text_layout",
        "标题与价格 must_keep 冲突及 UNSAFE 状态",
    ),
    FixtureSpec(
        "product-single",
        320,
        240,
        "precision",
        "single_product",
        "synthetic_rigid_object",
        "单个刚性区域的局部形变风险",
    ),
    FixtureSpec(
        "product-multi",
        320,
        240,
        "coverage",
        "multi_product",
        "synthetic_rigid_object",
        "多个刚性区域的覆盖与失败隔离",
    ),
    FixtureSpec(
        "ecommerce-mix",
        320,
        240,
        "coverage",
        "ecommerce",
        "synthetic_mixed_layout",
        "商品、标题、价格混合的算法压力测试",
    ),
    FixtureSpec(
        "people-group",
        320,
        240,
        "coverage",
        "people",
        "synthetic_people_layout",
        "多 must_keep 人脸与人体区域契约",
    ),
    FixtureSpec(
        "portrait-person",
        240,
        320,
        "precision",
        "portrait",
        "synthetic_people_layout",
        "单人肖像极端目标比例压力测试",
    ),
    FixtureSpec(
        "portrait-pair",
        240,
        320,
        "coverage",
        "pair",
        "synthetic_people_layout",
        "双主体保护冲突测试",
    ),
    FixtureSpec(
        "landscape-mountain",
        320,
        200,
        "balanced",
        "landscape",
        "synthetic_background",
        "高容忍背景的 Seam 与 Mesh 路径测试",
    ),
    FixtureSpec(
        "architecture-lines",
        320,
        200,
        "precision",
        "architecture",
        "synthetic_structure_lines",
        "结构线和网格 Jacobian 压力测试",
    ),
    FixtureSpec(
        "abstract-center",
        256,
        256,
        "balanced",
        "abstract",
        "synthetic_geometry",
        "同心几何的非占位输出与形变回归",
    ),
    FixtureSpec(
        "difficult-mixed",
        256,
        256,
        "coverage",
        "mixed",
        "synthetic_mixed_layout",
        "复杂区域组合的契约与算法压力测试",
    ),
)


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _background(width: int, height: int, index: int) -> Image.Image:
    image = Image.new("RGB", (width, height))
    pixels = image.load()
    base = ((37 * index) % 120 + 70, (61 * index) % 100 + 80, (29 * index) % 90 + 90)
    for y in range(height):
        for x in range(width):
            wave = int(14 * math.sin((x + index * 7) / 31) + 10 * math.cos(y / 27))
            pixels[x, y] = tuple(max(0, min(255, channel + wave)) for channel in base)
    return image


def _add_region(
    regions: list[dict[str, object]],
    source_id: str,
    region_id: str,
    kind: str,
    box: tuple[int, int, int, int],
    importance: float,
    tolerance: float,
) -> None:
    x1, y1, x2, y2 = box
    regions.append(
        {
            "source_id": source_id,
            "region_id": region_id,
            "kind": kind,
            "x1": x1,
            "y1": y1,
            "x2": x2,
            "y2": y2,
            "importance": importance,
            "tolerance": tolerance,
            "confidence": 1.0,
            "source": "programmatic-fixture-v1",
        }
    )


def _draw_fixture(spec: FixtureSpec, index: int) -> tuple[Image.Image, list[dict[str, object]]]:
    image = _background(spec.width, spec.height, index)
    draw = ImageDraw.Draw(image)
    w, h = image.size
    regions: list[dict[str, object]] = []
    sid = spec.source_id

    if spec.style in {"poster", "price", "ecommerce", "mixed"}:
        title = (int(0.08 * w), int(0.07 * h), int(0.92 * w), int(0.25 * h))
        draw.rounded_rectangle(title, radius=7, fill=(245, 230, 72), outline=(35, 35, 35), width=3)
        draw.text((title[0] + 8, title[1] + 7), "SALE 2026", fill=(20, 20, 20))
        _add_region(regions, sid, "title", "must_keep", title, 1.0, 0.0)
        for line in range(3):
            y = int((0.30 + line * 0.10) * h)
            box = (int(0.10 * w), y, int(0.62 * w), y + max(10, h // 18))
            draw.rectangle(box, fill=(235, 235 - line * 25, 245 - line * 20))
            _add_region(regions, sid, f"copy-{line}", "prefer_keep", box, 0.65, 0.25)
        price = (int(0.60 * w), int(0.66 * h), int(0.93 * w), int(0.86 * h))
        draw.ellipse(price, fill=(226, 48, 62), outline=(255, 255, 255), width=3)
        draw.text((price[0] + 7, price[1] + 8), "$99", fill=(255, 255, 255))
        _add_region(regions, sid, "price", "must_keep", price, 1.0, 0.0)

    if spec.style in {"single_product", "multi_product", "ecommerce", "mixed"}:
        count = 1 if spec.style == "single_product" else 3
        for item in range(count):
            cx = int(w * (0.5 if count == 1 else 0.25 + item * 0.25))
            cy = int(h * (0.55 if spec.style != "mixed" else 0.62))
            radius = min(w, h) // (5 if count == 1 else 9)
            box = (cx - radius, cy - radius, cx + radius, cy + radius)
            draw.rounded_rectangle(
                box,
                radius=radius // 3,
                fill=(60 + item * 50, 190, 170 - item * 25),
                outline=(20, 40, 60),
                width=3,
            )
            _add_region(regions, sid, f"product-{item}", "rigid_region", box, 0.95, 0.05)

    if spec.style in {"people", "portrait", "pair"}:
        count = 1 if spec.style == "portrait" else 3 if spec.style == "people" else 2
        for person in range(count):
            cx = int(w * (0.5 if count == 1 else (person + 1) / (count + 1)))
            head_r = max(10, min(w, h) // 14)
            head_y = int(h * 0.30) + (person % 2) * 7
            head = (cx - head_r, head_y - head_r, cx + head_r, head_y + head_r)
            body = (cx - head_r * 2, head_y + head_r, cx + head_r * 2, int(h * 0.84))
            draw.ellipse(head, fill=(244, 194, 154), outline=(55, 35, 30), width=2)
            draw.rounded_rectangle(
                body, radius=head_r, fill=(50 + person * 55, 90, 205 - person * 40)
            )
            _add_region(regions, sid, f"face-{person}", "must_keep", head, 1.0, 0.0)
            _add_region(regions, sid, f"person-{person}", "rigid_region", body, 0.9, 0.05)

    if spec.style == "landscape":
        points = [
            (0, h),
            (0, int(h * 0.7)),
            (int(w * 0.28), int(h * 0.32)),
            (int(w * 0.5), int(h * 0.72)),
            (int(w * 0.72), int(h * 0.38)),
            (w, int(h * 0.68)),
            (w, h),
        ]
        draw.polygon(points, fill=(54, 103, 72))
        sun = (int(w * 0.72), int(h * 0.12), int(w * 0.84), int(h * 0.31))
        draw.ellipse(sun, fill=(248, 205, 76))
        _add_region(regions, sid, "mountain", "prefer_keep", (0, int(h * 0.28), w, h), 0.55, 0.45)
        _add_region(regions, sid, "sky", "removable", (0, 0, w, int(h * 0.27)), 0.1, 0.95)

    if spec.style == "architecture":
        for x in range(15, w, 35):
            draw.line((x, 8, x + 30, h - 8), fill=(235, 235, 225), width=4)
        for y in range(20, h, 32):
            draw.line((5, y, w - 5, y), fill=(32, 44, 58), width=3)
        _add_region(regions, sid, "structure", "rigid_region", (5, 8, w - 5, h - 8), 0.85, 0.1)

    if spec.style == "abstract":
        for radius, color in ((90, (245, 91, 74)), (62, (48, 190, 186)), (34, (250, 221, 92))):
            box = (w // 2 - radius, h // 2 - radius, w // 2 + radius, h // 2 + radius)
            draw.ellipse(box, outline=color, width=12)
        _add_region(regions, sid, "center", "prefer_keep", (54, 54, 202, 202), 0.8, 0.2)

    if not regions:
        center = (w // 4, h // 4, 3 * w // 4, 3 * h // 4)
        _add_region(regions, sid, "center", "prefer_keep", center, 0.7, 0.3)
    return image, regions


def materialize_fixture_dataset(
    root: Path,
    *,
    source_limit: int | None = None,
    dataset_id: str = "retarget-fixture-v1",
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    images_dir = root / "images"
    annotations_dir = root / "annotations"
    images_dir.mkdir(exist_ok=True)
    annotations_dir.mkdir(exist_ok=True)

    sources: list[dict[str, object]] = []
    regions: list[dict[str, object]] = []
    tasks: list[dict[str, object]] = []
    targets = [
        {"target_id": "square-192x192", "width": 192, "height": 192, "format": "png"},
        {"target_id": "wide-256x134", "width": 256, "height": 134, "format": "png"},
        {"target_id": "wide-256x144", "width": 256, "height": 144, "format": "png"},
        {"target_id": "portrait-144x256", "width": 144, "height": 256, "format": "png"},
    ]

    selected_fixtures = FIXTURES if source_limit is None else FIXTURES[:source_limit]
    for index, spec in enumerate(selected_fixtures, start=1):
        image, image_regions = _draw_fixture(spec, index)
        image_path = images_dir / f"{spec.source_id}.png"
        image.save(image_path, format="PNG", optimize=False)
        sources.append(
            {
                "source_id": spec.source_id,
                "image_path": f"images/{spec.source_id}.png",
                "width": spec.width,
                "height": spec.height,
                "sha256": sha256_file(image_path),
                "split": "smoke",
                "scene_profile": spec.scene_profile,
                "enabled": "true",
                "source_kind": "programmatic_fixture",
                "license_status": "generated_in_repo",
                "scene_category": spec.scene_category,
                "fixture_type": "programmatic_algorithm_fixture",
                "test_purpose": spec.test_purpose,
            }
        )
        regions.extend(image_regions)
        if spec.width < spec.height:
            target_ids = ("square-192x192", "wide-256x134")
        elif spec.width > spec.height:
            target_ids = ("square-192x192", "portrait-144x256")
        else:
            target_ids = ("wide-256x144", "portrait-144x256")
        for target_id in target_ids:
            tasks.append(
                {
                    "task_id": f"{spec.source_id}__{target_id}",
                    "source_id": spec.source_id,
                    "target_id": target_id,
                    "enabled": "true",
                }
            )

    descriptor = {
        "schema_version": "1.0",
        "dataset_id": dataset_id,
        "version": "1.0.0",
        "description": (
            "Deterministic programmatic fixtures for unit, contract and algorithm stress tests; "
            "never counted as real-scene Smoke."
        ),
        "sources_file": "sources.csv",
        "targets_file": "targets.csv",
        "tasks_file": "tasks.csv",
    }
    (root / "dataset.yaml").write_text(
        yaml.safe_dump(descriptor, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    _write_csv(root / "sources.csv", list(sources[0]), sources)
    _write_csv(root / "targets.csv", list(targets[0]), targets)
    _write_csv(root / "tasks.csv", list(tasks[0]), tasks)
    _write_csv(annotations_dir / "regions.csv", list(regions[0]), regions)
    return root
