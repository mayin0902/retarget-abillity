"""Human-grade calibration with ordinal ranking and within-grade tolerance."""

from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any

from .hashing import sha256_json
from .review import load_review_workspace
from .storage import LocalArtifactStore

GRADE_ORDER = {"A": 4, "B": 3, "C": 2, "D": 1}


@dataclass(frozen=True)
class CalibrationObservation:
    task_id: str
    candidate_id: str
    method_id: str
    human_grade: str
    machine_quality: float


def _nearest_rank(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return float(ordered[rank - 1])


def _distribution(values: list[float]) -> dict[str, float | int | None]:
    return {
        "count": len(values),
        "median": float(median(values)) if values else None,
        "p10": _nearest_rank(values, 0.10),
        "p90": _nearest_rank(values, 0.90),
    }


def compute_grade_calibration(
    observations: Iterable[CalibrationObservation],
) -> dict[str, Any]:
    """Compare ordinal human grades with continuous machine scores without inventing ties."""

    rows = list(observations)
    if any(item.human_grade not in GRADE_ORDER for item in rows):
        raise ValueError("calibration observations must use human grades A/B/C/D")
    identities = {(item.task_id, item.candidate_id) for item in rows}
    if len(identities) != len(rows):
        raise ValueError("calibration observations contain duplicate task/candidate identities")

    grade_scores: dict[str, list[float]] = defaultdict(list)
    by_task: dict[str, list[CalibrationObservation]] = defaultdict(list)
    for item in rows:
        grade_scores[item.human_grade].append(item.machine_quality)
        by_task[item.task_id].append(item)

    relations: dict[str, dict[str, int]] = defaultdict(lambda: {"pair_count": 0, "correct": 0})
    same_grade_deltas: dict[str, list[float]] = defaultdict(list)
    for task_rows in by_task.values():
        for index, left in enumerate(task_rows):
            for right in task_rows[index + 1 :]:
                if left.human_grade == right.human_grade:
                    same_grade_deltas[left.human_grade].append(
                        abs(left.machine_quality - right.machine_quality)
                    )
                    continue
                better, worse = (
                    (left, right)
                    if GRADE_ORDER[left.human_grade] > GRADE_ORDER[right.human_grade]
                    else (right, left)
                )
                key = f"{better.human_grade}>{worse.human_grade}"
                relations[key]["pair_count"] += 1
                relations[key]["correct"] += int(better.machine_quality > worse.machine_quality)

    all_same_ranges: list[float] = []
    all_a_ranges: list[float] = []
    top1_total = 0
    top1_hits = 0
    for task_rows in by_task.values():
        grades = {item.human_grade for item in task_rows}
        scores = [item.machine_quality for item in task_rows]
        if len(grades) == 1:
            score_range = max(scores) - min(scores)
            all_same_ranges.append(score_range)
            if grades == {"A"}:
                all_a_ranges.append(score_range)
            continue
        top1_total += 1
        best_grade = max(grades, key=GRADE_ORDER.__getitem__)
        human_best = {item.candidate_id for item in task_rows if item.human_grade == best_grade}
        maximum_quality = max(scores)
        machine_best = {
            item.candidate_id for item in task_rows if item.machine_quality == maximum_quality
        }
        top1_hits += int(bool(human_best & machine_best))

    relation_rows = []
    total_pairs = total_correct = 0
    for key in sorted(
        relations,
        key=lambda value: (
            -GRADE_ORDER[value[0]],
            -GRADE_ORDER[value[2]],
        ),
    ):
        counts = relations[key]
        total_pairs += counts["pair_count"]
        total_correct += counts["correct"]
        relation_rows.append(
            {
                "human_relation": key,
                **counts,
                "ordering_accuracy": counts["correct"] / counts["pair_count"],
            }
        )

    payload = {
        "schema_version": "1.0",
        "observation_count": len(rows),
        "task_count": len(by_task),
        "grade_score_distribution": {
            grade: _distribution(grade_scores[grade]) for grade in GRADE_ORDER
        },
        "different_grade_ordering": {
            "relations": relation_rows,
            "pair_count": total_pairs,
            "correct": total_correct,
            "ordering_accuracy": total_correct / total_pairs if total_pairs else None,
        },
        "same_grade_score_gap": {
            grade: _distribution(same_grade_deltas[grade]) for grade in GRADE_ORDER
        },
        "top1": {
            "eligible_task_count": top1_total,
            "hit_count": top1_hits,
            "hit_rate": top1_hits / top1_total if top1_total else None,
            "excludes_all_same_grade_tasks": True,
        },
        "all_same_grade_tasks": _distribution(all_same_ranges),
        "all_a_tasks": _distribution(all_a_ranges),
        "notes": [
            "Different-grade pairs measure ordinal ranking accuracy.",
            "Same-grade pairs never count as ordering successes or failures.",
            "Top-1 accepts any candidate in the tied human-best set.",
            "All-same-grade tasks are excluded from Top-1 and summarized by machine range.",
        ],
    }
    payload["result_hash"] = sha256_json(payload)
    return payload


def _number(value: Any, digits: int = 3) -> str:
    return "—" if value is None else f"{float(value):.{digits}f}"


def calibration_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 人工等级与机器 Quality 校准报告",
        "",
        "人工 A/B/C/D 是有序宽松等级；同级不要求机器同分。",
        "",
        "## 人工等级对应机器分数",
        "",
        "| 人工等级 | 样本数 | Quality 中位数 | 80% 常见区间 |",
        "|---|---:|---:|---:|",
    ]
    for grade, row in report["grade_score_distribution"].items():
        interval = f"{_number(row['p10'])}–{_number(row['p90'])}"
        lines.append(f"| {grade} | {row['count']} | {_number(row['median'])} | {interval} |")
    lines.extend(
        [
            "",
            "## 人工有等级差：机器排序",
            "",
            "| 人工关系 | Pair 数 | 排序正确率 |",
            "|---|---:|---:|",
        ]
    )
    for row in report["different_grade_ordering"]["relations"]:
        lines.append(
            f"| {row['human_relation']} | {row['pair_count']} | "
            f"{_number(row['ordering_accuracy'] * 100, 1)}% |"
        )
    overall = report["different_grade_ordering"]
    lines.append(
        f"| **总体** | **{overall['pair_count']}** | "
        f"**{_number((overall['ordering_accuracy'] or 0) * 100, 1)}%** |"
    )
    lines.extend(
        [
            "",
            "## 同等级一致性",
            "",
            "| 人工同级 | Pair 数 | Quality 分差中位数 | P90 分差 |",
            "|---|---:|---:|---:|",
        ]
    )
    for grade, row in report["same_grade_score_gap"].items():
        lines.append(
            f"| {grade}={grade} | {row['count']} | {_number(row['median'])} | "
            f"{_number(row['p90'])} |"
        )
    top1 = report["top1"]
    lines.extend(
        [
            "",
            f"同图 Top-1 命中率：{_number((top1['hit_rate'] or 0) * 100, 1)}% "
            f"（{top1['hit_count']}/{top1['eligible_task_count']}，仅统计人工存在等级差异的任务）。",
            "",
            f"全同级任务数：{report['all_same_grade_tasks']['count']}；机器分差中位数："
            f"{_number(report['all_same_grade_tasks']['median'])}；P90："
            f"{_number(report['all_same_grade_tasks']['p90'])}。",
            "",
            f"全 A 任务数：{report['all_a_tasks']['count']}；机器分差中位数："
            f"{_number(report['all_a_tasks']['median'])}；P90："
            f"{_number(report['all_a_tasks']['p90'])}。",
            "",
        ]
    )
    return "\n".join(lines)


