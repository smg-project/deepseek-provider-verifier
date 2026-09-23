"""Exact authored sizes and evidence required to claim a generated-output boundary."""

import copy
import json

import httpx
import pytest
from test_compatibility_execution import stream_response
from test_depth_catalog import ROOT, depth_manifest
from test_resource_limits import limited
from test_runner import run
from test_task3_review_fixes import protocol_response
from test_workflows import selected

from deepseek_provider_verifier.catalog import load_cases
from deepseek_provider_verifier.config import load_config, load_profile
from deepseek_provider_verifier.planner import build_manifest
from deepseek_provider_verifier.runner import rehash_manifest
from deepseek_provider_verifier.size_cases import numbered_prefix, sized_text


def test_sized_text_is_exact_and_reproducible():
    first, facts = sized_text(16 * 1024, seed=7)
    assert sized_text(16 * 1024, seed=7) == (first, facts)
    assert len(first.encode()) == 16 * 1024
    assert set(facts) == {"begin", "middle", "end"}
    assert len(set(facts.values())) == 3
    assert first.index(facts["begin"]) < 1024
    assert 7000 < first.index(facts["middle"]) < 9000
    assert first.index(facts["end"]) > 15000
    assert sized_text(16384, seed=8)[0] != first
    assert len(sized_text(1024 * 1024)[0].encode()) == 1024 * 1024
    with pytest.raises(ValueError):
        sized_text(10)


def test_record_prefix_detects_repeats_and_partial_records():
    assert numbered_prefix("record-000001\nrecord-000002\n", False) == (True, 2)
    assert numbered_prefix("record-000001\nrecord-000001\n", False)[0] is False
    assert numbered_prefix("record-000001\nrecord-000", True) == (True, 1)
    assert numbered_prefix("record-000001\nrecord-000", False)[0] is False
    assert numbered_prefix("record-000001\nrecord-999", True)[0] is False


def output_body(protocol, *, usage=3700, reasoning=0, text=None, length=True):
    if text is None:
        text = "".join(f"record-{i:06d}\n" for i in range(1, 301))
    b = protocol_response(protocol, text=text, reasoning=False)
    if protocol == "chat":
        b["choices"][0]["finish_reason"] = "length" if length else "stop"
        b["usage"] = {
            "prompt_tokens": 20,
            "completion_tokens": usage,
            "total_tokens": 20 + usage,
            "completion_tokens_details": {"reasoning_tokens": reasoning},
        }
    else:
        b["status"] = "incomplete" if length else "completed"
        if length:
            b["incomplete_details"] = {"reason": "max_output_tokens"}
        b["usage"] = {
            "input_tokens": 20,
            "output_tokens": usage,
            "total_tokens": 20 + usage,
            "output_tokens_details": {"reasoning_tokens": reasoning},
        }
    return b


def wire(protocol, body, stream):
    if not stream:
        return httpx.Response(200, json=body)
    result = stream_response(protocol, body)
    if protocol == "responses" and body["status"] == "incomplete":
        return httpx.Response(
            200,
            content=result.content.replace(
                b"response.completed", b"response.incomplete"
            ),
            headers={"content-type": "text/event-stream"},
        )
    return result


@pytest.mark.parametrize("protocol", ["chat", "responses"])
@pytest.mark.parametrize("stream", [False, True])
def test_output_boundary_requires_visible_measured_generation(protocol, stream):
    m = selected("sizes-small", "L10", protocol, stream=stream)
    r = run(m, lambda req: wire(protocol, output_body(protocol), stream)).case_results[
        0
    ]
    assert r.status == "PASS", r
    measurement = next(a.observed for a in r.assertions if a.id == "SIZE_MEASUREMENTS")
    assert measurement["requested_output_utilization"] == pytest.approx(3700 / 4096)
    assert measurement["visible_complete_records"] == 300
    assert measurement["declared_output_tokens"] is None
    assert measurement["declared_output_utilization"] is None


