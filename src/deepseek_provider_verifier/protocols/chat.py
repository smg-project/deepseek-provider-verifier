"""Chat Completions assembly; tool identities are (choice index, tool index).

`assistant_messages` retains full raw nonstream messages for later tool replay.
Streaming raw delta objects stay in `raw_objects`, including additive fields.
"""

from typing import Any

from ..capture import Observation, Segment, SourcePosition, SSEEvent, ToolCall
from .common import arguments_done, is_index, object_event, terminal_missing, usage

_FINISH = {
    "stop": "completed",
    "tool_calls": "completed",
    "length": "incomplete",
    "content_filter": "failed",
    "aborted": "failed",
    "insufficient_system_resource": "failed",
}


def _choices(
    value: dict, observed: Observation, source: SourcePosition, stream: bool
) -> None:
    usage(observed, value)
    choices = value.get("choices")
    if not isinstance(choices, list):
        observed.flag("INVALID_CHOICES", source)
        return
    seen = set()
    for choice in choices:
        if not isinstance(choice, dict) or not is_index(choice.get("index")):
            observed.flag("MISSING_CHOICE_INDEX", source)
            continue
        ci = str(choice["index"])
        if ci in seen:
            observed.flag("DUPLICATE_CHOICE_INDEX", source)
        seen.add(ci)
        initial_choice = ci not in observed.choice_indices
        if initial_choice:
            observed.choice_indices.append(ci)
            if stream:
                observed.assistant_messages.append({})
        delta = choice.get("delta" if stream else "message")
        if not isinstance(delta, dict):
            observed.flag("INVALID_DELTA" if stream else "INVALID_MESSAGE", source)
            continue
        if stream and (
            (initial_choice and delta.get("role") != "assistant")
            or delta.get("role") not in (None, "assistant")
        ):
            observed.flag("RESPONSE_ENVELOPE", source)
        if ci in observed.finish_reasons:
            observed.flag("DATA_AFTER_FINISH", source)
            if choice.get("finish_reason") is not None:
                observed.flag("DUPLICATE_FINISH_REASON", source)
            continue
        if not stream:
            observed.assistant_messages.append(delta)
        else:
            replay = observed.assistant_messages[observed.choice_indices.index(ci)]
            replay.update(
                {
                    key: value
                    for key, value in delta.items()
                    if key not in ("content", "reasoning_content", "tool_calls")
                    and not (key == "role" and value is None)
                }
            )
        for field, segments in [
            ("content", observed.text_segments),
            ("reasoning_content", observed.reasoning_segments),
        ]:
            text = delta.get(field)
            if text is not None:
                if not isinstance(text, str):
                    observed.flag("INVALID_TEXT_DELTA", source)
                elif text:
                    segments.append(Segment(text=text, identity=ci, source=source))
        calls = delta.get("tool_calls", [])
        if not isinstance(calls, list):
            observed.flag("INVALID_TOOL_CALLS", source)
            calls = []
        for number, call in enumerate(calls):
            if not isinstance(call, dict):
                observed.flag("INVALID_TOOL_CALL", source)
                continue
            index = call.get("index") if stream else number
            if not is_index(index):
                observed.flag("MISSING_TOOL_INDEX", source)
                continue
            identity = f"{ci}:{index}"
            if identity not in observed.tools:
                observed.tools[identity] = ToolCall(identity=identity, index=index)
            tool = observed.tools[identity]
            tool.sources.append(source)
            call_id = call.get("id")
            if call_id is not None:
                if not isinstance(call_id, str) or not call_id:
                    observed.flag("MISSING_TOOL_ID", source)
                elif tool.call_id and tool.call_id != call_id:
                    observed.flag("CONFLICTING_TOOL_ID", source)
                elif any(
                    t.identity != identity and t.call_id == call_id
                    for t in observed.tools.values()
                ):
                    observed.flag("DUPLICATE_TOOL_ID", source)
                else:
                    tool.call_id = call_id
            function = call.get("function", {})
            if not isinstance(function, dict):
                observed.flag("INVALID_TOOL_CALL", source)
                continue
            name = function.get("name")
            if name is not None:
                if not isinstance(name, str):
                    observed.flag("INVALID_TOOL_NAME", source)
                elif tool.name and tool.name != name:
                    observed.flag("CONFLICTING_TOOL_NAME", source)
                else:
                    tool.name = name
            args = function.get("arguments", "")
            if isinstance(args, str):
                tool.arguments += args
            else:
                observed.flag("INVALID_TOOL_ARGUMENTS", source)
        finish = choice.get("finish_reason")
        if finish is not None:
            if ci in observed.finish_reasons:
                observed.flag("DUPLICATE_FINISH_REASON", source)
            if not isinstance(finish, str) or finish not in _FINISH:
                observed.flag("UNKNOWN_FINISH_REASON", source)
            observed.finish_reasons[ci] = str(finish)
            for identity, tool in observed.tools.items():
                if identity.startswith(ci + ":"):
                    arguments_done(tool, observed, source)


