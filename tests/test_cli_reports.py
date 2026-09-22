from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET

import pytest

from deepseek_provider_verifier.records import (
    AssertionResult,
    AttemptMetric,
    CaseResult,
    ComparisonMetric,
    ComparisonPolicy,
    ComparisonReportContext,
    ComparisonResult,
    RunReportContext,
    RunResult,
)
from deepseek_provider_verifier.reports import exit_status, render_report

ROOT = Path(__file__).resolve().parents[1]
CLI_ENV = {
    **os.environ,
    "PYTHONPATH": str(ROOT / "src"),
}
ALL_COUNTS = {name: 0 for name in ("PASS", "FAIL", "ERROR", "SKIP", "INCONCLUSIVE")}


def _run_result(*, complete: bool = True, status: str = "PASS") -> RunResult:
    result = CaseResult(
        case_id="C01.chat.non_thinking.nonstream",
        endpoint="candidate|unsafe\nnext",
        status=status,
        assertions=[
            AssertionResult(
                id="fixture",
                status=status,
                reason="provider <detail> [link](https://invalid.example)\x01 雪",
                rule_ids=["fixture.rule"],
                gating=True,
            )
        ],
        metric_observations=[],
        attempt_refs=["a" * 64],
        completed=complete,
    )
    counts = dict(ALL_COUNTS)
    counts[status] = 1
    return RunResult(
        manifest_hash="b" * 64,
        complete=complete,
        case_results=[result],
        counts=counts,
        budget_usage={"candidate|unsafe\nnext": 1},
        enabled_gates=["fixture"],
        exit_code=0,
        report=RunReportContext(
            run_id="fixture-run",
            created_at="2026-09-21T20:00:00Z",
            profile="fixture-profile",
            profile_hash="c" * 64,
            dataset_hash="d" * 64,
            endpoints={"candidate|unsafe\nnext": "fixture-model"},
            required_case_ids=["C01.chat.non_thinking.nonstream"],
            evidence_links={
                "C01.chat.non_thinking.nonstream": ["attempts.jsonl#" + "a" * 64]
            },
            integrity="verified",
        ),
    )


def _cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "deepseek_provider_verifier.cli", *args],
        cwd=ROOT,
        env=env or CLI_ENV,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def _write_fixture_inputs(tmp_path: Path, port: int, *, authenticated: bool = False):
    profile = json.loads((ROOT / "profiles/deepseek-api-2026-09-21.json").read_text())
    profile["id"] = "offline-fixture"
    profile["presets"] = {
        "confirmation": {
            "schema_version": 1,
            "max_requests_per_protocol": 2,
            "max_requests_per_endpoint": 2,
            "max_total_requests": 4,
            "max_requests_per_conversation": 1,
            "max_retries": 0,
            "concurrency": 1,
            "case_deadline_seconds": 30,
            "mode_max_output_tokens": {"non_thinking": 512, "thinking": 4096},
            "case_ids": ["C01"],
            "attached_assertion_case_ids": [],
        }
    }
    for rule in profile["rules"]:
        if rule["id"] == "chat.output.present":
            rule["maturity"] = "calibrated"
            rule["gating"] = True
    profile_path = tmp_path / "offline-profile.json"
    profile_path.write_text(json.dumps(profile))
    auth = "api_key_env='FIXTURE_API_KEY'" if authenticated else "auth_none=true"
    config_path = tmp_path / "providers.toml"
    config_path.write_text(
        f"""[run]
profile='offline-fixture'
suite='confirmation'
protocols=['chat']
concurrency=1
max_attempts_per_endpoint=2
retries=0
execution_order='paired'
scorer_revision='fixture-v1'

[endpoints.reference]
base_url='http://127.0.0.1:{port}/v1'
{auth}
model='fixture-reference'
contract_model='fixture-contract'
model_release='synthetic-v1'

[endpoints.candidate]
base_url='http://127.0.0.1:{port}/v1'
{auth}
model='fixture-candidate'
contract_model='fixture-contract'
model_release='synthetic-v1'
"""
    )
    return config_path, profile_path


