"""Typed in-memory observations and explicit credential-safe persistence boundary.

Raw fields are excluded from serialization. Persist through AttemptPayload.safe_evidence
when combining observations with an attempt: standalone assemblers do not know secrets.
"""

from __future__ import annotations

import base64
import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from .json_utils import strict_json_loads


class CaptureRecord(BaseModel):
    schema_version: Literal[1] = 1

    model_config = ConfigDict(extra="forbid", frozen=False, hide_input_in_errors=True)


class SourcePosition(CaptureRecord):
    event_index: int | None = None
    start_byte: int | None = None
    end_byte: int | None = None
    json_path: str | None = None


class SSEEvent(CaptureRecord):
    """A dispatched frame, or a retained faulty frame; byte end is exclusive."""

    event: str = "message"
    data: str | None = None
    id: str | None = None
    index: int
    start_byte: int
    end_byte: int
    complete: bool = True
    errors: list[str] = Field(default_factory=list)
    raw_bytes: bytes = Field(default=b"", exclude=True, repr=False)

    def source(self) -> SourcePosition:
        return SourcePosition(
            event_index=self.index, start_byte=self.start_byte, end_byte=self.end_byte
        )


class Segment(CaptureRecord):
    text: str
    identity: str
    source: SourcePosition


class ToolCall(CaptureRecord):
    identity: str
    index: int
    item_id: str | None = None
    call_id: str | None = None
    name: str | None = None
    kind: str = "function"
    arguments: str = ""
    parsed_arguments: Any = None
    complete: bool = False
    sources: list[SourcePosition] = Field(default_factory=list)


class Observation(CaptureRecord):
    protocol: Literal["chat", "responses"]
    text_segments: list[Segment] = Field(default_factory=list)
    reasoning_segments: list[Segment] = Field(default_factory=list)
    tools: dict[str, ToolCall] = Field(default_factory=dict)
    usage: Any = None
    usage_records: list[Any] = Field(default_factory=list)
    terminal_state: Literal[
        "missing", "completed", "incomplete", "failed", "unknown"
    ] = "missing"
    choice_indices: list[str] = Field(default_factory=list)
    finish_reasons: dict[str, str] = Field(default_factory=dict)
    violations: list[str] = Field(default_factory=list)
    evidence: dict[str, list[SourcePosition]] = Field(default_factory=dict)
    raw_events: list[SSEEvent] = Field(default_factory=list, exclude=True, repr=False)
    raw_response: Any = Field(default=None, exclude=True, repr=False)
    raw_objects: list[Any] = Field(default_factory=list, exclude=True, repr=False)
    assistant_messages: list[dict[str, Any]] = Field(
        default_factory=list, exclude=True, repr=False
    )

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.text_segments)

    @property
    def reasoning(self) -> str:
        return "".join(s.text for s in self.reasoning_segments)

    def flag(self, code: str, source: SourcePosition) -> None:
        if code not in self.violations:
            self.violations.append(code)
        self.evidence.setdefault(code, []).append(source)


class TimestampedChunk(CaptureRecord):
    start_byte: int
    end_byte: int
    elapsed_seconds: float


_SENSITIVE = re.compile(
    r"authorization|proxy.authorization|cookie|api[\W_]*key|access[\W_]*token|secret|password",
    re.IGNORECASE,
)


_MAX_REDACTION_DEPTH = 32
_REDACTION_OMITTED = "[OMITTED: redaction depth limit]"


class _RedactionDepthExceeded(ValueError):
    pass


def _bounded_json(value: str, remaining_depth: int) -> Any:
    """Inspect decoded depth iteratively before recursive redaction/comparison."""
    try:
        decoded = strict_json_loads(value)
    except RecursionError as error:
        raise _RedactionDepthExceeded from error
    pending = [(decoded, 0)]
    while pending:
        item, depth = pending.pop()
        if depth >= remaining_depth:
            raise _RedactionDepthExceeded
        if isinstance(item, dict):
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
    return decoded


