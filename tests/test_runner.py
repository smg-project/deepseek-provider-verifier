import asyncio
import json
from pathlib import Path

import httpx
import pytest
from test_assertions import rule

from deepseek_provider_verifier.planner import build_manifest
from deepseek_provider_verifier.records import (
    CaseTemplate,
    Config,
    Endpoint,
    Profile,
    ProfilePreset,
    RunSettings,
)


def manifest(
    *,
    protocol="chat",
    count=1,
    steps=None,
    max_requests=1,
    oracle=None,
    retries=0,
    repetitions=1,
    order="sequential",
    endpoints=1,
    diagnostic=False,
):
    r = rule(
        protocol=protocol,
        maturity="diagnostic" if diagnostic else "calibrated",
        gating=not diagnostic,
    )
    p = Profile(
        id="synthetic",
        models=["fixture-model"],
        rules=[r],
        presets={
            "smoke": ProfilePreset(
                max_requests_per_protocol=100,
                max_requests_per_endpoint=100,
                max_total_requests=200,
                max_requests_per_conversation=4,
                max_retries=retries,
                concurrency=1,
                case_deadline_seconds=300,
                mode_max_output_tokens={"non_thinking": 512, "thinking": 4096},
            )
        },
    )
    config = Config(
        run=RunSettings(
            profile=p.id,
            suite="smoke",
            protocols=[protocol],
            max_attempts_per_endpoint=100,
            retries=retries,
            repetitions=repetitions,
            execution_order=order,
        ),
        endpoints={
            name: Endpoint(
                name=name,
                base_url="http://fixture.test/v1",
                model="fixture-model",
                model_release="synthetic-v1",
                api_key_env="FIXTURE_KEY",
            )
            for name in ["candidate", "reference"][:endpoints]
        },
    )
    templates = [
        CaseTemplate(
            id=f"C{i + 1:02d}",
            prompt_id="fixture-prompt",
            protocol=protocol,
            modes=["non_thinking"],
            streams=[False],
            rule_ids=[r.id],
            steps=steps or [{"kind": "user", "content": "Reply amber."}],
            required=True,
            max_requests=max_requests,
            max_output_tokens={"non_thinking": 512},
            oracle=oracle or {"kind": "exact_text", "value": "amber"},
        )
        for i in range(count)
    ]
    return build_manifest(config, p, templates)


def response(text="amber", tools=None, reasoning=None):
    message = {"role": "assistant", "content": text}
    if tools:
        message["tool_calls"] = tools
    if reasoning:
        message["reasoning_content"] = reasoning
    return {
        "id": "chat-fixture",
        "object": "chat.completion",
        "created": 1,
        "model": "fixture-model",
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if tools else "stop",
            }
        ],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
    }


def tool(id="call-a", name="add_integers", arguments='{"a":17,"b":25}'):
    return {
        "id": id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def run(m, handler, out=None, resume=False):
    from deepseek_provider_verifier.runner import execute_manifest

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await execute_manifest(
                m,
                {name: client for name in m.endpoints},
                {name: "SENTINEL_SECRET" for name in m.endpoints},
                output_dir=out,
                resume=resume,
            )

    return asyncio.run(go())


def test_repetitions_expand_trials_and_self_contained_profile():
    m = manifest(repetitions=2)
    assert len(m.cases) == 2
    assert [c.repetition for c in m.cases] == [0, 1]
    assert len({c.id for c in m.cases}) == 2
    assert all(c.prompt_id == "fixture-prompt" for c in m.cases)
    assert m.profile_snapshot.rules[0].id == "test"
    assert m.request_ceiling == 2


def test_complete_positive_run_records_all_attempts(tmp_path):
    result = run(manifest(), lambda r: httpx.Response(200, json=response()), tmp_path)
    assert result.complete and result.exit_code == 0
    assert result.case_results[0].status == "PASS"
    assert result.budget_usage["candidate"] == 1
    assert len(result.case_results[0].attempt_refs) == 1
    assert json.loads((tmp_path / "summary.json").read_text())["complete"]


def test_history_replay_and_tool_result_use_original_id(tmp_path):
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        if len(bodies) == 1:
            return httpx.Response(
                200, json=response(None, [tool()], "Reasoning must survive intact")
            )
        return httpx.Response(200, json=response("42"))

    m = manifest(
        max_requests=2,
        oracle={"kind": "conversation", "value": "42", "continue_tools": True},
    )
    result = run(m, handler, tmp_path)
    assert result.exit_code == 0
    messages = bodies[1]["messages"]
    assert messages[1]["reasoning_content"] == "Reasoning must survive intact"
    assert messages[2] == {"role": "tool", "tool_call_id": "call-a", "content": "42"}


def test_two_user_turns_replay_assistant_without_tool_call():
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=response())

    result = run(
        manifest(
            steps=[
                {"kind": "user", "content": "Remember amber"},
                {"kind": "user", "content": "Recall"},
            ],
            max_requests=2,
        ),
        handler,
    )
    assert result.exit_code == 0
    assert [m["role"] for m in bodies[1]["messages"]] == ["user", "assistant", "user"]


