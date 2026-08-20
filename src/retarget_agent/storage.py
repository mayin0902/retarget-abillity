"""Local immutable artifact storage for images, arrays and JSON records."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path, PurePosixPath
from typing import Any

import cv2
import numpy as np
from PIL import Image
from pydantic import BaseModel

from .hashing import sha256_file
from .models import ArtifactRef


class LocalArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, relative_path: str) -> Path:
        posix = PurePosixPath(relative_path.replace("\\", "/"))
        if posix.is_absolute() or ".." in posix.parts:
            raise ValueError("artifact path must remain below the run root")
        resolved = (self.root / Path(*posix.parts)).resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError as error:
            raise ValueError("artifact path escapes the run root") from error
        return resolved

    def write_json(self, relative_path: str, value: Any, *, overwrite: bool = False) -> Path:
        path = self.path(relative_path)
        if path.exists() and not overwrite:
            raise FileExistsError(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(path)
        return path

    def read_json(self, relative_path: str) -> dict[str, Any]:
        return json.loads(self.path(relative_path).read_text(encoding="utf-8"))

    def write_image(self, relative_path: str, image: np.ndarray) -> ArtifactRef:
        path = self.path(relative_path)
        if path.exists():
            raise FileExistsError(path)
        if image.ndim != 3 or image.shape[2] != 3 or image.dtype != np.uint8:
            raise ValueError("candidate image must be uint8 RGB")
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(image, mode="RGB").save(path, format="PNG")
        with Image.open(path) as opened:
            opened.verify()
        height, width = image.shape[:2]
        return ArtifactRef(
            relative_path=path.relative_to(self.root).as_posix(),
            sha256=sha256_file(path),
            media_type="image/png",
            width=width,
            height=height,
        )

    def copy_file(self, relative_path: str, source: Path, media_type: str) -> ArtifactRef:
        path = self.path(relative_path)
        if path.exists():
            raise FileExistsError(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, path)
        with Image.open(path) as opened:
            width, height = opened.size
        return ArtifactRef(
            relative_path=path.relative_to(self.root).as_posix(),
            sha256=sha256_file(path),
            media_type=media_type,
            width=width,
            height=height,
        )

    def write_numpy(self, relative_path: str, array: np.ndarray) -> ArtifactRef:
        path = self.path(relative_path)
        if path.exists():
            raise FileExistsError(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            np.save(handle, array, allow_pickle=False)
        height, width = array.shape[:2]
        return ArtifactRef(
            relative_path=path.relative_to(self.root).as_posix(),
            sha256=sha256_file(path),
            media_type="application/x-npy",
            width=width,
            height=height,
        )

    def read_numpy(self, reference: ArtifactRef) -> np.ndarray:
        path = self.path(reference.relative_path)
        if sha256_file(path) != reference.sha256:
            raise ValueError(f"artifact hash mismatch: {reference.relative_path}")
        with path.open("rb") as handle:
            return np.load(handle, allow_pickle=False)

    def write_map_preview(self, relative_path: str, array: np.ndarray) -> ArtifactRef:
        preview = np.clip(array * 255.0, 0, 255).astype(np.uint8)
        path = self.path(relative_path)
        if path.exists():
            raise FileExistsError(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(path), preview):
            raise OSError(f"could not write map preview: {path}")
        height, width = preview.shape
        return ArtifactRef(
            relative_path=path.relative_to(self.root).as_posix(),
            sha256=sha256_file(path),
            media_type="image/png",
            width=width,
            height=height,
        )