class _FixtureHandler(BaseHTTPRequestHandler):
    requests = 0

    def do_POST(self):
        type(self).requests += 1
        length = int(self.headers.get("content-length", "0"))
        request = json.loads(self.rfile.read(length))
        if request["stream"]:
            chunks = [
                {
                    "id": "fixture",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": request["model"],
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant", "content": "amber"},
                            "finish_reason": None,
                        }
                    ],
                },
                {
                    "id": "fixture",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": request["model"],
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                },
            ]
            body = (
                "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
                + "data: [DONE]\n\n"
            ).encode()
            content_type = "text/event-stream"
        else:
            body = json.dumps(
                {
                    "id": "fixture",
                    "object": "chat.completion",
                    "created": 1,
                    "model": request["model"],
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "amber"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 2,
                        "completion_tokens": 1,
                        "total_tokens": 3,
                    },
                }
            ).encode()
            content_type = "application/json"
        self.send_response(200)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


class _SlowFixtureHandler(_FixtureHandler):
    entered = threading.Event()
    release = threading.Event()

    def do_POST(self):
        type(self).entered.set()
        type(self).release.wait(timeout=10)
        super().do_POST()


@pytest.fixture
def fixture_server():
    _FixtureHandler.requests = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


@pytest.fixture
def slow_fixture_server():
    _SlowFixtureHandler.entered.clear()
    _SlowFixtureHandler.release.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SlowFixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        _SlowFixtureHandler.release.set()
        server.shutdown()
        thread.join()
        server.server_close()


def test_incomplete_run_cannot_exit_success():
    assert exit_status(_run_result(complete=False)) == 2


def test_report_api_is_exported_from_package():
    import deepseek_provider_verifier as dpv

    assert dpv.exit_status(_run_result(complete=False)) == 2
    assert "Verification run" in dpv.render_report(_run_result(), "markdown")


@pytest.mark.parametrize(
    ("status", "expected"),
    [("ERROR", 2), ("INCONCLUSIVE", 2), ("FAIL", 1), ("PASS", 0)],
)
def test_exit_status_uses_required_case_precedence(status, expected):
    assert exit_status(_run_result(status=status)) == expected


def test_reports_keep_every_status_count_and_escape_server_text():
    result = _run_result(status="FAIL")

    markdown = render_report(result, "markdown")
    junit = render_report(result, "junit")
    canonical = json.loads(render_report(result, "json"))

    assert all(
        f"| {status} | {result.counts[status]} |" in markdown for status in ALL_COUNTS
    )
    assert "Required gates" in markdown
    assert "candidate\\|unsafe next" in markdown
    assert "provider &lt;detail&gt; \\[link\\]" in markdown
    assert "\x01" not in junit
    assert "雪" in junit
    assert canonical["counts"] == result.counts
    assert canonical["exit_code"] == 1


def test_comparison_report_marks_report_only_quality_as_inconclusive():
    result = ComparisonResult(
        comparable=True,
        reference_endpoint="reference",
        candidate_endpoint="candidate",
        manifest_differences=[],
        metrics={
            "task_success": ComparisonMetric(
                value=1,
                numerator=1,
                denominator=1,
                unavailable=0,
                reference_value=1,
                reference_numerator=1,
                reference_denominator=1,
                reference_unavailable=0,
                difference=0,
            )
        },
        report=ComparisonReportContext(
            created_at="2026-09-21T20:05:00Z",
            profile="fixture-profile",
            profile_hash="c" * 64,
            dataset_hash="d" * 64,
            endpoint_models={
                "reference": "fixture-model",
                "candidate": "fixture-model",
            },
            case_count=1,
            required_case_count=1,
            status_counts=dict(ALL_COUNTS, PASS=2),
            enabled_gates=[],
            evidence_links={
                "C01.chat.non_thinking.nonstream": [
                    "../reference/summary.json",
                    "../candidate/summary.json",
                ]
            },
            integrity="verified",
        ),
    )

    markdown = render_report(result, "markdown")
    junit = ET.fromstring(render_report(result, "junit"))

    assert "Quality gate | INCONCLUSIVE (report only)" in markdown
    assert "| PASS | 2 |" in markdown
    assert junit.attrib["errors"] == "0"
    assert junit.attrib["skipped"] == "1"
    assert junit.find("testcase/skipped").attrib["message"] == "report-only observation"