def test_no_progress_stops_after_four_outbound_requests():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=response(None, [tool()]))

    result = run(
        manifest(
            max_requests=4,
            oracle={"kind": "conversation", "value": "42", "continue_tools": True},
        ),
        handler,
    )
    assert len(calls) == 4
    assert not result.complete and result.exit_code == 2
    assert result.case_results[0].reason == "CONVERSATION_LIMIT"


def test_assertion_failure_is_never_retried():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=response("wrong"))

    result = run(manifest(retries=2), handler)
    assert len(calls) == 1 and result.exit_code == 1


def test_transient_retry_retains_first_and_eventual_outcome(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return (
            httpx.Response(503, json={"error": "busy"})
            if len(calls) == 1
            else httpx.Response(200, json=response())
        )

    result = run(manifest(retries=1), handler, tmp_path)
    trial = result.case_results[0]
    assert result.budget_usage["candidate"] == 2
    assert trial.first_attempt_status == "ERROR" and trial.eventual_status == "PASS"
    assert len(trial.attempt_refs) == 2
    from deepseek_provider_verifier.evidence import load_resume_state

    state = load_resume_state(tmp_path / "attempts.jsonl", result.manifest_hash)
    assert [a.attempt_number for a in state.prior_attempts] == [1, 2]
    assert [a.retry for a in state.prior_attempts] == [0, 1]
    assert [(a.retry, a.http_exchange_completed) for a in result.attempt_metrics] == [
        (0, True),
        (1, True),
    ]


def test_run_result_keeps_content_free_attempt_metrics_in_memory_and_summary(tmp_path):
    result = run(
        manifest(),
        lambda r: httpx.Response(200, content=b"not-json"),
        tmp_path,
    )

    metric = result.attempt_metrics[0]
    assert metric.status_code == 200
    assert metric.http_exchange_completed
    assert metric.error_type == "INVALID_JSON"
    serialized = metric.model_dump(mode="json")
    assert set(serialized) == {
        "schema_version",
        "endpoint",
        "case_id",
        "prompt_id",
        "repetition",
        "step",
        "retry",
        "attempt_number",
        "status_code",
        "timings",
        "http_exchange_completed",
        "error_type",
        "interrupted_reservation",
    }
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["attempt_metrics"] == [serialized]
    assert "request" not in serialized and "response" not in serialized


def test_resume_skips_completed_trials_and_rejects_changed_profile(tmp_path):
    m = manifest()
    calls = []

    def handler(r):
        calls.append(r)
        return httpx.Response(200, json=response())

    first = run(m, handler, tmp_path)
    second = run(m, handler, tmp_path, resume=True)
    assert len(calls) == 1 and first == second
    changed = manifest(diagnostic=True)
    with pytest.raises(ValueError, match="manifest"):
        run(changed, handler, tmp_path, resume=True)


@pytest.mark.parametrize(
    "field,value",
    [
        ("request_ceiling", 0),
        ("dataset_hash", "a" * 64),
        ("profile_hash", "a" * 64),
        ("manifest_hash", "a" * 64),
    ],
)
def test_tampered_manifest_is_rejected_before_network(field, value):
    def no_network(r):
        pytest.fail("tampered manifest sent network request")

    with pytest.raises(ValueError):
        run(manifest().model_copy(update={field: value}), no_network)


def test_recipe_cannot_override_model_or_generation_bound():
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=response())

    run(
        manifest(
            steps=[
                {
                    "kind": "user",
                    "content": "amber",
                    "request": {"model": "wrong", "max_tokens": 99999, "stream": True},
                }
            ]
        ),
        handler,
    )
    assert (
        bodies[0]["model"] == "fixture-model"
        and bodies[0]["max_tokens"] == 512
        and bodies[0]["stream"] is False
    )


