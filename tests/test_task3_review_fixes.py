"""Regressions for the four independently reproduced Task3 review findings."""

import base64
import json

import httpx
import pytest
from test_runner import manifest, response, run, tool

from deepseek_provider_verifier.catalog import load_cases
from deepseek_provider_verifier.evidence import load_resume_state
from deepseek_provider_verifier.planner import build_manifest
from deepseek_provider_verifier.records import Config, RunSettings


def catalog_manifest(case_id, protocol):
    base = manifest(protocol=protocol)
    template = load_cases([protocol], [case_id])[0]
    template = template.model_copy(update={"streams": [False], "rule_ids": ["test"]})
    config = Config(
        run=RunSettings(
            profile="synthetic",
            suite="smoke",
            protocols=[protocol],
            max_attempts_per_endpoint=100,
        ),
        endpoints=base.endpoints,
    )
    return build_manifest(config, base.profile_snapshot, [template])


def protocol_response(protocol, text="42", call=False, reasoning=True):
    if protocol == "chat":
        return response(
            None if call else text,
            [tool()] if call else None,
            "original trace" if reasoning else None,
        )
    items = []
    if reasoning:
        items.append(
            {
                "id": "reason-a",
                "type": "reasoning",
                "content": [{"type": "reasoning_text", "text": "original trace"}],
            }
        )
    if call:
        items.append(
            {
                "id": "item-a",
                "type": "function_call",
                "call_id": "call-a",
                "name": "add_integers",
                "arguments": '{"a":17,"b":25}',
                "status": "completed",
            }
        )
    else:
        items.append(
            {
                "id": "message-a",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
                "status": "completed",
            }
        )
    return {
        "id": "response-a",
        "object": "response",
        "status": "completed",
        "output": items,
    }


URL = "https://user:opaque-password@host.test/path?token=opaque-query#opaque-fragment"


@pytest.mark.parametrize(
    "content_type,body",
    [
        ("text/plain", f"Error at {URL}"),
        ("text/event-stream", f"Error at {URL}"),
        ("text/html", f'<html><a href="{URL}">bad</a></html>'),
        ("application/json", '{"url":"' + URL + '"'),
    ],
)
def test_unparsed_raw_body_cannot_persist_url_credentials(tmp_path, content_type, body):
    m = manifest()
    result = run(
        m,
        lambda r: httpx.Response(
            400, content=body, headers={"content-type": content_type}
        ),
        tmp_path,
    )
    attempt = load_resume_state(tmp_path, result.manifest_hash).prior_attempts[0]
    decoded = base64.b64decode(attempt.capture["body_base64"])
    assert b"opaque-" not in decoded and b"://user:" not in decoded
    assert attempt.capture["body_omission_reason"]


