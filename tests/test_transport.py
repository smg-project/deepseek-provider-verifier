import asyncio
import base64
import json
from types import SimpleNamespace

import httpx
import pytest

from deepseek_provider_verifier.records import Endpoint
from deepseek_provider_verifier.transport import endpoint_client, send_request

ENDPOINT = Endpoint(
    name="mock",
    base_url="https://example.test/v1/",
    model="x",
    model_release="unknown",
    api_key_env="MOCK_KEY",
)


def run(handler, payload=None, secret="secret-value", path="/chat/completions"):
    async def execute():
        async with endpoint_client(
            timeout=httpx.Timeout(4), transport=httpx.MockTransport(handler)
        ) as client:
            return await send_request(
                client, ENDPOINT, path, payload or {"stream": False}, secret
            )

    return asyncio.run(execute())


@pytest.mark.parametrize("status", [200, 401, 429, 500])
def test_exact_path_no_retries_and_status_headers_preserved(status):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url == "https://example.test/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer secret-value"
        assert request.extensions["timeout"] == {
            "connect": 4,
            "read": 4,
            "write": 4,
            "pool": 4,
        }
        return httpx.Response(
            status,
            headers={"x-request-id": "trace-123", "retry-after": "5"},
            json={"vendor": {"extra": True}},
        )

    result = run(handler)
    assert len(calls) == 1
    assert result.status_code == status
    assert result.headers["x-request-id"] == "trace-123"
    assert result.headers["retry-after"] == "5"
    assert result.decoded_json == {"vendor": {"extra": True}}


class Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks, error=None):
        self.chunks, self.error = chunks, error

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk
        if self.error:
            raise self.error


def test_credentials_redacted_even_when_reflected_across_chunks():
    wire = b'{"text":"secret-value","api_key":"other-secret","extra":1}'
    result = run(
        lambda req: httpx.Response(
            200,
            headers={
                "content-type": "application/json",
                "set-cookie": "session=abc",
                "x-echo": "secret-value",
            },
            stream=Chunks([wire[:19], wire[19:]]),
        )
    )
    persisted = result.model_dump_json()
    assert "secret-value" not in persisted
    assert "other-secret" not in persisted
    assert "session=abc" not in persisted
    assert result.raw_body == wire
    assert result.raw_json["text"] == "secret-value"
    assert b"secret-value" not in base64.b64decode(result.body_base64)
    assert result.decoded_json["extra"] == 1
    assert "secret-value" not in json.dumps(
        result.safe_evidence({"nested": result.raw_json})
    )


def test_timeout_and_partial_stream_keep_evidence():
    result = run(
        lambda req: httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=Chunks(
                [b'data: {"choices":[]}\n\n', b"data: partial"],
                httpx.ReadTimeout("contains secret-value"),
            ),
        ),
        payload={"stream": True},
    )
    assert result.status_code == 200
    assert result.error["type"] == "ReadTimeout"
    assert result.raw_body.endswith(b"data: partial")
    assert len(result.chunks) == 2
    assert "secret-value" not in result.model_dump_json()
    assert result.timings["first_event_seconds"] is not None
    assert result.timings["first_meaningful_output_seconds"] is None


def test_pre_header_timeout_not_retried():
    calls = []

    def handler(req):
        calls.append(req)
        raise httpx.ConnectTimeout("no connection")

    result = run(handler)
    assert result.status_code is None
    assert result.error["type"] == "ConnectTimeout"
    assert len(calls) == 1


def test_timing_distinguishes_role_and_meaningful_delta(monkeypatch):
    ticks = iter([0.0, 1.0, 2.0, 3.0, 4.0, 5.0])
    monkeypatch.setattr(
        "deepseek_provider_verifier.transport.time",
        SimpleNamespace(perf_counter=lambda: next(ticks)),
    )
    result = run(
        lambda req: httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=Chunks(
                [
                    b": heartbeat\n\n",
                    b'data: {"choices":[{"index":0,"delta":{"role":"assistant"}}]}\n\n',
                    b'data: {"choices":[{"index":0,"delta":{"content":"Hi"}}]}\n\n',
                ]
            ),
        ),
        payload={"stream": True},
    )
    assert result.timings["first_event_seconds"] == 3.0
    assert result.timings["first_meaningful_output_seconds"] == 4.0