def test_required_inconclusive_comparison_is_a_junit_error():
    result = ComparisonResult(
        comparable=True,
        reference_endpoint="reference",
        candidate_endpoint="candidate",
        manifest_differences=[],
        metrics={
            "task_success": ComparisonMetric(
                value=1,
                numerator=1,
                denominator=1,
                unavailable=0,
                reference_value=1,
                reference_numerator=1,
                reference_denominator=1,
                reference_unavailable=0,
                difference=0,
            )
        },
        policy=ComparisonPolicy(
            allowed_drops={"task_success": 0.1},
            minimum_distinct_prompts=2,
            minimum_repetitions=1,
            confidence_level=0.95,
            bootstrap_samples=100,
            bootstrap_seed=7,
        ),
        metric_gates={"task_success": "INCONCLUSIVE"},
        quality_gate="INCONCLUSIVE",
    )

    junit = ET.fromstring(render_report(result, "junit"))

    assert junit.attrib["errors"] == "1"
    assert junit.attrib.get("skipped", "0") == "0"
    assert junit.find("testcase/error").attrib["message"] == "quality gate inconclusive"


def test_legacy_sparse_counts_keep_http_completion_unavailable():
    legacy = RunResult(
        manifest_hash="e" * 64,
        complete=True,
        case_results=[
            CaseResult(
                case_id="C01.chat.non_thinking.nonstream",
                endpoint="reference",
                status="INCONCLUSIVE",
                assertions=[],
                metric_observations=[],
                attempt_refs=[],
            )
        ],
        counts={"INCONCLUSIVE": 1},
        budget_usage={"reference": 1},
        enabled_gates=["fixture"],
        exit_code=2,
        attempt_metrics=[
            AttemptMetric(
                endpoint="reference",
                case_id="C01.chat.non_thinking.nonstream",
                repetition=0,
                step=0,
                retry=0,
                attempt_number=1,
                status_code=200,
                http_exchange_completed=None,
            )
        ],
    )

    canonical = json.loads(render_report(legacy, "json"))

    assert canonical["counts"] == dict(ALL_COUNTS, INCONCLUSIVE=1)
    assert canonical["attempt_metrics"][0]["http_exchange_completed"] is None
    assert canonical["exit_code"] == 2


def test_cli_help_and_safe_setup_errors(tmp_path):
    help_result = _cli("--help")
    assert help_result.returncode == 0
    assert all(
        name in help_result.stdout for name in ("plan", "run", "compare", "report")
    )

    missing = _cli("plan", "--config", str(tmp_path / "missing.toml"))
    assert missing.returncode == 2
    assert "missing.toml" in missing.stderr

    bad_config = tmp_path / "bad.toml"
    bad_config.write_text(
        """[run]\nprofile='x'\nsuite='smoke'\nprotocols=['bogus']\n"
        "concurrency=1\nmax_attempts_per_endpoint=1\nretries=0\n"
        "[endpoints.fixture]\nbase_url='https://user:secret@example.test?token=secret'\n"
        "api_key_env='SECRET_ENV'\nmodel='secret-model'\nmodel_release='unknown'\n"""
    )
    invalid = _cli("plan", "--config", str(bad_config))
    assert invalid.returncode == 2
    assert "secret" not in invalid.stderr
    assert "bogus" not in invalid.stderr