class InterruptedBody(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield f"Error at {URL}".encode()
        raise httpx.ReadError("interrupted fixture body")


def test_interrupted_raw_body_cannot_persist_url_credentials(tmp_path):
    result = run(
        manifest(),
        lambda r: httpx.Response(
            400, stream=InterruptedBody(), headers={"content-type": "text/plain"}
        ),
        tmp_path,
    )
    attempt = load_resume_state(tmp_path, result.manifest_hash).prior_attempts[0]
    assert attempt.error["type"] == "ReadError"
    assert b"opaque-" not in base64.b64decode(attempt.capture["body_base64"])
    assert attempt.capture["body_omission_reason"]
    assert result.case_results[0].status == "ERROR"


@pytest.mark.parametrize("case_id", ["C13", "C14"])
@pytest.mark.parametrize("protocol", ["chat", "responses"])
def test_catalog_continuation_cannot_pass_without_a_tool_exchange(case_id, protocol):
    result = run(
        catalog_manifest(case_id, protocol),
        lambda r: httpx.Response(200, json=protocol_response(protocol)),
    )
    trial = result.case_results[0]
    assert result.exit_code == 1 and trial.status == "FAIL"
    assert any(
        a.id == "TOOL_CONTINUATION_EXERCISED" and a.status == "FAIL"
        for a in trial.assertions
    )
    assert (
        next(m for m in trial.metric_observations if m.name == "task_success").value
        == 1
    )


@pytest.mark.parametrize("case_id", ["C13", "C14"])
@pytest.mark.parametrize("protocol", ["chat", "responses"])
def test_catalog_continuation_valid_exchange_passes(case_id, protocol):
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(
            200, json=protocol_response(protocol, call=len(calls) == 1)
        )

    result = run(catalog_manifest(case_id, protocol), handler)
    assert result.exit_code == 0 and len(calls) == 2
    assert any(
        a.id == "TOOL_CONTINUATION_EXERCISED" and a.status == "PASS"
        for a in result.case_results[0].assertions
    )


@pytest.mark.parametrize("case_id", ["C15", "C22"])
@pytest.mark.parametrize("protocol", ["chat", "responses"])
def test_negative_probe_cannot_pass_when_setup_is_rejected(case_id, protocol):
    result = run(
        catalog_manifest(case_id, protocol),
        lambda r: httpx.Response(400, json={"error": "setup rejected"}),
    )
    assert result.exit_code == 1 and result.case_results[0].status == "FAIL"
    assert any(
        a.id == "NEGATIVE_PROBE_SETUP" and a.status == "FAIL"
        for a in result.case_results[0].assertions
    )


@pytest.mark.parametrize("case_id", ["C15", "C22"])
@pytest.mark.parametrize("protocol", ["chat", "responses"])
def test_negative_probe_requires_actual_mutated_continuation(case_id, protocol):
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            return httpx.Response(200, json=protocol_response(protocol, call=True))
        return httpx.Response(400, json={"error": "mutated continuation rejected"})

    result = run(catalog_manifest(case_id, protocol), handler)
    assert result.exit_code == 0 and len(calls) == 2
    assert any(
        a.id == "NEGATIVE_PROBE_MUTATION" and a.status == "PASS"
        for a in result.case_results[0].assertions
    )


@pytest.mark.parametrize("protocol", ["chat", "responses"])
def test_reasoning_negative_probe_without_reasoning_is_not_exercised(protocol):
    calls = []

    def handler(request):
        calls.append(request)
        return (
            httpx.Response(
                200, json=protocol_response(protocol, call=True, reasoning=False)
            )
            if len(calls) == 1
            else httpx.Response(400, json={"error": "unrelated rejection"})
        )

    result = run(catalog_manifest("C15", protocol), handler)
    assert result.exit_code != 0
    assert any(
        a.id == "NEGATIVE_PROBE_MUTATION" and a.status != "PASS"
        for a in result.case_results[0].assertions
    )


@pytest.mark.parametrize(
    "status,diagnostic,expected",
    [
        (404, False, "FAIL"),
        (404, True, "FAIL"),
        (200, False, "FAIL"),
        (200, True, "INCONCLUSIVE"),
    ],
)
def test_fully_received_invalid_body_is_a_format_finding_not_transport_error(
    tmp_path, status, diagnostic, expected
):
    result = run(
        manifest(diagnostic=diagnostic),
        lambda r: httpx.Response(
            status,
            text="<html>not the response JSON</html>",
            headers={"content-type": "text/html"},
        ),
        tmp_path,
    )
    trial = result.case_results[0]
    assert trial.status == expected and trial.completed
    assert any(
        a.id == "RESPONSE_BODY_FORMAT" and a.status == "FAIL" for a in trial.assertions
    )
    attempt = load_resume_state(tmp_path, result.manifest_hash).prior_attempts[0]
    assert attempt.error["type"] == "INVALID_JSON"
    if status == 200:
        assert (
            next(m for m in trial.metric_observations if m.name == "available").value
            == 1
        )


def test_parser_resource_limit_remains_error_without_claiming_invalid_syntax():
    body = b"[" * 10000 + b"0" + b"]" * 10000
    result = run(manifest(), lambda r: httpx.Response(200, content=body))
    trial = result.case_results[0]
    assert trial.status == "ERROR"
    assert not any(a.id == "RESPONSE_BODY_FORMAT" for a in trial.assertions)


@pytest.mark.parametrize("protocol", ["chat", "responses"])
def test_continuation_must_use_the_intended_fixture_arguments(protocol):
    calls = []

    def handler(request):
        calls.append(request)
        payload = protocol_response(protocol, call=len(calls) == 1)
        if len(calls) == 1:
            if protocol == "chat":
                payload["choices"][0]["message"]["tool_calls"][0]["function"][
                    "arguments"
                ] = '{"a":1,"b":41}'
            else:
                payload["output"][-1]["arguments"] = '{"a":1,"b":41}'
        return httpx.Response(200, json=payload)

    result = run(catalog_manifest("C13", protocol), handler)
    assert result.exit_code == 1
    assert any(a.status == "FAIL" for a in result.case_results[0].assertions)


@pytest.mark.parametrize("protocol", ["chat", "responses"])
def test_correct_answer_does_not_repair_wrong_returned_tool_result(protocol):
    from deepseek_provider_verifier.assertions import evaluate_case
    from deepseek_provider_verifier.protocols.chat import assemble_chat_json
    from deepseek_provider_verifier.protocols.responses import assemble_responses_json

    m = catalog_manifest("C13", protocol)
    assemble = assemble_chat_json if protocol == "chat" else assemble_responses_json
    first, second = (
        assemble(protocol_response(protocol, call=True)),
        assemble(protocol_response(protocol)),
    )
    first.status_code = second.status_code = 200
    originals = (
        first.assistant_messages if protocol == "chat" else first.raw_response["output"]
    )
    result_item = (
        {"role": "tool", "tool_call_id": "call-a", "content": "wrong"}
        if protocol == "chat"
        else {"type": "function_call_output", "call_id": "call-a", "output": "wrong"}
    )
    second.request_payload = {
        "messages" if protocol == "chat" else "input": [*originals, result_item]
    }
    result = evaluate_case(m.cases[0], [first, second], m.profile_snapshot.rules)
    assert result.status == "FAIL"
    assert next(a for a in result.assertions if a.id == "TASK_SUCCESS").status == "PASS"
    assert (
        next(
            a for a in result.assertions if a.id == "TOOL_CONTINUATION_EXERCISED"
        ).status
        == "FAIL"
    )
