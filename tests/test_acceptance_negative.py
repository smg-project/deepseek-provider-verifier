"""Controlled broken providers must never earn acceptance."""

import copy
import json

import httpx
import pytest
from test_acceptance_facets import observed  # noqa: F401 -- shared capture fixture
from test_runner import run
from test_size_cases import wire
from test_task3_review_fixes import protocol_response
from test_workflows import selected

from deepseek_provider_verifier.acceptance import assess_run, load_policy
from deepseek_provider_verifier.acceptance_facets import derive_facets
from deepseek_provider_verifier.cli import _run_context
from deepseek_provider_verifier.runner import rehash_manifest


def single_protocol(m, protocol):
    return rehash_manifest(
        m.model_copy(
            update={"budgets": m.budgets.model_copy(update={"protocols": [protocol]})}
        )
    )


@pytest.mark.parametrize("protocol", ["chat", "responses"])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize(
    "defect",
    ["wrong_fact", "400", "401", "429", "500", "malformed", "unfinished", "missing"],
)
def test_broken_provider_rejected(protocol, stream, defect, observed):  # noqa: F811
    m = single_protocol(
        selected("sizes-small", "L01", protocol, stream=stream), protocol
    )
    facts = copy.deepcopy(m.cases[0].oracle["expected_facts"])
    if defect == "wrong_fact":
        facts["middle"] = "wrong"

    def respond(req):
        if defect.isdigit():
            return httpx.Response(int(defect), json={"error": "synthetic rejection"})
        if defect == "malformed":
            return httpx.Response(
                200,
                content=b"not-json",
                headers={
                    "content-type": "text/event-stream"
                    if stream
                    else "application/json"
                },
            )
        if defect == "unfinished":
            if stream:
                return httpx.Response(
                    200,
                    content=b'data: {"unfinished": true}\n\n',
                    headers={"content-type": "text/event-stream"},
                )
            body = protocol_response(protocol, text=json.dumps(facts))
            if protocol == "chat":
                body["choices"][0]["finish_reason"] = None
            else:
                body["status"] = "in_progress"
            return httpx.Response(200, json=body)
        return wire(
            protocol, protocol_response(protocol, text=json.dumps(facts)), stream
        )

    r = run(m, respond)
    r = r.model_copy(update={"report": _run_context(m, r, "verified")})
    if defect == "missing":
        observed.clear()
    result = assess_run(
        m,
        r,
        derive_facets(m, r, observed),
        load_policy("official-compatible-v1"),
        "test",
    )
    assert result.exit_code != 0
    assert result.verdict in ("FAIL", "INCONCLUSIVE")


@pytest.mark.parametrize("protocol", ["chat", "responses"])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("defect", ["answer", "association"])
def test_wrong_tool_results_or_history_fail(protocol, stream, defect, observed):  # noqa: F811
    from test_workflows import execute_fixture

    m = single_protocol(
        selected("workflows-small", "W04", protocol, stream=stream), protocol
    )
    r, _ = execute_fixture(m)
    values = next(iter(observed.values()))
    if defect == "answer":
        values[-1].text_segments[0].text = "wrong"
    else:
        values[-1].request_payload["messages" if protocol == "chat" else "input"] = []
    from deepseek_provider_verifier.assertions import evaluate_case

    raw = evaluate_case(m.cases[0], values, m.profile_snapshot.rules)
    raw = raw.model_copy(
        update={"endpoint": "candidate", "attempt_refs": r.case_results[0].attempt_refs}
    )
    r = r.model_copy(update={"case_results": [raw], "counts": {raw.status: 1}})
    r = r.model_copy(update={"report": _run_context(m, r, "verified")})
    result = assess_run(
        m,
        r,
        derive_facets(m, r, observed),
        load_policy("official-compatible-v1"),
        "test",
    )
    assert result.exit_code == 1


@pytest.mark.parametrize("protocol", ["chat", "responses"])
@pytest.mark.parametrize("capability_ok", [True, False])
def test_extra_capability_is_rewarded_and_operator_can_require_it(
    protocol,
    capability_ok,
    observed,  # noqa: F811
):
    from test_workflows import fixture_body

    from deepseek_provider_verifier.acceptance_records import AcceptancePolicy

    core = selected("schemas", "S01", protocol).cases[0]
    advanced = selected("schemas", "S45", protocol).cases[0]
    m = single_protocol(selected("schemas", "S01", protocol), protocol)
    m = rehash_manifest(
        m.model_copy(
            update={
                "cases": [core, advanced],
                "request_ceiling": 2,
                "output_token_ceiling": 1024,
            }
        )
    )
    count = 0

    def respond(req):
        nonlocal count
        case = [core, advanced][count]
        count += 1
        value = case.oracle["expected_value"]
        if case == advanced and not capability_ok:
            value = {"child": value}
        return httpx.Response(
            200,
            json=fixture_body(
                protocol,
                {"tools": [{"name": "record_schema_fixture", "arguments": value}]},
            ),
        )

    r = run(m, respond)
    r = r.model_copy(update={"report": _run_context(m, r, "verified")})
    facets = derive_facets(m, r, observed)
    policy = load_policy("official-compatible-v1")
    result = assess_run(m, r, facets, policy, "test")
    assert result.exit_code == 0
    assert next(
        f.status for f in result.diagnostic_facets if f.name == "capability"
    ) == ("PASS" if capability_ok else "FAIL")
    value = policy.model_dump(mode="json")
    value["id"] = "operator-required-advanced-schema"
    for selector in value["selectors"]:
        if selector["facet"] == "capability":
            selector["required"] = True
    required = assess_run(m, r, facets, AcceptancePolicy.model_validate(value), "test")
    assert required.exit_code == (0 if capability_ok else 1)
