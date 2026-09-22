# Transport and protocol assembly interface

This layer captures one HTTP attempt and assembles structural evidence. It does not
score capability requirements, execute tools, schedule retries, or decide a case's
PASS/FAIL status. A completed terminal is not a passing assertion.

## Public functions

```python
from deepseek_provider_verifier.transport import endpoint_client, send_request
from deepseek_provider_verifier.sse import decode_sse, SSEDecoder
from deepseek_provider_verifier.protocols.chat import assemble_chat, assemble_chat_json
from deepseek_provider_verifier.protocols.responses import (
    assemble_responses, assemble_responses_json,
)
```

- `endpoint_client(*, timeout: httpx.Timeout, transport=None) -> httpx.AsyncClient`
  requires finite positive connect/read/write/pool limits. Create and close **one
  client per endpoint/run**, sharing it across attempts. The built-in transport
  has zero retries; redirects and environment proxy inheritance are disabled.
- `await send_request(client, endpoint: Endpoint, path: str, payload: dict,
  secret: str | None) -> AttemptPayload` makes one POST. `path` is
  `chat/completions` or `responses`, optionally with a leading slash. A configured
  `/v1` prefix is preserved. No model/default parameters are added to `payload`.
  Authentication is only the supplied secret in the Authorization header; an
  `auth_none` endpoint sends no such header with the provided client factory.
- `decode_sse(chunks: Iterable[bytes]) -> list[SSEEvent]` supports CR, LF, CRLF,
  comments, multiline data, an initial UTF-8 BOM, split delimiters, and split UTF-8.
  `SSEDecoder.feed(bytes)` and `.finish()` expose the same incremental decoder.
  Incomplete EOF frames and invalid UTF-8 are retained as error events, never
  repaired or dispatched as successful terminal markers.
- `assemble_chat(events: list[SSEEvent]) -> Observation` and
  `assemble_responses(events: list[SSEEvent]) -> Observation` preserve malformed
  JSON/frame and protocol lifecycle defects in `violations` and `evidence`.
- `assemble_chat_json(value: Any) -> Observation` and
  `assemble_responses_json(value: Any) -> Observation` accept decoded nonstream
  bodies, including erroneous shapes. Pass `attempt.raw_json` for assembly and
  replay. HTTP/body decode failures remain on the attempt and must not be dropped.

HTTP timeouts and exceptions are returned in `AttemptPayload.error` without
retries. HTTP error statuses remain available even when their bodies are JSON.
The runner must enforce attempt budgets, concurrency, and the whole-case deadline
around this operation; a read timeout is not a whole-request or whole-case timer.
Custom clients/transports must also have retries disabled.

Every request overrides `Accept-Encoding` to `identity`. A server returning an
unexpected encoding produces `UNSUPPORTED_CONTENT_ENCODING`: **transport ERROR**,
not a provider contract FAIL. Raw entity bytes remain available in memory, and
`body_omission_reason` explains why encoded bytes cannot be persisted safely.
Compression support is intentionally not inferred from SDK defaults. HTTP transfer
framing is handled by HTTPX; captured bytes are the response entity bytes.

## Evidence and persistence

Support models are in `deepseek_provider_verifier.capture`. They are Pydantic
models with `schema_version: 1`, separate from the planner's exported-record
registry. Their `.model_json_schema()` is available when needed.

`AttemptPayload` exposes `status_code`, redacted `headers`, `chunks` (timestamped
byte spans), `decoded_json`, `error`, `timings`, and sanitized `body_base64`.
`raw_chunks`, `raw_body`, and `raw_json` are memory-only replay inputs excluded
from ordinary serialization. `body_base64` preserves original framing and faulty
bytes after credential-value redaction; it is not an invented valid JSON/SSE body.
Header maps use HTTPX's lowercase, combined representation for duplicate names.

`SSEEvent` includes event name, joined data lines, optional ID, event index,
`complete`, error codes, and a half-open `[start_byte, end_byte)` raw-body span.
`raw_bytes` is memory-only. Indexes and byte spans include retained faulty frames;
comments do not create events. Do not infer server sequence numbers from indexes.

