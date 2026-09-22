"""Wire expectations are literal and independent of the assemblers."""

import json
from pathlib import Path

import pytest

from deepseek_provider_verifier.protocols.chat import assemble_chat, assemble_chat_json
from deepseek_provider_verifier.protocols.responses import (
    assemble_responses,
    assemble_responses_json,
)
from deepseek_provider_verifier.sse import decode_sse

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(protocol, name):
    return (FIXTURES / protocol / name).read_bytes()


@pytest.mark.parametrize("size", [1, 7, 10000])
@pytest.mark.parametrize(
    "protocol,assemble", [("chat", assemble_chat), ("responses", assemble_responses)]
)
def test_interleaved_tools(protocol, assemble, size):
    wire = fixture(protocol, "interleaved.sse")
    result = assemble(
        decode_sse([wire[i : i + size] for i in range(0, len(wire), size)])
    )
    assert result.violations == []
    assert result.terminal_state == "completed"
    assert [
        (t.call_id, t.name, t.arguments, t.parsed_arguments, t.complete)
        for t in result.tools.values()
    ] == [
        ("a", "add", '{"x":2}', {"x": 2}, True),
        ("b", "echo", '{"s":"你"}', {"s": "你"}, True),
    ]
    assert result.usage is not None
    assert result.raw_events


def test_eof_does_not_invent_success():
    observed = assemble_chat(decode_sse([fixture("chat", "missing-terminal.sse")]))
    assert observed.terminal_state == "missing"
    assert "MISSING_TERMINAL" in observed.violations
    assert observed.text == "unfinished"
    assert observed.evidence["MISSING_TERMINAL"][0].event_index == 0


@pytest.mark.parametrize("assemble", [assemble_chat, assemble_responses])
@pytest.mark.parametrize(
    "wire,code",
    [
        (b"data: {bad}\n\n", "INVALID_JSON"),
        (b"data: []\n\n", "NON_OBJECT_JSON"),
        (b"data: \xff\n\n", "INVALID_UTF8"),
        (b"data: [DONE]", "INCOMPLETE_SSE_FRAME"),
    ],
)
def test_malformed_frame_survives_assembly(assemble, wire, code):
    observed = assemble(decode_sse([wire]))
    assert code in observed.violations
    assert observed.evidence[code][0].start_byte == 0
    assert observed.terminal_state == "missing"


@pytest.mark.parametrize(
    "finish,state",
    [
        ("length", "incomplete"),
        ("content_filter", "failed"),
        ("aborted", "failed"),
        ("insufficient_system_resource", "failed"),
    ],
)
def test_chat_failed_finish_is_not_upgraded_by_done(finish, state):
    wire = (
        'data: {"choices":[{"index":0,"delta":{},"finish_reason":"'
        + finish
        + '"}]}\n\ndata: [DONE]\n\n'
    ).encode()
    result = assemble_chat(decode_sse([wire]))
    assert result.terminal_state == state


@pytest.mark.parametrize(
    "wire,code",
    [
        (b"data: [DONE]\n\n", "MISSING_FINISH_REASON"),
        (b"data: [DONE]\n\ndata: [DONE]\n\n", "DUPLICATE_TERMINAL"),
        (b'data: [DONE]\n\ndata: {"choices":[]}\n\n', "DATA_AFTER_TERMINAL"),
        (
            b'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"function":{"arguments":"{}"}}]}}]}\n\n',
            "MISSING_TOOL_INDEX",
        ),
        (
            b'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"a","function":{"name":"f","arguments":"{bad}"}}]},"finish_reason":"tool_calls"}]}\n\ndata: [DONE]\n\n',
            "INVALID_TOOL_ARGUMENTS",
        ),
    ],
)
def test_chat_faults(wire, code):
    assert code in assemble_chat(decode_sse([wire])).violations


def test_responses_failure_and_unknown_fields_survive():
    result = assemble_responses(decode_sse([fixture("responses", "failed.sse")]))
    assert result.terminal_state == "failed"
    assert result.raw_response["error"]["code"] == "server_error"
    assert result.violations == []


@pytest.mark.parametrize(
    "old,new,code",
    [
        ('"sequence_number":4', '"sequence_number":3', "NON_MONOTONIC_SEQUENCE"),
        (
            '"item_id":"i2","delta":"{',
            '"item_id":"absent","delta":"{',
            "UNKNOWN_ITEM_ID",
        ),
        (
            '"item":{"id":"i2","type":"function_call","call_id":"b"',
            '"item":{"id":"i1","type":"function_call","call_id":"b"',
            "DUPLICATE_ITEM_ID",
        ),
        (
            '"arguments":"{\\"x\\":2}","status":"completed"',
            '"arguments":"{\\"x\\":3}","status":"completed"',
            "SNAPSHOT_MISMATCH",
        ),
        (
            "event: response.created",
            "event: response.in_progress",
            "EVENT_TYPE_MISMATCH",
        ),
    ],
)
def test_responses_faults(old, new, code):
    wire = fixture("responses", "interleaved.sse").decode()
    assert old in wire
    wire = wire.replace(old, new, 1).encode()
    result = assemble_responses(decode_sse([wire]))
    assert code in result.violations
    assert result.evidence[code]


