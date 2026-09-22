"""Final review regressions preserve evidence and readable measurement context."""

import json
from pathlib import Path
from xml.etree import ElementTree as ET

import httpx
import pytest
from test_cli_reports import _run_result
from test_runner import manifest, run

from deepseek_provider_verifier.protocols.chat import assemble_chat
from deepseek_provider_verifier.protocols.responses import assemble_responses
from deepseek_provider_verifier.records import (
    ComparisonMetric,
    ComparisonPolicy,
    ComparisonReportContext,
    ComparisonResult,
)
from deepseek_provider_verifier.reports import render_report
from deepseek_provider_verifier.runner import rehash_manifest
from deepseek_provider_verifier.sse import decode_sse


def test_comparison_retains_canonical_populations_uncertainty_and_policy():
    metric = ComparisonMetric(
        value=1,
        numerator=2,
        denominator=2,
        unavailable=98,
        reference_value=0.5,
        reference_numerator=50,
        reference_denominator=100,
        reference_unavailable=0,
        difference=0.5,
        lower_bound=-0.4,
        upper_bound=0.8,
        confidence_level=0.95,
        bootstrap_seed=19,
        paired_observations=2,
        paired_distinct_prompts=2,
        paired_repetitions=1,
        missing_reference=0,
        missing_candidate=98,
    )
    policy = ComparisonPolicy(
        allowed_drops={"task_success": 0.1},
        minimum_distinct_prompts=2,
        minimum_repetitions=1,
        confidence_level=0.95,
        bootstrap_seed=19,
        bootstrap_samples=123,
    )
    result = ComparisonResult(
        comparable=True,
        reference_endpoint="reference",
        candidate_endpoint="candidate",
        manifest_differences=[],
        metrics={"task_success": metric},
        policy=policy,
        metric_gates={"task_success": "INCONCLUSIVE"},
        reasons=["task_success: interval overlaps margin 0.1"],
        report=ComparisonReportContext(
            created_at="2026-09-21T20:00:00Z",
            profile_hash="c" * 64,
            case_count=100,
            required_case_count=99,
            integrity="verified",
        ),
    )
    md = render_report(result, "markdown")
    for value in (
        "50/100",
        "2/2",
        "98",
        "-0.4",
        "0.8",
        "0.95",
        "19",
        "123",
        "0.1",
        "c" * 64,
    ):
        assert value in md
    assert (
        "| task_success | 50/100 | 0 | 2/2 | 98 | 2 / 2 / 1 | 0 / 98 | -0.4 to 0.8 | 0.95 | 19 |"
        in md
    )
    xml = ET.fromstring(render_report(result, "junit"))
    props = {
        p.attrib["name"]: p.attrib["value"] for p in xml.findall("properties/property")
    }
    assert props["profile_hash"] == "c" * 64
    assert props["required_case_count"] == "99"
    assert props["integrity"] == "verified"
    assert json.loads(props["policy"]) == policy.model_dump(mode="json")
    stored = json.loads(xml.find("testcase/system-out").text)
    assert stored == metric.model_dump(mode="json")
    assert (
        "task_success: interval overlaps margin 0.1"
        in xml.find("testcase/error").attrib["message"]
    )


def test_legacy_required_membership_and_provenance_fallbacks():
    result = _run_result()
    assert "c" * 64 in render_report(result, "junit")
    legacy = result.model_copy(update={"report": None})
    assert "| PASS | unavailable |" in render_report(legacy, "markdown")
    props = {
        p.attrib["name"]: p.attrib["value"]
        for p in ET.fromstring(render_report(legacy, "junit")).findall(
            "properties/property"
        )
    }
    assert props["profile_hash"] == "unavailable"


def stream_payloads(protocol):
    if protocol == "chat":
        envelope = {
            "id": "chat1",
            "object": "chat.completion.chunk",
            "created": 123,
            "model": "fixture",
            "extra": {"accepted": True},
        }
        return [
            envelope
            | {
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": "4"},
                        "finish_reason": None,
                    }
                ]
            },
            envelope
            | {
                "choices": [
                    {"index": 0, "delta": {"content": "2"}, "finish_reason": None}
                ]
            },
            envelope
            | {
                "choices": [
                    {"index": 0, "delta": {"role": None}, "finish_reason": "stop"}
                ]
            },
        ]
    wire = (Path(__file__).parent / "fixtures/responses/interleaved.sse").read_text()
    values = [
        json.loads(line[6:]) for line in wire.splitlines() if line.startswith("data: ")
    ]
    values[-1]["response"]["object"] = "response"
    return values


