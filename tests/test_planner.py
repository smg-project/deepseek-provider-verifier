from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest
from pydantic import ValidationError

from deepseek_provider_verifier.config import load_config, load_profile
from deepseek_provider_verifier.planner import build_manifest
from deepseek_provider_verifier.records import (
    CaseResult,
    CaseTemplate,
    Config,
    Endpoint,
    Profile,
    ProfilePreset,
    Rule,
    RunSettings,
)


def _rule(rule_id: str = "R_TEXT", protocol: str = "chat") -> Rule:
    return Rule(
        id=rule_id,
        protocol=protocol,
        conditions={"mode": "any"},
        expectation="A successful response contains an output item.",
        assertion_id="output_present",
        source_url="https://api-docs.deepseek.com/api/create-chat-completion/",
        source_section="Responses / 200",
        retrieved_at="2026-09-22T00:19:43Z",
        source_sha256="4c2384d7ad4e5f1929edb922900b2a96546a7e699e8b067d6e82dcb9a330989d",
        evidence_status="documented",
        maturity="diagnostic",
        gating=False,
    )


@pytest.fixture
def profile() -> Profile:
    return Profile(
        id="test-profile",
        models=["deepseek-chat"],
        rules=[_rule()],
        presets={
            "smoke": ProfilePreset(
                max_requests_per_protocol=17,
                max_requests_per_endpoint=34,
                max_total_requests=68,
                max_requests_per_conversation=4,
                max_retries=0,
                concurrency=1,
                case_deadline_seconds=300,
                mode_max_output_tokens={"non_thinking": 512, "thinking": 4096},
                case_ids=["C01"],
                attached_assertion_case_ids=["C02", "C17", "C20"],
            )
        },
    )


