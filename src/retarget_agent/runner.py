"""Frozen method-profile Generation Run with failure isolation and resume."""

from __future__ import annotations

import importlib.metadata
import platform
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from .analysis import SharedProtectionAnalyzer
from .config import RunConfig
from .datasets import FolderCsvDatasetAdapter
from .events import SqliteEventStore
from .hashing import sha256_file, sha256_json, short_hash
from .instrumentation import StageTimer
from .methods import built_in_methods
from .models import (
    AnalysisArtifact,
    CandidateRecord,
    ExecutionContext,
    GenerationStatus,
    MethodConfig,
    RunManifest,
    TaskSpec,
    TransformRecord,
)
from .selector import select_by_technical_risk
from .storage import LocalArtifactStore
from .visualization import comparison_grid

DEPENDENCIES = (
    "numpy",
    "opencv-python-headless",
    "Pillow",
    "psutil",
    "pydantic",
    "PyYAML",
    "typer",
)


def _dependency_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for name in DEPENDENCIES:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "missing"
    return versions


def _code_version() -> str:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        return f"{commit}{'+dirty' if dirty else ''}"
    except (OSError, subprocess.CalledProcessError):
        return "no-commit+working-tree"


def _load_rgb(path: Path) -> np.ndarray:
    with Image.open(path) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
        return np.asarray(image).copy()


