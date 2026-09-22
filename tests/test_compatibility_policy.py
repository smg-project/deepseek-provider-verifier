"""Functional failures must remain distinct from official-behavior differences."""

import copy
import json

import httpx
import pytest
from test_runner import manifest, run
from test_task3_review_fixes import catalog_manifest, protocol_response

from deepseek_provider_verifier.reports import render_report
from deepseek_provider_verifier.runner import rehash_manifest


def policy_manifest(m, policy="self_hosted"):
    profile = m.profile_snapshot.model_copy(
        update={
            "rules": [
                r.model_copy(
                    update={
                        "conditions": {**r.conditions, "compatibility_policy": policy}
                    }
                )
                for r in m.profile_snapshot.rules
            ]
        }
    )
    return rehash_manifest(m.model_copy(update={"profile_snapshot": profile}))


def identifier_manifest(*, policy="self_hosted", rejectable=True, reference=200):
    return policy_manifest(
        manifest(
            oracle={
                "kind": "exact_text",
                "value": "amber",
                "compatibility": {
                    "feature": "identifier",
                    "allow_rejection": rejectable,
                    "reference_status": reference,
                    "reference_date": "2026-09-22",
                },
            }
        ),
        policy,
    )


@pytest.mark.parametrize(
    "policy,status,expected",
    [
        ("self_hosted", 200, "PASS"),
        ("self_hosted", 400, "PASS"),
        ("official_parity", 200, "PASS"),
        ("official_parity", 400, "FAIL"),
    ],
)
def test_stricter_identifier_validation_is_only_a_parity_failure(
    policy, status, expected
):
    m = identifier_manifest(policy=policy)
    result = run(
        m,
        lambda r: httpx.Response(
            status,
            json=protocol_response("chat", text="amber", reasoning=False)
            if status == 200
            else {"error": "invalid identifier"},
        ),
    )
    assert result.case_results[0].status == expected
    observation = next(
        a
        for a in result.case_results[0].assertions
        if a.id == "COMPATIBILITY_OBSERVATION"
    )
    assert observation.observed["matches_reference"] == (status == 200)
    assert not observation.gating
    if status == 400:
        assert "DIFFERENCE" in render_report(result, "markdown")
    assert result.exit_code == (1 if expected == "FAIL" else 0)


@pytest.mark.parametrize("status", [400, 404, 422])
def test_valid_identifier_control_cannot_be_rejected(status):
    result = run(
        identifier_manifest(rejectable=False),
        lambda r: httpx.Response(status, json={"error": "rejected"}),
    )
    assert result.case_results[0].status == "FAIL"


@pytest.mark.parametrize("status", [401, 402, 403, 408, 429, 500])
def test_compatibility_rejection_does_not_swallow_service_errors(status):
    result = run(
        identifier_manifest(),
        lambda r: httpx.Response(status, json={"error": "unavailable"}),
    )
    assert result.case_results[0].status == "ERROR"


@pytest.mark.parametrize("status", [404, 405, 409])
def test_unrelated_client_errors_are_not_valid_feature_rejections(status):
    result = run(
        identifier_manifest(),
        lambda r: httpx.Response(status, json={"error": "unrelated"}),
    )
    assert result.case_results[0].status == "FAIL"


@pytest.mark.parametrize("protocol", ["chat", "responses"])
@pytest.mark.parametrize("case_id", ["C08", "C09"])
@pytest.mark.parametrize(
    "policy,expected", [("self_hosted", "PASS"), ("official_parity", "FAIL")]
)
def test_working_forced_tools_in_thinking_mode_are_valid_extensions(
    protocol, case_id, policy, expected
):
    m = policy_manifest(catalog_manifest(case_id, protocol), policy)

    def provider(request):
        body = protocol_response(protocol, call=True, reasoning=False)
        if protocol == "chat":
            body["choices"][0]["message"]["tool_calls"][0]["function"].update(
                name="lookup_fixture", arguments='{"key":"harbor"}'
            )
        else:
            body["output"][0].update(
                name="lookup_fixture", arguments='{"key":"harbor"}'
            )
        return httpx.Response(200, json=body)

    result = run(m, provider)
    assert [t.status for t in result.case_results] == ["PASS", expected]


