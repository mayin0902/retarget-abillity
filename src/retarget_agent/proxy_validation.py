"""Evaluation helpers for human-screened proxy labels (never human ground truth)."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .hashing import sha256_file
from .models import CandidateRecord, MetricBundle, RunManifest
from .storage import LocalArtifactStore

_GRADE_RANK = {"A": 3, "B": 2, "C": 1, "D": 0}
_HOLDOUT_QUOTAS = {"person": 3, "movie_poster": 4, "film_still": 4, "video_cover": 4}


def _grade(value: object) -> str:
    grade = str(value).removeprefix("proxy_").upper()
    if grade not in _GRADE_RANK:
        raise ValueError(f"invalid grade: {value}")
    return grade


def freeze_proxy_split(
    ratings_csv: Path,
    output_path: Path,
    *,
    seed: str = "movie60-v3-final",
) -> dict[str, Any]:
    """Freeze a label-free, scene-stratified 45/15 task split and five dev folds."""

    if output_path.exists():
        raise FileExistsError(output_path)
    with ratings_csv.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    task_scenes: dict[str, str] = {}
    for row in rows:
        task_id = row["task_id"]
        scene = row["scene_category"]
        if task_id in task_scenes and task_scenes[task_id] != scene:
            raise ValueError(f"{task_id}: inconsistent scene")
        task_scenes[task_id] = scene
    if len(rows) != 420 or len(task_scenes) != 60:
        raise ValueError("expected exactly 420 candidates across 60 tasks")
    holdout: set[str] = set()
    for scene, quota in _HOLDOUT_QUOTAS.items():
        candidates = sorted(
            (task_id for task_id, value in task_scenes.items() if value == scene),
            key=lambda task_id: hashlib.sha256(f"{seed}:{task_id}".encode()).hexdigest(),
        )
        if len(candidates) < quota:
            raise ValueError(f"scene {scene} cannot satisfy holdout quota")
        holdout.update(candidates[:quota])
    development = sorted(set(task_scenes) - holdout)
    folds: dict[str, int] = {}
    for scene in sorted(set(task_scenes.values())):
        scene_tasks = sorted(
            (task_id for task_id in development if task_scenes[task_id] == scene),
            key=lambda task_id: hashlib.sha256(
                f"{seed}:fold:{task_id}".encode()
            ).hexdigest(),
        )
        for index, task_id in enumerate(scene_tasks):
            folds[task_id] = index % 5
    records = [
        {
            "task_id": task_id,
            "scene_category": task_scenes[task_id],
            "partition": "proxy_holdout" if task_id in holdout else "development",
            "development_fold": None if task_id in holdout else folds[task_id],
        }
        for task_id in sorted(task_scenes)
    ]
    payload = {
        "schema_version": "1.0",
        "split_id": "movie60-proxy-v3-45dev-15holdout",
        "seed": seed,
        "ratings_sha256": sha256_file(ratings_csv),
        "label_free_split_statement": (
            "Only task_id and scene_category participate in split assignment; suggested grades "
            "and scores are not used."
        ),
        "label_provenance": "human_screened_large_model_proxy_not_human_ground_truth",
        "development_task_count": len(development),
        "proxy_holdout_task_count": len(holdout),
        "records": records,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


def _percentile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(probability * len(ordered)) - 1)
    return ordered[index]


def _classification(rows: list[dict[str, Any]]) -> dict[str, Any]:
    labels = tuple(_GRADE_RANK)
    exact = sum(row["truth_grade"] == row["predicted_grade"] for row in rows)
    pass_agreement = sum(
        (row["truth_grade"] in {"A", "B"}) == (row["predicted_grade"] in {"A", "B"})
        for row in rows
    )
    truth_cd = sum(row["truth_grade"] in {"C", "D"} for row in rows)
    predicted_cd = sum(row["predicted_grade"] in {"C", "D"} for row in rows)
    true_cd = sum(
        row["truth_grade"] in {"C", "D"} and row["predicted_grade"] in {"C", "D"}
        for row in rows
    )
    f1_values: list[float] = []
    for grade in labels:
        tp = sum(row["truth_grade"] == grade and row["predicted_grade"] == grade for row in rows)
        fp = sum(row["truth_grade"] != grade and row["predicted_grade"] == grade for row in rows)
        fn = sum(row["truth_grade"] == grade and row["predicted_grade"] != grade for row in rows)
        denominator = 2 * tp + fp + fn
        f1_values.append(2 * tp / denominator if denominator else 0.0)
    return {
        "candidate_count": len(rows),
        "exact_grade_accuracy": exact / len(rows),
        "ab_cd_agreement": pass_agreement / len(rows),
        "cd_recall": true_cd / truth_cd if truth_cd else None,
        "cd_precision": true_cd / predicted_cd if predicted_cd else None,
        "macro_f1": statistics.fmean(f1_values),
        "truth_grade_counts": dict(Counter(row["truth_grade"] for row in rows)),
        "predicted_grade_counts": dict(Counter(row["predicted_grade"] for row in rows)),
    }


def _ranking(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_task[row["task_id"]].append(row)
    top1_eligible = 0
    top1_hits = 0
    ordered_pairs = 0
    correct_pairs = 0
    same_differences: dict[str, list[float]] = defaultdict(list)
    all_same_ranges: list[float] = []
    all_a_ranges: list[float] = []
    for task_rows in by_task.values():
        truth_ranks = [_GRADE_RANK[row["truth_grade"]] for row in task_rows]
        best_truth = max(truth_ranks)
        best_methods = {
            row["method_id"]
            for row in task_rows
            if _GRADE_RANK[row["truth_grade"]] == best_truth
        }
        if len(set(truth_ranks)) > 1:
            top1_eligible += 1
            predicted_top = max(task_rows, key=lambda row: row["quality_score"])["method_id"]
            top1_hits += predicted_top in best_methods
        else:
            score_range = max(row["quality_score"] for row in task_rows) - min(
                row["quality_score"] for row in task_rows
            )
            all_same_ranges.append(score_range)
            if task_rows[0]["truth_grade"] == "A":
                all_a_ranges.append(score_range)
        for index, left in enumerate(task_rows):
            for right in task_rows[index + 1 :]:
                left_truth = _GRADE_RANK[left["truth_grade"]]
                right_truth = _GRADE_RANK[right["truth_grade"]]
                score_difference = float(left["quality_score"]) - float(right["quality_score"])
                if left_truth == right_truth:
                    same_differences[left["truth_grade"]].append(abs(score_difference))
                    continue
                ordered_pairs += 1
                correct_pairs += (left_truth > right_truth and score_difference > 0) or (
                    left_truth < right_truth and score_difference < 0
                )
    return {
        "task_count": len(by_task),
        "top1_eligible_task_count": top1_eligible,
        "top1_hit_rate": top1_hits / top1_eligible if top1_eligible else None,
        "different_grade_pair_count": ordered_pairs,
        "different_grade_pair_order_accuracy": (
            correct_pairs / ordered_pairs if ordered_pairs else None
        ),
        "same_grade_score_spread": {
            grade: {
                "pair_count": len(same_differences[grade]),
                "median": (
                    statistics.median(same_differences[grade])
                    if same_differences[grade]
                    else None
                ),
                "p90": _percentile(same_differences[grade], 0.90),
            }
            for grade in _GRADE_RANK
        },
        "all_same_grade_task_count": len(all_same_ranges),
        "all_same_grade_range_median": (
            statistics.median(all_same_ranges) if all_same_ranges else None
        ),
        "all_same_grade_range_p90": _percentile(all_same_ranges, 0.90),
        "all_a_task_count": len(all_a_ranges),
        "all_a_range_median": statistics.median(all_a_ranges) if all_a_ranges else None,
        "all_a_range_p90": _percentile(all_a_ranges, 0.90),
    }


def evaluate_proxy_strategy(
    run_dir: Path,
    *,
    evaluation_id: str,
    ratings_csv: Path,
    split_manifest: Path,
    partitions: Iterable[str],
) -> dict[str, Any]:
    """Compare a frozen Rule evaluation with screened proxy suggestions."""

    store = LocalArtifactStore(run_dir.resolve())
    run = RunManifest.model_validate(store.read_json("run.json"))
    split = json.loads(split_manifest.read_text(encoding="utf-8"))
    if split["ratings_sha256"] != sha256_file(ratings_csv):
        raise ValueError("ratings file differs from frozen split evidence")
    partition_set = set(partitions)
    task_partition = {item["task_id"]: item for item in split["records"]}
    candidates = {
        (record.task_id, record.method_id): record
        for path in sorted(run_dir.glob("candidates/*/*/candidate.json"))
        if (record := CandidateRecord.model_validate_json(path.read_text(encoding="utf-8")))
    }
    with ratings_csv.open(encoding="utf-8-sig", newline="") as handle:
        ratings = list(csv.DictReader(handle))
    rows: list[dict[str, Any]] = []
    provenance = Counter()
    for rating in ratings:
        task_id = rating["task_id"]
        split_row = task_partition[task_id]
        if split_row["partition"] not in partition_set:
            continue
        key = (task_id, rating["method"])
        candidate = candidates.get(key)
        if candidate is None or candidate.output is None:
            raise ValueError(f"missing candidate for rating {key}")
        if candidate.output.sha256 != rating["image_sha256"]:
            raise ValueError(f"image hash mismatch for rating {key}")
        metric = MetricBundle.model_validate(
            store.read_json(f"evaluations/{evaluation_id}/metrics/{candidate.candidate_id}.json")
        )
        predicted_grade = _grade(metric.metrics.get("proxy_grade"))
        quality_score = metric.metrics.get("quality_score")
        if quality_score is None:
            raise ValueError(f"missing quality score for {candidate.candidate_id}")
        provenance[rating["evaluation_provenance"]] += 1
        rows.append(
            {
                "task_id": task_id,
                "scene_category": rating["scene_category"],
                "method_id": rating["method"],
                "truth_grade": _grade(rating["suggested_grade"]),
                "predicted_grade": predicted_grade,
                "quality_score": float(quality_score),
                "partition": split_row["partition"],
                "development_fold": split_row["development_fold"],
            }
        )
    expected = 7 * sum(
        item["partition"] in partition_set for item in split["records"]
    )
    if len(rows) != expected:
        raise ValueError(f"expected {expected} evaluated rows, found {len(rows)}")
    by_scene = {
        scene: _classification([row for row in rows if row["scene_category"] == scene])
        for scene in sorted({row["scene_category"] for row in rows})
    }
    by_method = {
        method: _classification([row for row in rows if row["method_id"] == method])
        for method in sorted({row["method_id"] for row in rows})
    }
    folds = {
        str(fold): _classification(
            [row for row in rows if row["development_fold"] == fold]
        )
        for fold in range(5)
        if any(row["development_fold"] == fold for row in rows)
    }
    return {
        "schema_version": "1.0",
        "run_id": run.run_id,
        "evaluation_id": evaluation_id,
        "partitions": sorted(partition_set),
        "label_provenance": "human_screened_large_model_proxy_not_human_ground_truth",
        "independent_human_validation": False,
        "ratings_sha256": sha256_file(ratings_csv),
        "split_manifest_sha256": sha256_file(split_manifest),
        "evaluation_manifest_sha256": sha256_file(
            store.path(f"evaluations/{evaluation_id}/evaluation.json")
        ),
        "evaluation_provenance_counts": dict(provenance),
        "candidate_classification": _classification(rows),
        "task_ranking": _ranking(rows),
        "by_scene": by_scene,
        "by_method": by_method,
        "development_fold_diagnostics": folds,
    }


__all__ = ["evaluate_proxy_strategy", "freeze_proxy_split"]