def test_secrets_are_absent_from_all_stored_artifacts_including_errors(tmp_path):
    def handler(request):
        raise httpx.ReadError(
            "https://user:SENTINEL_SECRET@fixture.test?api_key=SENTINEL_SECRET",
            request=request,
        )

    result = run(manifest(), handler, tmp_path)
    assert result.exit_code == 2
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert "SENTINEL_SECRET" not in path.read_text()


def test_assembler_exception_preserves_capture_as_error(tmp_path, monkeypatch):
    from deepseek_provider_verifier import runner

    def broken(value):
        raise RecursionError("assembler recursion")

    monkeypatch.setattr(runner, "assemble_chat_json", broken)
    result = run(manifest(), lambda r: httpx.Response(200, json=response()), tmp_path)
    assert result.case_results[0].status == "ERROR"
    from deepseek_provider_verifier.evidence import load_resume_state

    attempt = load_resume_state(
        tmp_path / "attempts.jsonl", result.manifest_hash
    ).prior_attempts[0]
    assert attempt.status_code == 200 and attempt.capture["body_base64"]
    assert attempt.error["type"] == "ASSEMBLY_ERROR"


def test_cancellation_stops_queued_attempts_and_keeps_all_trials(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        raise asyncio.CancelledError()

    result = run(manifest(count=3), handler, tmp_path)
    assert len(calls) == 1 and not result.complete and result.exit_code == 2
    assert len(result.case_results) == 3
    assert result.budget_usage["candidate"] == 1


def test_deadline_cancels_request_and_leaves_unstarted_trials_visible(tmp_path):
    async def handler(request):
        await asyncio.sleep(1.1)
        return httpx.Response(200, json=response())

    m = manifest()
    # Use an internally consistent small deadline in the synthetic profile.
    profile = m.profile_snapshot.model_copy(
        update={
            "presets": {
                "smoke": m.profile_snapshot.presets["smoke"].model_copy(
                    update={"case_deadline_seconds": 1}
                )
            }
        }
    )
    from deepseek_provider_verifier.runner import rehash_manifest

    m = rehash_manifest(
        m.model_copy(
            update={
                "profile_snapshot": profile,
                "budgets": m.budgets.model_copy(update={"case_deadline_seconds": 1}),
            }
        )
    )
    result = run(m, handler, tmp_path)
    assert not result.complete and result.exit_code == 2
    assert result.case_results[0].reason == "CASE_DEADLINE"


@pytest.mark.parametrize(
    "order,expected",
    [
        ("sequential", ["candidate", "candidate", "reference", "reference"]),
        ("paired", ["candidate", "reference", "candidate", "reference"]),
    ],
)
def test_execution_order_is_explicit_and_reproducible(order, expected):
    from deepseek_provider_verifier.runner import execute_manifest

    names = []

    async def go():
        async with (
            httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda r: (
                        names.append("candidate")
                        or httpx.Response(200, json=response())
                    )
                )
            ) as a,
            httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda r: (
                        names.append("reference")
                        or httpx.Response(200, json=response())
                    )
                )
            ) as b,
        ):
            return await execute_manifest(
                manifest(count=2, endpoints=2, order=order),
                {"candidate": a, "reference": b},
                {"candidate": "SENTINEL_SECRET", "reference": "SENTINEL_SECRET"},
            )

    asyncio.run(go())
    assert names == expected


