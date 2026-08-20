"""Single-image scoring without requiring a Generation Run.

Two explicit modes are provided:

* reference: compare a retargeted candidate with its source;
* standalone: inspect one candidate without claiming content preservation.

Both produce machine-readable JSON, a short Markdown explanation, detector
overlays, and (when supplied) an immutable Strategy snapshot.
"""

from __future__ import annotations

import base64
import json
import math
import os
import shutil
import time
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import cv2
import numpy as np
import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps
from pydantic import Field

from .config import AnalysisConfig
from .evaluation import EvaluationConfig
from .hashing import sha256_file
from .models import FrozenModel, RegionRecord, SourceRecord, TargetSpec, TaskSpec
from .prompting import LoadedPromptTemplate
from .strategy import LoadedStrategyBundle

if TYPE_CHECKING:
    from .plugin_catalog import PluginCatalog


class ImageAgentReview(FrozenModel):
    grade: str
    directly_usable: bool
    content_preserved: bool | None
    visible_defects: tuple[str, ...] = Field(default=(), max_length=8)
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str = Field(min_length=1, max_length=200)


class OpenAICompatibleImageReviewBackend:
    """Optional visual pre-review for the standalone/reference scoring command."""

    backend_id = "openai_compatible_image_review_v1"
    backend_version = "1.0.0"

    def __init__(
        self,
        *,
        base_url: str,
        model_version: str,
        prompt_template: LoadedPromptTemplate,
        api_key_env: str | None = None,
        timeout_seconds: float = 120.0,
    ) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("base_url must be HTTP(S)")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.base_url = base_url.rstrip("/")
        self.model_version = model_version
        self.prompt_template = prompt_template
        self.api_key_env = api_key_env
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _data_url(path: Path) -> str:
        media = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        return f"data:{media};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"

    def review(
        self,
        *,
        source_path: Path | None,
        candidate_path: Path,
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        mode = "reference" if source_path is not None else "standalone"
        prompt = self.prompt_template.render(
            mode=mode,
            evidence_json=json.dumps(evidence, ensure_ascii=False),
        )
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        if source_path is not None:
            content.extend(
                [
                    {"type": "text", "text": "SOURCE"},
                    {"type": "image_url", "image_url": {"url": self._data_url(source_path)}},
                ]
            )
        content.extend(
            [
                {"type": "text", "text": "CANDIDATE"},
                {
                    "type": "image_url",
                    "image_url": {"url": self._data_url(candidate_path)},
                },
            ]
        )
        headers = {"Content-Type": "application/json"}
        if self.api_key_env:
            token = os.environ.get(self.api_key_env)
            if not token:
                raise ValueError(f"missing API key environment variable {self.api_key_env}")
            headers["Authorization"] = f"Bearer {token}"
        started = time.perf_counter()
        response = requests.post(
            f"{self.base_url}/chat/completions",
            headers=headers,
            json={
                "model": self.model_version,
                "temperature": 0.0,
                "max_tokens": 320,
                "structured_outputs": {"json": ImageAgentReview.model_json_schema()},
                "messages": [{"role": "user", "content": content}],
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        body = response.json()
        raw = body["choices"][0]["message"]["content"]
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end < start:
            raise ValueError("image review response contains no JSON")
        review = ImageAgentReview.model_validate_json(raw[start : end + 1])
        if mode == "standalone" and review.content_preserved is not None:
            raise ValueError("standalone Agent review must not claim content preservation")
        usage = body.get("usage") or {}
        return {
            "backend_id": self.backend_id,
            "backend_version": self.backend_version,
            "model_version": self.model_version,
            "prompt_template_id": self.prompt_template.spec.template_id,
            "prompt_template_sha256": self.prompt_template.source_sha256,
            "latency_seconds": time.perf_counter() - started,
            "input_tokens": int(usage.get("prompt_tokens") or 0),
            "output_tokens": int(usage.get("completion_tokens") or 0),
            "review": review.model_dump(mode="json"),
        }


def _read_rgb(path: Path) -> np.ndarray:
    with Image.open(path) as opened:
        return np.asarray(ImageOps.exif_transpose(opened).convert("RGB")).copy()


def _semantic_type(region: RegionRecord) -> str:
    value = region.attributes.get("semantic_type")
    return str(value) if value else str(region.label or "region")


def _counts(regions: tuple[RegionRecord, ...]) -> dict[str, int]:
    return dict(sorted(Counter(_semantic_type(region) for region in regions).items()))


def _region_records(regions: tuple[RegionRecord, ...]) -> list[dict[str, Any]]:
    """Preserve boxes, labels, confidence and OCR attributes for downstream review."""

    return [region.model_dump(mode="json") for region in regions]


def compute_no_reference_metrics(
    *, image: np.ndarray, regions: tuple[RegionRecord, ...]
) -> dict[str, Any]:
    """Technical candidate checks that deliberately omit preservation claims."""

    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    edge_density = float(np.mean(cv2.Canny(gray, 80, 180) > 0))
    channel_std = float(np.std(image))
    brightness = float(np.mean(gray) / 255.0)
    contrast = float(np.std(gray) / 127.5)
    blank = channel_std < 1.5
    technical_score = 100.0 * (
        0.30 * min(1.0, math.log1p(sharpness) / math.log(501.0))
        + 0.25 * min(1.0, edge_density / 0.12)
        + 0.25 * min(1.0, contrast / 0.55)
        + 0.20 * max(0.0, 1.0 - abs(brightness - 0.5) / 0.5)
    )
    if blank:
        technical_score = 0.0
    return {
        "width": int(image.shape[1]),
        "height": int(image.shape[0]),
        "aspect_ratio": float(image.shape[1] / image.shape[0]),
        "blank_or_near_blank": blank,
        "laplacian_variance": sharpness,
        "edge_density": edge_density,
        "brightness": brightness,
        "contrast": contrast,
        "technical_quality_score": float(max(0.0, min(100.0, technical_score))),
        "detections": _counts(regions),
        "content_preservation_score": None,
        "content_preservation_status": "not_available_without_source",
        "grade": None,
        "grade_status": "not_assigned_without_reference_or_human_review",
    }


def _draw_regions(image: np.ndarray, regions: tuple[RegionRecord, ...], label: str) -> Image.Image:
    canvas = Image.fromarray(image)
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()
    colors = {
        "text": (225, 0, 46),
        "face": (0, 102, 204),
        "person": (0, 150, 90),
        "product": (255, 128, 0),
        "logo_candidate": (150, 50, 190),
    }
    draw.rectangle((0, 0, canvas.width, 28), fill=(20, 20, 20))
    draw.text((9, 7), label, fill="white", font=font)
    for region in regions:
        semantic = _semantic_type(region)
        color = colors.get(semantic, (80, 80, 80))
        rect = region.rect
        draw.rectangle((rect.x1, rect.y1, rect.x2, rect.y2), outline=color, width=3)
        draw.text(
            (rect.x1 + 3, max(30, rect.y1 + 3)),
            f"{semantic} {region.confidence:.2f}",
            fill=color,
            font=font,
            stroke_width=1,
            stroke_fill="white",
        )
    return canvas


def _overlay(
    source: tuple[np.ndarray, tuple[RegionRecord, ...]] | None,
    candidate: tuple[np.ndarray, tuple[RegionRecord, ...]],
) -> Image.Image:
    panels = []
    if source is not None:
        panels.append(_draw_regions(source[0], source[1], "SOURCE detections"))
    panels.append(_draw_regions(candidate[0], candidate[1], "CANDIDATE detections"))
    target_height = min(1200, max(panel.height for panel in panels))
    resized = []
    for panel in panels:
        scale = target_height / panel.height
        resized.append(
            panel.resize(
                (round(panel.width * scale), target_height), Image.Resampling.LANCZOS
            )
        )
    canvas = Image.new("RGB", (sum(panel.width for panel in resized), target_height), "white")
    x = 0
    for panel in resized:
        canvas.paste(panel, (x, 0))
        x += panel.width
    return canvas


def _evaluation_config(strategy: LoadedStrategyBundle) -> EvaluationConfig:
    policy = strategy.scoring
    return EvaluationConfig(
        evaluator_id=policy.evaluator_id,
        evaluator_version=policy.evaluator_version,
        rerun_detectors=True,
        max_analysis_edge=policy.max_analysis_edge,
        proxy_a_threshold=policy.proxy_a_threshold,
        proxy_b_threshold=policy.proxy_b_threshold,
        proxy_c_threshold=policy.proxy_c_threshold,
        critical_text_recall=policy.critical_text_recall,
        blank_std_threshold=policy.blank_std_threshold,
        direct_warp_proxy_a_cap_d_stretch=policy.direct_warp_proxy_a_cap_d_stretch,
        direct_warp_proxy_c_cap_d_stretch=policy.direct_warp_proxy_c_cap_d_stretch,
    )


def _write_report(output_dir: Path, payload: dict[str, Any]) -> None:
    (output_dir / "report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    metrics = payload["metrics"]
    detector_lines = "\n".join(
        f"- {name}: {count}" for name, count in metrics.get("detections", {}).items()
    ) or "- 无检测结果"
    if payload["mode"] == "reference":
        summary = (
            f"- Quality: {metrics.get('quality_score')}\n"
            f"- Proxy grade: {metrics.get('proxy_grade')}\n"
            f"- OCR 字符召回: {metrics.get('ocr_character_recall')}\n"
            f"- 技术有效: {metrics.get('technical_valid')}"
        )
    else:
        summary = (
            f"- 仅技术质量分: {metrics.get('technical_quality_score')}\n"
            "- 内容保留与 A/B/C/D: 无原图，不计算"
        )
    markdown = f"""# 单图评分报告

- 模式：`{payload['mode']}`
- 检测器：`{payload['detector_suite_plugin']}`
- 评分器：`{payload['scorer_plugin']}`
- 策略：`{payload.get('strategy_id') or '未使用'}`

## 结论

{summary}

## 候选图检测

{detector_lines}

## 产物

- `report.json`：完整结构化输入、哈希、指标和耗时
- `overlay.png`：OCR/人脸/人物/商品/Logo候选区域
- `inputs/`：本次实际评分的输入副本
- `strategy/`：本次使用的不可变策略快照（如有）
"""
    (output_dir / "report.md").write_text(markdown, encoding="utf-8")


def score_image(
    *,
    candidate_path: Path,
    output_dir: Path,
    strategy: LoadedStrategyBundle,
    source_path: Path | None = None,
    analysis_config: AnalysisConfig | None = None,
    plugin_catalog: PluginCatalog | None = None,
    agent_backend: OpenAICompatibleImageReviewBackend | None = None,
) -> dict[str, Any]:
    from .plugin_catalog import built_in_plugin_catalog

    candidate_path = candidate_path.resolve()
    source_path = source_path.resolve() if source_path is not None else None
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise FileExistsError(output_dir)
    if not candidate_path.is_file() or (source_path is not None and not source_path.is_file()):
        raise FileNotFoundError(source_path if source_path is not None else candidate_path)
    output_dir.mkdir(parents=True)
    inputs_dir = output_dir / "inputs"
    inputs_dir.mkdir()
    candidate_copy = inputs_dir / f"candidate{candidate_path.suffix.lower()}"
    shutil.copy2(candidate_path, candidate_copy)
    if source_path is not None:
        shutil.copy2(source_path, inputs_dir / f"source{source_path.suffix.lower()}")
    strategy.snapshot_to(output_dir / "strategy")

    catalog = plugin_catalog or built_in_plugin_catalog()
    suite_id = strategy.bundle.detector_suite_plugin
    scorer_id = (
        strategy.bundle.reference_scorer_plugin
        if source_path is not None
        else strategy.bundle.standalone_scorer_plugin
    )
    config = analysis_config or AnalysisConfig(
        detector_mode="required",
        detector_suite_plugin=suite_id,
    )
    suite = catalog.detector_suites.get(suite_id)(config)
    candidate = _read_rgb(candidate_path)
    started = time.perf_counter()
    candidate_regions = suite.detect(candidate, 0.0)
    source: np.ndarray | None = None
    source_regions: tuple[RegionRecord, ...] | None = None
    if source_path is None:
        metrics = catalog.standalone_scorers.get(scorer_id)(
            image=candidate, regions=candidate_regions
        )
        mode = "standalone"
    else:
        source = _read_rgb(source_path)
        source_regions = suite.detect(source, 0.0)
        source_record = SourceRecord(
            source_id="single-source",
            image_path=source_path.name,
            width=source.shape[1],
            height=source.shape[0],
            sha256=sha256_file(source_path),
        )
        target = TargetSpec(
            target_id=f"target-{candidate.shape[1]}x{candidate.shape[0]}",
            width=candidate.shape[1],
            height=candidate.shape[0],
        )
        task = TaskSpec(
            dataset_id="single-image-score",
            task_id=f"{source_record.source_id}__{target.target_id}",
            source=source_record,
            target=target,
        )
        metrics = catalog.reference_scorers.get(scorer_id)(
            source=source,
            candidate=candidate,
            task=task,
            source_regions=source_regions,
            candidate_regions=candidate_regions,
            transform=None,
            config=_evaluation_config(strategy),
            scoring_policy=strategy.scoring,
        )
        metrics["detections"] = _counts(candidate_regions)
        metrics["source_detections"] = _counts(source_regions)
        mode = "reference"
    elapsed = time.perf_counter() - started
    _overlay(
        (source, source_regions) if source is not None and source_regions is not None else None,
        (candidate, candidate_regions),
    ).save(output_dir / "overlay.png", format="PNG", optimize=True)
    agent_review = None
    agent_review_status = "not_requested"
    if agent_backend is not None:
        try:
            agent_review = agent_backend.review(
                source_path=source_path,
                candidate_path=candidate_path,
                evidence=metrics,
            )
            agent_review_status = "success"
        except (OSError, ValueError, KeyError, TypeError, requests.RequestException) as error:
            agent_review_status = "failed"
            agent_review = {
                "error_type": type(error).__name__,
                "error_summary": str(error)[:300],
            }
    payload = {
        "schema_version": "1.0",
        "mode": mode,
        "source": (
            {"path": str(source_path), "sha256": sha256_file(source_path)}
            if source_path is not None
            else None
        ),
        "candidate": {"path": str(candidate_path), "sha256": sha256_file(candidate_path)},
        "strategy_id": strategy.bundle.strategy_id,
        "strategy_version": strategy.bundle.version,
        "strategy_sha256": strategy.source_sha256,
        "detector_suite_plugin": suite_id,
        "scorer_plugin": scorer_id,
        "analyzer_ids": list(suite.analyzer_ids),
        "elapsed_seconds": elapsed,
        "candidate_regions": _region_records(candidate_regions),
        "source_regions": (
            _region_records(source_regions) if source_regions is not None else None
        ),
        "metrics": metrics,
        "agent_review": agent_review,
        "agent_review_status": agent_review_status,
    }
    _write_report(output_dir, payload)
    return payload


__all__ = [
    "ImageAgentReview",
    "OpenAICompatibleImageReviewBackend",
    "compute_no_reference_metrics",
    "score_image",
]