def test_plan_is_offline_and_reports_named_missing_prerequisites(tmp_path):
    config, profile = _write_fixture_inputs(tmp_path, 9, authenticated=True)
    env = dict(CLI_ENV)
    env.pop("FIXTURE_API_KEY", None)

    planned = _cli("plan", "--config", str(config), "--profile", str(profile), env=env)

    assert planned.returncode == 0
    output = json.loads(planned.stdout)
    assert output["manifest"]["request_ceiling"] == 4
    assert output["missing_prerequisites"] == ["FIXTURE_API_KEY"]
    assert not list(tmp_path.glob("attempts.jsonl"))


def test_run_requires_selected_endpoints_and_credentials_before_output(tmp_path):
    config, profile = _write_fixture_inputs(tmp_path, 9, authenticated=True)
    env = dict(CLI_ENV)
    env.pop("FIXTURE_API_KEY", None)
    out = tmp_path / "run"

    unnamed = _cli(
        "run",
        "--config",
        str(config),
        "--profile",
        str(profile),
        "--out",
        str(out),
        env=env,
    )
    missing = _cli(
        "run",
        "--config",
        str(config),
        "--profile",
        str(profile),
        "--endpoint",
        "candidate",
        "--out",
        str(out),
        env=env,
    )

    assert unnamed.returncode == 2 and "--endpoint" in unnamed.stderr
    assert missing.returncode == 2 and "FIXTURE_API_KEY" in missing.stderr
    assert "127.0.0.1" not in missing.stderr
    assert not out.exists()


def test_run_repeated_endpoints_writes_consistent_reports_without_overwrite(
    tmp_path, fixture_server
):
    config, profile = _write_fixture_inputs(tmp_path, fixture_server)
    out = tmp_path / "paired"

    completed = _cli(
        "run",
        "--config",
        str(config),
        "--profile",
        str(profile),
        "--endpoint",
        "reference",
        "--endpoint",
        "candidate",
        "--out",
        str(out),
    )

    assert completed.returncode == 0, completed.stderr
    assert _FixtureHandler.requests == 4
    assert {path.name for path in out.iterdir()} >= {
        "manifest.json",
        "attempts.jsonl",
        "results.jsonl",
        "evidence-index.json",
        "summary.json",
        "summary.md",
        "junit.xml",
    }
    summary = json.loads((out / "summary.json").read_text())
    assert summary["counts"] == dict(ALL_COUNTS, PASS=4)
    assert summary["exit_code"] == 0
    assert len(summary["report"]["evidence_links"]) == 4
    assert "fixture-reference" in (out / "summary.md").read_text()
    assert 'tests="4"' in (out / "junit.xml").read_text()

    repeated = _cli(
        "run",
        "--config",
        str(config),
        "--profile",
        str(profile),
        "--endpoint",
        "candidate",
        "--out",
        str(out),
    )
    assert repeated.returncode == 2
    assert "already exists" in repeated.stderr
    assert _FixtureHandler.requests == 4


