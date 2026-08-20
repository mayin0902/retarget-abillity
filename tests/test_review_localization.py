from __future__ import annotations

from retarget_agent.review_localization import (
    localize_pair_review,
    localize_reason_codes,
    localize_strict_review,
)


def _dimension(grade: str, *codes: str, applicable: bool = True) -> dict[str, object]:
    return {
        "applicable": applicable,
        "grade": grade,
        "reason_codes": list(codes),
        "reason": "raw model text",
    }


def test_localize_strict_review_uses_codes_instead_of_raw_english() -> None:
    localized = localize_strict_review(
        {
            "overall_grade": "C",
            "directly_usable": False,
            "confidence": 0.9,
            "subject": _dimension("A"),
            "face_body": _dimension("C", "face_body_deformation"),
            "text": _dimension("B", "text_damage"),
            "product_logo": _dimension("NA", applicable=False),
            "structure": _dimension("A"),
            "composition": _dimension("C", "composition_damage"),
            "summary": "raw English summary",
        }
    )
    assert localized["overall_grade"] == "C"
    assert "人物脸部或身体变形" in localized["summary"]
    assert "文字缺失、不可读或变形" in localized["dimensions"]["text"]
    assert "raw English" not in str(localized)


def test_localize_pair_and_unknown_reason_code_are_explicit() -> None:
    localized = localize_pair_review(
        {
            "preferred": "RULE",
            "clear_visual_evidence": False,
            "evidence_consistent": True,
            "confidence": 0.8,
            "rule_defects": [],
            "agent_defects": ["critical_text_missing"],
            "reason_codes": ["rule_default_applies"],
            "summary": "raw English summary",
        }
    )
    assert "保留Rule候选" in localized["summary"]
    assert "关键文字缺失" in localized["summary"]
    assert localize_reason_codes(["new_code"]) == ["其他问题（new_code）"]
