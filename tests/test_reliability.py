"""Derived reliability must not turn missing work or recovery into success."""

import json

import httpx
import pytest
from test_runner import manifest, response, run

from deepseek_provider_verifier.depth_catalog import depth_metadata
from deepseek_provider_verifier.reliability import (
    build_reliability,
    summarize_trials,
    wilson_interval,
)
from deepseek_provider_verifier.reports import render_report, write_report_bundle


def tagged(**kwargs):
    return manifest(
        oracle={
            "kind": "exact_text",
            "value": "amber",
            "depth": depth_metadata("repeatability.instruction"),
        },
        **kwargs,
    )


def test_errors_and_missing_work_do_not_disappear():
    row = summarize_trials(["PASS", "FAIL", "ERROR", "INCONCLUSIVE", "SKIP", None])
    assert row["planned"] == 6
    assert row["counts"]["MISSING"] == 1
    assert row["failure_rate"] == {"numerator": 1, "denominator": 2, "value": 0.5}
    assert wilson_interval(0, 0) is None
    assert summarize_trials(["ERROR", None])["failure_rate"]["value"] is None
    low, high = wilson_interval(0, 5)
    assert low == pytest.approx(0)
    assert 0.43 < high < 0.44


@pytest.mark.parametrize(
    "args",
    [
        (True, 5),
        (1, False),
        (-1, 5),
        (6, 5),
        (0, 1, 0),
        (0, 1, 1),
        (0, 1, float("nan")),
    ],
)
def test_invalid_counts_cannot_produce_intervals(args):
    with pytest.raises(ValueError):
        wilson_interval(*args)


def test_five_repetitions_are_one_prompt_with_two_failures():
    m = tagged(repetitions=5)
    replies = iter(["amber", "amber", "wrong", "amber", "wrong"])
    result = run(m, lambda r: httpx.Response(200, json=response(next(replies))))
    analysis = build_reliability(m, result)
    group = analysis["groups"][0]
    assert group["distinct_prompts"] == 1
    assert group["repetitions"] == 5
    assert group["counts"]["FAIL"] == 2
    assert group["failure_rate"]["value"] == 0.4
    assert group["unstable_prompts"] == 1
    assert group["http_attempts"] == 5
    assert group["first_http_2xx_rate"]["value"] == 1
    assert group["prompts"][0]["failure_interval"]["sample_size"] == 5
    assert "failure_interval" not in group
    assert group["end_to_end_success"]["value"] == 0.6


def test_execution_errors_are_separate_from_contract_failures():
    m = tagged()
    result = run(m, lambda r: httpx.Response(503, json={"error": "busy"}))
    g = build_reliability(m, result)["groups"][0]
    assert g["counts"]["ERROR"] == 1
    assert g["failure_rate"]["value"] is None
    assert g["end_to_end_success"]["value"] == 0
    assert g["end_to_end_success"]["unavailable"] == 1
    assert g["error_affected_prompts"] == 1
    skipped = tagged()
    from deepseek_provider_verifier.runner import rehash_manifest

    c = skipped.cases[0]
    skipped = rehash_manifest(
        skipped.model_copy(
            update={
                "cases": [
                    c.model_copy(
                        update={
                            "oracle": dict(
                                c.oracle, applicable=False, reason="Unsupported fixture"
                            )
                        }
                    )
                ]
            }
        )
    )
    g = build_reliability(
        skipped, run(skipped, lambda r: pytest.fail("Skipped case sent traffic"))
    )["groups"][0]
    assert g["counts"]["SKIP"] == 1
    assert g["started"] == 0
    assert g["failure_rate"]["value"] is None


def test_duplicate_and_foreign_results_rejected():
    m = tagged()
    r = run(m, lambda r: httpx.Response(200, json=response()))
    for records in [
        r.case_results * 2,
        [r.case_results[0].model_copy(update={"case_id": "foreign"})],
    ]:
        with pytest.raises(ValueError, match="identity"):
            build_reliability(m, r.model_copy(update={"case_results": records}))


@pytest.mark.parametrize("interrupted", [False, True])
def test_resumed_trial_keeps_earliest_http_but_unknown_first_contract(
    tmp_path, interrupted
):
    m = tagged(max_requests=2)
    if interrupted:
        from deepseek_provider_verifier.evidence import append_record, atomic_json

        atomic_json(tmp_path / "manifest.json", m.model_dump(mode="json"))
        for name in ["attempts.jsonl", "results.jsonl"]:
            append_record(
                tmp_path / name, {"kind": "manifest", "manifest_hash": m.manifest_hash}
            )
        c = m.cases[0]
        append_record(
            tmp_path / "attempts.jsonl",
            {
                "kind": "attempt_start",
                "endpoint": "candidate",
                "protocol": c.protocol,
                "case_id": c.id,
                "prompt_id": c.prompt_id,
                "repetition": 0,
                "step": 0,
                "retry": 0,
                "attempt_number": 1,
                "request_hash": "a" * 64,
            },
        )
    else:
        run(m, lambda r: httpx.Response(503, json={"error": "busy"}), tmp_path)
    r = run(m, lambda r: httpx.Response(200, json=response()), tmp_path, resume=True)
    g = build_reliability(m, r)["groups"][0]
    assert g["first_http_2xx_rate"]["value"] == 0
    assert g["eventual_http_2xx_rate"]["value"] == 1
    assert g["http_attempts"] == 2
    assert g["http_retry_attempts"] == 1
    assert g["first_contract"]["counts"]["MISSING"] == 1
    assert g["counts"]["PASS"] == 1
    again = run(
        m, lambda r: pytest.fail("Completed trial rerun"), tmp_path, resume=True
    )
    assert build_reliability(m, again)["groups"] == [g]


def test_sidecar_is_derived_and_does_not_change_canonical_json(tmp_path):
    m = tagged()
    r = run(m, lambda r: httpx.Response(200, json=response()), tmp_path)
    write_report_bundle(tmp_path, r, allow_existing=True, manifest=m)
    before = (tmp_path / "summary.json").read_text()
    assert (
        json.loads((tmp_path / "reliability.json").read_text())["schema_version"] == 1
    )
    assert render_report(r, "json", manifest=m) == render_report(r, "json")
    (tmp_path / "reliability.json").write_text('{"groups": "tampered"}')
    from deepseek_provider_verifier.cli import main

    out = tmp_path / "regenerated.md"
    assert (
        main(
            [
                "report",
                str(tmp_path),
                "--format",
                "markdown",
                "--out",
                str(out),
            ]
        )
        == 0
    )
    assert "Reliability" in out.read_text()
    assert "tampered" not in out.read_text()
    assert (tmp_path / "summary.json").read_text() == before
    # A changed authoritative summary still fails journal validation.
    value = json.loads(before)
    value["case_results"][0]["reason"] = "corrupted"
    (tmp_path / "summary.json").write_text(json.dumps(value))
    from deepseek_provider_verifier.reports import load_run_evidence

    with pytest.raises(ValueError, match="journals"):
        load_run_evidence(tmp_path)
