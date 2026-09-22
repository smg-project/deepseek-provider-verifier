"""Responses assembly keyed by item ID plus output index, never by call_id.

Done values are snapshots, not deltas. Full raw objects remain inspectable in
memory; source references identify every recorded structural violation.
"""

from typing import Any

from ..capture import Observation, Segment, SourcePosition, SSEEvent, ToolCall
from .common import arguments_done, is_index, object_event, terminal_missing, usage

_TERMINALS = {
    "response.completed": "completed",
    "response.incomplete": "incomplete",
    "response.failed": "failed",
}
_TEXT_KINDS = {
    "output_text": "text_segments",
    "reasoning_text": "reasoning_segments",
    "reasoning_summary_text": "reasoning_segments",
}


class _Assembly:
    def __init__(self, observed: Observation):
        self.o = observed
        self.items: dict[str, dict] = {}
        self.indices: dict[int, str] = {}
        self.parts: dict[tuple[str, int, str], str] = {}
        self.done: set[tuple] = set()

    def mark_done(self, key: tuple, source: SourcePosition) -> None:
        if key in self.done:
            self.o.flag("DUPLICATE_FINALIZATION", source)
        self.done.add(key)

    def add_item(self, item: Any, index: Any, source: SourcePosition) -> str | None:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("id"), str)
            or not item.get("id")
        ):
            self.o.flag("MISSING_ITEM_ID", source)
            return None
        if not is_index(index):
            self.o.flag("MISSING_OUTPUT_INDEX", source)
            return None
        identity = item["id"]
        if identity in self.items:
            self.o.flag("DUPLICATE_ITEM_ID", source)
            return None
        if index in self.indices:
            self.o.flag("DUPLICATE_OUTPUT_INDEX", source)
            return None
        self.items[identity], self.indices[index] = item, identity
        if item.get("type") in ("function_call", "custom_tool_call"):
            call_id = item.get("call_id")
            if not isinstance(call_id, str) or not call_id:
                self.o.flag("MISSING_TOOL_ID", source)
                call_id = None
            elif any(t.call_id == call_id for t in self.o.tools.values()):
                self.o.flag("DUPLICATE_TOOL_ID", source)
            name = item.get("name")
            if not isinstance(name, str) or not name:
                self.o.flag("MISSING_TOOL_NAME", source)
                name = None
            self.o.tools[identity] = ToolCall(
                identity=identity,
                index=index,
                item_id=identity,
                call_id=call_id,
                name=name,
                kind="custom" if item["type"] == "custom_tool_call" else "function",
                sources=[source],
            )
        return identity

    def identity(self, data: dict, source: SourcePosition) -> str | None:
        identity, index = data.get("item_id"), data.get("output_index")
        if not isinstance(identity, str) or not identity:
            self.o.flag("MISSING_ITEM_ID", source)
            return None
        if identity not in self.items:
            self.o.flag("UNKNOWN_ITEM_ID", source)
            return None
        if not is_index(index):
            self.o.flag("MISSING_OUTPUT_INDEX", source)
            return None
        if self.indices.get(index) != identity:
            self.o.flag("ITEM_INDEX_MISMATCH", source)
            return None
        return identity

    def snapshot(self, identity: str, item: Any, source: SourcePosition) -> None:
        if not isinstance(item, dict):
            self.o.flag("INVALID_ITEM", source)
            return
        added = self.items[identity]
        for field in ("id", "type", "call_id", "name"):
            if field in added and item.get(field) != added[field]:
                self.o.flag("SNAPSHOT_MISMATCH", source)
        if identity in self.o.tools:
            tool = self.o.tools[identity]
            if (
                item.get("input" if tool.kind == "custom" else "arguments")
                != tool.arguments
            ):
                self.o.flag("SNAPSHOT_MISMATCH", source)
        content = item.get("content", [])
        if not isinstance(content, list):
            self.o.flag("INVALID_CONTENT", source)
            return
        expected = {
            (ci, kind): text
            for (iid, ci, kind), text in self.parts.items()
            if iid == identity
        }
        actual = {}
        for index, part in enumerate(content):
            if not isinstance(part, dict) or not isinstance(part.get("type"), str):
                self.o.flag("INVALID_CONTENT_PART", source)
                continue
            if part.get("type") in _TEXT_KINDS:
                actual[index, part["type"]] = part.get("text")
        if expected != actual:
            self.o.flag("SNAPSHOT_MISMATCH", source)

    def event(self, kind: str, data: dict, source: SourcePosition) -> None:
        if kind == "response.output_item.added":
            self.add_item(data.get("item"), data.get("output_index"), source)
            return
        if kind == "response.output_item.done":
            item = data.get("item")
            identity = self.identity(
                {
                    "item_id": item.get("id") if isinstance(item, dict) else None,
                    "output_index": data.get("output_index"),
                },
                source,
            )
            if identity:
                self.mark_done(("item", identity), source)
                self.snapshot(identity, item, source)
            return
        if kind in ("response.created", "response.in_progress") or kind in _TERMINALS:
            return
        known = kind.startswith(
            (
                "response.output_text.",
                "response.reasoning_text.",
                "response.reasoning_summary_text.",
                "response.function_call_arguments.",
                "response.custom_tool_call_input.",
                "response.content_part.",
            )
        )
        if not known:
            # Additive unknown semantic events remain in raw_objects for assertions.
            return
        identity = self.identity(data, source)
        if identity is None:
            return
        if ("item", identity) in self.done:
            self.o.flag("DATA_AFTER_ITEM_DONE", source)
        if kind.startswith(
            ("response.function_call_arguments.", "response.custom_tool_call_input.")
        ):
            tool = self.o.tools.get(identity)
            if tool is None:
                self.o.flag("WRONG_ITEM_TYPE", source)
                return
            if tool.complete:
                self.o.flag("DATA_AFTER_ARGUMENTS_DONE", source)
            tool.sources.append(source)
            if kind.endswith(".delta"):
                delta = data.get("delta")
                if isinstance(delta, str):
                    tool.arguments += delta
                else:
                    self.o.flag("INVALID_TOOL_ARGUMENTS", source)
            elif kind.endswith(".done"):
                self.mark_done(("arguments", identity), source)
                full = data.get("input" if tool.kind == "custom" else "arguments")
                if full != tool.arguments:
                    self.o.flag("SNAPSHOT_MISMATCH", source)
                arguments_done(tool, self.o, source)
            return
        index = data.get("content_index")
        if not is_index(index):
            self.o.flag("MISSING_CONTENT_INDEX", source)
            return
        if any(done[:3] == ("part", identity, index) for done in self.done):
            self.o.flag("DATA_AFTER_PART_DONE", source)
        if kind.startswith("response.content_part."):
            part = data.get("part")
            if not isinstance(part, dict):
                self.o.flag("INVALID_CONTENT_PART", source)
                return
            part_kind = part.get("type")
            if not isinstance(part_kind, str):
                self.o.flag("INVALID_CONTENT_PART", source)
                return
            if part_kind not in _TEXT_KINDS:
                return
            key = (identity, index, part_kind)
            if kind.endswith(".added"):
                if key in self.parts:
                    self.o.flag("DUPLICATE_CONTENT_PART", source)
                else:
                    self.parts[key] = ""
                if part.get("text", "") != "":
                    self.o.flag("NONEMPTY_INITIAL_CONTENT", source)
            elif kind.endswith(".done"):
                self.mark_done(("part", *key), source)
                if key not in self.parts or part.get("text") != self.parts.get(key):
                    self.o.flag("SNAPSHOT_MISMATCH", source)
            return
        part_kind = kind.split(".")[1]
        key = (identity, index, part_kind)
        if key not in self.parts:
            self.o.flag("MISSING_CONTENT_PART", source)
            self.parts[key] = ""
        if ("text", *key) in self.done:
            self.o.flag("DATA_AFTER_TEXT_DONE", source)
        if kind.endswith(".delta"):
            text = data.get("delta")
            if not isinstance(text, str):
                self.o.flag("INVALID_TEXT_DELTA", source)
                return
            self.parts[key] += text
            getattr(self.o, _TEXT_KINDS[part_kind]).append(
                Segment(text=text, identity=f"{identity}:{index}", source=source)
            )
        elif kind.endswith(".done"):
            self.mark_done(("text", *key), source)
            if data.get("text") != self.parts[key]:
                self.o.flag("SNAPSHOT_MISMATCH", source)

    def terminal_snapshot(
        self, response: Any, state: str, source: SourcePosition
    ) -> None:
        self.o.raw_response = response
        if not (
            isinstance(response, dict)
            and isinstance(response.get("id"), str)
            and bool(response["id"])
            and response.get("object") == "response"
        ):
            self.o.flag("RESPONSE_ENVELOPE", source)
        if not isinstance(response, dict):
            self.o.flag("INVALID_RESPONSE", source)
            return
        usage(self.o, response)
        if response.get("status") != state:
            self.o.flag("TERMINAL_STATUS_MISMATCH", source)
        output = response.get("output")
        if not isinstance(output, list):
            self.o.flag("INVALID_OUTPUT", source)
            return
        ids = set()
        for index, item in enumerate(output):
            identity = item.get("id") if isinstance(item, dict) else None
            if not isinstance(identity, str):
                self.o.flag("MISSING_ITEM_ID", source)
                continue
            if identity in ids:
                self.o.flag("DUPLICATE_ITEM_ID", source)
            ids.add(identity)
            if self.indices.get(index) != identity:
                self.o.flag("SNAPSHOT_MISMATCH", source)
            elif identity in self.items:
                self.snapshot(identity, item, source)
        if ids != set(self.items):
            self.o.flag("SNAPSHOT_MISMATCH", source)