def build_run_calibration(
    run_dir: Path,
    evaluation_id: str,
    reviewer_id: str,
    calibration_id: str,
) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    output_dir = run_dir / "calibrations" / calibration_id
    if output_dir.exists():
        raise FileExistsError(f"calibration_id already exists: {calibration_id}")
    store = LocalArtifactStore(run_dir)
    workspace = load_review_workspace(run_dir, reviewer_id)
    observations: list[CalibrationObservation] = []
    for item in workspace["tasks"]:
        task = item["task"]
        if task["source"]["split"] != "calibration":
            continue
        for candidate in item["candidates"]:
            review = candidate.get("review")
            if not review or review["grade"] == "Skip":
                continue
            metric_path = store.path(
                f"evaluations/{evaluation_id}/metrics/{candidate['candidate_id']}.json"
            )
            metric = json.loads(metric_path.read_text(encoding="utf-8"))["metrics"]
            quality = metric.get("quality_score")
            if quality is None:
                continue
            observations.append(
                CalibrationObservation(
                    task_id=task["task_id"],
                    candidate_id=candidate["candidate_id"],
                    method_id=candidate["method_id"],
                    human_grade=review["grade"],
                    machine_quality=float(quality),
                )
            )
    report = compute_grade_calibration(observations)
    report.update(
        {
            "calibration_id": calibration_id,
            "run_id": workspace["run_id"],
            "evaluation_id": evaluation_id,
            "reviewer_id": reviewer_id,
        }
    )
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "calibration.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "calibration_report.md").write_text(
        calibration_markdown(report), encoding="utf-8"
    )
    return report