def test_resume_counts_incomplete_start_and_cannot_pass_a_budget_subset(tmp_path):
    from deepseek_provider_verifier.evidence import (
        append_record,
        atomic_json,
        load_resume_state,
    )

    m = manifest(count=2)
    atomic_json(tmp_path / "manifest.json", m.model_dump(mode="json"))
    for filename in ["attempts.jsonl", "results.jsonl"]:
        append_record(
            tmp_path / filename, {"kind": "manifest", "manifest_hash": m.manifest_hash}
        )
    for number, case in enumerate(m.cases, start=1):
        append_record(
            tmp_path / "attempts.jsonl",
            {
                "kind": "attempt_start",
                "endpoint": "candidate",
                "protocol": "chat",
                "case_id": case.id,
                "prompt_id": case.prompt_id,
                "repetition": case.repetition,
                "step": 0,
                "retry": 0,
                "attempt_number": number,
                "request_hash": "a" * 64,
            },
        )
    state = load_resume_state(tmp_path, m.manifest_hash)
    assert len(state.incomplete_records) == 2

    def no_network(r):
        pytest.fail("Exhausted resume budget sent another request")

    result = run(m, no_network, tmp_path, resume=True)
    assert not result.complete and result.exit_code == 2
    assert len(result.case_results) == 2 and result.budget_usage["candidate"] == 2
    assert all(r.reason == "ATTEMPT_BUDGET" for r in result.case_results)
    assert len(result.attempt_metrics) == 2
    assert all(metric.interrupted_reservation for metric in result.attempt_metrics)
    assert all(
        metric.http_exchange_completed is False for metric in result.attempt_metrics
    )
    from deepseek_provider_verifier.comparison import compare_runs

    comparison = compare_runs(result, result, (m, m), policy=None)
    for name in ("first_http_2xx_rate", "eventual_http_2xx_rate"):
        metric = comparison.metrics[name]
        assert (metric.value, metric.numerator, metric.denominator) == (0.0, 0, 2)


def test_resume_keeps_interrupted_first_attempt_and_eventual_http_success(tmp_path):
    from deepseek_provider_verifier.comparison import compare_runs
    from deepseek_provider_verifier.evidence import append_record, atomic_json

    m = manifest(max_requests=2)
    case = m.cases[0]
    atomic_json(tmp_path / "manifest.json", m.model_dump(mode="json"))
    for filename in ("attempts.jsonl", "results.jsonl"):
        append_record(
            tmp_path / filename, {"kind": "manifest", "manifest_hash": m.manifest_hash}
        )
    append_record(
        tmp_path / "attempts.jsonl",
        {
            "kind": "attempt_start",
            "endpoint": "candidate",
            "protocol": case.protocol,
            "case_id": case.id,
            "prompt_id": case.prompt_id,
            "repetition": case.repetition,
            "step": 0,
            "retry": 0,
            "attempt_number": 1,
            "request_hash": "a" * 64,
        },
    )

    resumed = run(
        m, lambda r: httpx.Response(200, json=response()), tmp_path, resume=True
    )
    from deepseek_provider_verifier.reports import (
        load_run_evidence,
        render_report,
        write_report_bundle,
    )

    write_report_bundle(tmp_path, resumed, allow_existing=True)
    loaded_manifest, loaded = load_run_evidence(tmp_path)
    assert loaded.complete and loaded.exit_code == 0
    assert loaded.budget_usage == {"candidate": 2}
    assert loaded.resume_dispositions[0]["disposition"] == "interrupted_attempt"
    assert loaded.report.integrity == "verified"
    for format in ("markdown", "junit", "json"):
        assert render_report(loaded, format)
    comparison = compare_runs(
        loaded, loaded, (loaded_manifest, loaded_manifest), policy=None
    )
    for format in ("markdown", "junit", "json"):
        assert render_report(comparison, format)

    assert [metric.http_exchange_completed for metric in resumed.attempt_metrics] == [
        False,
        True,
    ]
    assert comparison.metrics["first_http_2xx_rate"].value == 0
    assert comparison.metrics["eventual_http_2xx_rate"].value == 1

    for metrics in (
        loaded.attempt_metrics[:1],
        loaded.attempt_metrics + loaded.attempt_metrics[:1],
    ):
        damaged = loaded.model_copy(update={"attempt_metrics": metrics})
        atomic_json(tmp_path / "summary.json", damaged.model_dump(mode="json"))
        with pytest.raises(ValueError, match="attempts do not match"):
            load_run_evidence(tmp_path)
    write_report_bundle(tmp_path, loaded, allow_existing=True)


def test_resume_retains_first_ever_http_attempt_for_availability(tmp_path):
    from deepseek_provider_verifier.comparison import compare_runs

    m = manifest(max_requests=2)
    run(m, lambda r: httpx.Response(503, json={"error": "busy"}), tmp_path)
    resumed = run(
        m, lambda r: httpx.Response(200, json=response()), tmp_path, resume=True
    )

    assert [metric.status_code for metric in resumed.attempt_metrics] == [503, 200]
    compared = compare_runs(resumed, resumed, (m, m), policy=None)
    assert compared.metrics["first_http_2xx_rate"].value == 0
    assert compared.metrics["eventual_http_2xx_rate"].value == 1