def assemble_responses(events: list[SSEEvent]) -> Observation:
    """Assemble semantic events; failed/incomplete terminals keep their exact state."""
    observed = Observation(protocol="responses", raw_events=events)
    assembly = _Assembly(observed)
    last_sequence, started, response_id = -1, False, None
    terminal = False
    for event in events:
        source = event.source()
        data = object_event(event, observed)
        if data is None:
            continue
        kind = data.get("type")
        if not isinstance(kind, str):
            observed.flag("MISSING_EVENT_TYPE", source)
            continue
        if event.event != "message" and event.event != kind:
            observed.flag("EVENT_TYPE_MISMATCH", source)
        sequence = data.get("sequence_number")
        if not is_index(sequence):
            observed.flag("MISSING_SEQUENCE_NUMBER", source)
        elif sequence <= last_sequence:
            observed.flag("NON_MONOTONIC_SEQUENCE", source)
        else:
            last_sequence = sequence
        if not started:
            if kind != "response.created":
                observed.flag("MISSING_RESPONSE_CREATED", source)
            started = True
        elif kind == "response.created":
            observed.flag("DUPLICATE_RESPONSE_CREATED", source)
        response = data.get("response")
        if kind in ("response.created", "response.in_progress") and not isinstance(
            response, dict
        ):
            observed.flag("INVALID_RESPONSE", source)
        if isinstance(response, dict):
            identity = response.get("id")
            if not isinstance(identity, str) or not identity:
                observed.flag("MISSING_RESPONSE_ID", source)
            elif response_id is not None and response_id != identity:
                observed.flag("RESPONSE_ID_MISMATCH", source)
            else:
                response_id = identity
        if terminal:
            observed.flag(
                "DUPLICATE_TERMINAL" if kind in _TERMINALS else "DATA_AFTER_TERMINAL",
                source,
            )
            # Preserve the original terminal; a later completed event cannot repair failure.
            continue
        if kind in _TERMINALS:
            terminal = True
            observed.terminal_state = _TERMINALS[kind]
            assembly.terminal_snapshot(response, observed.terminal_state, source)
        else:
            assembly.event(kind, data, source)
    source = events[-1].source() if events else SourcePosition()
    for identity, tool in observed.tools.items():
        if not tool.complete:
            observed.flag("INCOMPLETE_TOOL_ARGUMENTS", tool.sources[-1])
    if observed.terminal_state == "completed":
        for identity in assembly.items:
            if ("item", identity) not in assembly.done:
                observed.flag("MISSING_ITEM_DONE", source)
        for key in assembly.parts:
            if ("text", *key) not in assembly.done or (
                "part",
                *key,
            ) not in assembly.done:
                observed.flag("MISSING_CONTENT_DONE", source)
    terminal_missing(observed)
    return observed


