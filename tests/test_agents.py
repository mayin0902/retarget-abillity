from __future__ import annotations

import json
from pathlib import Path

import pytest

import retarget_agent.agents as agents_module
from retarget_agent.agents import (
    AgentInvocation,
    AgentMode,
    AgentReplayConfig,
    CandidateEvidence,
    JudgeAgentRequest,
    JudgeAgentResponse,
    OpenAICompatibleVisionBackend,
    RouteAction,
    decide_route,
    deterministic_ranking,
    should_call_agent,
)
from retarget_agent.models import GenerationStatus, ProxyGrade


def candidate(
    method: str,
    score: float,
    *,
    grade: ProxyGrade = ProxyGrade.B,
    hard: tuple[str, ...] = (),
) -> CandidateEvidence:
    return CandidateEvidence(
        candidate_id=f"task-1--{method}--v1",
        method_id=method,
        quality_score=score,
        proxy_grade=grade,
        technical_valid=not hard,
        hard_failures=hard,
    )


def request(*items: CandidateEvidence) -> JudgeAgentRequest:
    values = tuple(items)
    return JudgeAgentRequest(
        task_id="task-1",
        candidates=values,
        deterministic_ranking=deterministic_ranking(values),
    )


class FixtureBackend:
    agent_id = "fixture-judge"
    agent_version = "1.0.0"
    model_version = "fixture-model"

    def __init__(self, response: JudgeAgentResponse) -> None:
        self.response = response
        self.calls = 0

    def judge(self, request: JudgeAgentRequest, comparison_image: Path) -> AgentInvocation:
        del request, comparison_image
        self.calls += 1
        return AgentInvocation(response=self.response, latency_seconds=0.05)


def test_conditional_agent_skips_clear_high_quality_decision() -> None:
    value = request(candidate("crop", 91, grade=ProxyGrade.A), candidate("mesh", 72))
    config = AgentReplayConfig(mode=AgentMode.CONDITIONAL)
    called, reasons = should_call_agent(value, config)
    decision, call = decide_route(value, config)
    assert not called
    assert reasons == ()
    assert call is None
    assert decision.selected_candidate_id.endswith("--crop--v1")
    assert decision.route_action is RouteAction.USE_TRADITIONAL


def test_no_agent_rule_can_route_a_low_score_to_external_generation() -> None:
    value = request(candidate("crop", 51, grade=ProxyGrade.C), candidate("mesh", 48))
    decision, call = decide_route(
        value,
        AgentReplayConfig(
            mode=AgentMode.HARD_RANKER,
            allow_external_aigc=True,
            deterministic_fallback_threshold=58,
        ),
    )
    assert call is None
    assert decision.route_action is RouteAction.CALL_EXTERNAL_AIGC
    assert "deterministic_fallback" in decision.reason_codes


def test_empty_candidate_set_returns_failure() -> None:
    value = request()
    decision, call = decide_route(
        value,
        AgentReplayConfig(mode=AgentMode.HARD_RANKER, allow_external_aigc=True),
    )
    assert call is None
    assert decision.selected_candidate_id is None
    assert decision.route_action is RouteAction.RETURN_FAILURE
    assert "no_candidate_available" in decision.reason_codes


def test_fixed_no_agent_method_is_not_the_proxy_ranker() -> None:
    value = request(candidate("crop", 91, grade=ProxyGrade.A), candidate("mesh", 72))
    decision, call = decide_route(
        value,
        AgentReplayConfig(mode=AgentMode.HARD_RANKER, fixed_method_id="mesh"),
    )
    assert call is None
    assert decision.selected_candidate_id is not None
    assert "--mesh--" in decision.selected_candidate_id
    assert decision.deterministic_candidate_id is not None
    assert "--crop--" in decision.deterministic_candidate_id
    assert decision.changed_top1
    assert "fixed_method:mesh" in decision.reason_codes


def test_deterministic_ranker_prefers_safe_candidate_before_score() -> None:
    unsafe = candidate("crop", 95, grade=ProxyGrade.A).model_copy(
        update={"generation_status": GenerationStatus.UNSAFE}
    )
    safe = candidate("mesh", 80, grade=ProxyGrade.A)
    assert deterministic_ranking((unsafe, safe))[0] == safe.candidate_id


