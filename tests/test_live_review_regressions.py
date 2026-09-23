"""Evidence must distinguish missing observations from measured boundaries."""

import httpx
import pytest
from test_runner import run
from test_size_cases import output_body, wire
from test_workflows import selected

from deepseek_provider_verifier.assertions import evaluate_case


@pytest.mark.parametrize("protocol", ["chat", "responses"])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("missing", ["details", "reasoning"])
def test_output_boundary_requires_reported_reasoning_usage(protocol, stream, missing):
    m = selected("sizes-small", "L10", protocol, stream=stream)
    body = output_body(protocol)
    key = "completion_tokens_details" if protocol == "chat" else "output_tokens_details"
    if missing == "details":
        body["usage"].pop(key)
    else:
        body["usage"][key].pop("reasoning_tokens")
    r = run(m, lambda req: wire(protocol, body, stream)).case_results[0]
    assert r.status == "INCONCLUSIVE"
    measured = next(a.observed for a in r.assertions if a.id == "SIZE_MEASUREMENTS")
    assert measured["provider_usage"]["state"] == "missing"
    assert measured["provider_usage"]["reasoning_tokens"] is None
    assert measured["provider_usage"]["visible_output_tokens"] is None
    assert measured["requested_output_utilization"] is None
    assert not any(a.status == "FAIL" for a in r.assertions)


@pytest.mark.parametrize("protocol", ["chat", "responses"])
@pytest.mark.parametrize("stream", [False, True])
def test_normal_stop_near_cap_does_not_certify_output_boundary(protocol, stream):
    m = selected("sizes-small", "L10", protocol, stream=stream)
    body = output_body(protocol, length=False)
    r = run(m, lambda req: wire(protocol, body, stream)).case_results[0]
    assert r.status == "INCONCLUSIVE"
    assert next(a.status for a in r.assertions if a.id == "SIZE_TASK") == "PASS"
    assert (
        next(a.status for a in r.assertions if a.id == "OUTPUT_BOUNDARY")
        == "INCONCLUSIVE"
    )


@pytest.mark.parametrize("protocol", ["chat", "responses"])
@pytest.mark.parametrize("thinking", [False, True])
def test_unexecuted_workflow_remains_unscored(protocol, thinking):
    m = selected("workflows-small", "W01", protocol, thinking=thinking)
    r = evaluate_case(m.cases[0], [], m.profile_snapshot.rules)
    assert r.status == "INCONCLUSIVE"
    metric = next(x for x in r.metric_observations if x.name == "task_success")
    assert metric.scored is False
    assert metric.reason == "No observations; planned trial remains unscored"
    assert not any(a.status == "FAIL" for a in r.assertions)


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("failure", ["markdown", "junit", "reliability"])
def test_report_render_failure_preserves_existing_bundle(
    tmp_path, monkeypatch, existing, failure
):
    from test_runner import response

    from deepseek_provider_verifier import reliability, reports

    m = selected("repeatability", "R01")
    r = run(m, lambda req: httpx.Response(200, json=response("amber")))
    assert r.counts == {"PASS": 1}
    target = tmp_path / "report"
    before = {
        name: b"previous report"
        for name in ["summary.json", "summary.md", "junit.xml", "reliability.json"]
    }
    if existing:
        target.mkdir()
        for name, content in before.items():
            (target / name).write_bytes(content)
    original_render = reports.render_report
    original_reliability = reliability.build_reliability
    builds = 0

    def render(*args, **kwargs):
        if args[1] == failure:
            raise ValueError("injected rendering failure")
        return original_render(*args, **kwargs)

    def analysis(*args, **kwargs):
        nonlocal builds
        builds += 1
        if failure == "reliability" and builds == 2:
            raise ValueError("injected rendering failure")
        return original_reliability(*args, **kwargs)

    monkeypatch.setattr(reports, "render_report", render)
    monkeypatch.setattr(reliability, "build_reliability", analysis)
    with pytest.raises(ValueError, match="injected rendering failure"):
        reports.write_report_bundle(target, r, manifest=m, allow_existing=True)
    if existing:
        assert {p.name: p.read_bytes() for p in target.iterdir()} == before
    else:
        assert not target.exists()


