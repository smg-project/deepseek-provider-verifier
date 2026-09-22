"""Contract failures must remain observable, including diagnostic measurements."""

from datetime import UTC, datetime

import pytest

from deepseek_provider_verifier.capture import (
    Observation,
    Segment,
    SourcePosition,
    ToolCall,
)
from deepseek_provider_verifier.records import Case, Rule


def case(**updates):
    values = {
        "id": "C01.chat.non_thinking.nonstream",
        "protocol": "chat",
        "template_id": "C01",
        "mode": "non_thinking",
        "stream": False,
        "rule_ids": ["test"],
        "steps": [{"kind": "user", "content": "Say amber."}],
        "required": True,
        "max_requests": 1,
        "max_output_tokens": 512,
        "oracle": {"kind": "exact_text", "value": "amber"},
    }
    return Case(**(values | updates))


def rule(**updates):
    values = {
        "id": "test",
        "protocol": "chat",
        "conditions": {},
        "expectation": "Synthetic fixture output",
        "assertion_id": "output_present",
        "source_url": "https://example.test/policy",
        "source_section": "Fixture policy",
        "retrieved_at": datetime(2026, 9, 21, tzinfo=UTC),
        "evidence_status": "project-policy",
        "maturity": "calibrated",
        "gating": True,
    }
    return Rule(**(values | updates))


def observation(text="amber", **updates):
    return Observation(
        protocol="chat",
        terminal_state="completed",
        text_segments=[
            Segment(
                text=text, identity="0", source=SourcePosition(json_path="$.choices[0]")
            )
        ],
        **updates,
    )


def evaluate(c, observations, rules=None):
    from deepseek_provider_verifier.assertions import evaluate_case

    return evaluate_case(c, observations, rules or [rule()])


def test_positive_and_wrong_answers_are_measured():
    assert evaluate(case(), [observation()]).status == "PASS"
    result = evaluate(case(), [observation("blue")])
    assert result.status == "FAIL"
    assert any(a.id == "TASK_SUCCESS" and a.status == "FAIL" for a in result.assertions)


def test_diagnostic_rule_retains_failed_observation_without_gating():
    result = evaluate(
        case(), [observation("blue")], [rule(maturity="diagnostic", gating=False)]
    )
    assert result.status == "INCONCLUSIVE"
    assert any(a.status == "FAIL" for a in result.assertions)


def test_required_missing_protocol_fails():
    o = observation()
    o.status_code = 404
    assert evaluate(case(), [o]).status == "FAIL"


def test_transport_error_precedes_partial_output():
    o = observation()
    o.transport_error = {"type": "ReadTimeout"}
    assert evaluate(case(), [o]).status == "ERROR"


def test_malformed_arguments_are_not_repaired():
    o = observation(
        tools={
            "0:0": ToolCall(
                identity="0:0",
                index=0,
                call_id="a",
                name="add_integers",
                arguments='{"a":2,',
                complete=True,
            )
        }
    )
    result = evaluate(
        case(oracle={"kind": "tool_required", "name": "add_integers"}), [o]
    )
    assert result.status == "FAIL"
    assert any(a.id == "TOOL_ARGUMENTS_INVALID_JSON" for a in result.assertions)


def test_ignored_tool_prohibition_fails():
    o = observation(
        tools={
            "0:0": ToolCall(
                identity="0:0",
                index=0,
                call_id="a",
                name="add_integers",
                arguments='{"a":2,"b":3}',
                complete=True,
            )
        }
    )
    assert evaluate(case(oracle={"kind": "tool_forbidden"}), [o]).status == "FAIL"


def test_duplicate_call_ids_fail():
    tools = {
        str(i): ToolCall(
            identity=str(i),
            index=i,
            call_id="same",
            name="lookup_fixture",
            arguments='{"key":"harbor"}',
            complete=True,
        )
        for i in range(2)
    }
    assert (
        evaluate(
            case(oracle={"kind": "multiple_tools", "min_calls": 2}),
            [observation(tools=tools)],
        ).status
        == "FAIL"
    )


