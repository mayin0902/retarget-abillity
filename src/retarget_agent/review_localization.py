"""Chinese presentation layer for stable machine-review schemas and reason codes."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

REASON_CODE_ZH = {
    "agent_improvement": "Agent候选有明确改进",
    "agent_aigc_request_rejected_non_c": "当前等级未达到AIGC调用条件",
    "all_traditional_unusable": "所有传统候选均不可用",
    "always_on": "本任务始终调用Agent进行排序",
    "challenger_alias_derived_from_ranking": "挑战候选由Agent完整排名确定",
    "challenger_content_loss": "挑战候选丢失内容",
    "challenger_count_loss": "挑战候选主体数量下降",
    "challenger_for_pair_review": "挑战候选进入配对复核",
    "challenger_geometry_improved": "挑战候选几何形态更自然",
    "challenger_subject_preserved": "挑战候选保留主体",
    "challenger_text_loss": "挑战候选丢失文字",
    "challenger_text_preserved": "挑战候选保留文字",
    "composition_damage": "构图受损",
    "critical_regressions": "存在关键指标退化",
    "critical_text_missing": "关键文字缺失",
    "external_aigc_disabled": "本轮禁止调用外部AIGC",
    "face_body_deformation": "人物脸部或身体变形",
    "global_stretch": "全局拉伸明显",
    "local_deformation": "存在局部扭曲或接缝",
    "logo_count_preservation": "Logo数量保留异常",
    "logo_damage": "Logo受损",
    "logo_preservation": "Logo保留情况存在问题",
    "missing_content": "关键内容缺失",
    "no_composition_loss": "构图内容没有缺失",
    "no_contradictory_evidence": "视觉与指标证据不矛盾",
    "no_critical_defects": "未发现关键缺陷",
    "no_critical_regressions": "未发现关键指标退化",
    "no_face_body_deformation": "未发现人物脸部或身体变形",
    "no_face_or_body_deformation": "未发现人物脸部或身体变形",
    "no_global_stretch": "未发现明显全局拉伸",
    "no_material_improvement": "Agent候选没有实质改进",
    "no_missing_content": "未发现关键内容缺失",
    "no_product_logo_preservation": "原图无商品或Logo可供比较",
    "no_seam_damage": "未发现接缝损伤",
    "no_seam_or_mesh_damage": "未发现接缝或网格损伤",
    "no_structural_deformation": "未发现结构变形",
    "no_structure_damage": "未发现结构损伤",
    "no_text_or_logo": "原图无文字或Logo",
    "no_text_or_logos_present": "原图无文字或Logo",
    "no_text_preservation": "原图无文字可供比较",
    "no_text_product_logo": "原图无文字、商品或Logo",
    "no_visible_defects": "未发现明显可见缺陷",
    "no_visible_improvement": "未发现明显视觉改进",
    "product_count_preservation": "商品数量保留异常",
    "product_logo_missing": "商品或Logo缺失",
    "rule_content_missing": "Rule候选缺失内容",
    "rule_default_applies": "证据不足时保留Rule默认选择",
    "rule_defects": "Rule候选存在可见缺陷",
    "rule_local_deformation": "Rule候选存在局部扭曲",
    "rule_retained_no_clear_gain": "没有明确增益，保留Rule选择",
    "rule_visible_stretch": "Rule候选存在明显拉伸",
    "same_candidate": "Rule与Agent提出的是同一候选",
    "severe_global_stretch": "全局拉伸严重",
    "structure_deformation": "结构线或物体形状变形",
    "structure_line_similarity": "结构线相似度异常",
    "text_damage": "文字缺失、不可读或变形",
    "text_preservation": "文字保留情况存在问题",
    "tie_in_composition_preservation": "两者构图保留程度相当",
    "visual_indistinguishable": "两者视觉差异不足以区分",
    "visual_integrity_score_conflict": "视觉判断与完整性指标冲突",
}

_DIMENSIONS = (
    ("subject", "主体"),
    ("face_body", "人物"),
    ("text", "文字"),
    ("product_logo", "商品/Logo"),
    ("structure", "结构"),
    ("composition", "构图"),
)


def localize_reason_codes(codes: Iterable[str]) -> list[str]:
    """Translate stable English reason-code identifiers for human display."""

    return [REASON_CODE_ZH.get(code, f"其他问题（{code}）") for code in codes if code]


def _dimension_reason(label: str, item: dict[str, Any]) -> str:
    grade = str(item.get("grade") or "NA")
    if not item.get("applicable") or grade == "NA":
        return f"{label}不适用：原图中没有该类元素。"
    localized = localize_reason_codes(str(code) for code in item.get("reason_codes", []))
    if localized:
        detail = "、".join(localized)
    elif grade == "A":
        detail = "未发现影响上传的明显问题"
    elif grade == "B":
        detail = "存在轻微可见问题，但仍可直接使用"
    elif grade == "C":
        detail = "存在需要明显修复或重做的问题"
    else:
        detail = "存在导致图片不可使用的严重问题"
    return f"{label}{grade}：{detail}。"


def localize_strict_review(review: dict[str, Any]) -> dict[str, Any]:
    """Create a Chinese rendition without altering the frozen raw model output."""

    grade = str(review.get("overall_grade") or "")
    directly_usable = bool(review.get("directly_usable"))
    dimensions = {
        key: _dimension_reason(label, dict(review.get(key) or {})) for key, label in _DIMENSIONS
    }
    problems = []
    for key, label in _DIMENSIONS:
        item = dict(review.get(key) or {})
        if item.get("applicable") and item.get("grade") in {"B", "C", "D"}:
            codes = localize_reason_codes(str(code) for code in item.get("reason_codes", []))
            detail = "、".join(codes) if codes else "存在可见问题"
            problems.append(f"{label}（{item.get('grade')}）：{detail}")
    if grade == "A":
        summary = "建议A：整体自然完整，可直接上传；各适用维度未发现明显缺陷。"
    elif grade == "B":
        summary = "建议B：整体仍可上传，但存在轻微问题"
    elif grade == "C":
        summary = "建议C：存在明显问题，需要修复或重做"
    else:
        summary = "建议D：存在严重问题，当前不可使用"
    if problems:
        summary += "；主要依据：" + "；".join(problems)
    summary += "。"
    return {
        "overall_grade": grade,
        "directly_usable": directly_usable,
        "confidence": review.get("confidence"),
        "summary": summary,
        "dimensions": dimensions,
    }


def localize_pair_review(review: dict[str, Any]) -> dict[str, Any]:
    """Render a Rule-vs-Agent pair decision in concise Simplified Chinese."""

    preferred = str(review.get("preferred") or "TIE")
    preference = {
        "RULE": "保留Rule候选",
        "AGENT": "选择Agent候选",
        "TIE": "两者相当，按默认策略保留Rule",
    }.get(preferred, preferred)
    rule_defects = localize_reason_codes(str(v) for v in review.get("rule_defects", []))
    agent_defects = localize_reason_codes(str(v) for v in review.get("agent_defects", []))
    codes = localize_reason_codes(str(v) for v in review.get("reason_codes", []))
    clauses = [
        f"配对结论：{preference}",
        (
            "存在足以改变选择的清晰视觉证据"
            if review.get("clear_visual_evidence")
            else "没有足以改变选择的清晰视觉证据"
        ),
        (
            "视觉与传统指标证据一致"
            if review.get("evidence_consistent")
            else "视觉与传统指标证据存在矛盾"
        ),
    ]
    if rule_defects:
        clauses.append("Rule问题：" + "、".join(rule_defects))
    if agent_defects:
        clauses.append("Agent问题：" + "、".join(agent_defects))
    if codes:
        clauses.append("补充依据：" + "、".join(codes))
    return {
        "preferred": preferred,
        "clear_visual_evidence": bool(review.get("clear_visual_evidence")),
        "evidence_consistent": bool(review.get("evidence_consistent")),
        "confidence": review.get("confidence"),
        "summary": "；".join(clauses) + "。",
    }


__all__ = [
    "REASON_CODE_ZH",
    "localize_pair_review",
    "localize_reason_codes",
    "localize_strict_review",
]
