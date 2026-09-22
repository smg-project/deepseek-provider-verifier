"""Literal wire frames catch framing repairs and chunk-dependent decoding."""

import pytest

from deepseek_provider_verifier.sse import decode_sse


def test_sse_preserves_fragmented_utf8():
    events = decode_sse([b'data: {"text":"\xe4', b'\xbd\xa0"}\r', b"\n\r\n"])
    assert events[0].data == '{"text":"你"}'
    assert events[0].errors == []
    assert (events[0].start_byte, events[0].end_byte) == (0, 24)


def test_comments_multiline_and_split_delimiters():
    wire = b": ping\r\nevent: custom\r\ndata: one\r\ndata: two\r\nid: 7\r\n\r\n"
    for size in [1, 2, 7, 100]:
        events = decode_sse([wire[i : i + size] for i in range(0, len(wire), size)])
        assert len(events) == 1
        assert (events[0].event, events[0].data, events[0].id) == (
            "custom",
            "one\ntwo",
            "7",
        )
        assert events[0].raw_bytes == wire


@pytest.mark.parametrize("wire", [b"data: [DONE]", b"data: [DONE]\n"])
def test_partial_final_frame_is_retained_as_error(wire):
    events = decode_sse([wire])
    assert len(events) == 1
    assert events[0].complete is False
    assert "INCOMPLETE_SSE_FRAME" in events[0].errors
    assert events[0].raw_bytes == wire


def test_invalid_utf8_is_not_repaired():
    events = decode_sse([b"data: \xff\n\n"])
    assert "INVALID_UTF8" in events[0].errors
    assert events[0].data is None
    assert events[0].raw_bytes == b"data: \xff\n\n"


def test_comment_heartbeat_is_not_an_event():
    assert decode_sse([b": heartbeat\n\n"]) == []


def test_initial_utf8_bom_is_ignored_without_losing_positions():
    event = decode_sse([b"\xef", b"\xbb\xbfdata: hi\n\n"])[0]
    assert event.data == "hi"
    assert (event.start_byte, event.end_byte) == (0, 13)