@pytest.mark.parametrize(
    "protocol,assemble",
    [("chat", assemble_chat_json), ("responses", assemble_responses_json)],
)
def test_nonstream_json_preserves_replay_records(protocol, assemble):
    raw = json.loads(fixture(protocol, "nonstream.json"))
    result = assemble(raw)
    assert result.terminal_state == "completed"
    assert result.text == "hello"
    assert result.reasoning in ["thought", "think"]
    assert result.violations == []
    assert result.raw_response == raw


@pytest.mark.parametrize("assemble", [assemble_chat_json, assemble_responses_json])
def test_nonstream_wrong_shape_is_evidence(assemble):
    result = assemble([])
    assert "NON_OBJECT_JSON" in result.violations
    assert result.terminal_state == "missing"


def test_chat_data_after_terminal_cannot_upgrade_failure():
    result = assemble_chat(
        decode_sse(
            [
                b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"aborted"}]}\n\ndata: [DONE]\n\ndata: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
            ]
        )
    )
    assert result.terminal_state == "failed"
    assert "DATA_AFTER_TERMINAL" in result.violations


def test_chat_every_started_choice_requires_finish():
    result = assemble_chat(
        decode_sse(
            [
                b'data: {"choices":[{"index":0,"delta":{"content":"a"},"finish_reason":"stop"},{"index":1,"delta":{"content":"b"},"finish_reason":null}]}\n\ndata: [DONE]\n\n'
            ]
        )
    )
    assert "MISSING_FINISH_REASON" in result.violations
    assert result.terminal_state == "unknown"


def test_chat_stream_replay_keeps_reasoning_and_tools():
    result = assemble_chat(decode_sse([fixture("chat", "interleaved.sse")]))
    assert result.assistant_messages[0]["reasoning_content"] == "think"
    assert (
        result.assistant_messages[0]["tool_calls"][1]["function"]["arguments"]
        == '{"s":"你"}'
    )
    assert result.assistant_messages[0]["provider_extra"] is True


def test_responses_duplicate_done_and_terminal_are_reported():
    wire = fixture("responses", "interleaved.sse")
    line = b'event: response.function_call_arguments.done\ndata: {"type":"response.function_call_arguments.done","sequence_number":7,"output_index":0,"item_id":"i1","arguments":"{\\"x\\":2}"}\n\n'
    result = assemble_responses(decode_sse([wire.replace(line, line + line)]))
    assert "DUPLICATE_FINALIZATION" in result.violations
    failed = fixture("responses", "failed.sse")
    result = assemble_responses(
        decode_sse(
            [
                failed
                + b'event: response.completed\ndata: {"type":"response.completed","sequence_number":2,"response":{"id":"r3","status":"completed","output":[]}}\n\n'
            ]
        )
    )
    assert "DUPLICATE_TERMINAL" in result.violations
    assert result.terminal_state == "failed"


def test_responses_nonstream_tool_failure_keeps_incomplete_status():
    result = assemble_responses_json(
        {
            "id": "r",
            "status": "incomplete",
            "output": [
                {
                    "id": "i",
                    "type": "function_call",
                    "call_id": "a",
                    "name": "f",
                    "arguments": '{"x":',
                }
            ],
        }
    )
    assert result.terminal_state == "incomplete"
    assert "INVALID_TOOL_ARGUMENTS" in result.violations


def test_responses_missing_response_object_is_structural_failure():
    result = assemble_responses(
        decode_sse(
            [
                b'event: response.created\ndata: {"type":"response.created","sequence_number":0}\n\n'
            ]
        )
    )
    assert "INVALID_RESPONSE" in result.violations


@pytest.mark.parametrize("size", [1, 19, 10000])
def test_responses_text_done_snapshots_do_not_double_append(size):
    wire = fixture("responses", "text.sse")
    result = assemble_responses(
        decode_sse([wire[i : i + size] for i in range(0, len(wire), size)])
    )
    assert result.text == "你"
    assert result.reasoning == "think"
    assert result.violations == []
    assert result.terminal_state == "completed"