def _finish(observed: Observation, has_terminal: bool) -> Observation:
    source = (
        observed.raw_events[-1].source()
        if observed.raw_events
        else SourcePosition(json_path="$")
    )
    for tool in observed.tools.values():
        if not tool.call_id:
            observed.flag("MISSING_TOOL_ID", tool.sources[-1])
        if not tool.name:
            observed.flag("MISSING_TOOL_NAME", tool.sources[-1])
        if not tool.complete:
            observed.flag("INCOMPLETE_TOOL_ARGUMENTS", tool.sources[-1])
    if has_terminal:
        states = [_FINISH.get(x, "unknown") for x in observed.finish_reasons.values()]
        if set(observed.choice_indices) != set(observed.finish_reasons):
            observed.flag("MISSING_FINISH_REASON", source)
            states.append("unknown")
        if not states:
            observed.flag("MISSING_FINISH_REASON", source)
            observed.terminal_state = "unknown"
        else:
            observed.terminal_state = next(
                s
                for s in ["failed", "incomplete", "unknown", "completed"]
                if s in states
            )
    if observed.raw_events:
        for index, ci in enumerate(observed.choice_indices):
            replay = observed.assistant_messages[index]
            for field, segments in [
                ("content", observed.text_segments),
                ("reasoning_content", observed.reasoning_segments),
            ]:
                selected = [
                    segment.text for segment in segments if segment.identity == ci
                ]
                if selected:
                    replay[field] = "".join(selected)
            tools = [
                tool
                for identity, tool in observed.tools.items()
                if identity.startswith(ci + ":")
            ]
            if tools:
                replay["tool_calls"] = [
                    {
                        "id": tool.call_id,
                        "type": "function",
                        "function": {"name": tool.name, "arguments": tool.arguments},
                    }
                    for tool in sorted(tools, key=lambda tool: tool.index)
                ]
    terminal_missing(observed)
    return observed


def assemble_chat(events: list[SSEEvent]) -> Observation:
    """Keep DONE distinct from choice finish reasons; EOF never supplies either."""
    observed = Observation(protocol="chat", raw_events=events)
    terminal = False
    identity = None
    for event in events:
        if event.complete and not event.errors and event.data == "[DONE]":
            if terminal:
                observed.flag("DUPLICATE_TERMINAL", event.source())
            terminal = True
            continue
        if terminal:
            observed.flag("DATA_AFTER_TERMINAL", event.source())
        value = object_event(event, observed)
        if value is not None and not terminal:
            envelope_ok = (
                isinstance(value.get("id"), str)
                and bool(value["id"])
                and value.get("object") == "chat.completion.chunk"
                and type(value.get("created")) is int
                and isinstance(value.get("model"), str)
            )
            current_identity = (
                value.get("id"),
                value.get("created"),
                value.get("model"),
            )
            if not envelope_ok or (
                identity is not None and identity != current_identity
            ):
                observed.flag("RESPONSE_ENVELOPE", event.source())
            if identity is None and envelope_ok:
                identity = current_identity
            _choices(value, observed, event.source(), True)
    return _finish(observed, terminal)


def assemble_chat_json(value: Any) -> Observation:
    """Assemble a decoded nonstream body; retain exact assistant messages in memory."""
    observed = Observation(protocol="chat", raw_response=value)
    if not isinstance(value, dict):
        observed.flag("NON_OBJECT_JSON", SourcePosition(json_path="$"))
        return _finish(observed, False)
    _choices(value, observed, SourcePosition(json_path="$.choices"), False)
    return _finish(observed, bool(observed.finish_reasons))
