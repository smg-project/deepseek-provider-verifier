"""End-to-end depth fixtures through the real runner and all report views."""

import json
from xml.etree import ElementTree as ET

import httpx
import pytest
from test_compatibility_execution import stream_response
from test_depth_catalog import depth_manifest
from test_reliability import tagged
from test_runner import response, run
from test_size_cases import output_body
from test_workflows import execute_fixture, fixture_body, selected

from deepseek_provider_verifier.reliability import build_reliability
from deepseek_provider_verifier.reports import render_report


def test_depth_presets_are_bounded_opt_in_workloads():
    for name in [
        "repeatability",
        "repeatability-expanded",
        "workflows-small",
        "workflows-full",
        "schemas",
        "sizes-small",
        "sizes-large",
    ]:
        m = depth_manifest(
            name, repetitions=5 if name.startswith("repeatability") else 1
        )
        assert m.profile_snapshot.id == "deepseek-depth-2026-09-22-v1"
        assert m.budgets.retries == 0 and m.budgets.concurrency == 1
        assert all("depth" in c.oracle for c in m.cases)


@pytest.mark.parametrize(
    "suite,count,requests",
    [("repeatability", 40, 50), ("repeatability-expanded", 160, 200)],
)
def test_all_authored_repeatability_fixtures_run_on_both_protocols(
    suite, count, requests
):
    m = depth_manifest(suite)
    schedule = iter((c, i) for c in m.cases for i in range(c.max_requests))

    def handler(req):
        c, i = next(schedule)
        kind = c.oracle["kind"]
        p = json.loads(req.content)
        assert p["messages" if c.protocol == "chat" else "input"]
        if kind == "named_tool" or (c.oracle.get("continue_tools") and i == 0):
            name = c.oracle.get("name", "add_integers")
            expected = {
                "tools": [{"name": name, "arguments": c.oracle["expected_arguments"]}]
            }
        elif kind == "schema":
            expected = {
                "text": json.dumps(
                    {
                        k: v["enum"][0]
                        for k, v in c.oracle["schema"]["properties"].items()
                    }
                )
            }
        else:
            expected = {"text": c.oracle["value"]}
        body = fixture_body(c.protocol, expected, reasoning=c.mode == "thinking")
        return (
            stream_response(c.protocol, body)
            if c.stream
            else httpx.Response(200, json=body)
        )

    r = run(m, handler)
    assert r.counts["PASS"] == count, r.counts
    assert r.budget_usage == {"candidate": requests}


@pytest.mark.parametrize("family", ["repeatability", "workflow", "schema", "size"])
def test_seeded_family_failures_agree_across_report_formats(family):
    if family == "workflow":
        m = selected("workflows-full", "W06")

        def mutate(b, i):
            if i == 2:
                b["choices"][0]["message"]["content"] = "wrong"
            return b

        r, _ = execute_fixture(m, mutate=mutate)
    elif family == "repeatability":
        m = selected("repeatability", "R01")
        r = run(m, lambda req: httpx.Response(302, json=response("amber")))
    elif family == "schema":
        m = selected("schemas", "S01")
        b = fixture_body(
            "chat",
            {
                "tools": [
                    {
                        "name": "record_schema_fixture",
                        "arguments": {"label": "amber", "count": True},
                    }
                ]
            },
            reasoning=False,
        )
        r = run(m, lambda req: httpx.Response(200, json=b))
    else:
        m = selected("sizes-small", "L10")
        r = run(
            m,
            lambda req: httpx.Response(
                200, json=output_body("chat", text="record-000002\n")
            ),
        )
    assert r.exit_code == 1
    assert json.loads(render_report(r, "json"))["counts"]["FAIL"] == 1
    assert "| FAIL |" in render_report(r, "markdown", manifest=m)
    assert ET.fromstring(render_report(r, "junit")).find(".//failure") is not None


def test_mixed_trials_keep_original_http_failure_after_resume(tmp_path):
    m = tagged(repetitions=3, max_requests=2)
    replies = iter([(503, None), (200, "wrong"), (200, "amber")])

    def handler(req):
        status, text = next(replies)
        return httpx.Response(
            status, json=response(text) if text else {"error": "busy"}
        )

    first = run(m, handler, tmp_path)
    assert first.counts["ERROR"] == 1 and first.counts["FAIL"] == 1
    later = run(
        m, lambda req: httpx.Response(200, json=response()), tmp_path, resume=True
    )
    g = build_reliability(m, later)["groups"][0]
    assert g["counts"]["PASS"] == 2 and g["counts"]["FAIL"] == 1
    assert g["http_attempts"] == 4
    assert g["first_http_2xx_rate"]["value"] == pytest.approx(2 / 3)
    assert g["eventual_http_2xx_rate"]["value"] == 1
    assert g["first_contract"]["counts"]["MISSING"] == 1
