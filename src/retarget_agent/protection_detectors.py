"""Local CPU analyzers used by the shared protection pass.

The neural-network assets are intentionally external to Git. Their exact URLs,
licenses, byte sizes and SHA-256 values live in
``datasets/analyzer_models_v1/model_manifest.csv``.
"""

from __future__ import annotations

import ast
import importlib.metadata
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .config import AnalysisConfig
from .hashing import sha256_file, sha256_json
from .models import Rect, RegionKind, RegionRecord

COCO_CLASSES = (
    "person",
    "bicycle",
    "car",
    "motorcycle",
    "airplane",
    "bus",
    "train",
    "truck",
    "boat",
    "traffic light",
    "fire hydrant",
    "stop sign",
    "parking meter",
    "bench",
    "bird",
    "cat",
    "dog",
    "horse",
    "sheep",
    "cow",
    "elephant",
    "bear",
    "zebra",
    "giraffe",
    "backpack",
    "umbrella",
    "handbag",
    "tie",
    "suitcase",
    "frisbee",
    "skis",
    "snowboard",
    "sports ball",
    "kite",
    "baseball bat",
    "baseball glove",
    "skateboard",
    "surfboard",
    "tennis racket",
    "bottle",
    "wine glass",
    "cup",
    "fork",
    "knife",
    "spoon",
    "bowl",
    "banana",
    "apple",
    "sandwich",
    "orange",
    "broccoli",
    "carrot",
    "hot dog",
    "pizza",
    "donut",
    "cake",
    "chair",
    "couch",
    "potted plant",
    "bed",
    "dining table",
    "toilet",
    "tv",
    "laptop",
    "mouse",
    "remote",
    "keyboard",
    "cell phone",
    "microwave",
    "oven",
    "toaster",
    "sink",
    "refrigerator",
    "book",
    "clock",
    "vase",
    "scissors",
    "teddy bear",
    "hair drier",
    "toothbrush",
)

PRODUCT_CLASSES = frozenset(
    {
        "backpack",
        "umbrella",
        "handbag",
        "tie",
        "suitcase",
        "sports ball",
        "bottle",
        "wine glass",
        "cup",
        "fork",
        "knife",
        "spoon",
        "bowl",
        "banana",
        "apple",
        "sandwich",
        "orange",
        "broccoli",
        "carrot",
        "hot dog",
        "pizza",
        "donut",
        "cake",
        "chair",
        "couch",
        "potted plant",
        "bed",
        "dining table",
        "toilet",
        "tv",
        "laptop",
        "mouse",
        "remote",
        "keyboard",
        "cell phone",
        "microwave",
        "oven",
        "toaster",
        "sink",
        "refrigerator",
        "book",
        "clock",
        "vase",
        "scissors",
        "teddy bear",
        "hair drier",
        "toothbrush",
    }
)

MODEL_FILES = {
    "face_yunet": "face_detection_yunet_2023mar.onnx",
    "text_ppocrv3": "text_detection_cn_ppocrv3_2023may.onnx",
    "text_crnn_cn": "text_recognition_CRNN_CN_2021nov.onnx",
    "text_crnn_charset": "crnn.py",
    "object_yolox": "object_detection_yolox_2022nov.onnx",
}


def directory_audit(root: Path) -> dict[str, Any]:
    """Content audit for one local model directory without executing its files."""

    if not root.is_dir():
        raise FileNotFoundError(root)
    files = sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and not {".locks", ".cache", ".no_exist"}.intersection(path.parts)
        and path.name != "CACHEDIR.TAG"
    )
    hashes = {path.relative_to(root).as_posix(): sha256_file(path) for path in files}
    return {
        "path": str(root),
        "file_count": len(files),
        "total_bytes": sum(path.stat().st_size for path in files),
        "content_sha256": sha256_json(hashes),
        "files": hashes,
    }


@dataclass(frozen=True, slots=True)
class Detection:
    detector_id: str
    label: str
    rect: tuple[float, float, float, float]
    confidence: float
    kind: RegionKind
    importance: float
    tolerance: float
    attributes: dict[str, Any] = field(default_factory=dict)