@pytest.fixture
def config(monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv("DEEPSEEK_TEST_KEY", "secret-must-never-be-serialized")
    return Config(
        run=RunSettings(
            profile="test-profile",
            suite="smoke",
            protocols=["chat"],
            concurrency=1,
            max_attempts_per_endpoint=34,
            retries=0,
            scorer_revision="scorer-v1",
        ),
        endpoints={
            "reference": Endpoint(
                name="reference",
                base_url="https://api.deepseek.com",
                model="deepseek-chat",
                model_release="DeepSeek-V3.1",
                api_key_env="DEEPSEEK_TEST_KEY",
            ),
            "candidate": Endpoint(
                name="candidate",
                base_url="http://localhost:8000",
                model="deepseek-chat",
                model_release="candidate-build-17",
                auth_none=True,
            ),
        },
    )


@pytest.fixture
def one_text_template() -> CaseTemplate:
    return CaseTemplate(
        id="C01",
        protocol="chat",
        modes=["non_thinking", "thinking"],
        streams=[False, True],
        rule_ids=["R_TEXT"],
        steps=[{"kind": "user", "content": "Reply with pong."}],
        required=True,
        max_requests=1,
        max_output_tokens={"non_thinking": 512, "thinking": 4096},
        oracle={"kind": "exact_text", "value": "pong"},
    )


def test_planner_counts_every_variant(config, profile, one_text_template):
    manifest = build_manifest(config, profile, [one_text_template])

    assert len(manifest.cases) == 4
    assert manifest.request_ceiling == 4 * len(config.endpoints)
    assert manifest.output_token_ceiling == (2 * 512 + 2 * 4096) * len(config.endpoints)
    assert manifest.budgets.suite == "smoke"
    assert manifest.budgets.protocols == ["chat"]
    assert manifest.budgets.retries == 0
    assert manifest.budgets.concurrency == 1
    assert manifest.budgets.max_attempts_per_endpoint == 34
    assert [case.id for case in manifest.cases] == [
        "C01.chat.non_thinking.nonstream",
        "C01.chat.non_thinking.stream",
        "C01.chat.thinking.nonstream",
        "C01.chat.thinking.stream",
    ]


def test_planner_rejects_budget_overflow(config, profile, one_text_template):
    oversized_cases = [one_text_template.model_copy(update={"max_requests": 18})]

    with pytest.raises(ValueError, match="request budget"):
        build_manifest(config, profile, oversized_cases)


def test_planner_counts_each_possible_step_and_retry(config, profile):
    retry_config = config.model_copy(
        update={"run": config.run.model_copy(update={"retries": 1})}
    )
    retry_profile = profile.model_copy(
        update={
            "presets": {
                "smoke": profile.presets["smoke"].model_copy(
                    update={
                        "max_retries": 1,
                        "max_requests_per_protocol": 4,
                        "max_requests_per_endpoint": 4,
                        "max_total_requests": 8,
                    }
                )
            }
        }
    )
    template = CaseTemplate(
        id="C13",
        protocol="chat",
        modes=["non_thinking"],
        streams=[False],
        rule_ids=["R_TEXT"],
        steps=[{"kind": "user"}, {"kind": "assistant"}],
        required=True,
        max_requests=2,
        max_output_tokens={"non_thinking": 512},
        oracle={"kind": "conversation"},
    )

    manifest = build_manifest(retry_config, retry_profile, [template])

    assert manifest.request_ceiling == 8  # 2 steps * (initial + retry) * 2 endpoints
    assert manifest.output_token_ceiling == 4096


def test_planner_rejects_missing_rule_reference(config, profile, one_text_template):
    template = one_text_template.model_copy(update={"rule_ids": ["DOES_NOT_EXIST"]})

    with pytest.raises(ValueError, match="unknown rule"):
        build_manifest(config, profile, [template])


def test_planner_rejects_duplicate_expanded_case_ids(
    config, profile, one_text_template
):
    with pytest.raises(ValueError, match="duplicate case ID"):
        build_manifest(config, profile, [one_text_template, one_text_template])


def test_planner_is_network_free_and_never_serializes_secret(
    monkeypatch, config, profile, one_text_template
):
    def deny_network(*_args, **_kwargs):
        raise AssertionError("planner attempted network access")

    monkeypatch.setattr(socket, "create_connection", deny_network)
    first = build_manifest(config, profile, [one_text_template])
    second = build_manifest(config, profile, [one_text_template])
    payload = first.model_dump_json()

    assert "DEEPSEEK_TEST_KEY" in payload
    assert "secret-must-never-be-serialized" not in payload
    assert first.dataset_hash == second.dataset_hash
    assert first.profile_hash == second.profile_hash
    assert first.manifest_hash == second.manifest_hash
    assert first.run_id != second.run_id


def test_unresolved_rules_cannot_be_gating():
    payload = _rule().model_dump()
    payload["gating"] = True
    with pytest.raises(ValidationError, match="diagnostic rule cannot be gating"):
        Rule.model_validate(payload)


@pytest.mark.parametrize("protocol", ["completions", "grpc", "CHAT"])
def test_records_reject_invalid_protocol_names(protocol):
    with pytest.raises(ValidationError):
        _rule(protocol=protocol)


def test_endpoint_rejects_conflicting_authentication_settings():
    with pytest.raises(ValidationError, match="exactly one"):
        Endpoint(
            name="bad",
            base_url="https://example.com",
            model="deepseek-chat",
            model_release="release",
            api_key_env="TOKEN",
            auth_none=True,
        )


def test_records_reject_negative_budget():
    with pytest.raises(ValidationError):
        ProfilePreset(
            max_requests_per_protocol=-1,
            max_requests_per_endpoint=34,
            max_total_requests=34,
            max_requests_per_conversation=4,
            max_retries=0,
            concurrency=1,
            case_deadline_seconds=300,
            mode_max_output_tokens={"non_thinking": 512, "thinking": 4096},
        )


def test_result_status_uses_canonical_spelling():
    base = {
        "case_id": "C01.chat.non_thinking.nonstream",
        "endpoint": "candidate",
        "assertions": [],
        "metric_observations": [],
        "attempt_refs": [],
    }

    for status in ("PASS", "FAIL", "ERROR", "SKIP", "INCONCLUSIVE"):
        assert CaseResult(**base, status=status).status == status
    with pytest.raises(ValidationError):
        CaseResult(**base, status="pass")


def test_config_loader_rejects_unknown_keys(tmp_path: Path):
    path = tmp_path / "providers.toml"
    path.write_text(
        """
[run]
profile = "test-profile"
suite = "smoke"
protocols = ["chat"]
concurrency = 1
max_attempts_per_endpoint = 34
retries = 0
scorer_revision = "v1"
surprise = true

[endpoints.local]
base_url = "http://localhost:8000"
model = "deepseek-chat"
model_release = "local"
auth_none = true
"""
    )

    with pytest.raises(ValidationError, match="surprise"):
        load_config(path)


def test_public_loaders_accept_toml_and_json_fixtures(tmp_path: Path, profile):
    config_path = tmp_path / "providers.toml"
    config_path.write_text(
        """
[run]
profile = "test-profile"
suite = "smoke"
protocols = ["chat"]
concurrency = 1
max_attempts_per_endpoint = 34
retries = 0
scorer_revision = "v1"

[endpoints.local]
base_url = "http://localhost:8000"
model = "deepseek-chat"
model_release = "local-build"
auth_none = true
"""
    )
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(profile.model_dump_json())

    loaded_config = load_config(config_path)
    loaded_profile = load_profile(profile_path)

    assert loaded_config.endpoints["local"].name == "local"
    assert loaded_profile.id == "test-profile"


def test_all_serialized_records_include_schema_version(
    config, profile, one_text_template
):
    manifest = build_manifest(config, profile, [one_text_template])

    assert config.model_dump()["schema_version"] == 1
    assert config.endpoints["reference"].model_dump()["schema_version"] == 1
    assert profile.model_dump()["schema_version"] == 1
    assert one_text_template.model_dump()["schema_version"] == 1
    assert manifest.model_dump()["schema_version"] == 1
    assert manifest.cases[0].model_dump()["schema_version"] == 1


def test_smoke_profile_encodes_exact_request_ceiling():
    loaded = load_profile(Path("profiles/deepseek-api-2026-09-21.json"))
    smoke = loaded.presets["smoke"]

    assert smoke.max_requests_per_protocol == 17
    assert smoke.max_requests_per_endpoint == 34
    assert smoke.max_retries == 0
    assert smoke.max_requests_per_conversation == 4
    assert smoke.concurrency == 1
    assert smoke.case_deadline_seconds == 300
    assert smoke.mode_max_output_tokens == {"non_thinking": 512, "thinking": 4096}
    assert smoke.case_ids == ["C01", "C03", "C05", "C07", "C11", "C13", "C14"]
    assert smoke.attached_assertion_case_ids == ["C02", "C17", "C20"]


def test_exact_smoke_matrix_uses_17_requests_per_protocol():
    loaded_profile = load_profile(Path("profiles/deepseek-api-2026-09-21.json"))
    loaded_config = load_config(Path("configs/providers.example.toml"))

    manifest = build_manifest(loaded_config, loaded_profile, _smoke_templates())

    assert (
        sum(case.max_requests for case in manifest.cases if case.protocol == "chat")
        == 17
    )
    assert (
        sum(
            case.max_requests for case in manifest.cases if case.protocol == "responses"
        )
        == 17
    )
    assert manifest.request_ceiling == 34 * len(loaded_config.endpoints)


def test_full_matrix_fails_under_smoke_budget():
    loaded_profile = load_profile(Path("profiles/deepseek-api-2026-09-21.json"))
    loaded_config = load_config(Path("configs/providers.example.toml"))
    templates = []
    for protocol in ("chat", "responses"):
        rule_id = f"{protocol}.output.present"
        templates.extend(
            _template(f"C{number:02d}", protocol, rule_id, ["non_thinking"], [False], 1)
            for number in range(1, 25)
        )

    with pytest.raises(ValueError, match="request budget"):
        build_manifest(loaded_config, loaded_profile, templates)


def test_exported_schemas_come_from_runtime_records():
    schema = json.loads(Path("schemas/manifest.schema.json").read_text())

    assert schema["title"] == "Manifest"
    assert schema["properties"]["schema_version"]["const"] == 1


def _smoke_templates() -> list[CaseTemplate]:
    variants = [
        ("C01", ["non_thinking"], [False, True], 1),
        ("C03", ["thinking"], [False, True], 1),
        ("C05", ["non_thinking"], [False], 1),
        ("C07", ["non_thinking"], [False, True], 1),
        ("C11", ["non_thinking"], [False, True], 1),
        ("C13", ["non_thinking"], [False, True], 2),
        ("C14", ["thinking"], [False], 4),
    ]
    templates = []
    for protocol in ("chat", "responses"):
        rule_id = f"{protocol}.output.present"
        templates.extend(
            _template(case_id, protocol, rule_id, modes, streams, max_requests)
            for case_id, modes, streams, max_requests in variants
        )
    return templates


def _template(case_id, protocol, rule_id, modes, streams, max_requests):
    return CaseTemplate(
        id=case_id,
        protocol=protocol,
        modes=modes,
        streams=streams,
        rule_ids=[rule_id],
        steps=[{"kind": "user", "content": "fixture"}],
        required=True,
        max_requests=max_requests,
        max_output_tokens={
            mode: 512 if mode == "non_thinking" else 4096 for mode in modes
        },
        oracle={"kind": "fixture"},
    )