def test_fixture_reports_an_os_assigned_port_after_binding(tmp_path):
    import json
    import subprocess
    import sys
    import time

    ready = tmp_path / "ready.json"
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "deepseek_provider_verifier.synthetic_fixture",
            "--port",
            "0",
            "--max-requests",
            "1",
            "--ready-file",
            str(ready),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        for _ in range(100):
            if ready.exists() or server.poll() is not None:
                break
            time.sleep(0.05)
        assert ready.exists(), (
            server.communicate(timeout=2)[0]
            if server.poll() is not None
            else "fixture never became ready"
        )
        port = json.loads(ready.read_text())["port"]
        assert 1 <= port <= 65535
        with httpx.Client(trust_env=False) as client:
            response = client.post(
                f"http://127.0.0.1:{port}/v1/chat/completions",
                json={
                    "model": "synthetic-fixture-model",
                    "stream": False,
                    "messages": [
                        {
                            "role": "user",
                            "content": "Reply with exactly the lowercase word amber.",
                        }
                    ],
                },
            )
        assert response.status_code == 200
        assert response.json()["choices"][0]["message"]["content"] == "amber"
        assert server.wait(timeout=5) == 0
    finally:
        if server.poll() is None:
            server.terminate()
            server.wait(timeout=5)
        if server.stdout:
            server.stdout.close()


@pytest.mark.parametrize("protocol", ["chat", "responses"])
def test_missing_reasoning_preserves_valid_context_measurement(protocol):
    import json

    from test_size_cases import declared

    m = selected("sizes-small", "L01", protocol)
    m = declared(m, {"candidate": {"context_tokens": 200}})
    body = output_body(
        protocol,
        usage=5,
        length=False,
        text=json.dumps(m.cases[0].oracle["expected_facts"]),
    )
    key = "completion_tokens_details" if protocol == "chat" else "output_tokens_details"
    body["usage"].pop(key)
    r = run(m, lambda req: httpx.Response(200, json=body)).case_results[0]
    assert r.status == "PASS"
    measured = next(a.observed for a in r.assertions if a.id == "SIZE_MEASUREMENTS")
    assert measured["provider_usage"]["input_tokens"] == 20
    assert measured["context_utilization"] == 0.1
    assert measured["requested_output_utilization"] is None


@pytest.mark.parametrize("protocol", ["chat", "responses"])
@pytest.mark.parametrize("stream", [False, True])
def test_sixteen_parallel_calls_have_a_full_response_budget(protocol, stream):
    from test_workflows import execute_fixture

    m = selected("workflows-full", "W05", protocol, stream=stream)
    result, requests = execute_fixture(m)
    assert result.counts == {"PASS": 1}
    cap_key = "max_tokens" if protocol == "chat" else "max_output_tokens"
    assert requests[0][cap_key] == 2048
    assert m.cases[0].max_output_tokens == 2048
    assert (
        m.profile_snapshot.presets["workflows-full"].mode_max_output_tokens[
            "non_thinking"
        ]
        == 2048
    )
    # The small preset keeps its original lower per-request cost.
    small = selected("workflows-small", "W04", protocol, stream=stream)
    assert small.cases[0].max_output_tokens == 512


@pytest.mark.parametrize(
    "kind", ["large_output", "large_input", "repeatability", "legacy"]
)
def test_cli_size_probes_use_the_declared_case_read_budget(kind, monkeypatch):
    import asyncio
    import json

    from test_runner import manifest, response

    from deepseek_provider_verifier import cli

    if kind == "large_output":
        m = selected("sizes-large", "L11")
        body = output_body("chat", usage=16384)
    elif kind == "large_input":
        m = selected("sizes-small", "L01")
        body = response(json.dumps(m.cases[0].oracle["expected_facts"]))
    elif kind == "repeatability":
        m = selected("repeatability", "R01")
        body = response("amber")
    else:
        m = manifest()
        body = response("amber")

    def client(*, timeout):
        assert timeout.connect == 10
        assert timeout.read == (
            m.budgets.case_deadline_seconds
            if kind in ("large_input", "large_output")
            else 30
        )
        assert timeout.write == 30
        return httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=body)
            ),
            timeout=timeout,
        )

    monkeypatch.setattr(cli, "endpoint_client", client)
    result = asyncio.run(
        cli._execute(
            m, {name: "fixture-credential" for name in m.endpoints}, None, False
        )
    )
    assert sum(result.budget_usage.values()) == 1