@pytest.mark.parametrize(
    "defect",
    [
        "short",
        "missing_usage",
        "reasoning",
        "above_cap",
        "negative",
        "inconsistent",
        "wrong_sequence",
        "empty",
        "tool",
        "redirect",
        "local_cap",
        "partial_eos",
    ],
)
@pytest.mark.parametrize("protocol", ["chat", "responses"])
def test_unproven_or_invalid_generation_never_certifies_boundary(protocol, defect):
    m = selected("sizes-small", "L10", protocol)
    b = output_body(protocol)
    if defect == "short":
        b = output_body(protocol, usage=100)
    if defect == "missing_usage":
        b.pop("usage")
    if defect == "reasoning":
        b = output_body(protocol, reasoning=3600)
    if defect == "above_cap":
        b = output_body(protocol, usage=5000)
    if defect == "negative":
        b = output_body(protocol, usage=-1)
    if defect == "inconsistent":
        b["usage"]["total_tokens"] = 1
    if defect == "wrong_sequence":
        b = output_body(protocol, text="record-000002\n")
    if defect == "empty":
        b = output_body(protocol, text="")
    if defect == "partial_eos":
        b = output_body(protocol, text="record-000001\nrecord-000", length=False)
    if defect == "tool":
        b = protocol_response(protocol, call=True, reasoning=False)
    if defect == "local_cap":
        m = limited(m, capture=64)
    r = run(
        m, lambda req: httpx.Response(302 if defect == "redirect" else 200, json=b)
    ).case_results[0]
    assert r.status != "PASS"
    assert not any(
        a.id == "OUTPUT_BOUNDARY" and a.status == "PASS" for a in r.assertions
    )
    if defect in ("short", "missing_usage", "reasoning"):
        assert r.status == "INCONCLUSIVE"
        assert (
            next(x.value for x in r.metric_observations if x.name == "task_success")
            == 1
        )
    if defect == "local_cap":
        assert r.status == "ERROR"


@pytest.mark.parametrize("id", ["L01", "L05"])
@pytest.mark.parametrize("protocol", ["chat", "responses"])
@pytest.mark.parametrize("corrupt", [None, "begin", "middle"])
def test_input_recall_checks_all_positions_without_context_overclaim(
    id, protocol, corrupt
):
    m = selected("sizes-small", id, protocol)
    c = m.cases[0]
    count = 0
    expected = copy.deepcopy(c.oracle["expected_facts"])
    if corrupt:
        expected[corrupt] = "wrong"

    def handler(req):
        nonlocal count
        count += 1
        text = "ack" if count < c.max_requests else json.dumps(expected)
        return httpx.Response(
            200, json=protocol_response(protocol, text=text, reasoning=False)
        )

    r = run(m, handler).case_results[0]
    assert r.status == ("FAIL" if corrupt else "PASS")
    measured = next(a.observed for a in r.assertions if a.id == "SIZE_MEASUREMENTS")
    assert measured["authored_input_bytes"] == 16384
    assert measured["context_utilization"] is None
    assert measured["retrieval_correct"][corrupt or "end"] is (not bool(corrupt))


def test_size_inventory_and_unselected_generation(monkeypatch):
    small = depth_manifest("sizes-small")
    large = depth_manifest("sizes-large")
    assert (len(small.cases), small.request_ceiling) == (24, 48)
    assert (len(large.cases), large.request_ceiling) == (44, 92)
    for c in large.cases:
        if c.oracle["kind"] == "large_input":
            assert (
                sum(len(s["content"].encode()) for s in c.steps)
                == c.oracle["authored_bytes"]
            )
    from deepseek_provider_verifier import size_cases

    monkeypatch.setattr(
        size_cases,
        "sized_text",
        lambda *a, **k: pytest.fail("Unselected size generation"),
    )
    assert len(load_cases(case_ids=["R01"])) == 2