@pytest.mark.parametrize(
    "protocol,fault",
    [
        ("chat", f)
        for f in [
            "valid",
            "missing_all",
            "id_missing",
            "id_type",
            "object_missing",
            "object_value",
            "created_missing",
            "created_bool",
            "model_missing",
            "model_type",
            "id_changed",
            "created_changed",
            "model_changed",
            "role_missing",
            "role_wrong",
            "role_continuation_wrong",
        ]
    ]
    + [
        ("responses", f)
        for f in ["valid", "id_missing", "id_type", "object_missing", "object_value"]
    ],
)
def test_stream_envelope_corruption_reaches_report_and_source(protocol, fault):
    values = stream_payloads(protocol)
    target = values[0] if protocol == "chat" else values[-1]["response"]
    event_index = 0 if protocol == "chat" else len(values) - 1
    if fault == "missing_all":
        for value in values:
            for key in ("id", "object", "created", "model"):
                value.pop(key)
        values[0]["choices"][0]["delta"].pop("role")
    elif fault.endswith("_missing"):
        key = fault.removesuffix("_missing")
        (target["choices"][0]["delta"] if key == "role" else target).pop(key)
    elif fault.endswith("_type"):
        target[fault.removesuffix("_type")] = 42
    elif fault == "object_value":
        target["object"] = "wrong"
    elif fault == "created_bool":
        target["created"] = True
    elif fault.endswith("_changed"):
        key = fault.removesuffix("_changed")
        event_index = 1
        values[1][key] = 124 if key == "created" else "different"
    elif fault == "role_wrong":
        target["choices"][0]["delta"]["role"] = "user"
    elif fault == "role_continuation_wrong":
        values[1]["choices"][0]["delta"]["role"] = "user"
        event_index = 1
    wire = "".join("data: " + json.dumps(v) + "\n\n" for v in values)
    if protocol == "chat":
        wire += "data: [DONE]\n\n"
    events = decode_sse([wire.encode()])
    observation = (assemble_chat if protocol == "chat" else assemble_responses)(events)
    m = manifest(protocol=protocol, oracle={"kind": "structure"})
    m = rehash_manifest(
        m.model_copy(update={"cases": [m.cases[0].model_copy(update={"stream": True})]})
    )
    result = run(
        m,
        lambda r: httpx.Response(
            200, content=wire, headers={"content-type": "text/event-stream"}
        ),
    )
    if fault == "valid":
        assert result.exit_code == 0
        assert observation.violations == []
        if protocol == "chat":
            assert observation.assistant_messages[0]["role"] == "assistant"
    else:
        assert result.exit_code == 1
        assertion = next(
            a for a in result.case_results[0].assertions if a.id == "RESPONSE_ENVELOPE"
        )
        assert assertion.status == "FAIL" and assertion.gating
        assert any(
            s.event_index == event_index
            for s in observation.evidence["RESPONSE_ENVELOPE"]
        )
        assert observation.raw_events[event_index].data == json.dumps(
            values[event_index]
        )
        assert "FAIL" in render_report(result, "markdown")


def test_loader_does_not_accept_missing_trial_as_complete(tmp_path):
    from deepseek_provider_verifier.evidence import append_record, atomic_json
    from deepseek_provider_verifier.records import RunResult
    from deepseek_provider_verifier.reports import load_run_evidence

    m = manifest()
    atomic_json(tmp_path / "manifest.json", m.model_dump(mode="json"))
    for name in ("attempts.jsonl", "results.jsonl"):
        append_record(
            tmp_path / name, {"kind": "manifest", "manifest_hash": m.manifest_hash}
        )
    result = RunResult(
        manifest_hash=m.manifest_hash,
        complete=False,
        case_results=[],
        counts={},
        budget_usage={"candidate": 0},
        enabled_gates=m.gates,
        exit_code=2,
    )
    atomic_json(tmp_path / "summary.json", result.model_dump(mode="json"))
    assert not load_run_evidence(tmp_path)[1].complete
    atomic_json(
        tmp_path / "summary.json",
        result.model_copy(update={"complete": True}).model_dump(mode="json"),
    )
    with pytest.raises(ValueError, match="completion does not match"):
        load_run_evidence(tmp_path)


def test_inconclusive_reason_identifies_repetition_floor():
    from test_comparison import endpoint_manifest, policy, run_result

    from deepseek_provider_verifier.comparison import compare_runs

    m = endpoint_manifest(prompts=2, repetitions=1)
    run = run_result(m, "candidate", [{"task_success": 1.0} for _ in m.cases])
    compared = compare_runs(run, run, (m, m), policy=policy(minimum_repetitions=2))
    assert any(
        "task_success:" in r
        and "paired repetitions 1 < 2" in r
        and "interval unavailable" in r
        for r in compared.reasons
    )


def test_junit_keeps_ungated_metric_populations_beside_enabled_gate():
    metric = ComparisonMetric(
        numerator=2,
        denominator=2,
        unavailable=98,
        reference_denominator=100,
        reference_unavailable=0,
    )
    policy = ComparisonPolicy(
        allowed_drops={"task_success": 0.1},
        minimum_distinct_prompts=2,
        minimum_repetitions=1,
        confidence_level=0.95,
        bootstrap_seed=7,
    )
    result = ComparisonResult(
        comparable=True,
        reference_endpoint="reference",
        candidate_endpoint="candidate",
        manifest_differences=[],
        metrics={"task_success": metric, "schema_validity": metric},
        policy=policy,
        metric_gates={"task_success": "INCONCLUSIVE"},
    )
    xml = ET.fromstring(render_report(result, "junit"))
    cases = {case.attrib["name"]: case for case in xml.findall("testcase")}
    assert set(cases) == set(result.metrics)
    assert cases["schema_validity"].find("skipped") is not None
    assert json.loads(
        cases["schema_validity"].find("system-out").text
    ) == metric.model_dump(mode="json")