def test_lost_reasoning_and_mismatched_call_ids_fail():
    first = observation()
    first.assistant_messages = [
        {
            "role": "assistant",
            "content": None,
            "reasoning_content": "private trace",
            "tool_calls": [
                {
                    "id": "real",
                    "type": "function",
                    "function": {
                        "name": "lookup_fixture",
                        "arguments": '{"key":"harbor"}',
                    },
                }
            ],
        }
    ]
    second = observation()
    second.request_payload = {
        "messages": [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": first.assistant_messages[0]["tool_calls"],
            },
            {"role": "tool", "tool_call_id": "wrong", "content": "amber"},
        ]
    }
    result = evaluate(
        case(oracle={"kind": "conversation", "value": "amber"}), [first, second]
    )
    assert {a.id for a in result.assertions if a.status == "FAIL"} >= {
        "HISTORY_REPLAY",
        "TOOL_RESULT_PAIRING",
    }


@pytest.mark.parametrize("value", [True, "2", 2.5, None])
def test_mock_tool_rejects_coerced_integer_arguments(value):
    from deepseek_provider_verifier.mock_tools import add_integers

    with pytest.raises(ValueError):
        add_integers(value, 3)


def test_mock_tools_only_execute_registered_strict_schemas():
    from deepseek_provider_verifier.mock_tools import execute_tool, lookup_fixture

    assert lookup_fixture("harbor") == "amber"
    assert execute_tool("add_integers", {"a": 17, "b": 25}) == 42
    for name, args in [
        ("shell", {"cmd": "anything"}),
        ("lookup_fixture", {"key": "unknown"}),
        ("add_integers", {"a": 1, "b": 2, "extra": 3}),
    ]:
        with pytest.raises(ValueError):
            execute_tool(name, args)


def test_catalog_resources_expand_exact_smoke_and_hash_original_prompts():
    from pathlib import Path

    from deepseek_provider_verifier.catalog import load_cases, load_prompts
    from deepseek_provider_verifier.config import load_config, load_profile
    from deepseek_provider_verifier.planner import build_manifest

    profile = load_profile(Path("profiles/deepseek-api-2026-09-21.json"))
    templates = load_cases()
    assert {t.id for t in templates} == {f"C{i:02d}" for i in range(1, 25)}
    selected = set(
        profile.presets["smoke"].case_ids
        + profile.presets["smoke"].attached_assertion_case_ids
    )
    manifest = build_manifest(
        load_config(Path("configs/providers.example.toml")),
        profile,
        [t for t in templates if t.id in selected],
    )
    assert sum(c.max_requests for c in manifest.cases) == 34
    prompts = load_prompts()
    assert {p.category for p in prompts} >= {
        "required",
        "forbidden",
        "ambiguous",
        "schema",
        "follow_up",
    }
    assert all(
        p.license == "MIT" and p.dataset_version and len(p.content_hash) == 64
        for p in prompts
    )


def test_rule_scope_does_not_upgrade_untested_modes_or_streams():
    result = evaluate(
        case(mode="thinking"),
        [observation()],
        [rule(conditions={"mode": "non_thinking", "stream": False})],
    )
    assert result.status == "INCONCLUSIVE"
    assert all(not a.gating for a in result.assertions)


def test_json_truncation_is_inconclusive_and_separate_from_bad_schema():
    truncated = observation('{"label":')
    truncated.terminal_state = "incomplete"
    c = case(oracle={"kind": "json_object"})
    assert evaluate(c, [truncated]).status == "INCONCLUSIVE"
    assert evaluate(c, [observation("not-json")]).status == "FAIL"


def test_reasoning_markers_in_visible_thinking_content_fail_channel_separation():
    c = case(template_id="C03", mode="thinking", oracle={"kind": "structure"})
    assert (
        evaluate(c, [observation("<think>hidden trace</think>amber")]).status == "FAIL"
    )


def test_unknown_oracle_cannot_become_a_terminal_only_pass():
    assert (
        evaluate(case(oracle={"kind": "not-registered"}), [observation()]).status
        == "INCONCLUSIVE"
    )