def _scaled_image(image: np.ndarray, longest_edge: int) -> tuple[np.ndarray, float]:
    height, width = image.shape[:2]
    scale = min(1.0, longest_edge / max(height, width))
    if scale == 1.0:
        return image, 1.0
    resized = cv2.resize(
        image,
        (max(1, round(width * scale)), max(1, round(height * scale))),
        interpolation=cv2.INTER_AREA,
    )
    return resized, scale


def _rect(
    bounds: tuple[float, float, float, float],
    width: int,
    height: int,
    padding_ratio: float,
) -> Rect | None:
    x1, y1, x2, y2 = bounds
    padding = padding_ratio * max(x2 - x1, y2 - y1, 1.0)
    left = max(0, int(np.floor(x1 - padding)))
    top = max(0, int(np.floor(y1 - padding)))
    right = min(width, int(np.ceil(x2 + padding)))
    bottom = min(height, int(np.ceil(y2 + padding)))
    if right <= left or bottom <= top:
        return None
    return Rect(x1=left, y1=top, x2=right, y2=bottom)


def _nms_indices(
    boxes: list[list[float]], scores: list[float], score_threshold: float, nms_threshold: float
) -> list[int]:
    if not boxes:
        return []
    indices = cv2.dnn.NMSBoxes(boxes, scores, score_threshold, nms_threshold)
    return [int(value) for value in np.asarray(indices).reshape(-1)]


def _load_cn_charset(source_path: Path) -> str:
    """Extract the official CRNN charset as data without importing/executing helper code."""
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != "CRNN":
            continue
        for item in node.body:
            if not isinstance(item, ast.Assign):
                continue
            is_cn_charset = any(
                isinstance(target, ast.Name) and target.id == "CHARSET_CN_3944"
                for target in item.targets
            )
            if is_cn_charset:
                value = ast.literal_eval(item.value)
                if not isinstance(value, str):
                    break
                charset = "".join(value.splitlines())
                if len(charset) < 3000:
                    raise ValueError("official CRNN Chinese charset is unexpectedly short")
                return charset
    raise ValueError("CHARSET_CN_3944 not found in official CRNN helper")


class FaceDetector:
    detector_id = "face_yunet"

    def __init__(self, model_path: Path, threshold: float) -> None:
        self.model_path = model_path
        self.threshold = threshold
        self.model = cv2.FaceDetectorYN.create(
            str(model_path), "", (320, 320), threshold, 0.3, 5000
        )

    def detect(self, image_rgb: np.ndarray) -> list[Detection]:
        image, scale = _scaled_image(image_rgb, 1280)
        height, width = image.shape[:2]
        self.model.setInputSize((width, height))
        _retval, faces = self.model.detect(cv2.cvtColor(image, cv2.COLOR_RGB2BGR))
        if faces is None:
            return []
        detections: list[Detection] = []
        for face in faces:
            x, y, box_width, box_height = (float(value) / scale for value in face[:4])
            landmarks = [round(float(value) / scale, 2) for value in face[4:14]]
            detections.append(
                Detection(
                    detector_id=self.detector_id,
                    label="face",
                    rect=(x, y, x + box_width, y + box_height),
                    confidence=float(face[14]),
                    kind=RegionKind.MUST_KEEP,
                    importance=1.0,
                    tolerance=0.0,
                    attributes={"semantic_type": "face", "landmarks_xy": landmarks},
                )
            )
        return detections


