from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _proxy_grade(metric: dict[str, Any]) -> str:
    return {"proxy_a": "A", "proxy_b": "B", "proxy_c": "C"}.get(
        str(metric.get("proxy_grade")), "N/A"
    )


def _rate(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    grades = [str(row[key]) for row in rows]
    passed = sum(grade in {"A", "B"} for grade in grades)
    return {
        "denominator": len(grades),
        "passed": passed,
        "pass_rate": passed / len(grades) if grades else None,
        "grade_counts": {grade: grades.count(grade) for grade in ("A", "B", "C", "D", "N/A")},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--evaluation-id", required=True)
    parser.add_argument("--agent-run-id", required=True)
    parser.add_argument("--strict-run-id", required=True)
    parser.add_argument("--plan-id", required=True)
    parser.add_argument("--seedream-review-id", required=True)
    parser.add_argument("--report-id", required=True)
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    output = run_dir / "benchmarks" / args.report_id
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    plan = _json(run_dir / "external-generation" / "plans" / args.plan_id / "plan.json")
    entries = {str(item["task_id"]): item for item in plan["entries"]}
    agent_decisions = run_dir / "agent-runs" / args.agent_run_id / "decisions"
    strict_decisions = run_dir / "strict-reviews" / args.strict_run_id / "decisions"
    seedream_metrics = run_dir / "external-generation" / "evaluation" / "metrics"
    seedream_reviews = (
        run_dir / "external-generation" / "strict-reviews" / args.seedream_review_id / "reviews"
    )
    rows: list[dict[str, Any]] = []
    for task_id, entry in entries.items():
        rule_id = str(entry["rule_selected_candidate_id"])
        rule_metric = _json(
            run_dir / "evaluations" / args.evaluation_id / "metrics" / f"{rule_id}.json"
        )["metrics"]
        agent_decision = _json(agent_decisions / f"{task_id}.json")
        agent_id = str(agent_decision["selected_candidate_id"])
        agent_metric = _json(
            run_dir / "evaluations" / args.evaluation_id / "metrics" / f"{agent_id}.json"
        )["metrics"]
        agent_strict = _json(strict_decisions / f"{task_id}.json")["selected_grade"]
        result_path = run_dir / "external-generation" / "results" / f"{task_id}.json"
        result = _json(result_path) if result_path.is_file() else None
        aigc_metric_path = seedream_metrics / f"{task_id}--seedream--v1.json"
        aigc_metric = _json(aigc_metric_path)["metrics"] if aigc_metric_path.is_file() else None
        aigc_review_path = seedream_reviews / f"{task_id}.json"
        aigc_review = _json(aigc_review_path) if aigc_review_path.is_file() else None
        aigc_strict = (
            str(aigc_review["invocation"]["review"]["overall_grade"])
            if aigc_review is not None
            else "N/A"
        )
        aigc_proxy = _proxy_grade(aigc_metric) if aigc_metric is not None else "N/A"
        aigc_proxy_pass = aigc_proxy in {"A", "B"}
        aigc_strict_pass = aigc_strict in {"A", "B"}
        grade_rank = {"A": 0, "B": 1, "C": 2, "D": 3, "N/A": 4}
        rule_adopts_aigc = bool(
            entry["rule_trigger"] and aigc_strict_pass and aigc_proxy_pass
        )
        agent_adopts_aigc = bool(
            entry["qwen_trigger"]
            and aigc_strict_pass
            and grade_rank[aigc_strict] < grade_rank[str(agent_strict)]
        )
        rows.append(
            {
                "task_id": task_id,
                "selected_for_paid_generation": bool(entry["selected_for_paid_generation"]),
                "aigc_status": result.get("status") if result else "not_run",
                "rule_proxy_grade": _proxy_grade(rule_metric),
                "agent_proxy_grade": _proxy_grade(agent_metric),
                "agent_strict_grade": str(agent_strict),
                "aigc_proxy_grade": aigc_proxy,
                "aigc_strict_grade": aigc_strict,
                "rule_aigc_proxy_grade": (
                    aigc_proxy if aigc_proxy_pass else _proxy_grade(rule_metric)
                ),
                "agent_aigc_proxy_grade": (
                    aigc_proxy if aigc_proxy_pass else _proxy_grade(agent_metric)
                ),
                "agent_aigc_strict_grade": (
                    aigc_strict if agent_adopts_aigc else str(agent_strict)
                ),
                "rule_aigc_deployable_proxy_grade": (
                    aigc_proxy if rule_adopts_aigc else _proxy_grade(rule_metric)
                ),
                "agent_aigc_deployable_proxy_grade": (
                    aigc_proxy if agent_adopts_aigc else _proxy_grade(agent_metric)
                ),
                "rule_adopts_aigc": rule_adopts_aigc,
                "agent_adopts_aigc": agent_adopts_aigc,
            }
        )
    paid = [row for row in rows if row["selected_for_paid_generation"]]
    generated = [row for row in paid if row["aigc_status"] == "success"]
    report = {
        "schema_version": "1.0",
        "report_id": args.report_id,
        "run_id": run_dir.name,
        "pass_definition": "A_or_B",
        "proxy_baseline_full60": {
            "rule": _rate(rows, "rule_proxy_grade"),
            "agent": _rate(rows, "agent_proxy_grade"),
        },
        "proxy_hypothetical_upper_bound_full60": {
            "rule_aigc": _rate(rows, "rule_aigc_proxy_grade"),
            "agent_aigc": _rate(rows, "agent_aigc_proxy_grade"),
        },
        "strict_gated_route_proxy_full60": {
            "rule_aigc": _rate(rows, "rule_aigc_deployable_proxy_grade"),
            "agent_aigc": _rate(rows, "agent_aigc_deployable_proxy_grade"),
            "rule_aigc_adoption_count": sum(row["rule_adopts_aigc"] for row in rows),
            "agent_aigc_adoption_count": sum(row["agent_adopts_aigc"] for row in rows),
        },
        "strict_visual_full60": {
            "agent": _rate(rows, "agent_strict_grade"),
            "agent_aigc": _rate(rows, "agent_aigc_strict_grade"),
            "rule": None,
            "note": "Rule Top1 was not independently high-resolution-reviewed for all 60 tasks.",
        },
        "aigc_paid_subset": {
            "planned_denominator": len(paid),
            "generated_count": len(generated),
            "generation_success_rate": len(generated) / len(paid) if paid else None,
            "proxy_generated_only": _rate(generated, "aigc_proxy_grade"),
            "proxy_end_to_end_over_planned": _rate(paid, "aigc_proxy_grade"),
            "strict_generated_only": _rate(generated, "aigc_strict_grade"),
            "strict_end_to_end_over_planned": _rate(paid, "aigc_strict_grade"),
        },
        "rows": rows,
    }
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        "# Movie Visual 60 路线 A+B 通过率",
        "",
        "A/B 视为通过。Proxy 基线和高清严格复核分开报告，不把自动分冒充人工或高清结论。",
        "生成失败在 AIGC planned denominator 中计为未通过，不从分母删除。生成时延不计入效率比较。",
        "",
        "## 全 60｜Proxy 基线",
        "",
        "| 路线 | 通过 | 分母 | A+B率 |",
        "| --- | ---: | ---: | ---: |",
    ]
    for name, values in report["proxy_baseline_full60"].items():
        lines.append(
            f"| {name} | {values['passed']} | {values['denominator']} | "
            f"{values['pass_rate']:.1%} |"
        )
    lines.extend(
        [
            "",
            "## 全 60｜严格复核门禁后的可部署路由（仍列 Proxy 分）",
            "",
            "AIGC 只有在高清复核 A/B，且超过传统回退时才可被采用。",
            "",
            "| 路线 | 通过 | 分母 | A+B率 | 采用AIGC |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    gated = report["strict_gated_route_proxy_full60"]
    for name in ("rule_aigc", "agent_aigc"):
        values = gated[name]
        lines.append(
            f"| {name} | {values['passed']} | {values['denominator']} | "
            f"{values['pass_rate']:.1%} | {gated[name + '_adoption_count']} |"
        )
    lines.extend(
        [
            "",
            "## 高清视觉口径",
            "",
            "Rule 未对全 60 独立高清复核，因此不伪造 Rule 严格通过率。",
            "",
            "| 路线 | 通过 | 分母 | A+B率 |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    for name in ("agent", "agent_aigc"):
        values = report["strict_visual_full60"][name]
        lines.append(
            f"| {name} | {values['passed']} | {values['denominator']} | "
            f"{values['pass_rate']:.1%} |"
        )
    aigc = report["aigc_paid_subset"]
    lines.extend(
        [
            "",
            "## AIGC 付费子集",
            "",
            f"- 计划：{aigc['planned_denominator']}；成功生成：{aigc['generated_count']}；"
            f"生成成功率：{aigc['generation_success_rate']:.1%}；",
            f"- Proxy A+B（成功图分母）：{aigc['proxy_generated_only']['pass_rate']:.1%}；",
            f"- Proxy A+B（计划全分母，失败计未通过）："
            f"{aigc['proxy_end_to_end_over_planned']['pass_rate']:.1%}。",
            f"- 高清严格 A+B（成功图分母）："
            f"{aigc['strict_generated_only']['pass_rate']:.1%}；",
            f"- 高清严格 A+B（计划全分母，失败计未通过）："
            f"{aigc['strict_end_to_end_over_planned']['pass_rate']:.1%}。",
        ]
    )
    (output / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "tasks": len(rows)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
