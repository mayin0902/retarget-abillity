from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from retarget_agent.calibration import (
    CalibrationObservation,
    calibration_markdown,
    compute_grade_calibration,
)

GRADES = {"A", "B", "C", "D"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Freeze a provisional Movie60 human-review calibration report."
    )
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--report-id", required=True)
    parser.add_argument("--output-root", type=Path)
    args = parser.parse_args()

    source = args.workspace.resolve() / "all60" / "candidate-review.csv"
    output_root = (
        args.output_root.resolve()
        if args.output_root
        else args.workspace.resolve() / "reports"
    )
    output = output_root / args.report_id
    if output.exists():
        raise FileExistsError(f"report directory already exists: {output}")

    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        all_rows = list(csv.DictReader(handle))
    if len(all_rows) != 420:
        raise ValueError(f"expected 420 candidate rows, found {len(all_rows)}")

    reviewed = [row for row in all_rows if row.get("human_grade", "").strip() in GRADES]
    by_task: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in reviewed:
        by_task[row["task_id"]].append(row)
    incomplete = {task_id: len(rows) for task_id, rows in by_task.items() if len(rows) != 7}
    if incomplete:
        raise ValueError(f"reviewed tasks must contain exactly seven candidates: {incomplete}")
    if any(row.get("human_confirmed") != "true" for row in reviewed):
        raise ValueError("every reviewed candidate must be human_confirmed=true")

    observations = [
        CalibrationObservation(
            task_id=row["task_id"],
            candidate_id=row["candidate_id"],
            method_id=row["method"],
            human_grade=row["human_grade"],
            machine_quality=float(row["rule_quality"]),
        )
        for row in reviewed
    ]
    report = compute_grade_calibration(observations)
    grade_counts = Counter(row["human_grade"] for row in reviewed)
    pass_count = grade_counts["A"] + grade_counts["B"]
    report.update(
        {
            "report_id": args.report_id,
            "status": "provisional",
            "source": source.relative_to(args.workspace.resolve()).as_posix(),
            "source_sha256": _sha256(source),
            "total_task_count": 60,
            "total_candidate_count": 420,
            "reviewed_task_count": len(by_task),
            "reviewed_candidate_count": len(reviewed),
            "human_grade_counts": {grade: grade_counts[grade] for grade in "ABCD"},
            "human_ab_pass_count": pass_count,
            "human_ab_pass_rate": pass_count / len(reviewed) if reviewed else None,
            "reviewed_task_ids": sorted(by_task),
            "caveat": (
                "This is a provisional partial-sample calibration, not final Movie60 accuracy."
            ),
        }
    )

    output.mkdir(parents=True, exist_ok=False)
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    calibration = calibration_markdown(report).splitlines()
    markdown = [
        "# Movie60 人工评审阶段报告",
        "",
        f"- 已完成任务：{len(by_task)}/60；候选：{len(reviewed)}/420。",
        f"- 人工 A/B/C/D：{grade_counts['A']}/{grade_counts['B']}/"
        f"{grade_counts['C']}/{grade_counts['D']}。",
        f"- 人工 A+B：{pass_count}/{len(reviewed)}（"
        f"{pass_count / len(reviewed):.1%}）。",
        "- 当前只覆盖已完成小样本，以下结果是阶段性诊断，不是最终准确率。",
        "- 原始可变评审表不被覆盖；本目录是带 SHA256 的不可变快照。",
        "",
        *calibration[2:],
    ]
    (output / "calibration_report.md").write_text(
        "\n".join(markdown) + "\n", encoding="utf-8"
    )
    fields = list(all_rows[0])
    with (output / "reviewed-candidates.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(sorted(reviewed, key=lambda row: (row["task_id"], int(row["rule_rank"]))))
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