class TextDetectorRecognizer:
    detector_id = "text_ppocrv3_crnn_cn"
    input_size = (736, 736)

    def __init__(
        self,
        detection_model: Path,
        recognition_model: Path,
        charset_source: Path,
        config: AnalysisConfig,
    ) -> None:
        self.detector = cv2.dnn_TextDetectionModel_DB(cv2.dnn.readNet(str(detection_model)))
        self.detector.setBinaryThreshold(config.text_binary_threshold)
        self.detector.setPolygonThreshold(config.text_polygon_threshold)
        self.detector.setUnclipRatio(2.0)
        self.detector.setMaxCandidates(config.text_max_candidates)
        self.detector.setInputSize(self.input_size)
        self.detector.setInputMean((123.675, 116.28, 103.53))
        self.detector.setInputScale(1.0 / 255.0 / np.array([0.229, 0.224, 0.225]))
        self.recognizer = cv2.dnn.readNet(str(recognition_model))
        self.charset = _load_cn_charset(charset_source)
        self.target_vertices = np.array([[0, 31], [0, 0], [99, 0], [99, 31]], dtype=np.float32)

    def _recognize(self, image_bgr: np.ndarray, quadrilateral: np.ndarray) -> tuple[str, float]:
        vertices = quadrilateral.reshape(4, 2).astype(np.float32)
        transform = cv2.getPerspectiveTransform(vertices, self.target_vertices)
        crop = cv2.warpPerspective(image_bgr, transform, (100, 32))
        blob = cv2.dnn.blobFromImage(crop, 1 / 127.5, (100, 32), 127.5)
        self.recognizer.setInput(blob)
        output = self.recognizer.forward()
        text: list[str] = []
        probabilities: list[float] = []
        previous = -1
        for timestep in output:
            logits = np.asarray(timestep[0], dtype=np.float32)
            class_index = int(np.argmax(logits))
            if class_index != 0 and class_index != previous and class_index <= len(self.charset):
                shifted = logits - float(np.max(logits))
                probability = float(np.exp(shifted[class_index]) / np.exp(shifted).sum())
                text.append(self.charset[class_index - 1])
                probabilities.append(probability)
            previous = class_index
        confidence = float(np.mean(probabilities)) if probabilities else 0.0
        return "".join(text), confidence

    def detect(self, image_rgb: np.ndarray) -> list[Detection]:
        source_height, source_width = image_rgb.shape[:2]
        image_bgr = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR)
        resized = cv2.resize(image_bgr, self.input_size, interpolation=cv2.INTER_AREA)
        boxes, scores = self.detector.detect(resized)
        if boxes is None:
            return []
        scale_x = source_width / self.input_size[0]
        scale_y = source_height / self.input_size[1]
        detections: list[Detection] = []
        for box, score in zip(boxes, scores, strict=True):
            quadrilateral = np.asarray(box, dtype=np.float32)
            quadrilateral[:, 0] *= scale_x
            quadrilateral[:, 1] *= scale_y
            recognized, recognition_confidence = self._recognize(image_bgr, quadrilateral)
            x1, y1 = np.min(quadrilateral, axis=0)
            x2, y2 = np.max(quadrilateral, axis=0)
            detections.append(
                Detection(
                    detector_id=self.detector_id,
                    label="text",
                    rect=(float(x1), float(y1), float(x2), float(y2)),
                    confidence=float(score),
                    kind=RegionKind.MUST_KEEP,
                    importance=1.0,
                    tolerance=0.0,
                    attributes={
                        "semantic_type": "text",
                        "recognized_text": recognized,
                        "recognition_confidence": round(recognition_confidence, 6),
                        "quadrilateral_xy": quadrilateral.round(2).tolist(),
                    },
                )
            )
        return detections


