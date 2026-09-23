"""Versioned JSON examples retain strict value/schema gates and legacy findings."""

import json
from pathlib import Path

import pytest
from test_acceptance_facets import observed  # noqa: F401
from test_runner import run
from test_size_cases import wire
from test_task3_review_fixes import protocol_response

from deepseek_provider_verifier.acceptance_facets import derive_facets
from deepseek_provider_verifier.acceptance_records import case_family
from deepseek_provider_verifier.assertions import rule_applies
from deepseek_provider_verifier.catalog import load_cases
from deepseek_provider_verifier.cli import _load_configuration, _manifest
from deepseek_provider_verifier.depth_catalog import load_depth_prompts
from deepseek_provider_verifier.runner import rehash_manifest

ROOT = Path(__file__).resolve().parents[1]


def body(protocol, text):
    value = protocol_response(protocol, text=text, reasoning=False)
    if protocol == "responses":
        value["usage"] = {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5}
    return value


def selected(template, protocol, mode="non_thinking", stream=False):
    config, profile = _load_configuration(
        ROOT / "configs/self-hosted-verify.example.toml", None
    )
    config = config.model_copy(
        update={"run": config.run.model_copy(update={"suite": "full-stress"})}
    )
    m = _manifest(config, profile)
    c = next(
        c
        for c in m.cases
        if (c.template_id, c.protocol, c.mode, c.stream)
        == (template, protocol, mode, stream)
    )
    return rehash_manifest(
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


def test_new_examples_are_versioned_and_old_prompts_preserved():
    prompts = load_depth_prompts()
    assert (
        prompts["depth-structured-2"].content_hash
        == "7ddb52ef3f507af7d48b8de602689cd591223647eb5ab03b8bb37cd2cbacf43a"
    )
    for c in load_cases(case_ids=["R41", "R42"]):
        prompt = prompts[c.prompt_id]
        assert c.dataset_version == prompt.dataset_version == "depth-v2"
        assert (
            json.dumps(prompt.intended_answer, separators=(",", ":")) in prompt.content
        )
        assert c.oracle["schema"]["additionalProperties"] is False
    config, profile = _load_configuration(
        ROOT / "configs/self-hosted-verify.example.toml", None
    )
    m = _manifest(config, profile)
    assert {"R41", "R42"} <= {c.template_id for c in m.cases}
    assert not {"R36", "R37"} & {c.template_id for c in m.cases}


@pytest.mark.parametrize("protocol", ["chat", "responses"])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("mode", ["non_thinking", "thinking"])
@pytest.mark.parametrize(
    "text,expected",
    [
        ('{"label":"amber","count":1}', "PASS"),
        ('{"label":"amber","count":1,"type":"json_object"}', "FAIL"),
        ('{"label":"wrong","count":1}', "FAIL"),
        ('{"label":"amber"}', "FAIL"),
        ('{"label":"amber","count":"1"}', "FAIL"),
        ('```json\n{"label":"amber","count":1}\n```', "FAIL"),
        ('{"label":"wrong","label":"amber","count":1}', "FAIL"),
        ("not json", "FAIL"),
    ],
)
def test_v2_controls_keep_exact_schema_and_raw_json(
    protocol,
    stream,
    mode,
    text,
    expected,
    observed,  # noqa: F811
):
    m = selected("R41", protocol, mode, stream)
    r = run(
        m,
        lambda req: wire(protocol, body(protocol, text), stream),
    )
    facets = {f.name: f for f in derive_facets(m, r, observed)}
    assert case_family(m.cases[0]) == "functional"
    assert (facets["protocol"].status == facets["functional"].status == "PASS") == (
        expected == "PASS"
    )


@pytest.mark.parametrize("protocol", ["chat", "responses"])
@pytest.mark.parametrize(
    "text,valid_json",
    [
        ('{"label":"violet","count":2,"type":"json_object"}', True),
        ('{"label":"violet","count":2,"count":2}', False),
        ('```json\n{"label":"violet","count":2}\n```', False),
    ],
)
def test_legacy_structured_quality_keeps_protocol_contract(
    protocol,
    text,
    valid_json,
    observed,  # noqa: F811
):
    m = selected("R37", protocol)
    r = run(
        m,
        lambda req: wire(protocol, body(protocol, text), False),
    )
    facets = {f.name: f for f in derive_facets(m, r, observed)}
    assert facets["protocol"].status == ("PASS" if valid_json else "FAIL")
    assert case_family(m.cases[0]) == (
        "quality" if protocol == "chat" else "functional"
    )
    if valid_json:
        assert facets["raw_contract"].status == "FAIL"
        assert (
            facets["quality" if protocol == "chat" else "functional"].status == "FAIL"
        )
