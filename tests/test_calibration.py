"""Executable scope must not promote unobserved variants or model families."""

import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError
from test_assertions import rule
from test_runner import manifest, response, run
from test_task3_review_fixes import protocol_response

from deepseek_provider_verifier.config import load_profile
from deepseek_provider_verifier.runner import rehash_manifest

PROFILE = Path("profiles/deepseek-flash-smoke-2026-09-21-v1.json")


@pytest.mark.parametrize(
    "scope",
    [
        None,
        [],
        "",
        "C01.chat.non_thinking.nonstream",
        {},
        [1],
        [""],
        ["   "],
        ["a", "a"],
    ],
)
def test_invalid_calibration_scope_is_rejected(scope):
    with pytest.raises(ValidationError, match="calibrated_variants"):
        rule(conditions={"calibrated_variants": scope})


@pytest.mark.parametrize("protocol", ["chat", "responses"])
@pytest.mark.parametrize(
    "scope,model,expected",
    [
        ("match", "fixture-model", "PASS"),
        ("other", "fixture-model", "INCONCLUSIVE"),
        ("match", "other-model", "INCONCLUSIVE"),
        ("invalid_suffix", "fixture-model", "INCONCLUSIVE"),
    ],
)
def test_scoped_rule_is_downgraded_not_dropped_with_other_generic_gate(
    protocol, scope, model, expected
):
    m = manifest(protocol=protocol, repetitions=1 if scope == "invalid_suffix" else 2)
    base_id = f"C01.{protocol}.non_thinking.nonstream"
    scoped = rule(
        id="scoped",
        protocol=protocol,
        conditions={
            "models": [model],
            "calibrated_variants": [
                base_id if scope != "other" else "C99.chat.non_thinking.stream"
            ],
        },
    )
    cases = [
        c.model_copy(
            update={
                "rule_ids": ["test", "scoped"],
                **(
                    {"id": base_id + ".rnotaninteger"}
                    if scope == "invalid_suffix"
                    else {}
                ),
            }
        )
        for c in m.cases
    ]
    m = rehash_manifest(
        m.model_copy(
            update={
                "cases": cases,
                "profile_snapshot": m.profile_snapshot.model_copy(
                    update={"rules": [*m.profile_snapshot.rules, scoped]}
                ),
            }
        )
    )
    result = run(
        m,
        lambda r: httpx.Response(
            200,
            json=response()
            if protocol == "chat"
            else protocol_response(protocol, text="amber", reasoning=False),
        ),
    )
    assert all(c.status == expected for c in result.case_results)
    if expected == "INCONCLUSIVE":
        assert all(
            any("scoped" in a.rule_ids and not a.gating for a in c.assertions)
            for c in result.case_results
        )


def test_versioned_profile_promotes_only_recorded_coverage():
    p = load_profile(PROFILE)
    evidence = json.loads(
        Path("docs/calibration/official-flash-2026-09-21.json").read_text()
    )
    gates = {r.id: r for r in p.rules if r.gating}
    assert len(gates) == 19
    assert set(gates) == set(evidence["rule_case_coverage"])
    for rule_id, variants in evidence["rule_case_coverage"].items():
        assert gates[rule_id].conditions["calibrated_variants"] == variants
        assert gates[rule_id].conditions["models"] == ["deepseek-flash"]
    base = load_profile(Path("profiles/deepseek-api-2026-09-21.json"))
    assert all(not r.gating and r.maturity == "diagnostic" for r in base.rules)
    assert len(evidence["trials"]) == 24
    assert evidence["observed_assertion_counts"] == {"PASS": 197}


def test_calibrated_scope_matches_actual_shipped_smoke_variants():
    from deepseek_provider_verifier.catalog import load_cases
    from deepseek_provider_verifier.config import load_config
    from deepseek_provider_verifier.planner import build_manifest

    profile = load_profile(PROFILE)
    config = load_config(Path("configs/providers.example.toml"))
    config = config.model_copy(
        update={
            "run": config.run.model_copy(update={"profile": profile.id}),
            "endpoints": {"reference": config.endpoints["reference"]},
        }
    )
    selected = (
        profile.presets["smoke"].case_ids
        + profile.presets["smoke"].attached_assertion_case_ids
    )
    planned = build_manifest(
        config, profile, load_cases(["chat", "responses"], selected)
    )
    evidence = json.loads(
        Path("docs/calibration/official-flash-2026-09-21.json").read_text()
    )
    assert {c.id for c in planned.cases} == {t["case_id"] for t in evidence["trials"]}
    assert planned.request_ceiling == 34
    for r in profile.rules:
        if r.gating:
            actual = {c.id for c in planned.cases if r.id in c.rule_ids}
            assert actual == set(r.conditions["calibrated_variants"])