class ObjectProductDetector:
    detector_id = "object_yolox"
    input_size = 640

    def __init__(self, model_path: Path, config: AnalysisConfig) -> None:
        self.net = cv2.dnn.readNet(str(model_path))
        self.confidence_threshold = config.object_confidence_threshold
        self.nms_threshold = config.object_nms_threshold
        grids: list[np.ndarray] = []
        strides: list[np.ndarray] = []
        for stride in (8, 16, 32):
            size = self.input_size // stride
            x_values, y_values = np.meshgrid(np.arange(size), np.arange(size))
            grid = np.stack((x_values, y_values), axis=2).reshape(1, -1, 2)
            grids.append(grid)
            strides.append(np.full((*grid.shape[:2], 1), stride))
        self.grids = np.concatenate(grids, axis=1)
        self.strides = np.concatenate(strides, axis=1)

    def detect(self, image_rgb: np.ndarray) -> list[Detection]:
        height, width = image_rgb.shape[:2]
        scale = min(self.input_size / height, self.input_size / width)
        resized = cv2.resize(
            image_rgb,
            (max(1, int(width * scale)), max(1, int(height * scale))),
            interpolation=cv2.INTER_LINEAR,
        ).astype(np.float32)
        letterboxed = np.full((self.input_size, self.input_size, 3), 114.0, np.float32)
        letterboxed[: resized.shape[0], : resized.shape[1]] = resized
        blob = np.transpose(letterboxed, (2, 0, 1))[None]
        self.net.setInput(blob)
        outputs = self.net.forward(self.net.getUnconnectedOutLayersNames())[0].copy()
        predictions = outputs[0]
        predictions[:, :2] = (predictions[:, :2] + self.grids[0]) * self.strides[0]
        predictions[:, 2:4] = np.exp(predictions[:, 2:4]) * self.strides[0]
        scores_by_class = predictions[:, 4:5] * predictions[:, 5:]
        scores = np.max(scores_by_class, axis=1)
        class_ids = np.argmax(scores_by_class, axis=1)
        xywh = np.empty_like(predictions[:, :4])
        xywh[:, 0] = predictions[:, 0] - predictions[:, 2] / 2
        xywh[:, 1] = predictions[:, 1] - predictions[:, 3] / 2
        xywh[:, 2:] = predictions[:, 2:4]
        keep = _nms_indices(
            xywh.tolist(), scores.tolist(), self.confidence_threshold, self.nms_threshold
        )
        detections: list[Detection] = []
        for index in keep:
            x, y, box_width, box_height = (float(value) / scale for value in xywh[index])
            class_id = int(class_ids[index])
            label = COCO_CLASSES[class_id]
            semantic_type = "person" if label == "person" else "object"
            if label in PRODUCT_CLASSES:
                semantic_type = "product"
            kind = (
                RegionKind.MUST_KEEP
                if label == "person"
                else (RegionKind.RIGID if label in PRODUCT_CLASSES else RegionKind.PREFER_KEEP)
            )
            importance = (
                0.98
                if semantic_type == "person"
                else (0.92 if semantic_type == "product" else 0.78)
            )
            detections.append(
                Detection(
                    detector_id=self.detector_id,
                    label=label,
                    rect=(x, y, x + box_width, y + box_height),
                    confidence=float(scores[index]),
                    kind=kind,
                    importance=importance,
                    tolerance=max(0.0, 1.0 - importance),
                    attributes={"semantic_type": semantic_type, "coco_class_id": class_id},
                )
            )
        return detections


class LogoCandidateDetector:
    """Detect compact visual marks; this is region detection, not brand identification."""

    detector_id = "logo_candidate_cv"

    def __init__(self, limit: int) -> None:
        self.limit = limit

    def detect(self, image_rgb: np.ndarray) -> list[Detection]:
        if self.limit == 0:
            return []
        image, scale = _scaled_image(image_rgb, 1024)
        gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        hsv = cv2.cvtColor(image, cv2.COLOR_RGB2HSV)
        edges = cv2.Canny(gray, 80, 180)
        _regions, boxes = cv2.MSER_create().detectRegions(gray)
        image_area = float(gray.shape[0] * gray.shape[1])
        candidates: list[list[float]] = []
        scores: list[float] = []
        for x, y, width, height in boxes:
            area_ratio = (width * height) / image_area
            aspect = width / max(height, 1)
            if not 0.001 <= area_ratio <= 0.12 or not 0.25 <= aspect <= 4.0:
                continue
            edge_density = float(np.mean(edges[y : y + height, x : x + width] > 0))
            saturation = float(np.mean(hsv[y : y + height, x : x + width, 1])) / 255.0
            confidence = min(0.89, 0.40 + edge_density * 1.4 + saturation * 0.25)
            if edge_density < 0.08 or confidence < 0.55:
                continue
            candidates.append([float(x), float(y), float(width), float(height)])
            scores.append(confidence)
        keep = _nms_indices(candidates, scores, 0.55, 0.25)[: self.limit]
        return [
            Detection(
                detector_id=self.detector_id,
                label="logo_candidate",
                rect=(
                    candidates[index][0] / scale,
                    candidates[index][1] / scale,
                    (candidates[index][0] + candidates[index][2]) / scale,
                    (candidates[index][1] + candidates[index][3]) / scale,
                ),
                confidence=scores[index],
                kind=RegionKind.RIGID,
                importance=0.88,
                tolerance=0.05,
                attributes={
                    "semantic_type": "logo_candidate",
                    "brand_identity_recognized": False,
                },
            )
            for index in keep
        ]


