"""Shared evidence handling without protocol normalization."""

from typing import Any

from ..capture import Observation, SourcePosition, SSEEvent, ToolCall
from ..json_utils import strict_json_loads


def object_event(event: SSEEvent, observed: Observation) -> dict[str, Any] | None:
    for code in event.errors:
        observed.flag(code, event.source())
    if event.errors or not event.complete:
        return None
    try:
        value = strict_json_loads(event.data or "")
    except (ValueError, TypeError):
        observed.flag("INVALID_JSON", event.source())
        return None
    observed.raw_objects.append(value)
    if not isinstance(value, dict):
        observed.flag("NON_OBJECT_JSON", event.source())
        return None
    return value


def arguments_done(
    tool: ToolCall, observed: Observation, source: SourcePosition
) -> None:
    tool.complete = True
    if tool.kind == "custom":
        return
    try:
        tool.parsed_arguments = strict_json_loads(tool.arguments)
        if not isinstance(tool.parsed_arguments, dict):
            observed.flag("INVALID_TOOL_ARGUMENTS", source)
    except (ValueError, TypeError):
        observed.flag("INVALID_TOOL_ARGUMENTS", source)


def usage(observed: Observation, value: dict) -> None:
    if value.get("usage") is not None:
        observed.usage = value["usage"]
        observed.usage_records.append(value["usage"])


def terminal_missing(observed: Observation) -> None:
    source = (
        observed.raw_events[-1].source() if observed.raw_events else SourcePosition()
    )
    if observed.terminal_state == "missing":
        observed.flag("MISSING_TERMINAL", source)


def is_index(value: Any) -> bool:
    return type(value) is int and value >= 0