def declared(m, limits):
    rules = [
        r.model_copy(
            update={"conditions": dict(r.conditions, deployment_limits=limits)}
        )
        if r.id.endswith(".size")
        else r
        for r in m.profile_snapshot.rules
    ]
    return rehash_manifest(
        m.model_copy(
            update={
                "profile_snapshot": m.profile_snapshot.model_copy(
                    update={"rules": rules}
                )
            }
        )
    )


@pytest.mark.parametrize("with_claim", [False, True])
def test_output_rejection_only_fails_a_known_within_capacity_claim(with_claim):
    m = selected("sizes-small", "L10")
    if with_claim:
        m = declared(m, {"candidate": {"output_tokens": 8192}})
    r = run(
        m, lambda req: httpx.Response(400, json={"error": "capacity"})
    ).case_results[0]
    assert r.status == ("FAIL" if with_claim else "INCONCLUSIVE")
    assert (
        next(a.observed for a in r.assertions if a.id == "SIZE_CAPABILITY")
        == "REJECTED"
    )


def test_per_endpoint_claim_is_measured_and_hashed():
    m = selected("sizes-small", "L10")
    configured = declared(
        m,
        {
            "candidate": {"output_tokens": 8192, "context_tokens": 200},
            "reference": {"output_tokens": 16384},
        },
    )
    assert configured.profile_hash != m.profile_hash
    r = run(
        configured, lambda req: httpx.Response(200, json=output_body("chat"))
    ).case_results[0]
    measured = next(a.observed for a in r.assertions if a.id == "SIZE_MEASUREMENTS")
    assert measured["declared_output_tokens"] == 8192
    assert measured["declared_output_utilization"] == pytest.approx(3700 / 8192)
    assert measured["context_utilization"] == 0.1


def test_positive_output_caps_above_declared_limit_fail_planning():
    config = load_config(ROOT / "configs/depth-sizes.example.toml")
    profile = load_profile(ROOT / "profiles/deepseek-depth-2026-09-22-v1.json")
    profile = profile.model_copy(
        update={
            "rules": [
                r.model_copy(
                    update={
                        "conditions": {
                            "deployment_limits": {"candidate": {"output_tokens": 512}}
                        }
                    }
                )
                if r.id.endswith(".size")
                else r
                for r in profile.rules
            ]
        }
    )
    with pytest.raises(ValueError, match="declared.*output|output.*declared"):
        build_manifest(
            config,
            profile,
            load_cases(case_ids=profile.presets["sizes-small"].case_ids),
        )


def test_size_measurements_survive_derived_report_without_raw_bodies():
    from deepseek_provider_verifier.reliability import (
        build_reliability,
        render_reliability_markdown,
    )

    m = selected("sizes-small", "L10")
    r = run(m, lambda req: httpx.Response(200, json=output_body("chat")))
    analysis = build_reliability(m, r)
    assert analysis["observations"][0]["case_id"] == m.cases[0].id
    assert any(
        row["assertion_id"] == "SIZE_MEASUREMENTS"
        and row["observed"]["visible_complete_records"] == 300
        for row in analysis["observations"]
    )
    assert "3700" in render_reliability_markdown(analysis)
    assert "record-000001" not in json.dumps(analysis)


def test_fact_lines_are_whole_in_each_history_turn():
    import re

    m = depth_manifest("sizes-large")
    for c in m.cases:
        if c.oracle["kind"] != "large_input":
            continue
        found = {}
        for s in c.steps:
            found.update(
                re.findall(r"FACT (begin|middle|end)=([0-9a-f]{16})\n", s["content"])
            )
        assert found == c.oracle["expected_facts"]


def test_unknown_incomplete_reason_cannot_authorize_partial_record():
    m = selected("sizes-small", "L10", "responses")
    b = output_body("responses", text="record-000001\nrecord-000")
    b["incomplete_details"] = "malformed"
    r = run(m, lambda req: httpx.Response(200, json=b))
    assert r.case_results[0].status == "FAIL"