class GenerationRunner:
    def __init__(self) -> None:
        self.dataset_adapter = FolderCsvDatasetAdapter()
        self.methods = built_in_methods()

    @classmethod
    def default(cls) -> GenerationRunner:
        return cls()

    def run(self, config: RunConfig, config_path: Path) -> RunManifest:
        dataset_root = Path(config.dataset_root).resolve()
        validation = self.dataset_adapter.validate(dataset_root)
        if not validation.valid:
            raise ValueError("dataset validation failed: " + "; ".join(validation.errors))
        output_root = Path(config.output_root).resolve()
        run_dir = output_root / config.run_id
        store = LocalArtifactStore(run_dir)
        events = SqliteEventStore(run_dir / "events.sqlite")
        events.initialize()
        snapshot_path = store.path("config/run.yaml")
        if snapshot_path.exists():
            if sha256_file(snapshot_path) != sha256_file(config_path):
                raise ValueError("run_id already exists with a different config snapshot")
        else:
            snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(config_path, snapshot_path)

        previous_manifest = store.path("run.json")
        if previous_manifest.exists():
            previous = RunManifest.model_validate(store.read_json("run.json"))
            if previous.config_hash != config.config_hash:
                raise ValueError("run_id already exists with a different config hash")
            if previous.dataset_fingerprint != validation.dataset_fingerprint:
                raise ValueError("run_id already exists with a different dataset fingerprint")

        manifest = RunManifest(
            run_id=config.run_id,
            dataset_id=validation.dataset_id,
            dataset_fingerprint=validation.dataset_fingerprint,
            status="RUNNING",
            methods=config.methods,
            config_hash=config.config_hash,
            config_snapshot="config/run.yaml",
            code_version=_code_version(),
            python_version=platform.python_version(),
            dependency_versions=_dependency_versions(),
            task_ids=tuple(task.task_id for task in validation.tasks),
        )
        store.write_json("run.json", manifest, overwrite=True)
        events.append_run(manifest)
        analyzer = SharedProtectionAnalyzer(dataset_root, config.analysis)
        all_candidates: list[CandidateRecord] = []
        failed_candidates: list[CandidateRecord] = []
        context = ExecutionContext(run_id=config.run_id, run_root=str(run_dir), device="cpu")

        for task in validation.tasks:
            image_path = dataset_root / task.source.image_path
            image = _load_rgb(image_path)
            self._freeze_task_input(task, image_path, store)
            analysis, importance, tolerance = self._analysis_for_task(
                analyzer, task, image, config, store
            )
            task_candidates: list[CandidateRecord] = []
            task_transforms: dict[str, TransformRecord] = {}
            for method_id in config.methods:
                candidate, transform = self._candidate_for_method(
                    task,
                    image,
                    analysis,
                    importance,
                    tolerance,
                    method_id,
                    config,
                    context,
                    store,
                    events,
                )
                task_candidates.append(candidate)
                all_candidates.append(candidate)
                if candidate.generation_status == GenerationStatus.FAILED:
                    failed_candidates.append(candidate)
                if transform is not None:
                    task_transforms[candidate.candidate_id] = transform
            decision = select_by_technical_risk(
                task, config.run_id, task_candidates, task_transforms
            )
            store.write_json(
                f"decisions/{task.task_id}.json",
                decision,
                overwrite=store.path(f"decisions/{task.task_id}.json").exists(),
            )
            visualization_path = f"visualizations/{task.task_id}.png"
            if not store.path(visualization_path).exists():
                grid = comparison_grid(image, task, task_candidates, decision, run_dir)
                store.write_image(visualization_path, grid)

        status = "PARTIAL_COMPLETED" if failed_candidates else "COMPLETED"
        completed = manifest.model_copy(
            update={
                "completed_at": datetime.now(UTC),
                "status": status,
                "candidate_ids": tuple(candidate.candidate_id for candidate in all_candidates),
                "failed_candidate_ids": tuple(
                    candidate.candidate_id for candidate in failed_candidates
                ),
            }
        )
        store.write_json("run.json", completed, overwrite=True)
        events.append_run(completed)
        return completed

    def _freeze_task_input(
        self, task: TaskSpec, source_path: Path, store: LocalArtifactStore
    ) -> None:
        task_path = f"tasks/{task.task_id}.json"
        if not store.path(task_path).exists():
            store.write_json(task_path, task)
        source_suffix = source_path.suffix.lower() or ".img"
        source_image_path = f"sources/{task.source.source_id}{source_suffix}"
        source_record_path = f"sources/{task.source.source_id}.json"
        if not store.path(source_image_path).exists():
            media_type = "image/jpeg" if source_suffix in {".jpg", ".jpeg"} else "image/png"
            reference = store.copy_file(source_image_path, source_path, media_type)
            if reference.sha256 != task.source.sha256:
                raise ValueError(
                    f"frozen source hash changed for {task.source.source_id}: "
                    f"{reference.sha256} != {task.source.sha256}"
                )
            store.write_json(source_record_path, reference)

    def _analysis_for_task(
        self,
        analyzer: SharedProtectionAnalyzer,
        task: TaskSpec,
        image: np.ndarray,
        config: RunConfig,
        store: LocalArtifactStore,
    ) -> tuple[AnalysisArtifact, np.ndarray, np.ndarray]:
        base = f"analysis/{task.task_id}"
        record_path = f"{base}/analysis.json"
        if store.path(record_path).exists():
            artifact = AnalysisArtifact.model_validate(store.read_json(record_path))
            return (
                artifact,
                store.read_numpy(artifact.importance_map),
                store.read_numpy(artifact.tolerance_map),
            )
        output = analyzer.analyze(image, task)
        importance_ref = store.write_numpy(f"{base}/importance.npy", output.importance_map)
        tolerance_ref = store.write_numpy(f"{base}/tolerance.npy", output.tolerance_map)
        store.write_map_preview(f"{base}/importance.png", output.importance_map)
        store.write_map_preview(f"{base}/tolerance.png", output.tolerance_map)
        artifact_id = f"analysis-{short_hash(task.task_id + config.config_hash)}"
        artifact = AnalysisArtifact(
            artifact_id=artifact_id,
            analysis_version=analyzer.analyzer_version,
            task_id=task.task_id,
            source_id=task.source.source_id,
            target_id=task.target.target_id,
            source_width=task.source.width,
            source_height=task.source.height,
            scene_profile=task.source.scene_profile,
            regions=output.regions,
            importance_map=importance_ref,
            tolerance_map=tolerance_ref,
            analyzer_ids=output.analyzer_ids,
            config_hash=sha256_json(config.analysis.model_dump(mode="json")),
            warnings=output.warnings,
        )
        store.write_json(record_path, artifact)
        return artifact, output.importance_map, output.tolerance_map

    def _candidate_for_method(
        self,
        task: TaskSpec,
        image: np.ndarray,
        analysis: AnalysisArtifact,
        importance: np.ndarray,
        tolerance: np.ndarray,
        method_id: str,
        config: RunConfig,
        context: ExecutionContext,
        store: LocalArtifactStore,
        events: SqliteEventStore,
    ) -> tuple[CandidateRecord, TransformRecord | None]:
        method = self.methods.get(method_id)
        method_config = MethodConfig(
            method_id=method_id,
            method_version=method.method_version,
            seed=config.seed,
            parameters=config.method_parameters.get(method_id, {}),
        )
        method_hash = sha256_json(method_config.model_dump(mode="json"))
        candidate_id = f"{task.task_id}--{method_id}--{short_hash(method_hash)}"
        base = f"candidates/{task.task_id}/{method_id}"
        record_path = f"{base}/candidate.json"
        if store.path(record_path).exists():
            candidate = CandidateRecord.model_validate(store.read_json(record_path))
            transform = (
                TransformRecord.model_validate(store.read_json(candidate.transform.relative_path))
                if candidate.transform
                else None
            )
            return candidate, transform

        timer = StageTimer(
            run_id=config.run_id,
            stage=f"method:{method_id}",
            task_id=task.task_id,
            candidate_id=candidate_id,
        )
        transform: TransformRecord | None = None
        try:
            with timer:
                output = method.generate(
                    image,
                    task,
                    analysis,
                    importance.copy(),
                    tolerance.copy(),
                    None,
                    method_config,
                    context,
                )
                transform = output.transform
                if output.image is None:
                    raise RuntimeError("method returned no image without raising an error")
                expected_shape = (task.target.height, task.target.width, 3)
                if output.image.shape != expected_shape:
                    raise ValueError(
                        f"method returned shape {output.image.shape}; expected {expected_shape}"
                    )
                image_ref = store.write_image(f"{base}/candidate.png", output.image)
                transform_path = store.write_json(f"{base}/transform.json", transform)
                transform_ref = store_ref(transform_path, store, "application/json")
                status = GenerationStatus(output.status)
            assert timer.result is not None
            events.append_stage(timer.result)
            candidate = CandidateRecord(
                candidate_id=candidate_id,
                task_id=task.task_id,
                method_id=method_id,
                method_version=method.method_version,
                variant_id=method_config.variant_id,
                run_id=config.run_id,
                input_sha256=task.source.sha256,
                output=image_ref,
                target_width=task.target.width,
                target_height=task.target.height,
                seed=config.seed,
                config_hash=method_hash,
                analysis_artifact_id=analysis.artifact_id,
                transform=transform_ref,
                generation_status=status,
                performance=timer.result,
                warnings=output.warnings,
            )
        except Exception as error:
            if timer.result is not None:
                events.append_stage(timer.result)
            candidate = CandidateRecord(
                candidate_id=candidate_id,
                task_id=task.task_id,
                method_id=method_id,
                method_version=method.method_version,
                variant_id=method_config.variant_id,
                run_id=config.run_id,
                input_sha256=task.source.sha256,
                target_width=task.target.width,
                target_height=task.target.height,
                seed=config.seed,
                config_hash=method_hash,
                analysis_artifact_id=analysis.artifact_id,
                generation_status=GenerationStatus.FAILED,
                failure_type=type(error).__name__,
                error_summary=str(error)[:500],
                performance=timer.result,
            )
        store.write_json(record_path, candidate)
        return candidate, transform


def store_ref(path: Path, store: LocalArtifactStore, media_type: str):
    from .models import ArtifactRef

    return ArtifactRef(
        relative_path=path.relative_to(store.root).as_posix(),
        sha256=sha256_file(path),
        media_type=media_type,
    )
