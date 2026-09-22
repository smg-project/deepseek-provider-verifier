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


def redact(value: Any, secret: str | None = None) -> Any:
    """Redact credential-shaped keys and exact supplied-secret echoes recursively."""
    if isinstance(value, str):
        return value.replace(secret, "[REDACTED]") if secret else value
    if isinstance(value, dict):
        return {
            redact(str(k), secret): "[REDACTED]"
            if _SENSITIVE.search(str(k))
            else redact(v, secret)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(v, secret) for v in value]
    return value


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

        def collect(value: Any, sensitive: bool = False) -> None:
            if isinstance(value, dict):
                for key, item in value.items():
                    collect(item, sensitive or bool(_SENSITIVE.search(str(key))))
            elif isinstance(value, list):
                for item in value:
                    collect(item, sensitive)
            elif sensitive and isinstance(value, str) and value:
                secrets.add(value)

        collect(self.raw_json)
        if "text/event-stream" in self.headers.get("content-type", "").lower():
            for event in decode_sse(self.raw_chunks):
                if event.data:
                    try:
                        collect(json.loads(event.data))
                    except ValueError:
                        pass
        safe = self.raw_body
        encodings = set()
        for secret in secrets:
            encodings.add(secret.encode())
            encodings.add(json.dumps(secret, ensure_ascii=True)[1:-1].encode())
            encodings.add(json.dumps(secret, ensure_ascii=False)[1:-1].encode())
        for encoded in sorted(encodings, key=len, reverse=True):
            safe = safe.replace(encoded, b"[REDACTED]")
        self.body_base64 = base64.b64encode(safe).decode("ascii")