def test_compare_and_report_load_verified_stored_evidence(tmp_path, fixture_server):
    config, profile = _write_fixture_inputs(tmp_path, fixture_server)
    run_dir = tmp_path / "paired evidence #1"
    completed = _cli(
        "run",
        "--config",
        str(config),
        "--profile",
        str(profile),
        "--endpoint",
        "reference",
        "--endpoint",
        "candidate",
        "--out",
        str(run_dir),
    )
    assert completed.returncode == 0, completed.stderr
    comparison_dir = tmp_path / "comparison output"
    relative_run = os.path.relpath(run_dir, ROOT)
    relative_comparison = os.path.relpath(comparison_dir, ROOT)

    compared = _cli(
        "compare",
        relative_run,
        relative_run,
        "--reference-endpoint",
        "reference",
        "--candidate-endpoint",
        "candidate",
        "--out",
        relative_comparison,
    )
    rendered = _cli("report", str(comparison_dir), "--format", "markdown")

    assert compared.returncode == 0, compared.stderr
    assert rendered.returncode == 0, rendered.stderr
    assert "INCONCLUSIVE (report only)" in rendered.stdout
    assert "fixture-reference" in rendered.stdout
    assert "summary-only" in rendered.stdout
    assert {path.name for path in comparison_dir.iterdir()} == {
        "summary.json",
        "summary.md",
        "junit.xml",
    }
    comparison_summary = json.loads((comparison_dir / "summary.json").read_text())
    assert comparison_summary["report"]["quality_verdict"] == "REPORT_ONLY"
    assert comparison_summary["report"]["exit_code"] == 0
    evidence_links = [
        link
        for links in comparison_summary["report"]["evidence_links"].values()
        for link in links
    ]
    assert "%20" in evidence_links[0] and "%23" in evidence_links[0]
    assert all(
        (comparison_dir / unquote(urlsplit(link).path)).resolve().is_file()
        for link in evidence_links
    )
    junit = (comparison_dir / "junit.xml").read_text()
    assert all(link in junit for link in evidence_links)

    summary = json.loads((run_dir / "summary.json").read_text())
    summary["counts"]["PASS"] = 999
    (run_dir / "summary.json").write_text(json.dumps(summary))
    rejected = _cli(
        "compare",
        str(run_dir),
        str(run_dir),
        "--reference-endpoint",
        "reference",
        "--candidate-endpoint",
        "candidate",
        "--out",
        str(tmp_path / "rejected"),
    )
    assert rejected.returncode == 2
    assert (
        "aggregate" in rejected.stderr.lower()
        or "count mismatch" in rejected.stderr.lower()
    )
    assert not (tmp_path / "rejected").exists()


def test_compare_rejects_false_incomplete_aggregate_even_with_matching_exit(
    tmp_path, fixture_server
):
    config, profile = _write_fixture_inputs(tmp_path, fixture_server)
    run_dir = tmp_path / "paired"
    completed = _cli(
        "run",
        "--config",
        str(config),
        "--profile",
        str(profile),
        "--endpoint",
        "reference",
        "--endpoint",
        "candidate",
        "--out",
        str(run_dir),
    )
    assert completed.returncode == 0
    summary = json.loads((run_dir / "summary.json").read_text())
    summary["complete"] = False
    summary["exit_code"] = 2
    (run_dir / "summary.json").write_text(json.dumps(summary))

    rejected = _cli(
        "compare",
        str(run_dir),
        str(run_dir),
        "--reference-endpoint",
        "reference",
        "--candidate-endpoint",
        "candidate",
        "--out",
        str(tmp_path / "comparison"),
    )

    assert rejected.returncode == 2
    assert (
        "completion" in rejected.stderr.lower()
        or "aggregate" in rejected.stderr.lower()
    )


def test_compare_accepts_explicit_model_mapping_and_quality_policy(
    tmp_path, fixture_server
):
    config, profile = _write_fixture_inputs(tmp_path, fixture_server)
    config.write_text(
        config.read_text().replace("contract_model='fixture-contract'\n", "")
    )
    run_dir = tmp_path / "paired"
    completed = _cli(
        "run",
        "--config",
        str(config),
        "--profile",
        str(profile),
        "--endpoint",
        "reference",
        "--endpoint",
        "candidate",
        "--out",
        str(run_dir),
    )
    assert completed.returncode == 0

    unmapped = _cli(
        "compare",
        str(run_dir),
        str(run_dir),
        "--reference-endpoint",
        "reference",
        "--candidate-endpoint",
        "candidate",
        "--out",
        str(tmp_path / "unmapped"),
    )
    mapped = _cli(
        "compare",
        str(run_dir),
        str(run_dir),
        "--reference-endpoint",
        "reference",
        "--candidate-endpoint",
        "candidate",
        "--model-map",
        "fixture-reference=fixture-candidate",
        "--out",
        str(tmp_path / "mapped"),
    )
    policy = tmp_path / "policy.json"
    policy.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "allowed_drops": {"end_to_end_success": 0.1},
                "minimum_distinct_prompts": 2,
                "minimum_repetitions": 1,
                "confidence_level": 0.95,
                "bootstrap_samples": 100,
                "bootstrap_seed": 7,
            }
        )
    )
    gated = _cli(
        "compare",
        str(run_dir),
        str(run_dir),
        "--reference-endpoint",
        "reference",
        "--candidate-endpoint",
        "candidate",
        "--model-map",
        "fixture-reference=fixture-candidate",
        "--quality-policy",
        str(policy),
        "--out",
        str(tmp_path / "gated"),
    )

    assert unmapped.returncode == 2
    assert mapped.returncode == 0
    assert gated.returncode == 2
    assert (
        json.loads((tmp_path / "gated" / "summary.json").read_text())["quality_gate"]
        == "INCONCLUSIVE"
    )


