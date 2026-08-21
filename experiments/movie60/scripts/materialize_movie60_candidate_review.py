from __future__ import annotations

import csv
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from retarget_agent.review_localization import (
    localize_pair_review,
    localize_reason_codes,
    localize_strict_review,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKSPACE = REPO_ROOT / "deliverables" / "movie60-review"
RUN = REPO_ROOT / "runs" / "movie60-square-v1-20260818"
EVALUATION = RUN / "evaluations" / "movie60-auto-strict-v1p2-20260818" / "metrics"


def _json(path: Path) -> dict[str, Any] | list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _percent(value: Any) -> str:
    return "未测" if value is None else f"{float(value) * 100:.1f}%"


def _rule_reason(rank: dict[str, Any], metrics: dict[str, Any]) -> str:
    parts = [
        f"传统代理分 {float(rank['quality']):.2f}，Rule 排名 {rank['rank']}/7",
        f"内容保真 {_percent(metrics.get('content_fidelity_score'))}",
        f"视觉完整 {_percent(metrics.get('visual_integrity_score'))}",
        f"构图 {_percent(metrics.get('composition_score'))}",
        f"结构线 {_percent(metrics.get('structure_line_similarity'))}",
        f"变换安全 {_percent(metrics.get('transform_safety_score'))}",
    ]
    for label, key in (
        ("OCR字符召回", "ocr_character_recall"),
        ("人物数量保留", "person_count_preservation"),
        ("人脸数量保留", "face_count_preservation"),
        ("商品数量保留", "product_count_preservation"),
        ("Logo数量保留", "logo_count_preservation"),
    ):
        value = metrics.get(key)
        if value is not None:
            parts.append(f"{label} {_percent(value)}")
    hard = str(metrics.get("hard_failures") or "").strip()
    regressions = str(metrics.get("critical_regressions") or "").strip()
    if hard:
        parts.append(f"硬失败：{hard}")
    if regressions:
        parts.append(f"关键退化：{regressions}")
    return "；".join(parts) + "。这些是传统可计算指标，不是人工金标。"


def _review_summary(review_path: Path) -> tuple[str, str, str, str]:
    if not review_path.is_file():
        return "", "", "", ""
    payload = _json(review_path)
    assert isinstance(payload, dict)
    review = payload.get("invocation", {}).get("review", {})
    localized = localize_strict_review(review)
    grade = str(review.get("overall_grade") or "")
    usable = str(review.get("directly_usable")).lower()
    confidence = str(review.get("confidence") or "")
    dimensions = [str(value).rstrip("。") for value in localized["dimensions"].values()]
    reason = "；".join([str(localized["summary"]).rstrip("。"), *dimensions]) + "。"
    return grade, usable, confidence, reason


def main() -> None:
    all60 = WORKSPACE / "all60"
    summary_rows = _rows(all60 / "summary.csv")
    if len(summary_rows) != 60:
        raise RuntimeError(f"expected 60 tasks, got {len(summary_rows)}")

    existing = {
        (row["task_id"], row["method"]): row for row in _rows(all60 / "candidate-review.csv")
    }
    legacy = {row["task_id"]: row for row in _rows(all60 / "review.csv")}
    focus_advice = {row["task_id"]: row for row in _rows(WORKSPACE / "focus20" / "codex.csv")}
    output: list[dict[str, str]] = []

    for task in summary_rows:
        task_id = task["task_id"]
        task_dir = all60 / "tasks" / task_id
        machine = task_dir / "evidence" / "machine"
        ranking = _json(machine / "rule-ranking.json")
        decision = _json(machine / "decision.json")
        overview = _json(machine / "qwen-overview-decision.json")
        pair = _json(machine / "reviews" / "pair-review.json")
        assert isinstance(ranking, list)
        assert isinstance(decision, dict)
        assert isinstance(overview, dict)
        assert isinstance(pair, dict)
        if len(ranking) != 7:
            raise RuntimeError(f"{task_id}: expected 7 candidates")

        agent_ranking = list(overview.get("candidate_ranking") or [])
        pair_review = dict(pair.get("invocation", {}).get("review", {}))
        localized_pair = localize_pair_review(pair_review)
        pair_summary = str(localized_pair["summary"])
        rule_review_payload = _json(machine / "reviews" / "rule_top1.json")
        assert isinstance(rule_review_payload, dict)
        rule_review_zh = localize_strict_review(
            dict(rule_review_payload.get("invocation", {}).get("review", {}))
        )
        agent_review_path = machine / "reviews" / "agent_top1.json"
        agent_review_zh = None
        if agent_review_path.is_file():
            agent_review_payload = _json(agent_review_path)
            assert isinstance(agent_review_payload, dict)
            agent_review_zh = localize_strict_review(
                dict(agent_review_payload.get("invocation", {}).get("review", {}))
            )
        _write_json(
            machine / "reviews" / "agent-review.zh-CN.json",
            {
                "schema_version": "1.0",
                "task_id": task_id,
                "language": "zh-CN",
                "translation_basis": "structured_grades_and_reason_codes",
                "raw_evidence_preserved": True,
                "rule_top1": rule_review_zh,
                "agent_top1": agent_review_zh,
                "pair_review": localized_pair,
            },
        )
        candidate_dir = task_dir / "candidates"
        candidate_dir.mkdir(exist_ok=True)

        for item in ranking:
            method = str(item["method"])
            candidate_id = str(item["candidate_id"])
            source_image = RUN / "candidates" / task_id / method / "candidate.png"
            target_image = candidate_dir / f"{method}.png"
            if not source_image.is_file():
                raise RuntimeError(f"missing candidate image: {source_image}")
            if not target_image.is_file() or _sha256(target_image) != _sha256(source_image):
                shutil.copy2(source_image, target_image)

            metric_payload = _json(EVALUATION / f"{candidate_id}.json")
            assert isinstance(metric_payload, dict)
            metrics = dict(metric_payload["metrics"])
            agent_rank = (
                agent_ranking.index(candidate_id) + 1 if candidate_id in agent_ranking else None
            )
            roles = []
            review_path: Path | None = None
            if candidate_id == decision.get("rule_top1_candidate_id"):
                roles.append("Rule Top1")
                review_path = machine / "reviews" / "rule_top1.json"
            if candidate_id == decision.get("agent_proposed_candidate_id"):
                roles.append("Agent建议Top1")
                possible = machine / "reviews" / "agent_top1.json"
                if possible.is_file():
                    review_path = possible
            if candidate_id == decision.get("selected_candidate_id"):
                roles.append("最终选择")

            if review_path is not None:
                agent_grade, agent_usable, agent_confidence, agent_reason = _review_summary(
                    review_path
                )
                if pair_summary:
                    agent_reason = f"{agent_reason.rstrip('。')}；{pair_summary}"
                agent_scope = "高清单图复核"
            else:
                agent_grade = ""
                agent_usable = ""
                agent_confidence = ""
                agent_scope = "七候选总览排序"
                agent_reason = (
                    f"Agent 在七候选总览中排第 {agent_rank}/7；该候选未进入 "
                    "Rule Top1 与 Agent建议Top1 的高清复核，所以没有独立 A/B/C/D 判分。"
                )

            prior = existing.get((task_id, method), {})
            if not prior and method == task["final_method"]:
                old = legacy.get(task_id, {})
                prior = {
                    "human_grade": old.get("human_grade", ""),
                    "human_reason": old.get("human_reason", ""),
                    "human_issue_codes": old.get("human_issue_codes", ""),
                    "human_confirmed": old.get("human_confirmed", "false"),
                    "reviewer_id": old.get("reviewer_id", ""),
                    "updated_at": old.get("updated_at", ""),
                }

            advice_grade = prior.get("model_advice_grade", "")
            advice_reason = prior.get("model_advice_reason", "")
            advice_scope = prior.get("model_advice_scope", "")
            advice_source = prior.get("model_advice_source", "")
            focused = focus_advice.get(task_id)
            if not advice_grade and focused is not None:
                if method == focused["rule_method"]:
                    advice_grade = focused["codex_rule_grade"]
                elif method == focused["agent_method"]:
                    advice_grade = focused["codex_agent_grade"]
                if advice_grade:
                    advice_reason = focused["codex_reason"]
                    advice_scope = "重点样本高清人工式复核"
                    advice_source = "existing_focus20_prereview"

            output.append(
                {
                    "task_id": task_id,
                    "phase": task["phase"],
                    "scene_category": task["scene_category"],
                    "candidate_id": candidate_id,
                    "method": method,
                    "image_sha256": _sha256(target_image),
                    "rule_rank": str(item["rank"]),
                    "rule_quality": f"{float(item['quality']):.12f}",
                    "rule_grade": str(item["proxy_grade"]).removeprefix("proxy_").upper(),
                    "rule_reason": _rule_reason(item, metrics),
                    "rule_ocr_recall": ""
                    if metrics.get("ocr_character_recall") is None
                    else str(metrics["ocr_character_recall"]),
                    "rule_person_preservation": ""
                    if metrics.get("person_count_preservation") is None
                    else str(metrics["person_count_preservation"]),
                    "rule_face_preservation": ""
                    if metrics.get("face_count_preservation") is None
                    else str(metrics["face_count_preservation"]),
                    "rule_product_preservation": ""
                    if metrics.get("product_count_preservation") is None
                    else str(metrics["product_count_preservation"]),
                    "rule_logo_preservation": ""
                    if metrics.get("logo_count_preservation") is None
                    else str(metrics["logo_count_preservation"]),
                    "agent_rank": "" if agent_rank is None else str(agent_rank),
                    "agent_role": " + ".join(roles) if roles else "普通候选",
                    "agent_review_scope": agent_scope,
                    "agent_grade": agent_grade,
                    "agent_directly_usable": agent_usable,
                    "agent_confidence": agent_confidence,
                    "agent_reason": agent_reason,
                    "agent_reason_codes": ";".join(
                        str(code) for code in overview.get("reason_codes", [])
                    ),
                    "agent_reason_codes_zh": ";".join(
                        localize_reason_codes(
                            str(code) for code in overview.get("reason_codes", [])
                        )
                    ),
                    "final_selected": str(
                        candidate_id == decision.get("selected_candidate_id")
                    ).lower(),
                    "model_advice_grade": advice_grade,
                    "model_advice_reason": advice_reason,
                    "model_advice_scope": advice_scope or "待高清复核",
                    "model_advice_source": advice_source,
                    "human_grade": prior.get("human_grade", ""),
                    "human_reason": prior.get("human_reason", ""),
                    "human_issue_codes": prior.get("human_issue_codes", ""),
                    "human_confirmed": prior.get("human_confirmed", "false"),
                    "reviewer_id": prior.get("reviewer_id", ""),
                    "updated_at": prior.get("updated_at", ""),
                }
            )

    fields = list(output[0])
    _write(all60 / "candidate-review.csv", output, fields)
    if len(output) != 420 or len({(row["task_id"], row["method"]) for row in output}) != 420:
        raise RuntimeError("candidate review output is not exactly 60 x 7")
    print(f"materialized {len(output)} candidate review rows and images")


if __name__ == "__main__":
    main()
