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