def assemble_responses_json(value: Any) -> Observation:
    """Assemble a nonstream response with identical item identities and raw replay data."""
    observed = Observation(protocol="responses", raw_response=value)
    source = SourcePosition(json_path="$")
    if not isinstance(value, dict):
        observed.flag("NON_OBJECT_JSON", source)
        terminal_missing(observed)
        return observed
    status = value.get("status")
    if status in ("completed", "incomplete", "failed"):
        observed.terminal_state = status
    elif status is not None:
        observed.flag("NONTERMINAL_RESPONSE_STATUS", source)
    usage(observed, value)
    output = value.get("output")
    if not isinstance(output, list):
        observed.flag("INVALID_OUTPUT", source)
        terminal_missing(observed)
        return observed
    assembly = _Assembly(observed)
    for index, item in enumerate(output):
        source = SourcePosition(json_path=f"$.output[{index}]")
        identity = assembly.add_item(item, index, source)
        if identity is None:
            continue
        if identity in observed.tools:
            tool = observed.tools[identity]
            args = item.get("input" if tool.kind == "custom" else "arguments")
            if isinstance(args, str):
                tool.arguments = args
                arguments_done(tool, observed, source)
            else:
                observed.flag("INVALID_TOOL_ARGUMENTS", source)
        content = item.get("content", [])
        if not isinstance(content, list):
            observed.flag("INVALID_CONTENT", source)
            continue
        for ci, part in enumerate(content):
            if not isinstance(part, dict):
                observed.flag("INVALID_CONTENT_PART", source)
                continue
            kind, text = part.get("type"), part.get("text")
            if not isinstance(kind, str):
                observed.flag("INVALID_CONTENT_PART", source)
                continue
            if kind in _TEXT_KINDS:
                if isinstance(text, str):
                    getattr(observed, _TEXT_KINDS[kind]).append(
                        Segment(text=text, identity=f"{identity}:{ci}", source=source)
                    )
                else:
                    observed.flag("INVALID_TEXT_DELTA", source)
    terminal_missing(observed)
    return observed