class ProtectionDetectorSuite:
    suite_id = "protection_detectors"
    suite_version = "2.0.0"

    def __init__(self, config: AnalysisConfig) -> None:
        root = Path(config.model_root).resolve()
        missing = [filename for filename in MODEL_FILES.values() if not (root / filename).is_file()]
        if missing:
            raise FileNotFoundError(
                "missing analyzer model assets: "
                + ", ".join(missing)
                + "; run python scripts/materialize_analyzer_models.py"
            )
        self.detectors = (
            TextDetectorRecognizer(
                root / MODEL_FILES["text_ppocrv3"],
                root / MODEL_FILES["text_crnn_cn"],
                root / MODEL_FILES["text_crnn_charset"],
                config,
            ),
            FaceDetector(root / MODEL_FILES["face_yunet"], config.face_confidence_threshold),
            ObjectProductDetector(root / MODEL_FILES["object_yolox"], config),
            LogoCandidateDetector(config.logo_candidate_limit),
        )
        self.analyzer_ids = tuple(
            f"{analyzer_id}:{sha256_file(root / filename)[:12]}"
            for analyzer_id, filename in MODEL_FILES.items()
            if filename != "crnn.py"
        ) + (f"logo_candidate_cv:{self.suite_version}",)

    def detect(self, image_rgb: np.ndarray, padding_ratio: float) -> tuple[RegionRecord, ...]:
        height, width = image_rgb.shape[:2]
        records: list[RegionRecord] = []
        counts: dict[str, int] = {}
        protected_bounds: list[tuple[float, float, float, float]] = []
        for detector in self.detectors:
            for detection in detector.detect(image_rgb):
                if detection.detector_id == "logo_candidate_cv":
                    center_x = (detection.rect[0] + detection.rect[2]) / 2
                    center_y = (detection.rect[1] + detection.rect[3]) / 2
                    already_protected = any(
                        x1 <= center_x <= x2 and y1 <= center_y <= y2
                        for x1, y1, x2, y2 in protected_bounds
                    )
                    if already_protected:
                        continue
                else:
                    protected_bounds.append(detection.rect)
                rectangle = _rect(detection.rect, width, height, padding_ratio)
                if rectangle is None:
                    continue
                index = counts.get(detection.detector_id, 0)
                counts[detection.detector_id] = index + 1
                records.append(
                    RegionRecord(
                        region_id=f"{detection.detector_id}-{index:03d}",
                        kind=detection.kind,
                        rect=rectangle,
                        importance=detection.importance,
                        tolerance=detection.tolerance,
                        confidence=max(0.0, min(1.0, detection.confidence)),
                        source=detection.detector_id,
                        label=detection.label,
                        attributes=detection.attributes,
                    )
                )
        return tuple(records)