def test_first_attempt_status_measures_entire_conversation():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200, json=response(None, [tool()]) if len(calls) == 1 else response("42")
        )

    result = run(
        manifest(
            max_requests=2,
            oracle={"kind": "conversation", "value": "42", "continue_tools": True},
        ),
        handler,
    )
    assert result.case_results[0].first_attempt_status == "PASS"


def test_responses_stateless_replay_retains_all_output_items_and_call_ids():
    bodies = []
    original = [
        {
            "id": "reason-a",
            "type": "reasoning",
            "summary": [{"type": "summary_text", "text": "fixture thought"}],
        },
        {
            "id": "item-a",
            "type": "function_call",
            "call_id": "call-original",
            "name": "add_integers",
            "arguments": '{"a":17,"b":25}',
            "status": "completed",
        },
    ]

    def handler(request):
        bodies.append(json.loads(request.content))
        output = (
            original
            if len(bodies) == 1
            else [
                {
                    "id": "msg-a",
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {"type": "output_text", "text": "42", "annotations": []}
                    ],
                }
            ]
        )
        return httpx.Response(
            200,
            json={
                "id": f"resp-{len(bodies)}",
                "object": "response",
                "status": "completed",
                "output": output,
                "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
            },
        )

    result = run(
        manifest(
            protocol="responses",
            max_requests=2,
            oracle={"kind": "conversation", "value": "42", "continue_tools": True},
        ),
        handler,
    )
    assert result.exit_code == 0
    assert bodies[1]["input"][1:3] == original
    assert bodies[1]["input"][3] == {
        "type": "function_call_output",
        "call_id": "call-original",
        "output": "42",
    }
    assert bodies[1]["store"] is False and bodies[1]["reasoning"] == {"effort": "none"}


def test_required_unsupported_endpoint_is_not_discovered_into_skip():
    result = run(
        manifest(protocol="responses", diagnostic=True),
        lambda r: httpx.Response(404, json={"error": "unsupported"}),
    )
    assert result.case_results[0].status == "FAIL"
    assert result.exit_code == 2  # unresolved profile remains visible alongside failure


def test_unknown_mock_function_never_runs_and_cannot_pass():
    result = run(
        manifest(
            max_requests=2,
            oracle={"kind": "conversation", "value": "amber", "continue_tools": True},
        ),
        lambda r: httpx.Response(
            200,
            json=response(
                "amber", [tool(name="shell", arguments='{"command":"echo unsafe"}')]
            ),
        ),
    )
    assert result.exit_code != 0
    assert any(
        a.id == "TOOL_NOT_REGISTERED" and a.status == "FAIL"
        for a in result.case_results[0].assertions
    )


def test_resume_detects_deleted_previously_checkpointed_results(tmp_path):
    m = manifest()
    run(m, lambda r: httpx.Response(200, json=response()), tmp_path)
    path = tmp_path / "results.jsonl"
    path.write_text(path.read_text().splitlines()[0] + "\n")
    with pytest.raises(ValueError, match="artifact"):
        run(m, lambda r: httpx.Response(200, json=response()), tmp_path, resume=True)


def test_retry_never_exceeds_four_actual_conversation_requests():
    calls = []

    def handler(request):
        calls.append(request)
        return (
            httpx.Response(503, json={"error": "busy"})
            if len(calls) % 2
            else httpx.Response(200, json=response(None, [tool()]))
        )

    result = run(
        manifest(
            max_requests=4,
            retries=1,
            oracle={"kind": "conversation", "value": "42", "continue_tools": True},
        ),
        handler,
    )
    assert len(calls) == 4 and not result.complete


def test_malformed_successful_tool_arguments_stop_without_retry_or_repair():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=response(None, [tool(arguments='{"a":17,')]))

    result = run(
        manifest(
            max_requests=2,
            retries=1,
            oracle={"kind": "conversation", "value": "42", "continue_tools": True},
        ),
        handler,
    )
    assert len(calls) == 1 and result.case_results[0].status == "FAIL"