@pytest.mark.parametrize("protocol", ["chat", "responses"])
def test_accepted_forced_tool_request_still_requires_correct_tool_and_arguments(
    protocol,
):
    m = policy_manifest(catalog_manifest("C08", protocol))
    result = run(
        m,
        lambda r: httpx.Response(
            200, json=protocol_response(protocol, call=True, reasoning=False)
        ),
    )
    assert all(t.status == "FAIL" for t in result.case_results)


@pytest.mark.parametrize(
    "strict,policy,status,expected",
    [
        (True, "self_hosted", 200, "PASS"),
        (True, "self_hosted", 400, "PASS"),
        (True, "official_parity", 200, "FAIL"),
        (True, "official_parity", 400, "PASS"),
        (False, "self_hosted", 400, "PASS"),
        (False, "official_parity", 400, "FAIL"),
    ],
)
def test_schema_subset_rejection_is_separate_from_parity(
    strict, policy, status, expected
):
    m = policy_manifest(
        manifest(
            protocol="responses",
            oracle={
                "kind": "schema",
                "schema": {
                    "type": "object",
                    "properties": {"label": {"const": "amber"}},
                    "required": ["label"],
                },
                "compatibility": {
                    "feature": "schema",
                    "allow_rejection": True,
                    "reference_status": 400 if strict else 200,
                    "reference_date": "2026-09-22",
                },
            },
        ),
        policy,
    )
    result = run(
        m,
        lambda r: httpx.Response(
            status,
            json=protocol_response(
                "responses", text='{"label":"amber"}', reasoning=False
            )
            if status == 200
            else {"error": "unsupported schema"},
        ),
    )
    assert result.case_results[0].status == expected


def test_acceptance_never_hides_a_schema_violation():
    m = policy_manifest(
        manifest(
            protocol="responses",
            oracle={
                "kind": "schema",
                "schema": {
                    "type": "object",
                    "properties": {"label": {"const": "amber"}},
                    "required": ["label"],
                },
                "compatibility": {
                    "feature": "schema",
                    "allow_rejection": True,
                    "reference_status": 400,
                    "reference_date": "2026-09-22",
                },
            },
        )
    )
    result = run(
        m,
        lambda r: httpx.Response(
            200,
            json=protocol_response(
                "responses", text='{"label":"wrong"}', reasoning=False
            ),
        ),
    )
    assert result.case_results[0].status == "FAIL"
    assert any(
        a.id == "OUTPUT_SCHEMA" and a.status == "FAIL"
        for a in result.case_results[0].assertions
    )


def paired_manifest(protocol, policy="self_hosted"):
    m = catalog_manifest("C15", protocol)
    c = m.cases[0]
    c = c.model_copy(
        update={
            "max_requests": 3,
            "steps": [
                c.steps[0],
                {"kind": "continue"},
                {"kind": "continue", "replay_from": 0, "mutation": "omit_reasoning"},
            ],
            "oracle": {
                "kind": "conversation",
                "value": "42",
                "continue_tools": True,
                "bounded_steps": True,
                "expected_arguments": {"a": 17, "b": 25},
                "compatibility": {
                    "feature": "reasoning_pair",
                    "allow_rejection": True,
                    "reference_status": 200,
                    "reference_date": "2026-09-22",
                },
            },
        }
    )
    m = m.model_copy(
        update={
            "cases": [c],
            "request_ceiling": 3,
            "output_token_ceiling": 3 * c.max_output_tokens,
        }
    )
    return policy_manifest(m, policy)


