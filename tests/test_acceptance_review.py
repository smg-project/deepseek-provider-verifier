"""Regressions for whole-branch review and reconstructed resume history."""

import httpx
import pytest
from test_compatibility_policy import policy_manifest
from test_runner import manifest, response, run
from test_task3_review_fixes import catalog_manifest, protocol_response

from deepseek_provider_verifier.acceptance import assess_run, load_policy
from deepseek_provider_verifier.acceptance_evidence import load_observations
from deepseek_provider_verifier.acceptance_facets import derive_facets
from deepseek_provider_verifier.cli import _run_context, acceptance_preflight
from deepseek_provider_verifier.reports import exit_status, write_report_bundle


def save(m, r, path):
    r = r.model_copy(update={"report": _run_context(m, r, "verified")})
    r = r.model_copy(update={"exit_code": exit_status(r)})
    write_report_bundle(path, r, allow_existing=True, manifest=m)
    return load_observations(path)


@pytest.mark.parametrize("protocol", ["chat", "responses"])
def test_special_compatibility_probes_reject_duplicate_tool_members(protocol, tmp_path):
    m = policy_manifest(catalog_manifest("C08", protocol))
    body = protocol_response(protocol, call=True)
    if protocol == "chat":
        function = body["choices"][0]["message"]["tool_calls"][0]["function"]
    else:
        function = next(i for i in body["output"] if i["type"] == "function_call")
    function.update(name="lookup_fixture", arguments='{"key":"wrong","key":"harbor"}')
    r = run(m, lambda req: httpx.Response(200, json=body), tmp_path)
    m, r, observations = save(m, r, tmp_path)
    facets = derive_facets(m, r, observations)
    assert all(f.status == "FAIL" for f in facets if f.name == "protocol")
    assert (
        assess_run(
            m, r, facets, load_policy("official-compatible-v1"), "test"
        ).exit_code
        != 0
    )


@pytest.mark.parametrize(
    "oracle,status",
    [
        ({"kind": "structure"}, 200),
        ({"status_class": 4}, 400),
        ({"kind": "exact_text", "value": "amber", "status_class": 2}, 200),
        (
            {
                "kind": "exact_text",
                "value": "amber",
                "compatibility": {
                    "feature": "identifier",
                    "allow_rejection": True,
                    "reference_status": 200,
                    "reference_date": "2026-09-22",
                },
            },
            400,
        ),
    ],
)
def test_protocol_only_selection_cannot_certify_functional_coverage(
    oracle, status, tmp_path
):
    m = policy_manifest(manifest(oracle=oracle))
    policy = load_policy("official-compatible-v1")
    with pytest.raises(ValueError, match="functional"):
        acceptance_preflight(m, policy)
    r = run(
        m,
        lambda req: httpx.Response(
            status,
            json=response("arbitrary unrelated prose")
            if status == 200
            else {"error": "rejected"},
        ),
        tmp_path,
    )
    m, r, observations = save(m, r, tmp_path)
    result = assess_run(m, r, derive_facets(m, r, observations), policy, "test")
    assert result.verdict == "INCONCLUSIVE" and result.exit_code == 2
    assert not any(
        f.name == "functional" and f.status == "PASS" for f in result.required_facets
    )


def test_offline_assessment_of_resumed_run_uses_latest_trajectory(tmp_path):
    m = manifest(max_requests=2)
    run(m, lambda req: httpx.Response(503, json={"error": "busy"}), tmp_path)
    resumed = run(
        m, lambda req: httpx.Response(200, json=response()), tmp_path, resume=True
    )
    assert [a.status_code for a in resumed.attempt_metrics] == [503, 200]
    m, r, observations = save(m, resumed, tmp_path)
    assert len(next(iter(observations.values()))) == 1
    assert next(iter(observations.values()))[0].status_code == 200
    assert [a.status_code for a in r.attempt_metrics] == [503, 200]
    assert (
        assess_run(
            m,
            r,
            derive_facets(m, r, observations),
            load_policy("official-compatible-v1"),
            "test",
        ).exit_code
        == 0
    )
