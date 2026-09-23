"""Real protocol assembly and history checks for bounded workflow graphs."""

import copy
import json

import httpx
import pytest
from test_compatibility_execution import stream_response
from test_depth_catalog import depth_manifest
from test_runner import run, tool
from test_task3_review_fixes import protocol_response

from deepseek_provider_verifier.runner import rehash_manifest


def selected(suite, case_id, protocol="chat", thinking=False, stream=False):
    m = depth_manifest(suite)
    c = next(
        c
        for c in m.cases
        if c.template_id == case_id
        and c.protocol == protocol
        and (c.mode == "thinking") == thinking
        and c.stream == stream
    )
    return rehash_manifest(
        m.model_copy(
            update={
                "cases": [c],
                "request_ceiling": c.max_requests,
                "output_token_ceiling": c.max_requests * c.max_output_tokens,
            }
        )
    )


def fixture_body(protocol, expectation, reasoning=True, reverse=False):
    calls = expectation.get("tools", [])
    b = protocol_response(
        protocol, text=expectation.get("text", ""), reasoning=reasoning
    )
    if calls:
        if protocol == "chat":
            b["choices"][0]["message"]["content"] = None
            b["choices"][0]["message"]["tool_calls"] = [
                tool(
                    id=f"call-{i}", name=c["name"], arguments=json.dumps(c["arguments"])
                )
                for i, c in enumerate(calls)
            ]
            b["choices"][0]["finish_reason"] = "tool_calls"
            if reverse:
                b["choices"][0]["message"]["tool_calls"].reverse()
        else:
            b["output"] = [i for i in b["output"] if i["type"] == "reasoning"] + [
                {
                    "id": f"item-{i}",
                    "type": "function_call",
                    "call_id": f"call-{i}",
                    "name": c["name"],
                    "arguments": json.dumps(c["arguments"]),
                    "status": "completed",
                }
                for i, c in enumerate(calls)
            ]
            if reverse:
                b["output"].reverse()
    return b


def execute_fixture(m, *, mutate=None, reasoning=True, reverse=False):
    calls = []
    c = m.cases[0]

    def handle(request):
        idx = len(calls)
        p = json.loads(request.content)
        calls.append(p)
        body = fixture_body(
            c.protocol,
            c.steps[idx]["expect"],
            reasoning and c.mode == "thinking",
            reverse,
        )
        if mutate:
            body = mutate(body, idx)
        return (
            stream_response(c.protocol, body)
            if c.stream
            else httpx.Response(200, json=body)
        )

    return run(m, handle), calls


def test_workflow_bounds_are_exact():
    small = depth_manifest("workflows-small")
    full = depth_manifest("workflows-full")
    assert (len(small.cases), small.request_ceiling) == (32, 160)
    assert (len(full.cases), full.request_ceiling) == (56, 384)
    assert max(c.max_requests for c in full.cases) == 17


def test_authored_repeatability_prompt_reaches_wire():
    m = selected("repeatability", "R01")
    seen = []

    def handle(r):
        seen.append(json.loads(r.content))
        return httpx.Response(
            200, json=protocol_response("chat", text="amber", reasoning=False)
        )

    assert run(m, handle).exit_code == 0
    assert seen[0]["messages"] == [
        {"role": "user", "content": "Reply with exactly amber and nothing else."}
    ]


@pytest.mark.parametrize("protocol", ["chat", "responses"])
@pytest.mark.parametrize(
    "thinking,stream", [(False, False), (True, False), (False, True), (True, True)]
)
@pytest.mark.parametrize("id", ["W01", "W02", "W03", "W04", "W05", "W06", "W07"])
def test_complete_workflows_use_actual_parent_history(id, protocol, thinking, stream):
    m = selected("workflows-full", id, protocol, thinking, stream)
    result, requests = execute_fixture(m, reverse=True)
    assert result.exit_code == 0, result.case_results[0]
    assert len(requests) == m.cases[0].max_requests
    key = "messages" if protocol == "chat" else "input"
    assert requests[0][key][0]["role"] == "user"
    if id == "W06":
        assert requests[1]["temperature"] == 0.2
        assert requests[3]["temperature"] == 0.7
        assert not any("branch A" in str(i) for i in requests[3][key])
    if id == "W01":
        assert m.cases[0].steps[-1]["expect"] == {"text": "11"}
        assert m.cases[0].steps[2]["expect"]["tools"][0]["arguments"] == {
            "a": 4,
            "b": 3,
        }


