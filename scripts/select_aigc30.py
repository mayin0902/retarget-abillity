"""Freeze a stratified 30-task public benchmark for paid AIGC comparison."""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "datasets" / "retarget_square_public_v2"
RUNS = {
    "pilot60": (
        ROOT / "runs" / "square-public-v2-pilot60-20260812",
        "auto-proxy-v1p1-pilot60-20260812",
        "agent-qwen3vl4b-conditional-pilot-v1",
    ),
    "heldout240": (
        ROOT / "runs" / "square-public-v2-heldout240-20260812",
        "auto-proxy-v1p1-heldout240-20260812",
        "agent-qwen3vl4b-conditional-heldout-v1",
    ),
}
QUOTAS = {
    "chinese_dense_poster": 5,
    "single_product_promo": 4,
    "multi_product_commercial": 4,
    "multi_person": 4,
    "portrait": 4,
    "landscape_architecture_structure": 4,
    "complex_mixed": 5,
}


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _metric_rows(run: Path, evaluation_id: str, task_id: str) -> list[dict[str, Any]]:
    base = run / "evaluations" / evaluation_id / "metrics"
    rows = []
    for path in base.glob(f"{task_id}--*.json"):
        payload = _read_json(path)
        rows.append(payload)
    if len(rows) != 4:
        raise RuntimeError(f"{task_id}: expected four metrics, got {len(rows)}")
    return rows


def main() -> None:
    manifest_path = DATASET / "full300_source_manifest.csv"
    with manifest_path.open("r", encoding="utf-8-sig", newline="") as handle:
        sources = list(csv.DictReader(handle))
    candidates: list[dict[str, Any]] = []
    for source in sources:
        split = source["split"]
        run, evaluation_id, agent_run_id = RUNS[split]
        task_id = f"{source['source_id']}__square-1024x1024"
        metrics = _metric_rows(run, evaluation_id, task_id)
        scores = [float(item["metrics"]["quality_score"]) for item in metrics]
        decision = _read_json(run / "agent-runs" / agent_run_id / "decisions" / f"{task_id}.json")
        candidates.append(
            {
                **source,
                "task_id": task_id,
                "source_run_id": run.name,
                "evaluation_id": evaluation_id,
                "qwen4_agent_run_id": agent_run_id,
                "best_traditional_score": max(scores),
                "traditional_score_spread": max(scores) - min(scores),
                "qwen4_route_action": decision["route_action"],
                "qwen4_selected_candidate_id": decision["selected_candidate_id"],
            }
        )

    selected: list[dict[str, Any]] = []
    for scene, quota in QUOTAS.items():
        pool = [item for item in candidates if item["scene_category"] == scene]
        required = [item for item in pool if item["qwen4_route_action"] == "CALL_EXTERNAL_AIGC"]
        chosen = list(required)
        tiers = ("aspect_extreme", "aspect_hard_2", "aspect_hard_1")
        tier_index = 0
        while len(chosen) < quota:
            tier = tiers[tier_index % len(tiers)]
            available = [
                item for item in pool if item not in chosen and item["difficulty_tier"] == tier
            ]
            if available:
                chosen.append(
                    min(
                        available,
                        key=lambda item: (
                            float(item["best_traditional_score"]),
                            -float(item["traditional_score_spread"]),
                            item["task_id"],
                        ),
                    )
                )
            tier_index += 1
            if tier_index > 20 and len(chosen) < quota:
                remaining = [item for item in pool if item not in chosen]
                if not remaining:
                    raise RuntimeError(f"insufficient candidates for {scene}")
                chosen.append(min(remaining, key=lambda item: item["best_traditional_score"]))
        selected.extend(chosen[:quota])

    if len(selected) != 30 or len({item["task_id"] for item in selected}) != 30:
        raise RuntimeError("selection is not exactly 30 unique tasks")
    if Counter(item["scene_category"] for item in selected) != Counter(QUOTAS):
        raise RuntimeError("scene quotas do not match")
    if not all(item["public_release_eligible"].lower() == "true" for item in selected):
        raise RuntimeError("selection contains a source not approved for public release")
    if not all(item["license_review_status"] == "approved" for item in selected):
        raise RuntimeError("selection contains an unaudited license")
    if not all(item["content_safety_status"] == "approved" for item in selected):
        raise RuntimeError("selection contains unapproved content")

    output_yaml = DATASET / "aigc30_selection.yaml"
    output_csv = DATASET / "aigc30_source_audit.csv"
    payload = {
        "benchmark_id": "retarget_square_aigc30_v1",
        "target": "1024x1024",
        "selection_policy": {
            "source_benchmark": "retarget_square_public_v2",
            "quotas": QUOTAS,
            "priority": [
                "include every Qwen4B CALL_EXTERNAL_AIGC task",
                "cover all seven scenes",
                "round-robin extreme/hard2/hard1",
                "prefer lower best-traditional score, then larger method spread",
            ],
            "data_egress_authorization": "user_explicit_2026-08-12_aigc30",
            "note": (
                "This benchmark-specific authorization does not mutate the original Full300 "
                "api_egress_allowed field. Every selected source is public-release eligible, "
                "license-audited and content-safety approved."
            ),
        },
        "tasks": [
            {
                "task_id": item["task_id"],
                "source_id": item["source_id"],
                "split": item["split"],
                "scene_category": item["scene_category"],
                "difficulty_tier": item["difficulty_tier"],
                "source_run_id": item["source_run_id"],
                "evaluation_id": item["evaluation_id"],
                "qwen4_agent_run_id": item["qwen4_agent_run_id"],
                "best_traditional_score": item["best_traditional_score"],
                "traditional_score_spread": item["traditional_score_spread"],
                "qwen4_route_action": item["qwen4_route_action"],
            }
            for item in selected
        ],
    }
    output_yaml.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    audit_fields = [
        "task_id",
        "source_id",
        "split",
        "scene_category",
        "difficulty_tier",
        "official_source",
        "source_url",
        "license",
        "license_evidence_url",
        "author",
        "attribution",
        "materialized_sha256",
        "public_release_eligible",
        "original_api_egress_allowed",
        "aigc30_api_egress_allowed",
        "egress_authorization",
        "license_review_status",
        "content_safety_status",
        "personality_rights_status",
        "trademark_status",
        "qwen4_route_action",
    ]
    with output_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=audit_fields)
        writer.writeheader()
        for item in selected:
            writer.writerow(
                {
                    **{field: item.get(field, "") for field in audit_fields},
                    "original_api_egress_allowed": item["api_egress_allowed"],
                    "aigc30_api_egress_allowed": "true",
                    "egress_authorization": "user_explicit_2026-08-12_aigc30",
                }
            )
    print(
        json.dumps(
            {
                "tasks": len(selected),
                "scenes": dict(Counter(item["scene_category"] for item in selected)),
                "difficulty": dict(Counter(item["difficulty_tier"] for item in selected)),
                "qwen4_external_requests": sum(
                    item["qwen4_route_action"] == "CALL_EXTERNAL_AIGC" for item in selected
                ),
                "original_egress_true": sum(
                    item["api_egress_allowed"].lower() == "true" for item in selected
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
