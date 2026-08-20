from __future__ import annotations

import json

from retarget_agent.agent_proxy_reporting import (
    _resolve_visual_evidence_review,
    summarize_route_rows,
)


def test_route_summary_separates_grade_detection_from_selection_utility() -> None:
    rows = [
        {
            "proxy_best_methods": ["crop", "mesh"],
            "rule_method": "crop",
            "rule_predicted_grade": "A",
            "rule_truth_grade": "B",
        },
        {
            "proxy_best_methods": ["seam"],
            "rule_method": "mesh",
            "rule_predicted_grade": "C",
            "rule_truth_grade": "D",
        },
        {
            "proxy_best_methods": ["crop"],
            "rule_method": "crop",
            "rule_predicted_grade": "B",
            "rule_truth_grade": "C",
        },
    ]

    summary = summarize_route_rows(rows, "rule")

    assert summary["exact_grade_accuracy"] == 0
    assert summary["ab_cd_agreement"] == 2 / 3
    assert summary["cd_recall"] == 1 / 2
    assert summary["cd_precision"] == 1.0
    assert summary["selected_candidate_proxy_ab_rate"] == 1 / 3
    assert summary["proxy_best_method_hit_rate"] == 2 / 3


def test_visual_evidence_resolution_follows_nested_replays(tmp_path) -> None:
    reviews = tmp_path / "strict-reviews"
    payloads = {
        "final": {
            "complete": True,
            "source_review_run_id": "grading-patch",
            "visual_evidence_reused": True,
        },
        "grading-patch": {
            "complete": True,
            "source_review_run_id": "visual-run",
            "visual_evidence_reused": True,
        },
        "visual-run": {"complete": True},
    }
    for run_id, payload in payloads.items():
        root = reviews / run_id
        root.mkdir(parents=True)
        (root / "summary.json").write_text(json.dumps(payload), encoding="utf-8")

    run_id, root = _resolve_visual_evidence_review(tmp_path, "final")

    assert run_id == "visual-run"
    assert root == reviews / "visual-run"
