"""The acceptance policy cannot hide missing work or broken core behavior."""

import importlib.util
from collections import Counter

import httpx
import pytest
from test_runner import manifest, response, run

from deepseek_provider_verifier.cli import _run_context
from deepseek_provider_verifier.runner import rehash_manifest


def api():
    assert (
        importlib.util.find_spec("deepseek_provider_verifier.acceptance") is not None
    ), "acceptance evaluator is not implemented"
    from deepseek_provider_verifier.acceptance import assess_run
    from deepseek_provider_verifier.acceptance_records import AcceptancePolicy, Facet

    return assess_run, AcceptancePolicy, Facet


def fixture(condition="pass"):
    assess, Policy, Facet = api()
    m = manifest()
    r = run(m, lambda req: httpx.Response(200, json=response()))
    r = r.model_copy(update={"report": _run_context(m, r, "verified")})
    selectors = [
        {"family": "core", "facet": n, "required": n != "raw_contract"}
        for n in ["protocol", "functional", "raw_contract"]
    ]
    p = Policy(version=1, id="test-policy", mode="compatibility", selectors=selectors)
    facets = [
        Facet(
            endpoint="candidate",
            case_id=m.cases[0].id,
            name=n,
            status="PASS",
            source_assertion_ids=[],
            evidence_hashes=r.case_results[0].attempt_refs,
            reason="authored control",
        )
        for n in ["protocol", "functional", "raw_contract"]
    ]
    if condition == "diagnostic_fail":
        facets[2] = facets[2].model_copy(update={"status": "FAIL"})
    if condition in ("required_fail", "unknown", "error"):
        facets[1] = facets[1].model_copy(
            update={
                "status": {
                    "required_fail": "FAIL",
                    "unknown": "INCONCLUSIVE",
                    "error": "ERROR",
                }[condition]
            }
        )
    if condition == "missing_facet":
        facets.pop(1)
    if condition == "missing_result":
        r = r.model_copy(update={"case_results": [], "counts": {}, "complete": False})
    if condition == "summary_only":
        r = r.model_copy(
            update={"report": r.report.model_copy(update={"integrity": "summary-only"})}
        )
    if condition == "incomplete":
        r = r.model_copy(update={"complete": False})
    return assess, m, r, facets, p


@pytest.mark.parametrize(
    "condition,expected",
    [
        ("pass", ("PASS", 0)),
        ("diagnostic_fail", ("PASS", 0)),
        ("required_fail", ("FAIL", 1)),
        ("unknown", ("INCONCLUSIVE", 2)),
        ("error", ("INCONCLUSIVE", 2)),
        ("missing_facet", ("INCONCLUSIVE", 2)),
        ("missing_result", ("INCONCLUSIVE", 2)),
        ("summary_only", ("INCONCLUSIVE", 2)),
        ("incomplete", ("INCONCLUSIVE", 2)),
    ],
)
def test_acceptance_decision(condition, expected):
    assess, m, r, facets, p = fixture(condition)
    before = r.model_dump_json()
    actual = assess(m, r, facets, p, "test-scorer")
    assert (actual.verdict, actual.exit_code) == expected
    assert actual.source_scorer_revision == m.scorer_revision
    assert r.model_dump_json() == before


@pytest.mark.parametrize(
    "change",
    [
        "empty",
        "unknown_family",
        "unknown_facet",
        "duplicate",
        "protocol_waiver",
        "functional_waiver",
    ],
)
def test_invalid_policy_is_rejected(change):
    _, _, _, _, p = fixture()
    data = p.model_dump(mode="json")
    if change == "empty":
        data["selectors"] = []
    if change == "unknown_family":
        data["selectors"][0]["family"] = "guess"
    if change == "unknown_facet":
        data["selectors"][0]["facet"] = "guess"
    if change == "duplicate":
        data["selectors"].append(data["selectors"][0])
    if change == "protocol_waiver":
        data["selectors"][0]["required"] = False
    if change == "functional_waiver":
        data["selectors"][1]["required"] = False
    with pytest.raises(ValueError):
        type(p).model_validate(data)


@pytest.mark.parametrize(
    "change",
    ["duplicate", "unexpected", "unmapped_policy", "wrong_manifest", "wrong_counts"],
)
def test_inconsistent_inputs_cannot_pass(change):
    assess, m, r, f, p = fixture()
    if change == "duplicate":
        f.append(f[0])
    if change == "unexpected":
        f[0] = f[0].model_copy(update={"endpoint": "unrelated"})
    if change == "unmapped_policy":
        p = p.model_copy(update={"selectors": p.selectors[:2]})
    if change == "wrong_manifest":
        r = r.model_copy(update={"manifest_hash": "0" * 64})
    if change == "wrong_counts":
        r = r.model_copy(update={"counts": {"FAIL": 1}})
    with pytest.raises(ValueError):
        assess(m, r, f, p, "test-scorer")


def test_policy_hash_records_changed_requirements():
    assess, m, r, f, p = fixture("diagnostic_fail")
    a = assess(m, r, f, p, "test-scorer")
    strict = p.model_copy(
        update={
            "mode": "strict",
            "selectors": [x.model_copy(update={"required": True}) for x in p.selectors],
        }
    )
    b = assess(m, r, f, strict, "test-scorer")
    assert a.policy_hash != b.policy_hash
    assert a.verdict == "PASS" and b.verdict == "FAIL"
    assert a.diagnostic_facets[0].status == "FAIL"


def test_endpoint_and_model_names_do_not_change_acceptance():
    assess, m, r, f, p = fixture("required_fail")
    endpoint = m.endpoints["candidate"].model_copy(
        update={
            "name": "official",
            "model": "deepseek-v4-pro",
            "base_url": m.endpoints["candidate"].base_url,
        }
    )
    other = rehash_manifest(m.model_copy(update={"endpoints": {"official": endpoint}}))
    results = [x.model_copy(update={"endpoint": "official"}) for x in r.case_results]
    r = r.model_copy(
        update={
            "manifest_hash": other.manifest_hash,
            "case_results": results,
            "attempt_metrics": [],
            "counts": dict(Counter(x.status for x in results)),
        }
    )
    r = r.model_copy(update={"report": _run_context(other, r, "verified")})
    f = [x.model_copy(update={"endpoint": "official"}) for x in f]
    assert assess(other, r, f, p, "test-scorer").verdict == "FAIL"