def _redact(value: Any, secret: str | None, depth: int) -> Any:
    if depth >= _MAX_REDACTION_DEPTH:
        return _REDACTION_OMITTED
    if isinstance(value, str):
        try:
            decoded = _bounded_json(value, _MAX_REDACTION_DEPTH - depth)
        except _RedactionDepthExceeded:
            return _REDACTION_OMITTED
        except ValueError:
            decoded = None
        if isinstance(decoded, (dict, list, str)):
            safe = _redact(decoded, secret, depth + 1)
            if safe != decoded:
                return json.dumps(safe, ensure_ascii=True)
        if secret:
            for encoded in sorted(
                {
                    secret,
                    json.dumps(secret)[1:-1],
                    json.dumps(secret, ensure_ascii=False)[1:-1],
                },
                key=len,
                reverse=True,
            ):
                value = value.replace(encoded, "[REDACTED]")
        return value
    if isinstance(value, dict):
        return {
            _redact(str(k), secret, depth + 1): "[REDACTED]"
            if _SENSITIVE.search(str(k))
            else _redact(v, secret, depth + 1)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(v, secret, depth + 1) for v in value]
    return value


def redact(value: Any, secret: str | None = None) -> Any:
    """Redact structured and embedded JSON with a bounded, safe omission fallback.

    Raw inputs are never mutated. Uninspectable strings/subtrees are replaced by
    an explicit omission marker, never passed through with possibly encoded
    credentials. Invalid shallow JSON stays invalid after known-secret removal.
    """
    return _redact(value, secret, 0)


class AttemptPayload(CaptureRecord):
    """One HTTP attempt, never retried. Raw replay data is memory-only.

    `chunks` offsets reference raw_body, not the sanitized body_base64.
    `safe_evidence` is the persistence boundary for any additional observation data.
    """

    status_code: int | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    chunks: list[TimestampedChunk] = Field(default_factory=list)
    body_base64: str = ""
    body_omission_reason: str | None = None
    decoded_json: Any = None
    error: dict[str, Any] | None = None
    timings: dict[str, float | None] = Field(default_factory=dict)
    raw_chunks: list[bytes] = Field(default_factory=list, exclude=True, repr=False)
    raw_json: Any = Field(default=None, exclude=True, repr=False)
    _secret: str | None = PrivateAttr(default=None)

    @property
    def raw_body(self) -> bytes:
        return b"".join(self.raw_chunks)

    def safe_evidence(self, value: Any) -> Any:
        """Sanitize a JSON-compatible value before handing it to persisted records."""
        return redact(value, self._secret)

    def finalize_body(self) -> None:
        # Preserve framing (including malformed frames) while removing known
        # secrets and credential-shaped JSON values in their wire encodings.
        from .sse import decode_sse

        if self.headers.get("content-encoding", "identity").lower() != "identity":
            self.body_omission_reason = "Encoded body cannot be safely redacted; raw bytes retained in memory only"
            return
        secrets = {self._secret} if self._secret else set()

        def collect(value: Any, sensitive: bool = False, depth: int = 0) -> None:
            if depth >= _MAX_REDACTION_DEPTH:
                raise _RedactionDepthExceeded
            if isinstance(value, dict):
                for key, item in value.items():
                    collect(
                        item, sensitive or bool(_SENSITIVE.search(str(key))), depth + 1
                    )
            elif isinstance(value, list):
                for item in value:
                    collect(item, sensitive, depth + 1)
            elif isinstance(value, str) and value:
                if sensitive:
                    secrets.add(value)
                else:
                    try:
                        decoded = _bounded_json(value, _MAX_REDACTION_DEPTH - depth)
                    except _RedactionDepthExceeded:
                        raise
                    except ValueError:
                        return
                    if isinstance(decoded, (dict, list, str)):
                        collect(decoded, depth=depth + 1)

        try:
            if self.error and self.error.get("type") == "JSON_DEPTH_LIMIT":
                raise _RedactionDepthExceeded
            collect(self.raw_json)
            if "text/event-stream" in self.headers.get("content-type", "").lower():
                for event in decode_sse(self.raw_chunks):
                    if event.data:
                        collect(event.data)
        except _RedactionDepthExceeded:
            self.body_base64 = ""
            self.body_omission_reason = (
                "Redaction depth limit exceeded; raw bytes retained in memory only"
            )
            return
        safe = self.raw_body
        encodings = set()
        for secret in secrets:
            # Escaped lone surrogates are legal input to the JSON decoder but
            # have no UTF-8 representation. Their ASCII JSON escape can still
            # be removed from the original wire body without losing the capture.
            encodings.add(json.dumps(secret, ensure_ascii=True)[1:-1].encode())
            for candidate in (secret, json.dumps(secret, ensure_ascii=False)[1:-1]):
                try:
                    encodings.add(candidate.encode())
                except UnicodeEncodeError:
                    continue
        for encoded in sorted(encodings, key=len, reverse=True):
            safe = safe.replace(encoded, b"[REDACTED]")
        self.body_base64 = base64.b64encode(safe).decode("ascii")
