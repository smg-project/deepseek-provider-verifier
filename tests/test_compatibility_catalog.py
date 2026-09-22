"""New opt-in contracts must remain usable for arbitrary self-hosted model labels."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_compatibility_policy import paired_manifest, policy_manifest
from test_runner import manifest

from deepseek_provider_verifier.catalog import load_cases
from deepseek_provider_verifier.config import load_config, load_profile
from deepseek_provider_verifier.planner import build_manifest
from deepseek_provider_verifier.records import Manifest, Profile, Rule

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("policy", ["self-hosted", "official-parity"])
def test_new_profile_plans_both_protocols_without_model_name_calibration_downgrade(
    policy,
):
    config = load_config(ROOT / "configs/self-hosted.example.toml")
    profile = load_profile(ROOT / f"profiles/deepseek-{policy}-2026-09-22-v1.json")
    config = config.model_copy(
        update={"run": config.run.model_copy(update={"profile": profile.id})}
    )
    templates = load_cases(case_ids=profile.presets[config.run.suite].case_ids)
    m = build_manifest(config, profile, templates)
    assert len(m.cases) == 99
    assert m.request_ceiling == 123
    assert {c.template_id for c in m.cases} >= {f"P{i:02d}" for i in range(1, 11)}
    assert {c.template_id for c in m.cases}.isdisjoint({"C10", "C15", "C24"})
    assert m.gates
    assert all(not r.conditions.get("models") for r in profile.rules)
    assert all(not r.conditions.get("calibrated_variants") for r in profile.rules)
    probe_config = config.model_copy(
        update={"run": config.run.model_copy(update={"suite": "probes"})}
    )
    probes = build_manifest(
        probe_config, profile, load_cases(case_ids=profile.presets["probes"].case_ids)
    )
    assert len(probes.cases) == 37
    assert probes.request_ceiling == 43


def test_legacy_default_catalog_stays_unchanged():
    assert {c.id for c in load_cases()} == {f"C{i:02d}" for i in range(1, 25)}


def test_catalog_boundaries_are_explicit_and_paired():
    chat = {c.id: c for c in load_cases(["chat"], [f"P{i:02d}" for i in range(1, 11)])}
    assert len(chat) == 10
    assert chat["P01"].max_requests == 3
    assert chat["P01"].steps[2]["replay_from"] == 0
    assert len(chat["P05"].steps[0]["request"]["user_id"]) == 512
    assert len(chat["P06"].steps[0]["request"]["user_id"]) == 513
    responses = {
        c.id: c for c in load_cases(["responses"], [f"P{i:02d}" for i in range(7, 11)])
    }
    formats = {
        id: c.steps[0]["request"]["text"]["format"] for id, c in responses.items()
    }
    assert formats["P07"]["schema"] == formats["P08"]["schema"]
    assert formats["P09"]["schema"] == formats["P10"]["schema"]
    assert formats["P09"]["strict"] is True
    assert "strict" not in formats["P10"]
    assert "type" not in formats["P09"]["schema"]["properties"]["label"]


@pytest.mark.parametrize("source", [-1, 1, 99, True, "0"])
def test_replay_branches_are_validated_before_network(source):
    value = paired_manifest("chat").model_dump(mode="json")
    value["cases"][0]["steps"][1]["replay_from"] = source
    with pytest.raises(ValidationError, match="replay_from"):
        Manifest.model_validate(value)


def test_unknown_and_mixed_compatibility_policies_fail_closed():
    r = manifest().profile_snapshot.rules[0].model_dump(mode="json")
    r["conditions"]["compatibility_policy"] = "typo"
    with pytest.raises(ValidationError, match="compatibility_policy"):
        Rule.model_validate(r)
    p = policy_manifest(manifest()).profile_snapshot.model_dump(mode="json")
    second = json.loads(json.dumps(p["rules"][0]))
    second["id"] = "second"
    second["conditions"]["compatibility_policy"] = "official_parity"
    p["rules"].append(second)
    with pytest.raises(ValidationError, match="compatibility polic"):
        Profile.model_validate(p)