class PaddleOcrV6SmallDetector:
    """PP-OCRv6 small adapter using PaddleOCR's documented local pipeline API."""

    detector_id = "text_ppocrv6_small"
    detection_model_name = "PP-OCRv6_small_det"
    recognition_model_name = "PP-OCRv6_small_rec"

    def __init__(self) -> None:
        try:
            from paddleocr import PaddleOCR
        except ImportError as error:
            raise RuntimeError(
                "company_cpu_v2 requires the optional company-model dependencies; "
                "follow docs/runbooks/WINDOWS_INSTALL.md"
            ) from error
        self.pipeline = PaddleOCR(
            text_detection_model_name=self.detection_model_name,
            text_recognition_model_name=self.recognition_model_name,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            device="cpu",
            engine="onnxruntime",
        )
        cache_root = Path.home() / ".paddlex" / "official_models"
        self.model_audits = (
            directory_audit(cache_root / f"{self.detection_model_name}_onnx"),
            directory_audit(cache_root / f"{self.recognition_model_name}_onnx"),
        )

    @staticmethod
    def _payload(item: Any) -> dict[str, Any]:
        payload = getattr(item, "json", item)
        if callable(payload):
            payload = payload()
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            raise ValueError("PP-OCRv6 returned an unsupported result object")
        nested = payload.get("res")
        return nested if isinstance(nested, dict) else payload

    def detect(self, image_rgb: np.ndarray) -> list[Detection]:
        results = self.pipeline.predict(image_rgb)
        detections: list[Detection] = []
        for item in results:
            payload = self._payload(item)
            polygons = payload.get("rec_polys") or payload.get("dt_polys") or []
            texts = payload.get("rec_texts") or []
            scores = payload.get("rec_scores") or []
            for index, polygon in enumerate(polygons):
                vertices = np.asarray(polygon, dtype=np.float32).reshape(-1, 2)
                if vertices.shape[0] < 4:
                    continue
                x1, y1 = np.min(vertices, axis=0)
                x2, y2 = np.max(vertices, axis=0)
                text = str(texts[index]) if index < len(texts) else ""
                confidence = float(scores[index]) if index < len(scores) else 0.0
                detections.append(
                    Detection(
                        detector_id=self.detector_id,
                        label="text",
                        rect=(float(x1), float(y1), float(x2), float(y2)),
                        confidence=max(0.0, min(1.0, confidence)),
                        kind=RegionKind.MUST_KEEP,
                        importance=1.0,
                        tolerance=0.0,
                        attributes={
                            "semantic_type": "text",
                            "recognized_text": text,
                            "recognition_confidence": round(confidence, 6),
                            "quadrilateral_xy": vertices.round(2).tolist(),
                            "model_family": "PP-OCRv6-small",
                        },
                    )
                )
        return detections


class DFineNanoDetector:
    """Apache-2.0 D-FINE nano COCO adapter through Transformers."""

    detector_id = "object_dfine_hgnetv2_n"
    model_id = "ustc-community/dfine-nano-coco"
    model_revision = "066438d3d8f0da137a37b38fdf3368fd4afceced"

    def __init__(self, config: AnalysisConfig, *, local_files_only: bool = True) -> None:
        try:
            import torch
            from transformers import AutoImageProcessor, DFineForObjectDetection
        except ImportError as error:
            raise RuntimeError(
                "company_cpu_v2 requires the optional company-model dependencies; "
                "follow docs/runbooks/WINDOWS_INSTALL.md"
            ) from error
        cache_dir = Path(config.model_root).resolve() / "company_cpu_v2" / "huggingface"
        kwargs = {
            "revision": self.model_revision,
            "cache_dir": str(cache_dir),
            "local_files_only": local_files_only,
        }
        self.processor = AutoImageProcessor.from_pretrained(self.model_id, **kwargs)
        self.model = DFineForObjectDetection.from_pretrained(self.model_id, **kwargs)
        self.model.to("cpu").eval()
        self.torch = torch
        self.confidence_threshold = config.object_confidence_threshold

    def detect(self, image_rgb: np.ndarray) -> list[Detection]:
        from PIL import Image

        image = Image.fromarray(image_rgb)
        inputs = self.processor(images=image, return_tensors="pt")
        with self.torch.inference_mode():
            outputs = self.model(**inputs)
        results = self.processor.post_process_object_detection(
            outputs,
            target_sizes=[(image.height, image.width)],
            threshold=self.confidence_threshold,
        )[0]
        id_to_label = getattr(self.model.config, "id2label", {})
        detections: list[Detection] = []
        for score, label_id, box in zip(
            results["scores"], results["labels"], results["boxes"], strict=True
        ):
            confidence = float(score.item())
            class_id = int(label_id.item())
            label = str(id_to_label.get(class_id, class_id)).lower()
            x1, y1, x2, y2 = (float(value) for value in box.tolist())
            semantic_type = "person" if label == "person" else "object"
            if label in PRODUCT_CLASSES:
                semantic_type = "product"
            kind = (
                RegionKind.MUST_KEEP
                if semantic_type == "person"
                else RegionKind.RIGID
                if semantic_type == "product"
                else RegionKind.PREFER_KEEP
            )
            importance = (
                0.98
                if semantic_type == "person"
                else 0.92
                if semantic_type == "product"
                else 0.78
            )
            detections.append(
                Detection(
                    detector_id=self.detector_id,
                    label=label,
                    rect=(x1, y1, x2, y2),
                    confidence=confidence,
                    kind=kind,
                    importance=importance,
                    tolerance=max(0.0, 1.0 - importance),
                    attributes={
                        "semantic_type": semantic_type,
                        "coco_class_id": class_id,
                        "model_family": "D-FINE-HGNetV2-N",
                    },
                )
            )
        return detections