def test_profile_calibration_cannot_gate_another_model_release():
    from deepseek_provider_verifier.runner import rehash_manifest

    m = manifest()
    profile = m.profile_snapshot.model_copy(
        update={
            "rules": [
                m.profile_snapshot.rules[0].model_copy(
                    update={"conditions": {"model_releases": ["different-release"]}}
                )
            ]
        }
    )
    result = run(
        rehash_manifest(m.model_copy(update={"profile_snapshot": profile})),
        lambda r: httpx.Response(200, json=response()),
    )
    assert result.exit_code == 2 and result.case_results[0].status == "INCONCLUSIVE"


def test_diagnostic_success_keeps_measured_end_to_end_success():
    result = run(
        manifest(diagnostic=True), lambda r: httpx.Response(200, json=response())
    )
    trial = result.case_results[0]
    assert trial.status == "INCONCLUSIVE"
    assert (
        next(
            m for m in trial.metric_observations if m.name == "end_to_end_success"
        ).value
        == 1
    )


def test_expected_http_rejection_is_successful_trial_but_not_2xx_availability():
    result = run(
        manifest(oracle={"kind": "structure", "status_class": 4}),
        lambda r: httpx.Response(400, json={"error": "expected fixture rejection"}),
    )
    trial = result.case_results[0]
    metrics = {metric.name: metric.value for metric in trial.metric_observations}

    assert trial.status == "PASS"
    assert metrics["end_to_end_success"] == 1
    assert metrics["available"] == 0


def test_unreconciled_torn_tail_is_reported_and_cannot_be_a_passing_subset(tmp_path):
    m = manifest()
    run(m, lambda r: httpx.Response(200, json=response()), tmp_path)
    with (tmp_path / "attempts.jsonl").open("ab") as handle:
        handle.write(b'{"kind":"attempt",')
    result = run(
        m, lambda r: pytest.fail("completed trial repeated"), tmp_path, resume=True
    )
    assert not result.complete and result.exit_code == 2
    assert result.resume_dispositions[0]["disposition"] == "retained_torn_tail"
    from deepseek_provider_verifier.evidence import atomic_json
    from deepseek_provider_verifier.reports import load_run_evidence

    assert not load_run_evidence(tmp_path)[1].complete
    atomic_json(
        tmp_path / "summary.json",
        result.model_copy(update={"complete": True}).model_dump(mode="json"),
    )
    with pytest.raises(ValueError, match="completion does not match"):
        load_run_evidence(tmp_path)


def test_failed_infrastructure_trial_is_incomplete_and_not_marked_done(tmp_path):
    result = run(
        manifest(), lambda r: httpx.Response(503, json={"error": "down"}), tmp_path
    )
    assert not result.complete and not result.case_results[0].completed
    from deepseek_provider_verifier.evidence import load_resume_state

    assert not load_resume_state(tmp_path, result.manifest_hash).completed_case_ids


def test_chat_stream_continuation_replays_fragmented_arguments():
    from deepseek_provider_verifier.runner import rehash_manifest

    m = manifest(
        max_requests=2,
        oracle={"kind": "conversation", "value": "42", "continue_tools": True},
    )
    m = rehash_manifest(
        m.model_copy(update={"cases": [m.cases[0].model_copy(update={"stream": True})]})
    )
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        deltas = (
            [
                {"role": "assistant", "reasoning_content": "fixture trace"},
                {"tool_calls": [{"index": 0, **tool(arguments='{"a":17,')}]},
                {"tool_calls": [{"index": 0, "function": {"arguments": '"b":25}'}}]},
            ]
            if len(bodies) == 1
            else [{"role": "assistant", "content": "42"}]
        )
        chunks = [
            {
                "id": "chat-fixture",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "fixture-model",
                "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
            }
            for delta in deltas
        ]
        chunks.append(
            {
                "id": "chat-fixture",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "fixture-model",
                "choices": [
                    {
                        "index": 0,
                        "delta": {},
                        "finish_reason": "tool_calls" if len(bodies) == 1 else "stop",
                    }
                ],
            }
        )
        content = (
            "".join("data: " + json.dumps(c) + "\n\n" for c in chunks)
            + "data: [DONE]\n\n"
        )
        return httpx.Response(
            200, content=content, headers={"content-type": "text/event-stream"}
        )

    result = run(m, handler)
    assert result.exit_code == 0
    assert (
        bodies[1]["messages"][1]["tool_calls"][0]["function"]["arguments"]
        == '{"a":17,"b":25}'
    )
    assert bodies[1]["messages"][1]["reasoning_content"] == "fixture trace"