def test_malformed_nonstream_json_is_not_repaired():
    result = run(
        lambda req: httpx.Response(
            200, headers={"content-type": "application/json"}, content=b"{broken"
        )
    )
    assert result.error["type"] == "INVALID_JSON"
    assert result.raw_body == b"{broken"


def test_redirect_is_not_followed():
    result = run(
        lambda req: httpx.Response(307, headers={"location": "https://elsewhere.test"})
    )
    assert result.status_code == 307


def test_sse_persistence_redacts_nested_credential_fields():
    wire = b'data: {"extra":{"api_key":"never-persist"},"text":"secret-value"}\n\n'
    result = run(
        lambda req: httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=Chunks([wire])
        )
    )
    assert b"never-persist" not in base64.b64decode(result.body_base64)
    assert result.raw_body == wire


@pytest.mark.parametrize("seconds", [float("inf"), float("nan")])
def test_timeout_must_be_finite(seconds):
    with pytest.raises(ValueError):
        endpoint_client(timeout=httpx.Timeout(seconds))


def test_escaped_secret_is_redacted_from_sse_persistence():
    result = run(
        lambda req: httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=Chunks([b'data: {"text":"a\\"b"}\n\n']),
        ),
        secret='a"b',
    )
    assert b'a\\"b' not in base64.b64decode(result.body_base64)


def test_unexpected_compressed_body_is_preserved_without_silent_decoding():
    import gzip

    compressed = gzip.compress(b'{"value":1}', mtime=0)
    result = run(
        lambda req: httpx.Response(
            200,
            headers={"content-type": "application/json", "content-encoding": "gzip"},
            stream=Chunks([compressed]),
        )
    )
    assert result.raw_body == compressed
    assert result.error["type"] == "UNSUPPORTED_CONTENT_ENCODING"


def test_last_cr_complete_frame_counts_for_first_event():
    result = run(
        lambda req: httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=Chunks(
                [b'data: {"choices":[{"index":0,"delta":{"content":"Hi"}}]}\r\r']
            ),
        ),
        payload={"stream": True},
    )
    assert result.timings["first_event_seconds"] is not None
    assert result.timings["first_meaningful_output_seconds"] is not None


def test_nonstandard_json_numbers_are_not_normalized():
    result = run(lambda req: httpx.Response(200, content=b'{"x":NaN}'))
    assert result.error["type"] == "INVALID_JSON"


def test_explicit_identity_encoding_overrides_client_defaults():
    def handler(request):
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(200, json={"ok": True})

    result = run(handler)
    assert result.decoded_json == {"ok": True}


def test_safe_events_redacts_encoded_and_nested_credentials():
    from deepseek_provider_verifier.sse import decode_sse

    wire = b'data: {"text":"a\\"b","api_key":"other-secret","nested":"{\\"password\\":\\"nested-secret\\"}","extra":1}\n\n'
    attempt = run(
        lambda req: httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=Chunks([wire])
        ),
        secret='a"b',
    )
    events = decode_sse(attempt.raw_chunks)
    safe_events = attempt.safe_evidence(
        [event.model_dump(mode="json") for event in events]
    )
    decoded = json.loads(safe_events[0]["data"])
    assert decoded["text"] == "[REDACTED]"
    assert decoded["api_key"] == "[REDACTED]"
    assert json.loads(decoded["nested"])["password"] == "[REDACTED]"
    assert decoded["extra"] == 1
    assert attempt.raw_body == wire
    assert json.loads(events[0].data)["text"] == 'a"b'


@pytest.mark.parametrize("stream", [False, True])
def test_lone_surrogate_credential_keeps_capture_and_redacts_wire_escape(stream):
    body = b'{"api_key":"\\ud800","extra":1}'
    wire = b"data: " + body + b"\n\n" if stream else body
    attempt = run(
        lambda req: httpx.Response(
            200,
            headers={
                "content-type": "text/event-stream" if stream else "application/json"
            },
            stream=Chunks([wire]),
        )
    )
    assert attempt.status_code == 200
    assert attempt.raw_body == wire
    assert b"\\ud800" not in base64.b64decode(attempt.body_base64)
    assert "[REDACTED]" in base64.b64decode(attempt.body_base64).decode()
    assert attempt.error is None
    assert attempt.model_dump_json()
