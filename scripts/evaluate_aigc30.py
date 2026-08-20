"""Evaluate the frozen AIGC30 outputs with the same Full300 proxy evaluator."""

from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import psutil
import yaml

from retarget_agent.config import AnalysisConfig
from retarget_agent.evaluation import EvaluationConfig, compute_proxy_metrics
from retarget_agent.models import AnalysisArtifact, TaskSpec
from retarget_agent.protection_detectors import ProtectionDetectorSuite

ROOT = Path(__file__).resolve().parents[1]
RUNS = {
    "pilot60": ROOT / "runs/square-public-v2-pilot60-20260812",
    "heldout240": ROOT / "runs/square-public-v2-heldout240-20260812",
}


def _read_rgb(path: Path) -> np.ndarray:
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"failed to decode {path}")
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _analysis_config(run: Path) -> AnalysisConfig:
    raw = yaml.safe_load((run / "config/run.yaml").read_text(encoding="utf-8"))
    return AnalysisConfig.model_validate((raw or {}).get("analysis", {}))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--selection",
        type=Path,
        default=Path("datasets/retarget_square_public_v2/aigc30_selection.yaml"),
    )
    parser.add_argument(
        "--aigc-run",
        type=Path,
        default=Path("runs/aigc30-seedream5-v3-20260812"),
    )
    parser.add_argument("--evaluation-id", default="auto-proxy-v1p1-aigc30-20260812")
    args = parser.parse_args()
    selection = yaml.safe_load(args.selection.read_text(encoding="utf-8"))
    tasks = selection["tasks"]
    output = args.aigc_run / "evaluations" / args.evaluation_id
    if output.exists():
        raise FileExistsError(output)

    suites = {split: ProtectionDetectorSuite(_analysis_config(run)) for split, run in RUNS.items()}
    config = EvaluationConfig(rerun_detectors=True)
    process = psutil.Process()
    records: list[dict[str, Any]] = []
    started_all = time.perf_counter()
    for index, selected in enumerate(tasks, 1):
        task_id = selected["task_id"]
        result = json.loads(
            (args.aigc_run / "results" / f"{task_id}.json").read_text(encoding="utf-8")
        )
        before_wall = time.perf_counter()
        before_cpu = time.process_time()
        before_rss = process.memory_info().rss
        if result["status"] != "success":
            metrics: dict[str, Any] = {
                "technical_valid": False,
                "hard_failures": f"aigc_generation_{result['error_code'].lower()}",
                "critical_regressions": "",
                "quality_score": None,
                "proxy_grade": "proxy_c",
                "proxy_business_success": False,
                "proxy_is_a": False,
                "calibration_status": "uncalibrated_no_human_ground_truth",
                "generation_status": "failed",
            }
        else:
            run = RUNS[selected["split"]]
            task = TaskSpec.model_validate_json(
                (run / "tasks" / f"{task_id}.json").read_text(encoding="utf-8")
            )
            source_ref = json.loads(
                (run / "sources" / f"{task.source.source_id}.json").read_text(encoding="utf-8")
            )
            source = _read_rgb(run / source_ref["relative_path"])
            candidate = _read_rgb(args.aigc_run / result["evaluation_image_path"])
            analysis = AnalysisArtifact.model_validate_json(
                (run / "analysis" / task_id / "analysis.json").read_text(encoding="utf-8")
            )
            candidate_regions = suites[selected["split"]].detect(candidate, 0.0)
            metrics = compute_proxy_metrics(
                source=source,
                candidate=candidate,
                task=task,
                source_regions=analysis.regions,
                candidate_regions=candidate_regions,
                transform=None,
                config=config,
            )
            metrics["generation_status"] = "success"
        metrics.update(
            {
                "detector_rerun_requested": True,
                "detector_rerun_available": result["status"] == "success",
                "evaluation_wall_seconds": time.perf_counter() - before_wall,
                "evaluation_cpu_seconds": time.process_time() - before_cpu,
                "evaluation_rss_delta_bytes": process.memory_info().rss - before_rss,
            }
        )
        record = {
            "task_id": task_id,
            "source_id": selected["source_id"],
            "scene_category": selected["scene_category"],
            "difficulty_tier": selected["difficulty_tier"],
            "candidate_id": f"{task_id}--seedream5",
            "evaluator_id": config.evaluator_id,
            "evaluator_version": config.evaluator_version,
            "metrics": metrics,
        }
        _write_json(output / "metrics" / f"{task_id}--seedream5.json", record)
        records.append(record)
        print(f"evaluate-aigc30={index}/30 status={result['status']}", flush=True)

    successful = [item for item in records if item["metrics"]["generation_status"] == "success"]
    scores = [float(item["metrics"]["quality_score"]) for item in successful]
    grades = Counter(item["metrics"]["proxy_grade"] for item in records)
    summary = {
        "benchmark_id": selection["benchmark_id"],
        "evaluation_id": args.evaluation_id,
        "required_task_count": 30,
        "generation_success_count": len(successful),
        "generation_failure_count": 30 - len(successful),
        "end_to_end_success_rate": len(successful) / 30,
        "quality_observed_count": len(scores),
        "quality_score_mean_successful_outputs": float(np.mean(scores)) if scores else None,
        "quality_score_p50_successful_outputs": float(np.percentile(scores, 50))
        if scores
        else None,
        "quality_score_p95_successful_outputs": float(np.percentile(scores, 95))
        if scores
        else None,
        "proxy_grade_counts_all_tasks": dict(grades),
        "proxy_success_rate_all_tasks": (grades["proxy_a"] + grades["proxy_b"]) / 30,
        "proxy_success_rate_successful_outputs": (grades["proxy_a"] + grades["proxy_b"])
        / len(successful)
        if successful
        else None,
        "evaluation_wall_seconds": time.perf_counter() - started_all,
        "notes": [
            "Failed provider calls remain proxy_c in the complete 30-task denominator.",
            "AIGC has no geometric transform record; transform safety is unavailable "
            "and weights are renormalized.",
            "Proxy grades remain uncalibrated automatic evidence, not human labels.",
        ],
    }
    _write_json(output / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