class CompanyCpuProtectionDetectorSuite:
    """Current Windows-CPU detector profile; the sole new-developer default."""

    suite_id = "company_cpu_v2"
    suite_version = "2.0.0"

    def __init__(self, config: AnalysisConfig, *, allow_model_download: bool = False) -> None:
        root = Path(config.model_root).resolve()
        face_path = root / MODEL_FILES["face_yunet"]
        if not face_path.is_file():
            raise FileNotFoundError(
                f"missing YuNet model: {face_path}; run scripts/materialize_analyzer_models.py"
            )
        self.detectors = (
            PaddleOcrV6SmallDetector(),
            FaceDetector(face_path, config.face_confidence_threshold),
            DFineNanoDetector(config, local_files_only=not allow_model_download),
            LogoCandidateDetector(config.logo_candidate_limit),
        )
        package_versions = []
        for package in (
            "paddleocr",
            "paddlepaddle",
            "onnxruntime",
            "transformers",
            "torch",
            "torchvision",
        ):
            try:
                package_versions.append(f"{package}={importlib.metadata.version(package)}")
            except importlib.metadata.PackageNotFoundError:
                package_versions.append(f"{package}=missing")
        ocr = self.detectors[0]
        ocr_digest = sha256_json(
            [item["content_sha256"] for item in ocr.model_audits]  # type: ignore[attr-defined]
        )
        self.model_audits = (  # type: ignore[attr-defined]
            *ocr.model_audits,
            directory_audit(root / "company_cpu_v2" / "huggingface"),
        )
        self.analyzer_ids = (
            f"text_ppocrv6_small:{ocr_digest[:12]}:onnxruntime",
            f"face_yunet:{sha256_file(face_path)[:12]}",
            f"object_dfine_hgnetv2_n:{DFineNanoDetector.model_revision[:12]}",
            f"logo_candidate_cv:{self.suite_version}",
            "runtime:" + ",".join(package_versions),
        )

    def detect(self, image_rgb: np.ndarray, padding_ratio: float) -> tuple[RegionRecord, ...]:
        height, width = image_rgb.shape[:2]
        records: list[RegionRecord] = []
        counts: dict[str, int] = {}
        protected_bounds: list[tuple[float, float, float, float]] = []
        for detector in self.detectors:
            for detection in detector.detect(image_rgb):
                if detection.detector_id == "logo_candidate_cv":
                    center_x = (detection.rect[0] + detection.rect[2]) / 2
                    center_y = (detection.rect[1] + detection.rect[3]) / 2
                    if any(
                        x1 <= center_x <= x2 and y1 <= center_y <= y2
                        for x1, y1, x2, y2 in protected_bounds
                    ):
                        continue
                else:
                    protected_bounds.append(detection.rect)
                rectangle = _rect(detection.rect, width, height, padding_ratio)
                if rectangle is None:
                    continue
                index = counts.get(detection.detector_id, 0)
                counts[detection.detector_id] = index + 1
                records.append(
                    RegionRecord(
                        region_id=f"{detection.detector_id}-{index:03d}",
                        kind=detection.kind,
                        rect=rectangle,
                        importance=detection.importance,
                        tolerance=detection.tolerance,
                        confidence=max(0.0, min(1.0, detection.confidence)),
                        source=detection.detector_id,
                        label=detection.label,
                        attributes=detection.attributes,
                    )
                )
        return tuple(records)
