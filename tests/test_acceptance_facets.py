"""Semantic acceptance boundaries exercised through real protocol assembly."""

import importlib.util
import json

import httpx
import pytest
from test_runner import response, run
from test_size_cases import output_body, wire
from test_task3_review_fixes import protocol_response
from test_workflows import selected

from deepseek_provider_verifier import runner


@pytest.fixture
def observed(monkeypatch):
    observations = {}
    original = runner.evaluate_case

    def capture(case, values, rules):
        if values:
            observations[(values[0].endpoint, case.id)] = values
        return original(case, values, rules)

    monkeypatch.setattr(runner, "evaluate_case", capture)
    return observations


def facets(m, r, observations):
    assert importlib.util.find_spec("deepseek_provider_verifier.acceptance_facets"), (
        "facets not implemented"
    )
    from deepseek_provider_verifier.acceptance_facets import derive_facets

    return {f.name: f for f in derive_facets(m, r, observations)}


@pytest.mark.parametrize("protocol", ["chat", "responses"])
@pytest.mark.parametrize(
    "variant,expected",
    [
        ("raw", "PASS"),
        ("fence", "PASS"),
        ("unlabelled", "PASS"),
        ("prose", "FAIL"),
        ("wrong", "FAIL"),
        ("duplicate", "FAIL"),
        ("trailing", "FAIL"),
        ("multiple", "FAIL"),
        ("nan", "FAIL"),
        ("deep", "FAIL"),
        ("oversize", "FAIL"),
        ("empty", "FAIL"),
    ],
)
def test_retrieval_boundaries(protocol, variant, expected, observed):
    m = selected("sizes-small", "L01", protocol)
    value = json.dumps(m.cases[0].oracle["expected_facts"])
    texts = {
        "raw": value,
        "fence": f"```json\n{value}\n```",
        "unlabelled": f"```\n{value}\n```",
        "prose": "Answer: " + value,
        "wrong": value.replace("begin", "wrong"),
        "duplicate": '{"begin":"wrong",' + value[1:],
        "trailing": f"```json\n{value}\n``` extra",
        "multiple": f"```json\n{value}\n```\n```json\n{value}\n```",
        "nan": '{"begin":NaN}',
        "deep": "[" * 100 + "0" + "]" * 100,
        "oversize": " " * 65537 + value,
        "empty": "```json\n```",
    }
    r = run(
        m,
        lambda req: httpx.Response(
            200, json=protocol_response(protocol, text=texts[variant])
        ),
    )
    before = r.model_dump_json()
    f = facets(m, r, observed)
    assert f["retrieval"].status == expected
    assert f["format"].status == ("PASS" if variant == "raw" else "FAIL")
    assert r.model_dump_json() == before


@pytest.mark.parametrize("protocol", ["chat", "responses"])
def test_json_mode_cannot_use_fence(protocol, observed):
    m = selected("sizes-small", "L01", protocol)
    value = json.dumps(m.cases[0].oracle["expected_facts"])
    r = run(
        m,
        lambda req: httpx.Response(
            200, json=protocol_response(protocol, text=f"```json\n{value}\n```")
        ),
    )
    o = next(iter(observed.values()))[0]
    if protocol == "chat":
        o.request_payload["response_format"] = {"type": "json_object"}
    else:
        o.request_payload["text"] = {"format": {"type": "json_schema"}}
    assert facets(m, r, observed)["retrieval"].status == "FAIL"


@pytest.mark.parametrize("defect", ["ack", "history"])
def test_history_retrieval_requires_valid_prior_turns(defect, observed):
    m = selected("sizes-small", "L05")
    count = 0

    def respond(req):
        nonlocal count
        count += 1
        text = json.dumps(m.cases[0].oracle["expected_facts"]) if count == 4 else "ack"
        if count == 1 and defect == "ack":
            text = "wrong"
        return httpx.Response(200, json=response(text))

    r = run(m, respond)
    if defect == "history":
        next(iter(observed.values()))[-1].request_payload["messages"] = []
    assert facets(m, r, observed)["retrieval"].status == "FAIL"


