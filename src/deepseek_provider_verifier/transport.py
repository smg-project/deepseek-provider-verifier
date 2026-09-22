"""Raw HTTPX capture, one client per endpoint/run and no SDK normalization.

Use endpoint_client(timeout=...) to disable redirects and transport retries.
send_request makes exactly one send; caller owns concurrency, attempt budgets,
and the enclosing case deadline. Supplied custom clients/transports must not retry.
"""

from __future__ import annotations

import math
import time
from typing import Any

import httpx

from .capture import AttemptPayload, SSEEvent, TimestampedChunk, redact
from .json_utils import strict_json_loads
from .records import Endpoint
from .sse import SSEDecoder


def endpoint_client(
    *, timeout: httpx.Timeout, transport: httpx.AsyncBaseTransport | None = None
) -> httpx.AsyncClient:
    """Create the reusable run client; explicit finite timeout settings are required."""
    if any(
        value is None or not math.isfinite(value) or value <= 0
        for value in timeout.as_dict().values()
    ):
        raise ValueError("all HTTP timeout phases must be finite and positive")
    return httpx.AsyncClient(
        timeout=timeout,
        transport=transport or httpx.AsyncHTTPTransport(retries=0),
        follow_redirects=False,
        trust_env=False,
    )


def _meaningful(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    kind = value.get("type")
    if kind in (
        "response.output_text.delta",
        "response.reasoning_text.delta",
        "response.reasoning_summary_text.delta",
        "response.function_call_arguments.delta",
        "response.custom_tool_call_input.delta",
    ):
        return isinstance(value.get("delta"), str) and bool(value["delta"])
    if kind == "response.output_item.added":
        item = value.get("item")
        return isinstance(item, dict) and item.get("type") in (
            "function_call",
            "custom_tool_call",
        )
    choices = value.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            delta = choice.get("delta") if isinstance(choice, dict) else None
            if isinstance(delta, dict) and (
                delta.get("content")
                or delta.get("reasoning_content")
                or delta.get("tool_calls")
            ):
                return True
    return False


def _record_events(
    result: AttemptPayload, events: list[SSEEvent], elapsed: float
) -> None:
    for event in events:
        if not event.complete:
            continue
        if result.timings["first_event_seconds"] is None:
            result.timings["first_event_seconds"] = elapsed
        try:
            value = strict_json_loads(event.data or "")
        except (ValueError, RecursionError):
            continue
        if (
            _meaningful(value)
            and result.timings["first_meaningful_output_seconds"] is None
        ):
            result.timings["first_meaningful_output_seconds"] = elapsed


async def send_request(
    client: httpx.AsyncClient,
    endpoint: Endpoint,
    path: str,
    payload: dict,
    secret: str | None,
) -> AttemptPayload:
    """Capture one POST. Path is relative to API root; /v1 is never discarded.

    Times are seconds from send start: first_event means first complete dispatched
    SSE data frame (including malformed JSON); meaningful output means a nonempty
    text/reasoning/tool delta or Responses tool-item announcement. Heartbeats and
    role-only deltas do not count. Nonstream timing fields remain None.
    """
    if path.strip("/") not in ("chat/completions", "responses"):
        raise ValueError("path must be chat/completions or responses")
    if not endpoint.auth_none and not secret:
        raise ValueError("authenticated endpoint requires a secret")
    url = str(endpoint.base_url).rstrip("/") + "/" + path.lstrip("/")
    result = AttemptPayload()
    result._secret = secret
    result.timings = {
        "first_byte_seconds": None,
        "first_event_seconds": None,
        "first_meaningful_output_seconds": None,
        "headers_seconds": None,
        "total_seconds": None,
    }
    start = time.perf_counter()
    headers = {"accept-encoding": "identity"}
    if secret and not endpoint.auth_none:
        headers["authorization"] = "Bearer " + secret
    decoder = SSEDecoder()
    try:
        async with client.stream(
            "POST", url, json=payload, headers=headers, follow_redirects=False
        ) as response:
            result.status_code = response.status_code
            result.headers = redact(dict(response.headers), secret)
            result.timings["headers_seconds"] = time.perf_counter() - start
            encoded = (
                response.headers.get("content-encoding", "identity").lower()
                != "identity"
            )
            if encoded:
                result.error = {
                    "type": "UNSUPPORTED_CONTENT_ENCODING",
                    "message": "Expected identity encoding; raw entity bytes retained",
                }
            streaming = (
                not encoded
                and "text/event-stream"
                in response.headers.get("content-type", "").lower()
            )
            offset = 0
            # Preloaded mock/cached responses have already consumed their stream.
            iterator = (
                response.aiter_bytes()
                if response.is_stream_consumed
                else response.aiter_raw()
            )
            async for chunk in iterator:
                elapsed = time.perf_counter() - start
                if not chunk:
                    continue
                result.raw_chunks.append(chunk)
                result.chunks.append(
                    TimestampedChunk(
                        start_byte=offset,
                        end_byte=offset + len(chunk),
                        elapsed_seconds=elapsed,
                    )
                )
                offset += len(chunk)
                if result.timings["first_byte_seconds"] is None:
                    result.timings["first_byte_seconds"] = elapsed
                if streaming:
                    _record_events(result, decoder.feed(chunk), elapsed)
            if streaming and result.chunks:
                _record_events(
                    result, decoder.finish(), result.chunks[-1].elapsed_seconds
                )
            if not streaming and not encoded and result.raw_body:
                try:
                    result.raw_json = strict_json_loads(result.raw_body)
                    result.decoded_json = result.safe_evidence(result.raw_json)
                except RecursionError:
                    result.error = {
                        "type": "JSON_DEPTH_LIMIT",
                        "message": "JSON decoding depth exceeded; raw bytes retained in memory",
                    }
                except (ValueError, UnicodeDecodeError) as error:
                    result.error = {
                        "type": "INVALID_JSON",
                        "message": result.safe_evidence(str(error)),
                    }
    except httpx.HTTPError as error:
        result.error = {
            "type": type(error).__name__,
            "message": result.safe_evidence(str(error)),
        }
    result.timings["total_seconds"] = time.perf_counter() - start
    result.finalize_body()
    return result
