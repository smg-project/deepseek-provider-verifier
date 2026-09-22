"""Small deterministic HTTP fixture for the documented four-request offline example."""

from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_MODEL = "synthetic-fixture-model"


def _chat_response() -> dict:
    return {
        "id": "chat-synthetic-fixture",
        "object": "chat.completion",
        "created": 1,
        "model": _MODEL,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "amber"},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 2,
            "completion_tokens": 1,
            "total_tokens": 3,
            "completion_tokens_details": {"reasoning_tokens": 0},
        },
    }


def _chat_stream() -> bytes:
    chunks = [
        {
            "id": "chat-synthetic-fixture",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": _MODEL,
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": "amber"},
                    "finish_reason": None,
                }
            ],
        },
        {
            "id": "chat-synthetic-fixture",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": _MODEL,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": 2,
                "completion_tokens": 1,
                "total_tokens": 3,
                "completion_tokens_details": {"reasoning_tokens": 0},
            },
        },
    ]
    return (
        "".join(
            f"data: {json.dumps(chunk, separators=(',', ':'))}\n\n" for chunk in chunks
        )
        + "data: [DONE]\n\n"
    ).encode()


def _responses_response() -> dict:
    item = _responses_item()
    return {
        "id": "response-synthetic-fixture",
        "object": "response",
        "status": "completed",
        "model": _MODEL,
        "output": [item],
        "usage": {
            "input_tokens": 2,
            "output_tokens": 1,
            "total_tokens": 3,
            "output_tokens_details": {"reasoning_tokens": 0},
        },
    }


def _responses_item() -> dict:
    return {
        "id": "message-synthetic-fixture",
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": "amber", "annotations": []}],
    }


def _responses_stream() -> bytes:
    item = _responses_item()
    item_id = item["id"]
    events = [
        {
            "type": "response.created",
            "sequence_number": 0,
            "response": {"id": "response-synthetic-fixture", "status": "in_progress"},
        },
        {
            "type": "response.output_item.added",
            "sequence_number": 1,
            "output_index": 0,
            "item": {"id": item_id, "type": "message", "content": []},
        },
        {
            "type": "response.content_part.added",
            "sequence_number": 2,
            "output_index": 0,
            "item_id": item_id,
            "content_index": 0,
            "part": {"type": "output_text", "text": ""},
        },
        {
            "type": "response.output_text.delta",
            "sequence_number": 3,
            "output_index": 0,
            "item_id": item_id,
            "content_index": 0,
            "delta": "amber",
        },
        {
            "type": "response.output_text.done",
            "sequence_number": 4,
            "output_index": 0,
            "item_id": item_id,
            "content_index": 0,
            "text": "amber",
        },
        {
            "type": "response.content_part.done",
            "sequence_number": 5,
            "output_index": 0,
            "item_id": item_id,
            "content_index": 0,
            "part": {"type": "output_text", "text": "amber"},
        },
        {
            "type": "response.output_item.done",
            "sequence_number": 6,
            "output_index": 0,
            "item": item,
        },
        {
            "type": "response.completed",
            "sequence_number": 7,
            "response": _responses_response(),
        },
    ]
    return "".join(
        f"event: {event['type']}\ndata: {json.dumps(event, separators=(',', ':'))}\n\n"
        for event in events
    ).encode()


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], max_requests: int):
        super().__init__(address, _Handler)
        self.max_requests = max_requests
        self.completed_requests = 0
        self.counter_lock = threading.Lock()

    def completed(self) -> None:
        with self.counter_lock:
            self.completed_requests += 1
            finished = self.completed_requests >= self.max_requests
        if finished:
            threading.Thread(target=self.shutdown, daemon=True).start()


class _Handler(BaseHTTPRequestHandler):
    server: _Server

    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("content-length", "0"))
            request = json.loads(self.rfile.read(length))
            if not isinstance(request, dict) or request.get("model") != _MODEL:
                self._send_json(400, {"error": "expected synthetic-fixture-model"})
                return
            stream = request.get("stream") is True
            if self.path == "/v1/chat/completions":
                self._send(
                    200,
                    _chat_stream() if stream else json.dumps(_chat_response()).encode(),
                    "text/event-stream" if stream else "application/json",
                )
            elif self.path == "/v1/responses":
                self._send(
                    200,
                    _responses_stream()
                    if stream
                    else json.dumps(_responses_response()).encode(),
                    "text/event-stream" if stream else "application/json",
                )
            else:
                self._send_json(404, {"error": "fixture supports only C01 paths"})
        except (ValueError, TypeError, json.JSONDecodeError):
            self._send_json(400, {"error": "invalid synthetic fixture request"})
        finally:
            self.server.completed()

    def _send_json(self, status: int, value: dict) -> None:
        self._send(status, json.dumps(value).encode(), "application/json")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(body)))
        self.send_header("connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:
        return


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Synthetic local provider for the dpv four-request offline example"
    )
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--max-requests", type=int, default=4)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535 or args.max_requests < 1:
        parser.error("port and max-requests must be positive and bounded")
    host = "127.0.0.1"
    server = _Server((host, args.port), args.max_requests)
    print(
        f"Synthetic fixture listening on http://{host}:{args.port}/v1 "
        f"for {args.max_requests} request(s)",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Synthetic fixture stopped", flush=True)
    finally:
        server.server_close()
    if server.completed_requests >= args.max_requests:
        print("Synthetic fixture completed its request budget", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
