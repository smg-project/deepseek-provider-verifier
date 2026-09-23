"""The default core stays bounded and rejects incorrect provider responses."""

import json
from pathlib import Path

import httpx
import pytest
from test_acceptance_facets import observed  # noqa: F401
from test_acceptance_structured import body
from test_runner import run
from test_size_cases import wire

from deepseek_provider_verifier.acceptance import assess_run, load_policy
from deepseek_provider_verifier.acceptance_facets import derive_facets
from deepseek_provider_verifier.acceptance_records import case_family
from deepseek_provider_verifier.assertions import rule_applies
from deepseek_provider_verifier.cli import (
    _load_configuration,
    _manifest,
    _run_context,
    main,
)
from deepseek_provider_verifier.runner import rehash_manifest

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs/self-hosted-core.example.toml"


def test_core_plan_has_positive_coverage_without_stress_or_optional_probes(capsys):
    assert (
        main(["plan", "--config", str(CONFIG), "--policy", "strict-contract-v1"]) == 0
    )
    plan = json.loads(capsys.readouterr().out)
    m = plan["manifest"]
    assert (len(m["cases"]), m["request_ceiling"], m["output_token_ceiling"]) == (
        84,
        142,
        302080,
    )
    assert m["budgets"]["concurrency"] == 1 and m["budgets"]["retries"] == 0
    config, profile = _load_configuration(CONFIG, None)
    manifest = _manifest(config, profile)
    assert {case_family(c) for c in manifest.cases} == {
        "core",
        "contract",
        "schema.optional",
        "functional",
        "workflow",
        "workflow.thinking",
        "schema.core",
    }
    assert all(c.oracle.get("applicable") is not False for c in manifest.cases)
    assert all(
        c.oracle.get("depth", {}).get("family") == "schema.required"
        for c in manifest.cases
        if case_family(c) == "schema.optional"
    )
    assert {
        (c.protocol, c.mode, c.stream)
        for c in manifest.cases
        if case_family(c) == "functional"
    } == {
        (p, mode, stream)
        for p in ("chat", "responses")
        for mode in ("thinking", "non_thinking")
        for stream in (False, True)
    }


def test_core_preserves_calibrated_requests_oracles_and_assertions():
    config, profile = _load_configuration(CONFIG, None)
    current = _manifest(config, profile)
    old_config, old_profile = _load_configuration(
        ROOT / "configs/self-hosted-verify.example.toml", None
    )
    old = {c.id: c for c in _manifest(old_config, old_profile).cases}
    for case in current.cases:
        assert case == old[case.id]


@pytest.mark.parametrize("protocol", ["chat", "responses"])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize(
    "behavior", ["valid", "wrong", "extra", "malformed", "unfinished", "unauthorized"]
)
def test_core_keeps_exact_structured_and_protocol_gates(
    protocol,
    stream,
    behavior,
    observed,  # noqa: F811
):
    config, profile = _load_configuration(CONFIG, None)
    manifest = _manifest(config, profile)
    case = next(
        c
        for c in manifest.cases
        if (c.template_id, c.protocol, c.mode, c.stream)
        == ("R41", protocol, "non_thinking", stream)
    )
    manifest = rehash_manifest(
        manifest.model_copy(
            update={
                "cases": [case],
                "gates": sorted(
                    {
                        r.assertion_id
                        for r in profile.rules
                        if r.id in case.rule_ids and r.gating and rule_applies(r, case)
                    }
                ),
                "request_ceiling": 1,
                "output_token_ceiling": case.max_output_tokens,
                "budgets": manifest.budgets.model_copy(
                    update={"protocols": [protocol]}
                ),
            }
        )
    )

    def respond(request):
        if behavior == "unauthorized":
            return httpx.Response(401, json={"error": "synthetic authentication error"})
        text = {
            "wrong": '{"label":"wrong","count":1}',
            "extra": '{"label":"amber","count":1,"extra":true}',
            "malformed": "not JSON",
        }.get(behavior, '{"label":"amber","count":1}')
        value = body(protocol, text)
        if behavior == "unfinished" and not stream:
            if protocol == "chat":
                value["choices"][0]["finish_reason"] = None
            else:
                value["status"] = "in_progress"
        response = wire(protocol, value, stream)
        if behavior == "unfinished" and stream:
            content = (
                response.content.replace(b"data: [DONE]\n\n", b"")
                if protocol == "chat"
                else b"\n\n".join(response.content.split(b"\n\n")[:-2]) + b"\n\n"
            )
            response = httpx.Response(
                200, content=content, headers={"content-type": "text/event-stream"}
            )
        return response

    result = run(manifest, respond)
    result = result.model_copy(
        update={"report": _run_context(manifest, result, "verified")}
    )
    verdict = assess_run(
        manifest,
        result,
        derive_facets(manifest, result, observed),
        load_policy("strict-contract-v1"),
        "test",
    )
    assert (verdict.exit_code == 0) == (behavior == "valid")
    if behavior == "valid":
        assert result.counts == {"PASS": 1}


@pytest.mark.parametrize("protocol", ["chat", "responses"])
@pytest.mark.parametrize("valid", [True, False])
def test_core_strict_policy_gates_shallow_strict_schema(protocol, valid, observed):  # noqa: F811
    from test_workflows import fixture_body

    config, profile = _load_configuration(CONFIG, None)
    m = _manifest(config, profile)
    c = next(c for c in m.cases if c.template_id == "S02" and c.protocol == protocol)
    m = rehash_manifest(
        m.model_copy(
            update={
                "cases": [c],
                "gates": sorted(
                    {
                        r.assertion_id
                        for r in profile.rules
                        if r.id in c.rule_ids and r.gating and rule_applies(r, c)
                    }
                ),
                "request_ceiling": 1,
                "output_token_ceiling": c.max_output_tokens,
                "budgets": m.budgets.model_copy(update={"protocols": [protocol]}),
            }
        )
    )
    value = c.oracle["expected_value" if valid else "invalid_value"]
    response = fixture_body(
        protocol,
        {"tools": [{"name": "record_schema_fixture", "arguments": value}]},
        reasoning=False,
    )
    if protocol == "responses":
        response["usage"] = {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5}
    r = run(m, lambda request: httpx.Response(200, json=response))
    r = r.model_copy(update={"report": _run_context(m, r, "verified")})
    a = assess_run(
        m, r, derive_facets(m, r, observed), load_policy("strict-contract-v1"), "test"
    )
    assert a.exit_code == (0 if valid else 1)
