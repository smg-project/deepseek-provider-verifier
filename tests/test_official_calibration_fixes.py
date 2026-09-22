"""Regressions derived from the September 22 official two-model observations."""

import json

import httpx
import pytest
from test_runner import run
from test_task3_review_fixes import catalog_manifest, protocol_response


def test_responses_schema_fixture_sends_supported_typed_properties():
    def provider(request):
        payload = json.loads(request.content)
        schema = payload["text"]["format"]["schema"]
        if any("type" not in value for value in schema["properties"].values()):
            return httpx.Response(
                400, json={"error": "schema requires typed properties"}
            )
        return httpx.Response(
            200,
            json=protocol_response(
                "responses", text='{"label":"amber","count":3}', reasoning=False
            ),
        )

    result = run(catalog_manifest("C06", "responses"), provider)
    assert result.case_results[0].status == "PASS"


def test_chat_nonstream_usage_options_are_a_negative_probe():
    result = run(
        catalog_manifest("C24", "chat"),
        lambda request: httpx.Response(
            400, json={"error": "stream_options requires stream=true"}
        ),
    )
    assert result.case_results[0].status == "PASS"
    assert any(
        a.id == "EXPECTED_HTTP_REJECTION" and a.status == "PASS"
        for a in result.case_results[0].assertions
    )


@pytest.mark.parametrize("case_id", ["C08", "C09"])
def test_responses_forced_tools_are_rejected_only_in_thinking_mode(case_id):
    def provider(request):
        p = json.loads(request.content)
        if p["reasoning"]["effort"] != "none":
            return httpx.Response(
                400, json={"error": "Thinking mode does not support this tool_choice"}
            )
        body = protocol_response("responses", call=True, reasoning=False)
        body["output"][0].update(name="lookup_fixture", arguments='{"key":"harbor"}')
        return httpx.Response(200, json=body)

    result = run(catalog_manifest(case_id, "responses"), provider)
    assert [c.status for c in result.case_results] == ["PASS", "PASS"]
    rejected = result.case_results[1]
    assert any(
        a.id == "EXPECTED_HTTP_REJECTION" and a.status == "PASS"
        for a in rejected.assertions
    )


@pytest.mark.parametrize(
    "case_id,protocol", [("C04", "chat"), ("C04", "responses"), ("C23", "responses")]
)
def test_recall_fixture_specifies_the_exact_answer_format(case_id, protocol):
    requests = []

    def provider(request):
        p = json.loads(request.content)
        requests.append(p)
        if len(requests) == 1:
            text = "Acknowledged."
        else:
            prompt = p["messages" if protocol == "chat" else "input"][-1]["content"]
            explicit = "lowercase" in prompt and "no punctuation" in prompt
            text = "amber" if explicit else "Amber."
        return httpx.Response(
            200, json=protocol_response(protocol, text=text, reasoning=False)
        )

    result = run(catalog_manifest(case_id, protocol), provider)
    assert len(requests) == 2
    assert result.case_results[0].status == "PASS"


@pytest.mark.parametrize("protocol", ["chat", "responses"])
@pytest.mark.parametrize("continuation_status", [200, 400, 422])
def test_reasoning_omission_is_diagnostic_for_acceptance_and_rejection(
    protocol, continuation_status
):
    calls = []

    def provider(request):
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            return httpx.Response(200, json=protocol_response(protocol, call=True))
        if continuation_status == 200:
            return httpx.Response(
                200, json=protocol_response(protocol, reasoning=False)
            )
        return httpx.Response(continuation_status, json={"error": "omission rejected"})

    result = run(catalog_manifest("C15", protocol), provider)
    trial = result.case_results[0]
    assert len(calls) == 2
    assert trial.status == "INCONCLUSIVE"
    assert not any(a.id == "EXPECTED_HTTP_REJECTION" for a in trial.assertions)
    observed = next(
        a for a in trial.assertions if a.id == "REASONING_OMISSION_ACCEPTANCE"
    )
    assert observed.status == "INCONCLUSIVE" and not observed.gating
    assert observed.observed == {
        "status_code": continuation_status,
        "accepted": continuation_status == 200,
    }
    assert any(
        a.id == "NEGATIVE_PROBE_MUTATION" and a.status == "PASS"
        for a in trial.assertions
    )


@pytest.mark.parametrize("protocol", ["chat", "responses"])
@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_reasoning_diagnostic_does_not_hide_infrastructure_failure(protocol, status):
    result = run(
        catalog_manifest("C15", protocol),
        lambda request: httpx.Response(status, json={"error": "service failure"}),
    )
    assert result.case_results[0].status == "ERROR"


def test_two_model_full_profile_scope_matches_recorded_replay():
    from pathlib import Path

    from deepseek_provider_verifier.assertions import rule_applies
    from deepseek_provider_verifier.catalog import content_hash, load_cases
    from deepseek_provider_verifier.config import load_config, load_profile
    from deepseek_provider_verifier.planner import build_manifest

    profile = load_profile(Path("profiles/deepseek-official-full-2026-09-22-v1.json"))
    config = load_config(Path("configs/official-full.example.toml"))
    m = build_manifest(config, profile, load_cases())
    evidence = json.loads(
        Path("docs/calibration/official-both-models-2026-09-22.json").read_text()
    )
    assert len(m.cases) == 86 and m.request_ceiling == 212
    assert m.dataset_hash == evidence["dataset_hash"]
    assert content_hash(profile.model_dump(mode="json")) == evidence["profile_hash"]
    assert profile.models == ["deepseek-flash", "deepseek-v4-pro"]
    measured = {(t["endpoint"], t["case_id"]): t for t in evidence["trials"]}
    assert len(measured) == 172
    assert all(
        t["status"] in ("PASS", "INCONCLUSIVE", "SKIP") for t in measured.values()
    )
    gates = {r.id: r for r in profile.rules if r.gating}
    assert gates and set(gates) == set(evidence["rule_case_coverage"])
    for rule_id, variants in evidence["rule_case_coverage"].items():
        r = gates[rule_id]
        assert r.conditions["calibrated_variants"] == variants
        assert r.conditions["models"] == profile.models
        assert variants == sorted(
            c.id for c in m.cases if rule_id in c.rule_ids and rule_applies(r, c)
        )
        for variant in variants:
            for endpoint in config.endpoints:
                trial = measured[endpoint, variant]
                assert trial["status"] == "PASS"
                assert trial["assertion_counts"].get("FAIL", 0) == 0
                assert trial["exact_requests_matched"]
    for protocol in ("chat", "responses"):
        for case_id in ("c10", "c15"):
            r = next(r for r in profile.rules if r.id == f"{protocol}.case.{case_id}")
            assert not r.gating and r.maturity == "diagnostic"
    ignored = next(r for r in profile.rules if r.id == "responses.ignored-options")
    assert not ignored.gating