def test_responses_stream_full_history_is_available_for_next_user_turn():
    from deepseek_provider_verifier.runner import rehash_manifest

    m = manifest(
        protocol="responses",
        max_requests=2,
        steps=[
            {"kind": "user", "content": "first"},
            {"kind": "user", "content": "second"},
        ],
        oracle={"kind": "exact_text", "value": "你"},
    )
    m = rehash_manifest(
        m.model_copy(update={"cases": [m.cases[0].model_copy(update={"stream": True})]})
    )
    content = Path("tests/fixtures/responses/text.sse").read_bytes()
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200, content=content, headers={"content-type": "text/event-stream"}
        )

    result = run(m, handler)
    assert result.exit_code == 0
    assert [item.get("type", item.get("role")) for item in bodies[1]["input"]] == [
        "user",
        "reasoning",
        "message",
        "user",
    ]


def test_successful_secret_echo_is_redacted_before_all_evidence_hashes(tmp_path):
    import base64

    from deepseek_provider_verifier.evidence import load_resume_state

    payload = response("SENTINEL_SECRET")
    payload["url"] = "https://host.test/path?token=SENTINEL_SECRET"
    m = manifest()
    result = run(m, lambda r: httpx.Response(200, json=payload), tmp_path)
    state = load_resume_state(tmp_path, result.manifest_hash)
    assert (
        "SENTINEL_SECRET"
        not in base64.b64decode(state.prior_attempts[0].capture["body_base64"]).decode()
    )
    assert all(
        "SENTINEL_SECRET" not in p.read_text()
        for p in tmp_path.iterdir()
        if p.is_file()
    )


def test_required_response_envelope_fields_cannot_be_missing():
    malformed = response()
    malformed.pop("id")
    result = run(manifest(), lambda r: httpx.Response(200, json=malformed))
    assert result.case_results[0].status == "FAIL"


def test_raw_capture_does_not_persist_unknown_url_credentials(tmp_path):
    import base64

    from deepseek_provider_verifier.evidence import load_resume_state

    payload = response()
    payload["debug_url"] = (
        "https://user:opaque-password@host.test/path?token=opaque-query#opaque-fragment"
    )
    result = run(manifest(), lambda r: httpx.Response(200, json=payload), tmp_path)
    state = load_resume_state(tmp_path, result.manifest_hash)
    body = base64.b64decode(state.prior_attempts[0].capture["body_base64"]).decode()
    assert "opaque-" not in body


def test_malformed_responses_history_stops_cleanly_after_persisting_capture(tmp_path):
    result = run(
        manifest(
            protocol="responses",
            max_requests=2,
            steps=[
                {"kind": "user", "content": "one"},
                {"kind": "user", "content": "two"},
            ],
        ),
        lambda r: httpx.Response(200, json=["invalid-output-shape"]),
        tmp_path,
    )
    assert result.exit_code == 1
    assert result.case_results[0].status == "FAIL"
    assert result.budget_usage["candidate"] == 1


@pytest.mark.parametrize(
    "model,contract_model,expected",
    [
        ("deepseek-flash", None, "PASS"),
        ("deepseek-v4-pro", None, "INCONCLUSIVE"),
        ("served-local-alias", "deepseek-flash", "PASS"),
    ],
)
def test_calibration_model_scope_is_independent_of_unknown_release(
    model, contract_model, expected
):
    from deepseek_provider_verifier.runner import rehash_manifest

    m = manifest()
    endpoint_data = m.endpoints["candidate"].model_dump(mode="json") | {
        "model": model,
        "model_release": "unknown",
    }
    if contract_model is not None:
        endpoint_data["contract_model"] = contract_model
    endpoint = Endpoint.model_validate(endpoint_data)
    profile = m.profile_snapshot.model_copy(
        update={
            "rules": [
                m.profile_snapshot.rules[0].model_copy(
                    update={"conditions": {"models": ["deepseek-flash"]}}
                )
            ]
        }
    )
    m = rehash_manifest(
        m.model_copy(
            update={"endpoints": {"candidate": endpoint}, "profile_snapshot": profile}
        )
    )
    result = run(m, lambda r: httpx.Response(200, json=response()))
    assert result.case_results[0].status == expected
    assert result.exit_code == (0 if expected == "PASS" else 2)
