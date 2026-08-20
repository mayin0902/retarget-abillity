"""Automatic proxy evaluation over frozen candidates.

The evaluator deliberately emits ``ProxyGrade`` values.  They are useful for
ranking and routing experiments, but they are not calibrated human A/B/C
labels and must never be merged into ``ReviewEvent`` statistics.
"""

from __future__ import annotations

import math
import time
import unicodedata
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import psutil
import yaml
from PIL import Image, ImageOps
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .config import AnalysisConfig
from .hashing import sha256_json, short_hash
from .models import (
    AnalysisArtifact,
    CandidateRecord,
    EvaluationManifest,
    GenerationStatus,
    MetricBundle,
    ProxyGrade,
    RegionRecord,
    RunManifest,
    TaskSpec,
    TransformRecord,
    validate_id,
)
from .protection_detectors import ProtectionDetectorSuite
from .storage import LocalArtifactStore
from .strategy import LoadedStrategyBundle, ScoringPolicy


class EvaluationConfig(BaseModel):
    """Versioned thresholds for the uncalibrated automatic proxy evaluator."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evaluator_id: str = "auto-quality-proxy-v1"
    evaluator_version: str = "1.0.2"
    rerun_detectors: bool = True
    max_analysis_edge: int = Field(default=1024, ge=256, le=2048)
    proxy_a_threshold: float = Field(default=80.0, ge=0.0, le=100.0)
    proxy_b_threshold: float = Field(default=60.0, ge=0.0, le=100.0)
    proxy_c_threshold: float = Field(default=0.0, ge=0.0, le=100.0)
    critical_text_recall: float = Field(default=0.15, ge=0.0, le=1.0)
    blank_std_threshold: float = Field(default=1.5, ge=0.0)
    direct_warp_proxy_a_cap_d_stretch: float | None = Field(default=None, ge=0.0)
    direct_warp_proxy_c_cap_d_stretch: float | None = Field(default=None, ge=0.0)

    @model_validator(mode="after")
    def valid_grade_thresholds(self) -> EvaluationConfig:
        if not (self.proxy_a_threshold >= self.proxy_b_threshold >= self.proxy_c_threshold):
            raise ValueError("grade thresholds must satisfy A >= B >= C")
        return self

    @property
    def config_hash(self) -> str:
        return sha256_json(self.model_dump(mode="json"))


def normalize_ocr_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def character_recall(reference: str, candidate: str) -> float | None:
    reference = normalize_ocr_text(reference)
    candidate = normalize_ocr_text(candidate)
    if not reference:
        return None
    reference_counts = Counter(reference)
    candidate_counts = Counter(candidate)
    matched = sum(
        min(count, candidate_counts[character]) for character, count in reference_counts.items()
    )
    return matched / len(reference)


def text_similarity(reference: str, candidate: str) -> float | None:
    reference = normalize_ocr_text(reference)
    candidate = normalize_ocr_text(candidate)
    if not reference:
        return None
    return SequenceMatcher(None, reference, candidate, autojunk=False).ratio()


def _read_rgb(path: Path) -> np.ndarray:
    with Image.open(path) as opened:
        return np.asarray(ImageOps.exif_transpose(opened).convert("RGB")).copy()


def _scaled(image: np.ndarray, max_edge: int) -> np.ndarray:
    height, width = image.shape[:2]
    scale = min(1.0, max_edge / max(height, width))
    if scale == 1.0:
        return image
    return cv2.resize(
        image,
        (max(1, round(width * scale)), max(1, round(height * scale))),
        interpolation=cv2.INTER_AREA,
    )


def _ratio_score(value: float, reference: float) -> float | None:
    if value <= 1e-8 or reference <= 1e-8:
        return None
    return float(math.exp(-0.35 * abs(math.log(value / reference))))


def _sharpness_and_edge(
    source: np.ndarray, candidate: np.ndarray, max_edge: int
) -> dict[str, float | None]:
    source_gray = cv2.cvtColor(_scaled(source, max_edge), cv2.COLOR_RGB2GRAY)
    candidate_gray = cv2.cvtColor(_scaled(candidate, max_edge), cv2.COLOR_RGB2GRAY)
    source_lap = float(cv2.Laplacian(source_gray, cv2.CV_64F).var())
    candidate_lap = float(cv2.Laplacian(candidate_gray, cv2.CV_64F).var())
    source_edge = float(np.mean(cv2.Canny(source_gray, 80, 180) > 0))
    candidate_edge = float(np.mean(cv2.Canny(candidate_gray, 80, 180) > 0))
    return {
        "source_laplacian_variance": source_lap,
        "candidate_laplacian_variance": candidate_lap,
        "sharpness_preservation": _ratio_score(candidate_lap, source_lap),
        "source_edge_density": source_edge,
        "candidate_edge_density": candidate_edge,
        "edge_density_preservation": _ratio_score(candidate_edge, source_edge),
    }


def _color_similarity(source: np.ndarray, candidate: np.ndarray, max_edge: int) -> float:
    histograms: list[np.ndarray] = []
    for image in (source, candidate):
        hsv = cv2.cvtColor(_scaled(image, max_edge), cv2.COLOR_RGB2HSV)
        histogram = cv2.calcHist([hsv], [0, 1], None, [24, 16], [0, 180, 0, 256])
        histograms.append(cv2.normalize(histogram, None).flatten())
    distance = cv2.compareHist(histograms[0], histograms[1], cv2.HISTCMP_BHATTACHARYYA)
    return float(max(0.0, min(1.0, 1.0 - distance)))


def _orb_similarity(source: np.ndarray, candidate: np.ndarray, max_edge: int) -> float | None:
    detector = cv2.ORB_create(nfeatures=1800, fastThreshold=12)
    source_gray = cv2.cvtColor(_scaled(source, max_edge), cv2.COLOR_RGB2GRAY)
    candidate_gray = cv2.cvtColor(_scaled(candidate, max_edge), cv2.COLOR_RGB2GRAY)
    source_points, source_descriptors = detector.detectAndCompute(source_gray, None)
    candidate_points, candidate_descriptors = detector.detectAndCompute(candidate_gray, None)
    if source_descriptors is None or candidate_descriptors is None:
        return None
    matches = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(
        source_descriptors, candidate_descriptors, k=2
    )
    good = [first for first, second in matches if first.distance < 0.78 * second.distance]
    denominator = max(1, min(len(source_points), len(candidate_points)))
    match_ratio = len(good) / denominator
    inlier_ratio = 0.0
    if len(good) >= 4:
        source_xy = np.float32([source_points[item.queryIdx].pt for item in good])
        candidate_xy = np.float32([candidate_points[item.trainIdx].pt for item in good])
        _matrix, mask = cv2.findHomography(source_xy, candidate_xy, cv2.RANSAC, 5.0)
        if mask is not None:
            inlier_ratio = float(np.mean(mask.ravel() > 0))
    generous_match_score = min(1.0, math.sqrt(max(0.0, match_ratio) * 4.0))
    return float(0.65 * generous_match_score + 0.35 * inlier_ratio)


def _line_histogram(image: np.ndarray, max_edge: int) -> np.ndarray | None:
    gray = cv2.cvtColor(_scaled(image, max_edge), cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(gray, 70, 160)
    lines = cv2.HoughLinesP(
        edges,
        1,
        np.pi / 180,
        threshold=max(30, min(gray.shape) // 12),
        minLineLength=max(20, min(gray.shape) // 16),
        maxLineGap=8,
    )
    if lines is None:
        return None
    histogram = np.zeros(18, dtype=np.float64)
    for x1, y1, x2, y2 in lines[:, 0]:
        angle = math.atan2(float(y2 - y1), float(x2 - x1)) % math.pi
        length = math.hypot(float(x2 - x1), float(y2 - y1))
        histogram[min(17, int(angle / math.pi * 18))] += length
    norm = float(np.linalg.norm(histogram))
    return histogram / norm if norm > 0 else None


def _line_similarity(source: np.ndarray, candidate: np.ndarray, max_edge: int) -> float | None:
    left = _line_histogram(source, max_edge)
    right = _line_histogram(candidate, max_edge)
    if left is None or right is None:
        return None
    return float(max(0.0, min(1.0, np.dot(left, right))))


def _semantic_type(region: RegionRecord) -> str:
    semantic = region.attributes.get("semantic_type")
    if isinstance(semantic, str):
        return semantic
    if region.label == "face":
        return "face"
    if region.label == "text":
        return "text"
    return region.label or "unknown"


def _region_counts(regions: tuple[RegionRecord, ...]) -> Counter[str]:
    return Counter(_semantic_type(region) for region in regions)


def _count_preservation(source_count: int, candidate_count: int) -> float | None:
    if source_count <= 0:
        return None
    retained = min(1.0, candidate_count / source_count)
    additions = max(0, candidate_count - source_count) / source_count
    return float(retained * math.exp(-0.25 * additions))


def _object_label_score(
    source_regions: tuple[RegionRecord, ...], candidate_regions: tuple[RegionRecord, ...]
) -> float | None:
    def labels(regions: tuple[RegionRecord, ...]) -> Counter[str]:
        return Counter(
            region.label or "object"
            for region in regions
            if _semantic_type(region) in {"object", "product", "person"}
        )

    source_labels = labels(source_regions)
    if not source_labels:
        return None
    candidate_labels = labels(candidate_regions)
    intersection = sum(
        min(count, candidate_labels[label]) for label, count in source_labels.items()
    )
    precision = intersection / max(1, sum(candidate_labels.values()))
    recall = intersection / sum(source_labels.values())
    return 2.0 * precision * recall / (precision + recall) if precision + recall else 0.0


def _recognized_text(regions: tuple[RegionRecord, ...]) -> str:
    values: list[str] = []
    for region in regions:
        if _semantic_type(region) != "text":
            continue
        value = region.attributes.get("recognized_text")
        confidence = region.attributes.get("recognition_confidence", 0.0)
        if (
            isinstance(value, str)
            and value
            and isinstance(confidence, (float, int))
            and confidence >= 0.05
        ):
            values.append(value)
    return " ".join(values)


def _border_safety(regions: tuple[RegionRecord, ...], width: int, height: int) -> float | None:
    important = [
        region
        for region in regions
        if _semantic_type(region) in {"text", "face", "person", "product", "logo_candidate"}
    ]
    if not important:
        return None
    scores: list[float] = []
    for region in important:
        margin = min(
            region.rect.x1 / width,
            region.rect.y1 / height,
            (width - region.rect.x2) / width,
            (height - region.rect.y2) / height,
        )
        scores.append(max(0.0, min(1.0, margin / 0.025)))
    return float(np.mean(scores))


def _composition_center_score(image: np.ndarray, max_edge: int) -> float:
    gray = cv2.cvtColor(_scaled(image, max_edge), cv2.COLOR_RGB2GRAY).astype(np.float32)
    dx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    dy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    weight = cv2.magnitude(dx, dy) + 1e-6
    yy, xx = np.mgrid[: gray.shape[0], : gray.shape[1]]
    center_x = float(np.sum(xx * weight) / np.sum(weight)) / max(1, gray.shape[1] - 1)
    center_y = float(np.sum(yy * weight) / np.sum(weight)) / max(1, gray.shape[0] - 1)
    distance = math.hypot(center_x - 0.5, center_y - 0.5) / math.sqrt(0.5)
    return float(max(0.0, 1.0 - max(0.0, distance - 0.35) / 0.65))


def transform_safety_score(
    transform: TransformRecord | None,
    policy: ScoringPolicy | None = None,
) -> tuple[float | None, tuple[str, ...]]:
    if transform is None:
        return None, ()
    risk = transform.risk_features
    hard_failures: list[str] = []
    penalties = policy.transform_penalties if policy is not None else None
    if transform.method_id == "direct_warp":
        coefficient = penalties.direct_warp_stretch if penalties is not None else 0.65
        score = math.exp(-coefficient * float(risk.get("d_stretch", 0.0)))
    elif transform.method_id == "crop":
        cut_count = int(risk.get("cut_must_keep_count", 0))
        coverage = float(risk.get("importance_coverage", 1.0))
        coefficient = penalties.crop_cut_must_keep if penalties is not None else 2.0
        score = max(0.0, min(1.0, coverage)) * math.exp(-coefficient * cut_count)
    elif transform.method_id in {"seam", "seam_full", "seam_scale"}:
        importance = float(risk.get("mean_seam_importance", 0.0))
        anisotropy = max(
            1.0,
            float(
                risk.get(
                    "residual_alignment_anisotropy",
                    risk.get("final_alignment_anisotropy", 1.0),
                )
            ),
        )
        importance_coefficient = penalties.seam_importance if penalties is not None else 1.5
        anisotropy_coefficient = penalties.seam_anisotropy if penalties is not None else 0.6
        score = math.exp(
            -importance_coefficient * importance - anisotropy_coefficient * math.log(anisotropy)
        )
    elif transform.method_id in {"mesh", "mesh_full"}:
        foldovers = int(risk.get("foldover_count", 0))
        anisotropy = max(1.0, float(risk.get("max_axis_anisotropy", 1.0)))
        if foldovers:
            hard_failures.append("mesh_foldover")
        anisotropy_coefficient = penalties.mesh_anisotropy if penalties is not None else 0.55
        foldover_coefficient = penalties.mesh_foldover if penalties is not None else 8.0
        score = math.exp(
            -anisotropy_coefficient * math.log(anisotropy) - foldover_coefficient * foldovers
        )
    else:
        score = None
    return (float(max(0.0, min(1.0, score))) if score is not None else None, tuple(hard_failures))


def _weighted_score(values: list[tuple[float | None, float]]) -> float | None:
    present = [(value, weight) for value, weight in values if value is not None]
    if not present:
        return None
    return float(
        sum(value * weight for value, weight in present) / sum(weight for _, weight in present)
    )


def compute_proxy_metrics(
    *,
    source: np.ndarray,
    candidate: np.ndarray,
    task: TaskSpec,
    source_regions: tuple[RegionRecord, ...],
    candidate_regions: tuple[RegionRecord, ...] | None,
    transform: TransformRecord | None,
    config: EvaluationConfig,
    scoring_policy: ScoringPolicy | None = None,
) -> dict[str, float | int | bool | str | None]:
    height, width = candidate.shape[:2]
    hard_failures: list[str] = []
    critical_regressions: list[str] = []
    if (width, height) != (task.target.width, task.target.height):
        hard_failures.append("target_size_mismatch")
    candidate_std = float(np.std(candidate))
    if candidate_std < config.blank_std_threshold:
        hard_failures.append("blank_or_near_blank")

    sharpness = _sharpness_and_edge(source, candidate, config.max_analysis_edge)
    color_score = _color_similarity(source, candidate, config.max_analysis_edge)
    feature_score = _orb_similarity(source, candidate, config.max_analysis_edge)
    line_score = _line_similarity(source, candidate, config.max_analysis_edge)
    transform_score, transform_failures = transform_safety_score(transform, scoring_policy)
    hard_failures.extend(transform_failures)
    direct_warp_stretch = (
        float(transform.risk_features.get("d_stretch", 0.0))
        if transform is not None and transform.method_id == "direct_warp"
        else None
    )

    source_counts = _region_counts(source_regions)
    source_text = _recognized_text(source_regions)
    candidate_text = ""
    text_recall: float | None = None
    text_match: float | None = None
    face_score: float | None = None
    person_score: float | None = None
    product_score: float | None = None
    logo_score: float | None = None
    object_score: float | None = None
    border_score: float | None = None
    candidate_counts: Counter[str] = Counter()
    if candidate_regions is not None:
        candidate_counts = _region_counts(candidate_regions)
        candidate_text = _recognized_text(candidate_regions)
        text_recall = character_recall(source_text, candidate_text)
        text_match = text_similarity(source_text, candidate_text)
        face_score = _count_preservation(source_counts["face"], candidate_counts["face"])
        person_score = _count_preservation(source_counts["person"], candidate_counts["person"])
        product_score = _count_preservation(source_counts["product"], candidate_counts["product"])
        logo_score = _count_preservation(
            source_counts["logo_candidate"], candidate_counts["logo_candidate"]
        )
        object_score = _object_label_score(source_regions, candidate_regions)
        border_score = _border_safety(candidate_regions, width, height)

        normalized_source_text = normalize_ocr_text(source_text)
        if (
            len(normalized_source_text) >= 4
            and text_recall is not None
            and text_recall < config.critical_text_recall
        ):
            critical_regressions.append("critical_text_missing")
        prominent_face = any(
            _semantic_type(region) == "face"
            and region.confidence
            >= (scoring_policy.prominent_face_confidence if scoring_policy is not None else 0.75)
            and (region.rect.width * region.rect.height) / (task.source.width * task.source.height)
            >= (scoring_policy.prominent_face_area_ratio if scoring_policy is not None else 0.015)
            for region in source_regions
        )
        if prominent_face and candidate_counts["face"] == 0:
            critical_regressions.append("prominent_face_not_redetected")

    text_weights = scoring_policy.text_weights if scoring_policy is not None else None
    content_weights = scoring_policy.content_weights if scoring_policy is not None else None
    integrity_weights = scoring_policy.integrity_weights if scoring_policy is not None else None
    composition_weights = scoring_policy.composition_weights if scoring_policy is not None else None
    total_weights = scoring_policy.total_weights if scoring_policy is not None else None
    text_component = _weighted_score(
        [
            (text_recall, text_weights.character_recall if text_weights else 0.65),
            (text_match, text_weights.sequence_similarity if text_weights else 0.35),
        ]
    )
    content_score = _weighted_score(
        [
            (feature_score, content_weights.feature if content_weights else 0.25),
            (
                text_component,
                (content_weights.text if content_weights else 0.35) if source_text else 0.0,
            ),
            (
                face_score,
                (content_weights.face if content_weights else 0.20)
                if source_counts["face"]
                else 0.0,
            ),
            (
                person_score,
                (content_weights.person if content_weights else 0.20)
                if source_counts["person"]
                else 0.0,
            ),
            (
                product_score,
                (content_weights.product if content_weights else 0.20)
                if source_counts["product"]
                else 0.0,
            ),
            (
                logo_score,
                (content_weights.logo if content_weights else 0.10)
                if source_counts["logo_candidate"]
                else 0.0,
            ),
            (
                object_score,
                (content_weights.object if content_weights else 0.20)
                if object_score is not None
                else 0.0,
            ),
        ]
    )
    integrity_score = _weighted_score(
        [
            (
                sharpness["sharpness_preservation"],
                integrity_weights.sharpness if integrity_weights else 0.25,
            ),
            (
                sharpness["edge_density_preservation"],
                integrity_weights.edge_density if integrity_weights else 0.20,
            ),
            (color_score, integrity_weights.color if integrity_weights else 0.15),
            (
                line_score,
                integrity_weights.structure_lines if integrity_weights else 0.20,
            ),
            (
                transform_score,
                integrity_weights.transform_safety if integrity_weights else 0.20,
            ),
        ]
    )
    center_score = _composition_center_score(candidate, config.max_analysis_edge)
    composition_score = _weighted_score(
        [
            (
                border_score,
                composition_weights.protected_border if composition_weights else 0.60,
            ),
            (
                center_score,
                composition_weights.visual_center if composition_weights else 0.40,
            ),
        ]
    )
    total = _weighted_score(
        [
            (content_score, total_weights.content if total_weights else 0.50),
            (integrity_score, total_weights.integrity if total_weights else 0.30),
            (composition_score, total_weights.composition if total_weights else 0.20),
        ]
    )
    quality_score = 100.0 * total if total is not None else None
    if quality_score is not None and direct_warp_stretch is not None:
        c_cap = config.direct_warp_proxy_c_cap_d_stretch
        a_cap = config.direct_warp_proxy_a_cap_d_stretch
        if c_cap is not None and direct_warp_stretch >= c_cap:
            quality_score = min(quality_score, config.proxy_b_threshold - 1e-6)
            critical_regressions.append("severe_global_stretch")
        elif a_cap is not None and direct_warp_stretch >= a_cap:
            quality_score = min(quality_score, config.proxy_a_threshold - 1e-6)

    if hard_failures or critical_regressions or quality_score is None:
        grade = ProxyGrade.C if quality_score is not None else ProxyGrade.UNKNOWN
    elif quality_score >= config.proxy_a_threshold:
        grade = ProxyGrade.A
    elif quality_score >= config.proxy_b_threshold:
        grade = ProxyGrade.B
    elif quality_score >= config.proxy_c_threshold:
        grade = ProxyGrade.C
    else:
        grade = ProxyGrade.D

    return {
        "technical_valid": not hard_failures,
        "hard_failures": "|".join(sorted(set(hard_failures))),
        "critical_regressions": "|".join(sorted(set(critical_regressions))),
        "candidate_std": candidate_std,
        **sharpness,
        "color_histogram_similarity": color_score,
        "orb_content_similarity": feature_score,
        "structure_line_similarity": line_score,
        "transform_safety_score": transform_score,
        "direct_warp_d_stretch": direct_warp_stretch,
        "source_text": normalize_ocr_text(source_text)[:512],
        "candidate_text": normalize_ocr_text(candidate_text)[:512],
        "ocr_character_recall": text_recall,
        "ocr_sequence_similarity": text_match,
        "face_count_source": source_counts["face"],
        "face_count_candidate": candidate_counts["face"] if candidate_regions is not None else None,
        "face_count_preservation": face_score,
        "person_count_source": source_counts["person"],
        "person_count_candidate": candidate_counts["person"]
        if candidate_regions is not None
        else None,
        "person_count_preservation": person_score,
        "product_count_source": source_counts["product"],
        "product_count_candidate": candidate_counts["product"]
        if candidate_regions is not None
        else None,
        "product_count_preservation": product_score,
        "logo_count_source": source_counts["logo_candidate"],
        "logo_count_candidate": candidate_counts["logo_candidate"]
        if candidate_regions is not None
        else None,
        "logo_count_preservation": logo_score,
        "object_label_f1": object_score,
        "protected_border_safety": border_score,
        "composition_center_score": center_score,
        "content_fidelity_score": content_score,
        "visual_integrity_score": integrity_score,
        "composition_score": composition_score,
        "quality_score": quality_score,
        "proxy_grade": grade.value,
        "proxy_business_success": grade in {ProxyGrade.A, ProxyGrade.B},
        "proxy_is_a": grade == ProxyGrade.A,
        "calibration_status": "uncalibrated_no_human_ground_truth",
    }


def _load_analysis_config(run_dir: Path) -> AnalysisConfig:
    with (run_dir / "config" / "run.yaml").open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    return AnalysisConfig.model_validate((raw or {}).get("analysis", {}))


def _summary(records: list[tuple[CandidateRecord, MetricBundle]], elapsed: float) -> dict[str, Any]:
    by_method: dict[str, list[tuple[CandidateRecord, MetricBundle]]] = defaultdict(list)
    for candidate, metric in records:
        by_method[candidate.method_id].append((candidate, metric))
    methods: dict[str, Any] = {}
    for method_id, values in sorted(by_method.items()):
        scores = [
            float(metric.metrics["quality_score"])
            for _, metric in values
            if metric.metrics.get("quality_score") is not None
        ]
        grades = Counter(str(metric.metrics["proxy_grade"]) for _, metric in values)
        generation_wall = [
            candidate.performance.wall_seconds
            for candidate, _ in values
            if candidate.performance and candidate.performance.wall_seconds is not None
        ]
        methods[method_id] = {
            "candidate_count": len(values),
            "proxy_grade_counts": dict(grades),
            "proxy_a_rate": grades[ProxyGrade.A.value] / len(values) if values else None,
            "proxy_success_rate": (grades[ProxyGrade.A.value] + grades[ProxyGrade.B.value])
            / len(values)
            if values
            else None,
            "quality_score_mean": float(np.mean(scores)) if scores else None,
            "quality_score_p50": float(np.percentile(scores, 50)) if scores else None,
            "quality_score_p95": float(np.percentile(scores, 95)) if scores else None,
            "generation_wall_seconds_p50": float(np.percentile(generation_wall, 50))
            if generation_wall
            else None,
            "generation_wall_seconds_p95": float(np.percentile(generation_wall, 95))
            if generation_wall
            else None,
        }
    return {
        "schema_version": "1.0",
        "calibration_status": "uncalibrated_no_human_ground_truth",
        "candidate_count": len(records),
        "evaluation_wall_seconds": elapsed,
        "methods": methods,
        "notes": [
            "Proxy grades are automatic routing evidence, not human A/B/C labels.",
            "Method conclusions require held-out data and later human calibration.",
        ],
    }


def evaluate_run(
    run_dir: Path,
    evaluation_id: str,
    config: EvaluationConfig | None = None,
    strategy_bundle: LoadedStrategyBundle | None = None,
) -> EvaluationManifest:
    """Evaluate every frozen candidate without regenerating or changing it."""

    validate_id(evaluation_id)
    config = config or EvaluationConfig()
    if strategy_bundle is not None:
        scoring = strategy_bundle.scoring
        config = config.model_copy(
            update={
                "evaluator_id": scoring.evaluator_id,
                "evaluator_version": scoring.evaluator_version,
                "max_analysis_edge": scoring.max_analysis_edge,
                "proxy_a_threshold": scoring.proxy_a_threshold,
                "proxy_b_threshold": scoring.proxy_b_threshold,
                "proxy_c_threshold": scoring.proxy_c_threshold,
                "critical_text_recall": scoring.critical_text_recall,
                "blank_std_threshold": scoring.blank_std_threshold,
                "direct_warp_proxy_a_cap_d_stretch": (scoring.direct_warp_proxy_a_cap_d_stretch),
                "direct_warp_proxy_c_cap_d_stretch": (scoring.direct_warp_proxy_c_cap_d_stretch),
            }
        )
    run_dir = run_dir.resolve()
    store = LocalArtifactStore(run_dir)
    base = f"evaluations/{evaluation_id}"
    if store.path(f"{base}/evaluation.json").exists():
        raise FileExistsError(f"evaluation_id already exists: {evaluation_id}")
    strategy_snapshot = None
    if strategy_bundle is not None:
        strategy_snapshot = f"{base}/strategy"
        strategy_bundle.snapshot_to(store.path(strategy_snapshot))
    source_run = RunManifest.model_validate(store.read_json("run.json"))
    detector_suite: ProtectionDetectorSuite | None = None
    detector_error: str | None = None
    if config.rerun_detectors:
        try:
            detector_suite = ProtectionDetectorSuite(_load_analysis_config(run_dir))
        except (FileNotFoundError, OSError, ValueError, cv2.error) as error:
            detector_error = f"{type(error).__name__}: {error}"[:300]

    process = psutil.Process()
    started = time.perf_counter()
    all_records: list[tuple[CandidateRecord, MetricBundle]] = []
    metric_ids: list[str] = []
    evaluated_candidate_ids: list[str] = []
    for task_id in source_run.task_ids:
        task = TaskSpec.model_validate(store.read_json(f"tasks/{task_id}.json"))
        source_ref = store.read_json(f"sources/{task.source.source_id}.json")
        source = _read_rgb(store.path(source_ref["relative_path"]))
        analysis = AnalysisArtifact.model_validate(
            store.read_json(f"analysis/{task_id}/analysis.json")
        )
        for path in sorted((run_dir / "candidates" / task_id).glob("*/candidate.json")):
            candidate = CandidateRecord.model_validate_json(path.read_text(encoding="utf-8"))
            evaluated_candidate_ids.append(candidate.candidate_id)
            before_wall = time.perf_counter()
            before_cpu = time.process_time()
            before_rss = process.memory_info().rss
            if candidate.output is None or candidate.generation_status == GenerationStatus.FAILED:
                metrics: dict[str, float | int | bool | str | None] = {
                    "technical_valid": False,
                    "hard_failures": "candidate_output_missing",
                    "critical_regressions": "",
                    "quality_score": None,
                    "proxy_grade": ProxyGrade.C.value,
                    "proxy_business_success": False,
                    "proxy_is_a": False,
                    "calibration_status": "uncalibrated_no_human_ground_truth",
                }
            else:
                candidate_image = _read_rgb(store.path(candidate.output.relative_path))
                candidate_regions: tuple[RegionRecord, ...] | None = None
                if detector_suite is not None:
                    try:
                        candidate_regions = detector_suite.detect(candidate_image, 0.0)
                    except (OSError, ValueError, cv2.error) as error:
                        detector_error = f"{type(error).__name__}: {error}"[:300]
                transform = (
                    TransformRecord.model_validate(
                        store.read_json(candidate.transform.relative_path)
                    )
                    if candidate.transform
                    else None
                )
                metrics = compute_proxy_metrics(
                    source=source,
                    candidate=candidate_image,
                    task=task,
                    source_regions=analysis.regions,
                    candidate_regions=candidate_regions,
                    transform=transform,
                    config=config,
                    scoring_policy=(strategy_bundle.scoring if strategy_bundle else None),
                )
            metrics.update(
                {
                    "detector_rerun_requested": config.rerun_detectors,
                    "detector_rerun_available": detector_suite is not None,
                    "detector_error": detector_error,
                    "evaluation_wall_seconds": time.perf_counter() - before_wall,
                    "evaluation_cpu_seconds": time.process_time() - before_cpu,
                    "evaluation_rss_delta_bytes": process.memory_info().rss - before_rss,
                }
            )
            metric_id = f"metric-{short_hash(candidate.candidate_id + config.config_hash)}"
            metric = MetricBundle(
                metric_bundle_id=metric_id,
                candidate_id=candidate.candidate_id,
                evaluator_id=config.evaluator_id,
                evaluator_version=config.evaluator_version,
                metrics=metrics,
            )
            store.write_json(f"{base}/metrics/{candidate.candidate_id}.json", metric)
            metric_ids.append(metric_id)
            all_records.append((candidate, metric))

    elapsed = time.perf_counter() - started
    manifest = EvaluationManifest(
        evaluation_id=evaluation_id,
        source_run_id=source_run.run_id,
        evaluator_id=config.evaluator_id,
        evaluator_version=config.evaluator_version,
        config_hash=config.config_hash,
        strategy_id=(strategy_bundle.bundle.strategy_id if strategy_bundle else None),
        strategy_version=(strategy_bundle.bundle.version if strategy_bundle else None),
        strategy_sha256=(strategy_bundle.source_sha256 if strategy_bundle else None),
        strategy_snapshot=strategy_snapshot,
        task_ids=source_run.task_ids,
        candidate_ids=tuple(evaluated_candidate_ids),
        metric_bundle_ids=tuple(metric_ids),
    )
    store.write_json(f"{base}/summary.json", _summary(all_records, elapsed))
    store.write_json(f"{base}/evaluation.json", manifest)
    return manifest