def test_agent_may_change_top1_and_route_only_c_to_aigc(tmp_path: Path) -> None:
    crop = candidate("crop", 70)
    mesh = candidate("mesh", 68)
    value = request(crop, mesh)
    response = JudgeAgentResponse(
        task_id=value.task_id,
        candidate_ranking=(mesh.candidate_id, crop.candidate_id),
        best_candidate_id=mesh.candidate_id,
        proxy_grade=ProxyGrade.B,
        core_content_preserved=True,
        visible_distortion="minor",
        confidence=0.82,
        reason_codes=("mesh_preserves_subject_shape",),
    )
    backend = FixtureBackend(response)
    image = tmp_path / "comparison.png"
    image.write_bytes(b"fixture")
    decision, call = decide_route(
        value,
        AgentReplayConfig(mode=AgentMode.ALWAYS_ON),
        backend=backend,
        comparison_image=image,
    )
    assert backend.calls == 1
    assert call is not None and call.success
    assert decision.changed_top1
    assert decision.selected_candidate_id == mesh.candidate_id
    assert decision.route_action is RouteAction.USE_TRADITIONAL


def test_illegal_agent_candidate_falls_back_to_hard_ranker(tmp_path: Path) -> None:
    crop = candidate("crop", 70)
    mesh = candidate("mesh", 68)
    value = request(crop, mesh)
    response = JudgeAgentResponse(
        task_id=value.task_id,
        candidate_ranking=("unknown-candidate", crop.candidate_id),
        best_candidate_id="unknown-candidate",
        proxy_grade=ProxyGrade.B,
        core_content_preserved=True,
        visible_distortion="minor",
        confidence=0.5,
    )
    backend = FixtureBackend(response)
    image = tmp_path / "comparison.png"
    image.write_bytes(b"fixture")
    decision, call = decide_route(
        value,
        AgentReplayConfig(mode=AgentMode.ALWAYS_ON),
        backend=backend,
        comparison_image=image,
    )
    assert call is not None and not call.success
    assert call.error_type == "SCHEMA_INVALID"
    assert decision.selected_candidate_id == crop.candidate_id
    assert not decision.changed_top1
    assert "invalid_agent_response" in decision.reason_codes


def test_aigc_request_is_safely_downgraded_when_agent_grade_is_not_c() -> None:
    crop = candidate("crop", 55, grade=ProxyGrade.C)
    value = request(crop)
    response = JudgeAgentResponse(
        task_id=value.task_id,
        candidate_ranking=(crop.candidate_id,),
        best_candidate_id=crop.candidate_id,
        proxy_grade=ProxyGrade.B,
        core_content_preserved=True,
        visible_distortion="minor",
        confidence=0.5,
        fallback_action=RouteAction.CALL_EXTERNAL_AIGC,
    )
    backend = FixtureBackend(response)
    decision, call = decide_route(
        value,
        AgentReplayConfig(mode=AgentMode.ALWAYS_ON, allow_external_aigc=True),
        backend=backend,
        comparison_image=Path("unused.png"),
    )
    assert call is not None and call.success
    assert decision.route_action is RouteAction.USE_TRADITIONAL
    assert "agent_aigc_request_rejected_non_c" in decision.reason_codes