@pytest.mark.parametrize(
    "fault", ["early_answer", "wrong_arguments", "extra_call", "duplicate_id"]
)
def test_workflow_rejects_seeded_tool_defects(fault):
    m = selected("workflows-full", "W01")

    def mutate(body, idx):
        if idx != 0:
            return body
        msg = body["choices"][0]["message"]
        if fault == "early_answer":
            return protocol_response("chat", text="11", reasoning=False)
        if fault == "wrong_arguments":
            msg["tool_calls"][0]["function"]["arguments"] = '{"a":2,"b":1}'
        if fault in ("extra_call", "duplicate_id"):
            extra = copy.deepcopy(msg["tool_calls"][0])
            extra["id"] = "extra" if fault == "extra_call" else extra["id"]
            msg["tool_calls"].append(extra)
        return body

    r, _ = execute_fixture(m, mutate=mutate)
    assert r.case_results[0].status == "FAIL"


@pytest.mark.parametrize("protocol", ["chat", "responses"])
@pytest.mark.parametrize(
    "fault",
    [
        "wrong_result_id",
        "reasoning",
        "sibling",
        "option",
        "missing_round",
        "redirect",
        "service_error",
    ],
)
def test_parent_graph_defects_never_pass(monkeypatch, protocol, fault):
    from deepseek_provider_verifier import runner
    from deepseek_provider_verifier.workflows import evaluate_workflow

    captured = []
    original = runner.evaluate_case

    def capture(c, obs, rules):
        if len(obs) == 5:
            captured.append(copy.deepcopy(obs))
        return original(c, obs, rules)

    monkeypatch.setattr(runner, "evaluate_case", capture)
    m = selected("workflows-full", "W06", protocol, thinking=True)
    result, _ = execute_fixture(m)
    assert result.exit_code == 0
    obs = captured[0]
    key = "messages" if protocol == "chat" else "input"
    if fault == "wrong_result_id":
        for item in obs[3].request_payload[key]:
            if item.get("role") == "tool" or item.get("type") == "function_call_output":
                item["tool_call_id" if protocol == "chat" else "call_id"] = "wrong"
    elif fault == "reasoning":
        for item in obs[3].request_payload[key]:
            if "reasoning_content" in item:
                item["reasoning_content"] = "changed"
            if item.get("type") == "reasoning":
                item["content"] = []
    elif fault == "sibling":
        obs[3].request_payload[key].insert(
            0, {"role": "assistant", "content": "branch A secret"}
        )
    elif fault == "option":
        obs[3].request_payload["temperature"] = 0.2
    elif fault == "missing_round":
        obs.pop(1)
    elif fault == "redirect":
        obs[-1].status_code = 302
    elif fault == "service_error":
        obs[0].text_segments = []
        obs[-1].status_code = 503
    r = evaluate_workflow(m.cases[0], obs, m.profile_snapshot.rules)
    assert r.status == ("ERROR" if fault == "service_error" else "FAIL")


def test_empty_reasoning_cannot_certify_accumulated_reasoning():
    m = selected("workflows-full", "W01", thinking=True)
    r, _ = execute_fixture(m, reasoning=False)
    assert r.case_results[0].status == "INCONCLUSIVE"
    assert (
        next(
            v.value
            for v in r.case_results[0].metric_observations
            if v.name == "task_success"
        )
        == 1
    )


