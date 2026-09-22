"""Strict SSE framing with byte provenance and no synthesized EOF terminator."""

from __future__ import annotations

import codecs
import re
from collections.abc import Iterable

from .capture import SSEEvent

_EOL = re.compile(b"\r\n|\r(?!$)|\n")


class SSEDecoder:
    """Incremental decoder shared by live capture and offline replay."""

    def __init__(self) -> None:
        self._pending = b""
        self._frame = b""
        self._lines: list[bytes] = []
        self._offset = 0
        self.events: list[SSEEvent] = []
        self._closed = False

    def _emit(self, complete: bool) -> None:
        raw = self._frame
        errors = [] if complete else ["INCOMPLETE_SSE_FRAME"]
        decoder = codecs.getincrementaldecoder("utf-8")("strict")
        fields = []
        try:
            for line in self._lines:
                fields.append(decoder.decode(line + b"\n"))
            decoder.decode(b"", final=True)
        except UnicodeDecodeError:
            errors.append("INVALID_UTF8")
        data: list[str] = []
        event, event_id = "message", None
        if not errors or errors == ["INCOMPLETE_SSE_FRAME"]:
            for number, field in enumerate(fields):
                if self._offset == 0 and number == 0:
                    field = field.removeprefix("\ufeff")
                field = field[:-1]
                if field.startswith(":"):
                    continue
                key, sep, value = field.partition(":")
                if sep and value.startswith(" "):
                    value = value[1:]
                if key == "data":
                    data.append(value)
                elif key == "event":
                    event = value or "message"
                elif key == "id" and "\x00" not in value:
                    event_id = value
        if data or errors:
            self.events.append(
                SSEEvent(
                    event=event,
                    data="\n".join(data) if "INVALID_UTF8" not in errors else None,
                    id=event_id,
                    index=len(self.events),
                    start_byte=self._offset,
                    end_byte=self._offset + len(raw),
                    complete=complete,
                    errors=errors,
                    raw_bytes=raw,
                )
            )
        self._offset += len(raw)
        self._frame, self._lines = b"", []

    def feed(self, chunk: bytes) -> list[SSEEvent]:
        if self._closed:
            raise ValueError("SSE decoder is finalized")
        start = len(self.events)
        self._pending += chunk
        while match := _EOL.search(self._pending):
            line = self._pending[: match.start()]
            self._frame += self._pending[: match.end()]
            self._pending = self._pending[match.end() :]
            if line:
                self._lines.append(line)
            else:
                self._emit(True)
        return self.events[start:]

    def finish(self) -> list[SSEEvent]:
        if self._closed:
            return []
        start = len(self.events)
        # A final CR is an SSE line ending, but only a blank line dispatches.
        if self._pending.endswith(b"\r"):
            line = self._pending[:-1]
            self._frame += self._pending
            self._pending = b""
            if line:
                self._lines.append(line)
            else:
                self._emit(True)
        if self._pending:
            self._lines.append(self._pending)
            self._frame += self._pending
        if self._frame:
            self._emit(False)
        self._closed = True
        return self.events[start:]


def decode_sse(chunks: Iterable[bytes]) -> list[SSEEvent]:
    """Decode finite byte chunks, retaining invalid/incomplete frames as evidence."""
    decoder = SSEDecoder()
    for chunk in chunks:
        decoder.feed(chunk)
    decoder.finish()
    return decoder.events