@pytest.mark.parametrize(
    "old,new,code",
    [
        (
            '"content_index":0,"text":"你"',
            '"content_index":0,"text":"wrong"',
            "SNAPSHOT_MISMATCH",
        ),
        (
            '"item_id":"msg","content_index":0,"delta":"你"',
            '"item_id":"msg","content_index":0,"delta":5',
            "INVALID_TEXT_DELTA",
        ),
        (
            '"output_index":1,"item_id":"msg","content_index":0,"delta":"你"',
            '"output_index":0,"item_id":"msg","content_index":0,"delta":"你"',
            "ITEM_INDEX_MISMATCH",
        ),
    ],
)
def test_responses_text_faults(old, new, code):
    wire = fixture("responses", "text.sse").decode()
    assert old in wire
    result = assemble_responses(decode_sse([wire.replace(old, new, 1).encode()]))
    assert code in result.violations


def test_chat_duplicate_tool_identity_is_not_joined():
    result = assemble_chat(
        decode_sse(
            [
                b'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"same","function":{"name":"f","arguments":"{}"}},{"index":1,"id":"same","function":{"name":"g","arguments":"{}"}}]},"finish_reason":"tool_calls"}]}\n\ndata: [DONE]\n\n'
            ]
        )
    )
    assert "DUPLICATE_TOOL_ID" in result.violations
    assert len(result.tools) == 2


@pytest.mark.parametrize("value", [None, [], "bad", 4, True])
def test_bad_protocol_shapes_never_crash(value):
    chat = assemble_chat_json({"choices": value})
    responses = assemble_responses_json({"status": "completed", "output": value})
    assert "INVALID_CHOICES" in chat.violations or value == []
    assert "INVALID_OUTPUT" in responses.violations or value == []


@pytest.mark.parametrize("assemble", [assemble_chat, assemble_responses])
def test_nonstandard_json_numbers_are_rejected(assemble):
    observed = assemble(decode_sse([b'data: {"unexpected":NaN}\n\n']))
    assert "INVALID_JSON" in observed.violations


def test_nonstandard_json_tool_arguments_are_rejected():
    observed = assemble_chat_json(
        {
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "a",
                                "type": "function",
                                "function": {"name": "f", "arguments": '{"x":NaN}'},
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        }
    )
    assert "INVALID_TOOL_ARGUMENTS" in observed.violations


@pytest.mark.parametrize("part_type", [[], {}, 4])
def test_malformed_responses_content_type_is_evidence(part_type):
    observed = assemble_responses_json(
        {
            "status": "completed",
            "output": [
                {
                    "id": "i",
                    "type": "message",
                    "content": [{"type": part_type, "text": "hello"}],
                }
            ],
        }
    )
    assert "INVALID_CONTENT_PART" in observed.violations


def test_responses_content_after_part_done_retains_offending_source():
    # The part is finalized with the correct current (empty) snapshot, followed
    # by text and text.done. Every sequence number increases and all later full
    # snapshots match, so only lifecycle validation can detect the defect.
    wire = b"""event: response.created
data: {"type":"response.created","sequence_number":0,"response":{"id":"r","status":"in_progress"}}

event: response.output_item.added
data: {"type":"response.output_item.added","sequence_number":1,"output_index":0,"item":{"id":"m","type":"message","content":[]}}

event: response.content_part.added
data: {"type":"response.content_part.added","sequence_number":2,"output_index":0,"item_id":"m","content_index":0,"part":{"type":"output_text","text":""}}

event: response.content_part.done
data: {"type":"response.content_part.done","sequence_number":3,"output_index":0,"item_id":"m","content_index":0,"part":{"type":"output_text","text":""}}

event: response.output_text.delta
data: {"type":"response.output_text.delta","sequence_number":4,"output_index":0,"item_id":"m","content_index":0,"delta":"late"}

event: response.output_text.done
data: {"type":"response.output_text.done","sequence_number":5,"output_index":0,"item_id":"m","content_index":0,"text":"late"}

event: response.output_item.done
data: {"type":"response.output_item.done","sequence_number":6,"output_index":0,"item":{"id":"m","type":"message","content":[{"type":"output_text","text":"late"}]}}

event: response.completed
data: {"type":"response.completed","sequence_number":7,"response":{"id":"r","status":"completed","output":[{"id":"m","type":"message","content":[{"type":"output_text","text":"late"}]}]}}

"""
    observed = assemble_responses(decode_sse([wire]))
    assert "DATA_AFTER_PART_DONE" in observed.violations
    assert [
        source.event_index for source in observed.evidence["DATA_AFTER_PART_DONE"]
    ] == [4, 5]
    assert observed.evidence["DATA_AFTER_PART_DONE"][0].start_byte == wire.index(
        b"event: response.output_text.delta"
    )
    assert observed.text == "late"
    assert observed.terminal_state == "completed"