def test_interrupted_run_exits_two_and_retains_auditable_state(
    tmp_path, slow_fixture_server
):
    config, profile = _write_fixture_inputs(tmp_path, slow_fixture_server)
    out = tmp_path / "interrupted"
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "deepseek_provider_verifier.cli",
            "run",
            "--config",
            str(config),
            "--profile",
            str(profile),
            "--endpoint",
            "candidate",
            "--out",
            str(out),
        ],
        cwd=ROOT,
        env=CLI_ENV,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert _SlowFixtureHandler.entered.wait(timeout=10)
    process.send_signal(signal.SIGINT)
    stdout, stderr = process.communicate(timeout=10)

    assert process.returncode == 2, (stdout, stderr)
    assert (out / "attempts.jsonl").exists()
    if (out / "summary.json").exists():
        assert json.loads((out / "summary.json").read_text())["exit_code"] == 2


def test_packaged_synthetic_fixture_serves_the_four_request_example(tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    config = tmp_path / "offline.toml"
    config.write_text(
        (ROOT / "configs/offline-fixture.example.toml")
        .read_text()
        .replace("127.0.0.1:8765", f"127.0.0.1:{port}")
    )
    fixture = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "deepseek_provider_verifier.synthetic_fixture",
            "--port",
            str(port),
            "--max-requests",
            "4",
        ],
        cwd=tmp_path,
        env=CLI_ENV,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        for _ in range(50):
            if fixture.poll() is not None:
                break
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    break
            except OSError:
                time.sleep(0.05)
        completed = _cli(
            "run",
            "--config",
            str(config),
            "--profile",
            str(ROOT / "profiles/offline-confirmation.example.json"),
            "--endpoint",
            "fixture",
            "--out",
            str(tmp_path / "run"),
        )
        fixture_stdout, fixture_stderr = fixture.communicate(timeout=10)
    finally:
        if fixture.poll() is None:
            fixture.terminate()
            fixture.wait(timeout=5)

    assert completed.returncode == 0, completed.stderr
    assert fixture.returncode == 0, fixture_stderr
    assert "synthetic fixture" in fixture_stdout.lower()
    assert json.loads((tmp_path / "run" / "summary.json").read_text())[
        "counts"
    ] == dict(ALL_COUNTS, PASS=4)


def test_synthetic_fixture_rejects_non_loopback_bind():
    rejected = subprocess.run(
        [
            sys.executable,
            "-m",
            "deepseek_provider_verifier.synthetic_fixture",
            "--host",
            "0.0.0.0",
        ],
        cwd=ROOT,
        env=CLI_ENV,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert rejected.returncode == 2
    assert "--host" in rejected.stderr


def test_run_loader_rejects_checkpoint_with_deleted_journals(tmp_path, fixture_server):
    from deepseek_provider_verifier.reports import load_run_evidence

    config, profile = _write_fixture_inputs(tmp_path, fixture_server)
    run_dir = tmp_path / "damaged"
    completed = _cli(
        "run",
        "--config",
        str(config),
        "--profile",
        str(profile),
        "--endpoint",
        "candidate",
        "--out",
        str(run_dir),
    )
    assert completed.returncode == 0
    (run_dir / "attempts.jsonl").unlink()
    (run_dir / "results.jsonl").unlink()

    with pytest.raises(
        (FileNotFoundError, ValueError), match="checkpoint|No such file"
    ):
        load_run_evidence(run_dir)
