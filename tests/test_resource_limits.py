"""Byte bounds apply to actual wire data and never certify provider limits."""

import asyncio
import json

import httpx
import pytest
from test_runner import manifest, response, run

from deepseek_provider_verifier.depth_metadata import (
    plan_resource_summary,
    resource_limits,
)
from deepseek_provider_verifier.runner import rehash_manifest
from deepseek_provider_verifier.transport import send_request


def limited(m, request=8388608, capture=8388608):
    cases = [
        c.model_copy(
            update={
                "oracle": dict(
                    c.oracle,
                    depth={
                        "version": 1,
                        "family": "limits",
                        "resource_limits": {
                            "max_request_bytes": request,
                            "max_response_bytes": capture,
                        },
                    },
                )
            }
        )
        for c in m.cases
    ]
    return rehash_manifest(m.model_copy(update={"cases": cases}))


class Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.parts = chunks
        self.closed = False

    async def __aiter__(self):
        for chunk in self.parts:
            yield chunk

    async def aclose(self):
        self.closed = True


def test_oversize_request_is_not_sent_or_debited(tmp_path):
    m = limited(manifest(steps=[{"kind": "user", "content": "你" * 100}]), request=128)
    result = run(m, lambda req: pytest.fail("Oversized request sent"), tmp_path)
    assert result.exit_code == 2
    assert result.budget_usage.get("candidate", 0) == 0
    assert result.case_results[0].reason == "REQUEST_BYTE_LIMIT"
    assert "attempt_start" not in (tmp_path / "attempts.jsonl").read_text()


@pytest.mark.parametrize("content", ["ascii", "雪" * 10])
def test_exact_wire_size_passes_and_one_byte_less_stops(content):
    base = manifest(steps=[{"kind": "user", "content": content}])
    seen = []

    def handler(req):
        seen.append(req.content)
        return httpx.Response(200, json=response())

    assert run(base, handler).exit_code == 0
    size = len(seen[0])
    assert run(limited(base, request=size), handler).exit_code == 0
    assert seen[0] == seen[1]
    assert (
        run(
            limited(base, request=size - 1), lambda req: pytest.fail("Over cap")
        ).exit_code
        == 2
    )
    assert resource_limits(base.cases[0]) == (None, None)


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize(
    "chunks,cap", [([b"x" * 100000], 31), (["雪".encode(), b"abcdef"], 4)]
)
def test_transport_caps_retained_chunks_before_decode(chunks, cap, streaming):
    body = Chunks(chunks)

    async def go():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(
                    200,
                    stream=body,
                    headers={
                        "content-type": "text/event-stream"
                        if streaming
                        else "application/json"
                    },
                )
            )
        ) as client:
            return await send_request(
                client,
                manifest().endpoints["candidate"],
                "chat/completions",
                {"stream": streaming},
                "secret-test",
                max_response_bytes=cap,
            )

    result = asyncio.run(go())
    assert body.closed
    assert len(result.raw_body) == cap
    assert not result.http_exchange_completed
    assert result.error["type"] == "RESPONSE_BYTE_LIMIT"
    assert result.chunks[-1].end_byte == cap


def test_complete_response_exactly_at_cap_passes_but_partial_secret_is_omitted(
    tmp_path,
):
    data = json.dumps(response(), separators=(",", ":")).encode()
    body = Chunks([data[:41], data[41:]])
    result = run(
        limited(manifest(), capture=len(data)),
        lambda req: httpx.Response(
            200, stream=body, headers={"content-type": "application/json"}
        ),
    )
    assert result.exit_code == 0 and body.closed
    secret_body = Chunks(
        [b'{"text":"secret-test http://user:password@host/path?token=secret']
    )
    result = run(
        limited(manifest(), capture=30),
        lambda req: httpx.Response(
            200, stream=secret_body, headers={"content-type": "application/json"}
        ),
        tmp_path,
    )
    assert result.exit_code == 2 and secret_body.closed
    assert result.case_results[0].status == "ERROR"
    assert result.case_results[0].reason == "RESPONSE_BYTE_LIMIT"
    import base64

    from deepseek_provider_verifier.evidence import load_resume_state

    attempt = load_resume_state(tmp_path, result.manifest_hash).prior_attempts[0]
    assert not attempt.http_exchange_completed
    assert b"secret-test" not in base64.b64decode(attempt.capture["body_base64"])


def test_growing_history_is_checked_before_next_reservation():
    base = manifest(
        steps=[
            {"kind": "user", "content": "first"},
            {"kind": "user", "content": "second"},
        ],
        max_requests=2,
        oracle={"kind": "exact_text", "value": "amber"},
    )
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(200, json=response("x" * 1000))

    result = run(limited(base, request=300), handler)
    assert len(calls) == 1
    assert result.budget_usage == {"candidate": 1}
    assert result.case_results[0].reason == "REQUEST_BYTE_LIMIT"
    assert result.exit_code == 2


def test_plan_summary_is_derived_and_counts_authored_utf8_bytes():
    m = limited(
        manifest(steps=[{"kind": "user", "content": "雪abc"}]), request=500, capture=900
    )
    before = m.manifest_hash
    summary = plan_resource_summary(m)
    assert summary["cases"][0]["authored_input_bytes"] == 6
    assert summary["aggregate_request_byte_ceiling"] == 500
    assert summary["aggregate_capture_byte_ceiling"] == 900
    assert m.manifest_hash == before


@pytest.mark.parametrize("cut", [0, 1, 50])
def test_sse_capture_bound_never_completes_a_partial_delivery(cut):
    from test_compatibility_execution import stream_response

    data = stream_response("chat", response()).content
    cap = len(data) - cut
    m = manifest()
    c = m.cases[0].model_copy(update={"stream": True})
    m = rehash_manifest(m.model_copy(update={"cases": [c]}))
    body = Chunks([data[:17], data[17:]])
    result = run(
        limited(m, capture=cap),
        lambda req: httpx.Response(
            200, stream=body, headers={"content-type": "text/event-stream"}
        ),
    )
    assert body.closed
    if cut:
        assert result.exit_code == 2
        assert result.case_results[0].reason == "RESPONSE_BYTE_LIMIT"
        assert result.attempt_metrics[0].http_exchange_completed is False
    else:
        assert result.exit_code == 0
        assert result.attempt_metrics[0].http_exchange_completed is True