@pytest.mark.parametrize("protocol", ["chat", "responses"])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize(
    "defect,expected",
    [
        ("none", "PASS"),
        ("no_reasoning", "PASS"),
        ("stop", "INCONCLUSIVE"),
        ("missing", "INCONCLUSIVE"),
        ("negative", "FAIL"),
        ("inconsistent", "FAIL"),
        ("over_cap", "FAIL"),
        ("bad_sequence", "FAIL"),
        ("small", "INCONCLUSIVE"),
    ],
)
def test_total_output_budget(protocol, stream, defect, expected, observed):
    m = selected("sizes-small", "L10", protocol, stream=stream)
    body = output_body(
        protocol,
        length=defect != "stop",
        usage=20 if defect == "small" else 3700,
        text="record-000003\n" if defect == "bad_sequence" else None,
    )
    usage = body["usage"]
    output_key = "completion_tokens" if protocol == "chat" else "output_tokens"
    details = output_key + "_details"
    if defect == "no_reasoning":
        usage.pop(details)
    if defect == "missing":
        body["usage"] = None
    if defect == "negative":
        usage[output_key] = -1
    if defect == "inconsistent":
        usage["total_tokens"] += 1
    if defect == "over_cap":
        usage[output_key] = 4097
        usage["total_tokens"] = 4117
    r = run(m, lambda req: wire(protocol, body, stream))
    f = facets(m, r, observed)
    assert f["budget"].status == expected
    if defect == "no_reasoning":
        assert f["visible"].status == "INCONCLUSIVE"


@pytest.mark.parametrize("malformed", [False, True])
def test_optional_invalid_schema_retains_protocol_boundary(malformed, observed):
    from test_runner import tool

    m = selected("schemas", "S45")
    arguments = (
        "{" if malformed else '{"child": {"child": {"child": {"child": {"child": 7}}}}}'
    )
    r = run(
        m,
        lambda req: httpx.Response(
            200,
            json=response(
                "", tools=[tool(name="record_schema_fixture", arguments=arguments)]
            ),
        ),
    )
    f = facets(m, r, observed)
    assert f["capability"].status == "FAIL"
    assert f["protocol"].status == ("FAIL" if malformed else "PASS")


@pytest.mark.parametrize("protocol", ["chat", "responses"])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("reasoning_rounds", [0, 1, 2])
def test_workflow_functionality_does_not_invent_accumulated_reasoning(
    protocol, stream, reasoning_rounds, observed
):
    from test_workflows import fixture_body

    from deepseek_provider_verifier.acceptance import assess_run, load_policy
    from deepseek_provider_verifier.acceptance_facets import derive_facets
    from deepseek_provider_verifier.cli import _run_context
    from deepseek_provider_verifier.runner import rehash_manifest

    m = selected("workflows-small", "W04", protocol, thinking=True, stream=stream)
    m = rehash_manifest(
        m.model_copy(
            update={"budgets": m.budgets.model_copy(update={"protocols": [protocol]})}
        )
    )
    count = 0

    def respond(req):
        nonlocal count
        body = fixture_body(
            protocol,
            m.cases[0].steps[count]["expect"],
            reasoning=count < reasoning_rounds,
        )
        count += 1
        return wire(protocol, body, stream)

    r = run(m, respond)
    f = facets(m, r, observed)
    assert f["functional"].status == "PASS"
    assert f["reasoning"].status == (
        "PASS" if reasoning_rounds == 2 else "INCONCLUSIVE"
    )
    assert f["raw_contract"].status == r.case_results[0].status
    assert r.case_results[0].status == (
        "PASS" if reasoning_rounds == 2 else "INCONCLUSIVE"
    )
    r = r.model_copy(update={"report": _run_context(m, r, "verified")})
    derived = derive_facets(m, r, observed)
    accepted = assess_run(m, r, derived, load_policy("official-compatible-v1"), "test")
    assert accepted.exit_code == 0
    assert bool(accepted.uncertified_capabilities) == (reasoning_rounds < 2)
    strict = assess_run(m, r, derived, load_policy("strict-contract-v1"), "test")
    assert strict.exit_code == (0 if reasoning_rounds == 2 else 2)
