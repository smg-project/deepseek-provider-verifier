"""Manual workflow config and artifact boundaries, without live credentials."""

import importlib.util
import json
import tomllib
from pathlib import Path

import pytest

from deepseek_provider_verifier.records import Config

spec = importlib.util.spec_from_file_location("ci_live", Path("scripts/ci_live.py"))
ci_live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci_live)


def env(**values):
    return {
        "DPV_ENDPOINT": "reference",
        "DPV_PROFILE": "deepseek-flash-smoke-2026-09-21-v1",
        "DPV_PROTOCOLS": "both",
        "DPV_CANDIDATE_URL": "",
        "DPV_CANDIDATE_MODEL": "served-alias",
        "GITHUB_SHA": "abc123",
        **values,
    }


def config(values):
    return Config.model_validate(tomllib.loads(ci_live.config_text(values)))


def test_official_key_routing_cannot_follow_candidate_url():
    c = config(env(DPV_CANDIDATE_URL="https://attacker.test"))
    endpoint = c.endpoints["reference"]
    assert str(endpoint.base_url) == "https://api.deepseek.com/"
    assert endpoint.api_key_env == "DEEPSEEK_API_KEY"
    assert c.run.retries == 0 and c.run.repetitions == 1
    assert c.run.max_attempts_per_endpoint == 34 and c.run.concurrency == 1


def test_candidate_uses_only_its_separate_credential_and_quotes_inputs():
    model = '🧪alias"\n[endpoints.reference]\napi_key_env="DEEPSEEK_API_KEY'
    c = config(
        env(
            DPV_ENDPOINT="candidate",
            DPV_CANDIDATE_URL="https://candidate.test/v1",
            DPV_CANDIDATE_MODEL=model,
        )
    )
    assert set(c.endpoints) == {"candidate"}
    assert c.endpoints["candidate"].api_key_env == "CANDIDATE_API_KEY"
    assert c.endpoints["candidate"].model == model
    assert c.endpoints["candidate"].contract_model == "deepseek-flash"


@pytest.mark.parametrize(
    "updates",
    [
        {"DPV_ENDPOINT": "untrusted"},
        {"DPV_PROFILE": "../../custom"},
        {"DPV_PROTOCOLS": "all"},
        *[
            {"DPV_ENDPOINT": "candidate", "DPV_CANDIDATE_URL": u}
            for u in [
                "",
                "http://candidate.test",
                "https://user:key@candidate.test",
                "https://candidate.test?key=secret",
            ]
        ],
    ],
)
def test_live_config_rejects_unbounded_or_unsafe_inputs(updates):
    with pytest.raises(ValueError):
        ci_live.config_text(env(**updates))


def test_sanitized_artifact_is_allowlisted_not_a_copy_of_evidence(tmp_path):
    sentinel = "SENTINEL_PRIVATE_REASONING_KEY"
    source = {
        "manifest_hash": "a" * 64,
        "complete": True,
        "exit_code": 0,
        "counts": {"PASS": 1},
        "case_results": [{"observed": sentinel}],
        "budget_usage": {"reference": 1},
        "extra": sentinel,
    }
    (tmp_path / "summary.json").write_text(json.dumps(source))
    safe = ci_live.safe_summary(tmp_path)
    assert sentinel not in json.dumps(safe)
    assert safe["counts"] == {"PASS": 1}
    assert safe["attempts"] == 1
    assert set(safe) == {
        "schema_version",
        "artifact_kind",
        "manifest_hash",
        "complete",
        "exit_code",
        "counts",
        "attempts",
    }
    source["counts"] = {sentinel: 1}
    (tmp_path / "summary.json").write_text(json.dumps(source))
    with pytest.raises(ValueError):
        ci_live.safe_summary(tmp_path)