`Observation` contains:

- `protocol`, `text_segments`, `reasoning_segments`, `tools`, `usage`,
  `usage_records`, `terminal_state`, `choice_indices`, and `finish_reasons`.
- `.text` and `.reasoning` convenience properties concatenate segment text in
  arrival order. Each `Segment` also retains its identity and source; use those
  identities when comparing multiple choices/items rather than the convenience
  concatenation alone.
- `violations: list[str]` contains unique structural codes.
  `evidence: dict[str, list[SourcePosition]]` retains every recorded occurrence.
  Stream sources use event index/byte spans; nonstream sources use JSON paths.
- Memory-only `raw_events`, `raw_objects`, `raw_response`, `assistant_messages`
  retain unknown additive fields and replay evidence without exposing them in the
  default dump. Malformed JSON remains available through its SSE data/raw bytes.

Chat tools are keyed by `"<choice_index>:<tool_index>"`; Responses tools are keyed
by item ID, cross-checked against `output_index`. `ToolCall.call_id` is separately
retained for tool-result pairing, along with `name`, `arguments`, parsed arguments,
completion marker, and sources. Done snapshots never append to accumulated deltas.
`complete=True` means finalization was observed; consult violations before using
arguments. A terminal can be `missing`, `completed`, `incomplete`, `failed`, or
`unknown`. Failed/incomplete events are never upgraded by later terminal events.

Streaming Chat `assistant_messages` are assembled in `choice_indices` order and
include reasoning, tools sorted by tool index, and additive delta fields. Raw
nonstream assistant messages are retained verbatim. The runner should replay
reasoning history from these in-memory records, including assistant turns without
a tool call. Raw Responses output is available on `raw_response['output']`.

**Persist through the owning attempt's redactor**, since an isolated assembler
cannot know the API secret:

```python
safe_response = attempt.safe_evidence(observation.model_dump(mode='json'))
safe_events = attempt.safe_evidence([event.model_dump(mode='json') for event in events])
safe_capture = attempt.model_dump(mode='json')
```

`safe_evidence(value)` recursively removes the supplied secret and credential-shaped
keys. Raw body persistence also removes detected credential values and standard JSON
encodings of the supplied secret, including secret echoes split across chunks.
Never persist/repr raw replay fields or serialize a standalone observation/event
before applying `safe_evidence`. Redaction can change byte lengths: all byte spans
reference **raw in-memory bytes**, not positions in the sanitized body. Store the
provided spans and sanitized event text together rather than re-slicing persisted
bytes with raw offsets. Structural findings are recorded before any redaction.

## Timing definitions

All times are elapsed seconds from the start of `send_request`, measured with a
monotonic clock:

- `headers_seconds`: response headers available.
- `first_byte_seconds`: first nonempty entity chunk received.
- `first_event_seconds`: first complete dispatched SSE data frame, including a
  frame whose JSON is malformed; comment heartbeats do not count.
- `first_meaningful_output_seconds`: first nonempty Chat content/reasoning/tool
  delta, Responses text/reasoning/tool-argument/input delta, or Responses tool-item
  announcement. Role-only and response-created events do not count.
- `total_seconds`: capture/decode completed or a transport error was caught.

Stream times are stamped at receipt of the chunk completing the frame. Nonstream
first-event/meaningful-output times are `None`; text-in-body arrival time cannot be
inferred before the body has been decoded. Missing timings are not zero. The shared
persisted `Attempt.timings` record requires floats, so omit `None` entries when
constructing it; the full capture retains their explicit absence.

## Scope limits

Assembly validates the implemented framing/identity/lifecycle boundaries, not the
full provider schema or capability contracts. Unknown additive fields/events stay
in raw evidence for profile-specific assertions. User/case/schema assertions must
still validate required response fields, usage structure, advertised features,
actual tool behavior, and finish reasons against the selected rule set. No live
provider conformance or quality claim is made by the offline fixture suite.
