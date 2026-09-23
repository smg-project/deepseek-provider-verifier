"""Frozen policy and workload coverage is meaningful on any endpoint label."""

from pathlib import Path

import pytest

from deepseek_provider_verifier.acceptance import load_policy
from deepseek_provider_verifier.acceptance_records import FAMILY_FACETS, case_family
from deepseek_provider_verifier.catalog import load_cases
from deepseek_provider_verifier.cli import (
    _load_configuration,
    _manifest,
    acceptance_preflight,
    main,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("suite", ["verification", "full-stress"])
def test_verification_inventory(suite):
    config, profile = _load_configuration(
        ROOT / "configs/self-hosted-verify.example.toml", None
    )
    config = config.model_copy(
        update={"run": config.run.model_copy(update={"suite": suite})}
    )
    m = _manifest(config, profile)
    policy = load_policy("official-compatible-v1")
    strict = load_policy("strict-contract-v1")
    assert strict.mode == "strict"
    inventory = acceptance_preflight(m, policy)
    assert inventory["facet_counts"]["required"] > 0
    assert inventory["facet_counts"]["diagnostic"] > 0
    assert {case_family(c) for c in m.cases} == set(FAMILY_FACETS)
    assert {
        (c.protocol, c.mode, c.stream)
        for c in m.cases
        if case_family(c) == "functional"
    } == {
        (p, mode, stream)
        for p in ["chat", "responses"]
        for mode in ["thinking", "non_thinking"]
        for stream in [False, True]
    }
    assert any(c.oracle.get("applicable") is False for c in m.cases)
    assert m.budgets.retries == 0 and m.budgets.concurrency == 1
    originals = {(c.protocol, c.id): c for c in load_cases(case_ids=m.budgets.case_ids)}
    for c in m.cases:
        original = originals[c.protocol, c.template_id]
        assert c.steps == original.steps
        assert c.max_output_tokens == original.max_output_tokens[c.mode]
    for r in profile.rules:
        assert not {"models", "model_releases", "calibrated_variants"} & set(
            r.conditions
        )


def test_plan_acceptance_inventory_is_offline(capsys):
    assert (
        main(
            [
                "plan",
                "--config",
                str(ROOT / "configs/self-hosted-verify.example.toml"),
                "--policy",
                "official-compatible-v1",
            ]
        )
        == 0
    )
    import json

    output = json.loads(capsys.readouterr().out)
    assert output["acceptance"]["policy_hash"]
    assert output["acceptance"]["facet_counts"]["required"] > 0


@pytest.mark.parametrize("protocol", ["chat", "responses"])
@pytest.mark.parametrize("missing_terminal", [False, True])
def test_combined_profile_length_terminal_remains_valid(
    protocol, missing_terminal, monkeypatch
):
    import httpx
    from test_runner import run
    from test_size_cases import output_body, wire

    from deepseek_provider_verifier import runner
    from deepseek_provider_verifier.acceptance_facets import derive_facets
    from deepseek_provider_verifier.runner import rehash_manifest

    config, profile = _load_configuration(
        ROOT / "configs/self-hosted-verify.example.toml", None
    )
    m = _manifest(config, profile)
    c = next(
        c
        for c in m.cases
        if c.template_id == "L09" and c.protocol == protocol and c.stream
    )
    m = rehash_manifest(
        m.model_copy(
            update={
                "cases": [c],
                "request_ceiling": 1,
                "output_token_ceiling": c.max_output_tokens,
                "budgets": m.budgets.model_copy(update={"protocols": [protocol]}),
            }
        )
    )
    from deepseek_provider_verifier.assertions import rule_applies

    m = rehash_manifest(
        m.model_copy(
            update={
                "gates": sorted(
                    {
                        r.assertion_id
                        for r in profile.rules
                        if r.id in c.rule_ids and r.gating and rule_applies(r, c)
                    }
                )
            }
        )
    )
    observations = {}
    original = runner.evaluate_case

    def capture(case, values, rules):
        if values:
            observations[values[0].endpoint, case.id] = values
        return original(case, values, rules)

    monkeypatch.setattr(runner, "evaluate_case", capture)
    body = output_body(protocol, usage=c.max_output_tokens)
    response = wire(protocol, body, True)
    if missing_terminal:
        data = (
            response.content.replace(b"data: [DONE]\n\n", b"")
            if protocol == "chat"
            else b"\n\n".join(response.content.split(b"\n\n")[:-2]) + b"\n\n"
        )
        response = httpx.Response(
            200, content=data, headers={"content-type": "text/event-stream"}
        )
    r = run(m, lambda req: response)
    facets = {f.name: f for f in derive_facets(m, r, observations)}
    assert facets["protocol"].status == ("FAIL" if missing_terminal else "PASS")
    if not missing_terminal:
        assert facets["budget"].status == "PASS"