@pytest.mark.parametrize("protocol", ["chat", "responses"])
@pytest.mark.parametrize(
    "omitted_status,policy,expected",
    [
        (200, "self_hosted", "PASS"),
        (400, "self_hosted", "PASS"),
        (400, "official_parity", "FAIL"),
    ],
)
def test_reasoning_pair_reuses_the_same_setup_without_the_control_answer(
    protocol, omitted_status, policy, expected
):
    requests = []

    def provider(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(200, json=protocol_response(protocol, call=True))
        if len(requests) == 2:
            return httpx.Response(200, json=protocol_response(protocol))
        return httpx.Response(
            omitted_status,
            json=protocol_response(protocol, reasoning=False)
            if omitted_status == 200
            else {"error": "reasoning required"},
        )

    result = run(paired_manifest(protocol, policy), provider)
    assert len(requests) == 3
    control = copy.deepcopy(requests[1])
    key = "messages" if protocol == "chat" else "input"
    if protocol == "chat":
        for item in control[key]:
            item.pop("reasoning_content", None)
    else:
        control[key] = [i for i in control[key] if i.get("type") != "reasoning"]
    assert requests[2] == control
    assert result.case_results[0].status == expected


@pytest.mark.parametrize("protocol", ["chat", "responses"])
def test_reasoning_omission_cannot_pass_without_a_working_preserved_control(protocol):
    requests = []

    def provider(request):
        requests.append(request)
        return (
            httpx.Response(200, json=protocol_response(protocol, call=True))
            if len(requests) == 1
            else httpx.Response(400, json={"error": "control broken"})
        )

    result = run(paired_manifest(protocol), provider)
    assert len(requests) == 2
    assert result.case_results[0].status == "FAIL"


@pytest.mark.parametrize("protocol", ["chat", "responses"])
@pytest.mark.parametrize(
    "defect", ["no_reasoning", "wrong_arguments", "wrong_answer", "extra_call"]
)
def test_reasoning_pair_requires_real_history_and_correct_continuations(
    protocol, defect
):
    requests = []

    def provider(request):
        requests.append(json.loads(request.content))
        setup = len(requests) == 1
        body = protocol_response(
            protocol,
            call=setup or (defect == "extra_call" and len(requests) == 3),
            reasoning=defect != "no_reasoning",
            text="wrong" if defect == "wrong_answer" else "42",
        )
        if setup and defect == "wrong_arguments":
            call = (
                body["choices"][0]["message"]["tool_calls"][0]["function"]
                if protocol == "chat"
                else body["output"][1]
            )
            call["arguments"] = '{"a":1,"b":2}'
        return httpx.Response(200, json=body)

    result = run(paired_manifest(protocol), provider)
    assert result.case_results[0].status == "FAIL"
    assert len(requests) <= 3


@pytest.mark.parametrize("policy", ["self_hosted", "official_parity"])
def test_invalid_accepted_body_is_never_a_pass(policy):
    result = run(
        identifier_manifest(policy=policy),
        lambda r: httpx.Response(200, content="not json"),
    )
    assert result.case_results[0].status == "FAIL"


def test_stream_cut_off_during_rejection_is_execution_error():
    class Broken(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"error":'
            raise httpx.ReadError("synthetic interruption")

    result = run(identifier_manifest(), lambda r: httpx.Response(400, stream=Broken()))
    assert result.case_results[0].status == "ERROR"


def test_compatibility_evidence_survives_report_reload_and_resume(tmp_path):
    from deepseek_provider_verifier.reports import load_run_evidence

    m = identifier_manifest()
    first = run(
        m, lambda r: httpx.Response(422, json={"error": "invalid identifier"}), tmp_path
    )

    def forbidden(request):
        raise AssertionError("Completed case must not send another request")

    resumed = run(m, forbidden, tmp_path, resume=True)
    _, loaded = load_run_evidence(tmp_path)
    assert first.case_results == resumed.case_results == loaded.case_results
    assert loaded.exit_code == 0
    assert "DIFFERENCE" in render_report(loaded, "markdown")
    assert 'failures="0"' in render_report(loaded, "junit")


@pytest.mark.parametrize("protocol", ["chat", "responses"])
@pytest.mark.parametrize("error_step", [0, 1, 2])
@pytest.mark.parametrize("status", [402, 500])
def test_pair_execution_errors_take_precedence_over_wrong_control_answers(
    protocol, error_step, status
):
    requests = []

    def provider(request):
        step = len(requests)
        requests.append(request)
        if step == error_step:
            return httpx.Response(status, json={"error": "synthetic service error"})
        return httpx.Response(
            200,
            json=protocol_response(
                protocol, call=step == 0, text="wrong" if step == 1 else "42"
            ),
        )

    result = run(paired_manifest(protocol), provider)
    assert result.case_results[0].status == "ERROR"
    assert not result.complete
    assert result.exit_code == 2


@pytest.mark.parametrize("protocol", ["chat", "responses"])
@pytest.mark.parametrize("trace", ["", "   "])
def test_empty_reasoning_placeholder_does_not_exercise_omission(protocol, trace):
    requests = []

    def provider(request):
        requests.append(request)
        body = protocol_response(protocol, call=len(requests) == 1)
        if len(requests) == 1:
            if protocol == "chat":
                body["choices"][0]["message"]["reasoning_content"] = trace
            else:
                body["output"][0]["content"] = (
                    [{"type": "reasoning_text", "text": trace}] if trace else []
                )
        return httpx.Response(200, json=body)

    result = run(paired_manifest(protocol), provider)
    assert result.case_results[0].status == "FAIL"
    assert not any(
        a.id == "COMPATIBILITY_OBSERVATION" for a in result.case_results[0].assertions
    )


@pytest.mark.parametrize("protocol", ["chat", "responses"])
def test_reasoning_pair_retains_control_tool_schema_measurement(protocol):
    requests = []

    def provider(request):
        requests.append(request)
        return httpx.Response(
            200, json=protocol_response(protocol, call=len(requests) == 1)
        )

    result = run(paired_manifest(protocol), provider)
    metrics = result.case_results[0].metric_observations
    assert [m.value for m in metrics if m.name == "tool_argument_schema"] == [1.0]
    assert len([m for m in metrics if m.name == "task_success"]) == 1


@pytest.mark.parametrize("protocol", ["chat", "responses"])
def test_boundary_acceptance_cannot_emit_an_unadvertised_tool(protocol):
    m = policy_manifest(
        manifest(
            protocol=protocol,
            oracle={
                "kind": "exact_text",
                "value": "amber",
                "compatibility": {
                    "feature": "identifier",
                    "allow_rejection": True,
                    "reference_status": 200,
                    "reference_date": "2026-09-22",
                },
            },
        )
    )
    body = protocol_response(protocol, call=True, reasoning=False)
    if protocol == "chat":
        body["choices"][0]["message"]["content"] = "amber"
    else:
        body["output"].extend(
            protocol_response(protocol, text="amber", reasoning=False)["output"]
        )
    result = run(m, lambda r: httpx.Response(200, json=body))
    assert result.case_results[0].status == "FAIL"


@pytest.mark.parametrize("protocol", ["chat", "responses"])
@pytest.mark.parametrize("status", [302, 307])
def test_redirect_with_valid_body_is_not_feature_acceptance(protocol, status):
    m = policy_manifest(
        manifest(
            protocol=protocol,
            oracle={
                "kind": "exact_text",
                "value": "amber",
                "compatibility": {
                    "feature": "identifier",
                    "allow_rejection": False,
                    "reference_status": 200,
                    "reference_date": "2026-09-22",
                },
            },
        )
    )
    result = run(
        m,
        lambda r: httpx.Response(
            status, json=protocol_response(protocol, text="amber", reasoning=False)
        ),
    )
    assert result.case_results[0].status == "FAIL"


@pytest.mark.parametrize("protocol", ["chat", "responses"])
@pytest.mark.parametrize("redirect_step", [0, 1, 2])
def test_pair_redirect_is_not_a_successful_setup_or_continuation(
    protocol, redirect_step
):
    requests = []

    def provider(request):
        step = len(requests)
        requests.append(request)
        return httpx.Response(
            302 if step == redirect_step else 200,
            json=protocol_response(protocol, call=step == 0),
        )

    result = run(paired_manifest(protocol), provider)
    assert result.case_results[0].status == "FAIL"