@pytest.mark.parametrize("protocol", ["chat", "responses"])
def test_parallel_result_order_is_not_semantic(monkeypatch, protocol):
    from deepseek_provider_verifier import runner
    from deepseek_provider_verifier.workflows import evaluate_workflow

    original = runner.evaluate_case
    saved = []

    def capture(c, obs, rules):
        if len(obs) == 2:
            saved.append(copy.deepcopy(obs))
        return original(c, obs, rules)

    monkeypatch.setattr(runner, "evaluate_case", capture)
    m = selected("workflows-full", "W04", protocol)
    result, _ = execute_fixture(m)
    assert result.exit_code == 0
    obs = saved[0]
    key = "messages" if protocol == "chat" else "input"
    history = obs[1].request_payload[key]
    indexes = [
        i
        for i, item in enumerate(history)
        if item.get("role") == "tool" or item.get("type") == "function_call_output"
    ]
    values = [history[i] for i in indexes][::-1]
    for i, value in zip(indexes, values, strict=True):
        history[i] = value
    assert evaluate_workflow(m.cases[0], obs, m.profile_snapshot.rules).status == "PASS"


@pytest.mark.parametrize("protocol", ["chat", "responses"])
def test_fragmented_parallel_streams_preserve_call_identity(protocol):
    m = selected("workflows-full", "W04", protocol, thinking=True, stream=True)
    c = m.cases[0]
    count = 0

    def handler(request):
        nonlocal count
        body = fixture_body(protocol, c.steps[count]["expect"])
        count += 1
        stream = stream_response(protocol, body).content.decode()
        events = [
            json.loads(line[6:])
            for line in stream.splitlines()
            if line.startswith("data: ") and line != "data: [DONE]"
        ]
        expanded = []
        for event in events:
            if protocol == "chat" and event["choices"][0]["delta"].get("tool_calls"):
                calls = event["choices"][0]["delta"]["tool_calls"]
                initial = copy.deepcopy(event)
                for t in initial["choices"][0]["delta"]["tool_calls"]:
                    t["function"]["arguments"] = ""
                expanded.append(initial)
                for second in (False, True):
                    for t in calls[::-1] if second else calls:
                        args = t["function"]["arguments"]
                        part = (
                            args[len(args) // 2 :] if second else args[: len(args) // 2]
                        )
                        chunk = copy.deepcopy(event)
                        chunk["choices"][0]["delta"] = {
                            "tool_calls": [
                                {"index": t["index"], "function": {"arguments": part}}
                            ]
                        }
                        expanded.append(chunk)
            elif (
                protocol == "responses"
                and event["type"] == "response.function_call_arguments.delta"
            ):
                value = event["delta"]
                expanded.extend(
                    [
                        {**event, "delta": value[: len(value) // 2]},
                        {**event, "delta": value[len(value) // 2 :]},
                    ]
                )
            else:
                expanded.append(event)
        if protocol == "responses":
            for i, e in enumerate(expanded):
                e["sequence_number"] = i
        content = "".join(
            (f"event: {e['type']}\n" if protocol == "responses" else "")
            + f"data: {json.dumps(e)}\n\n"
            for e in expanded
        )
        if protocol == "chat":
            content += "data: [DONE]\n\n"
        return httpx.Response(
            200, content=content, headers={"content-type": "text/event-stream"}
        )

    assert run(m, handler).exit_code == 0


@pytest.mark.parametrize("defect", ["missing_expect", "unknown_tool", "unbounded"])
def test_invalid_workflow_metadata_rejected_before_execution(defect):
    from deepseek_provider_verifier.records import Case

    case = selected("workflows-full", "W01").cases[0].model_dump()
    if defect == "missing_expect":
        case["steps"][0].pop("expect")
    elif defect == "unknown_tool":
        case["steps"][0]["expect"]["tools"][0]["name"] = "execute_code"
    else:
        case["oracle"]["bounded_steps"] = False
    with pytest.raises(ValueError):
        Case.model_validate(case)