def test_openai_backend_sends_vllm_json_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = request(candidate("crop", 80, grade=ProxyGrade.A))
    response_payload = {
        "schema_version": "1.0",
        "candidate_ranking": ["C0"],
        "best_candidate_alias": "C0",
        "proxy_grade": "proxy_a",
        "core_content_preserved": True,
        "visible_distortion": "none",
        "confidence": 0.9,
        "reason_codes": [],
        "fallback_action": "USE_BEST_TRADITIONAL",
    }
    captured: dict[str, object] = {}
    post_count = 0

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "choices": [{"message": {"content": json.dumps(response_payload)}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            }

    def fake_post(url: str, **kwargs: object) -> Response:
        nonlocal post_count
        post_count += 1
        captured["url"] = url
        captured.update(kwargs)
        return Response()

    monkeypatch.setattr(agents_module.requests, "post", fake_post)
    image = tmp_path / "comparison.png"
    image.write_bytes(b"fake-png-for-transport-contract")
    backend = OpenAICompatibleVisionBackend(
        base_url="http://127.0.0.1:18000/v1",
        model_version="fixture-model",
        cache_path=tmp_path / "agent-cache.json",
    )

    invocation = backend.judge(value, image)
    cached = backend.judge(value, image)

    assert invocation.response.best_candidate_id == value.deterministic_ranking[0]
    assert invocation.input_tokens == 10
    assert invocation.output_tokens == 20
    assert not invocation.cache_hit
    assert cached.cache_hit
    assert post_count == 1
    payload = captured["json"]
    assert isinstance(payload, dict)
    assert payload["max_tokens"] == 256
    wire_schema = payload["structured_outputs"]["json"]
    assert "best_candidate_alias" in wire_schema["properties"]
    assert "challenger_alias" in wire_schema["properties"]
    assert "challenger_core_content_preserved" in wire_schema["properties"]
    assert "^C[0-9]{1,2}$" in json.dumps(wire_schema)
    assert "task_id" not in wire_schema["properties"]
    assert "response_format" not in payload
    prompt = payload["messages"][0]["content"][0]["text"]
    assert "C0" in prompt
    assert "Rule Top1 alias: C0" in prompt
    assert 'Complete Rule ranking best-to-worst: ["C0"]' in prompt
    assert value.candidates[0].candidate_id not in prompt


def test_openai_backend_retries_schema_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = request(candidate("crop", 80, grade=ProxyGrade.A))
    valid = json.dumps(
        {
            "schema_version": "1.0",
            "candidate_ranking": ["C0"],
            "best_candidate_alias": "C0",
            "proxy_grade": "proxy_a",
            "core_content_preserved": True,
            "visible_distortion": "none",
            "confidence": 0.9,
            "reason_codes": [],
            "fallback_action": "USE_BEST_TRADITIONAL",
        }
    )
    responses = iter(("{", valid))

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "choices": [{"message": {"content": next(responses)}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 7},
            }

    monkeypatch.setattr(agents_module.requests, "post", lambda *args, **kwargs: Response())
    image = tmp_path / "comparison.png"
    image.write_bytes(b"fixture")
    backend = OpenAICompatibleVisionBackend(
        base_url="http://127.0.0.1:18000/v1",
        model_version="fixture-model",
    )

    invocation = backend.judge(value, image)

    assert invocation.attempt_count == 2
    assert invocation.input_tokens == 10
    assert invocation.output_tokens == 14


def test_openai_backend_rejects_unknown_wire_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = request(candidate("crop", 80, grade=ProxyGrade.A))
    invalid = json.dumps(
        {
            "schema_version": "1.0",
            "candidate_ranking": ["C9"],
            "best_candidate_alias": "C9",
            "proxy_grade": "proxy_a",
            "core_content_preserved": True,
            "visible_distortion": "none",
            "confidence": 0.9,
            "reason_codes": [],
            "fallback_action": "USE_BEST_TRADITIONAL",
        }
    )

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"choices": [{"message": {"content": invalid}}]}

    monkeypatch.setattr(agents_module.requests, "post", lambda *args, **kwargs: Response())
    image = tmp_path / "comparison.png"
    image.write_bytes(b"fixture")
    backend = OpenAICompatibleVisionBackend(
        base_url="http://127.0.0.1:18000/v1",
        model_version="fixture-model",
    )

    with pytest.raises(ValueError, match="failed schema validation"):
        backend.judge(value, image)


def test_wire_schema_rejects_source_as_candidate_alias() -> None:
    with pytest.raises(ValueError):
        agents_module._JudgeWireResponse.model_validate(
            {
                "candidate_ranking": ["SOURCE"],
                "best_candidate_alias": "SOURCE",
                "proxy_grade": "proxy_a",
                "core_content_preserved": True,
                "visible_distortion": "none",
                "confidence": 0.9,
            }
        )


def test_pair_review_reason_derives_explicit_challenger_from_agent_ranking() -> None:
    rule = candidate("seam", 84, grade=ProxyGrade.B)
    challenger = candidate("crop", 80, grade=ProxyGrade.B)
    value = request(rule, challenger)
    response = JudgeAgentResponse(
        task_id=value.task_id,
        candidate_ranking=(rule.candidate_id, challenger.candidate_id),
        best_candidate_id=rule.candidate_id,
        proxy_grade=ProxyGrade.B,
        core_content_preserved=True,
        visible_distortion="rule seam damage",
        confidence=0.9,
        reason_codes=("challenger_for_pair_review",),
    )

    decision, call = decide_route(
        value,
        AgentReplayConfig(mode=AgentMode.ALWAYS_ON),
        backend=FixtureBackend(response),
        comparison_image=Path("unused.png"),
    )

    assert call is not None and call.success
    assert decision.agent_challenger_candidate_id == challenger.candidate_id
    assert "challenger_alias_derived_from_ranking" in decision.reason_codes
