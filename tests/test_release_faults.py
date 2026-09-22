"""Release inventory: corrupted wire behavior must reach report assertion IDs."""

import json
from pathlib import Path

import httpx
import pytest
from test_runner import manifest, response, run, tool

from deepseek_provider_verifier.runner import rehash_manifest

FIXTURES = Path(__file__).parent / "fixtures"


def failing_assertion(result, assertion_id):
    assert result.exit_code == 1
    assert result.case_results[0].status == "FAIL"
    assert any(
        a.id == assertion_id and a.status == "FAIL" and a.gating
        for a in result.case_results[0].assertions
    )


@pytest.mark.parametrize("protocol", ["chat", "responses"])
@pytest.mark.parametrize(
    "fault", ["valid", "mixed_indices", "failed_terminal", "wrong_usage"]
)
def test_stream_fault_inventory_reaches_report(protocol, fault):
    m = manifest(protocol=protocol, oracle={"kind": "structure"})
    rules = [
        m.profile_snapshot.rules[0].model_copy(
            update={"assertion_id": "usage_accounting"}
        )
    ]
    m = rehash_manifest(
        m.model_copy(
            update={
                "cases": [m.cases[0].model_copy(update={"stream": True})],
                "profile_snapshot": m.profile_snapshot.model_copy(
                    update={"rules": rules}
                ),
                "gates": ["usage_accounting"],
            }
        )
    )
    wire = (FIXTURES / protocol / "interleaved.sse").read_text()
    wire = wire.replace(
        '"completion_tokens":5', '"completion_tokens":5,"total_tokens":15'
    )
    wire = wire.replace(
        '"output_tokens":6', '"input_tokens":10,"output_tokens":6,"total_tokens":16'
    )
    code = None
    if fault == "mixed_indices":
        if protocol == "chat":
            old = '"index":1,"id":"b"'
            new = '"index":0,"id":"b"'
            code = "CONFLICTING_TOOL_ID"
        else:
            old = '"output_index":1,"item_id":"i2","delta"'
            new = '"output_index":0,"item_id":"i2","delta"'
            code = "ITEM_INDEX_MISMATCH"
        assert old in wire
        wire = wire.replace(old, new, 1)
    elif fault == "wrong_usage":
        old = '"total_tokens":15' if protocol == "chat" else '"total_tokens":16'
        assert old in wire
        wire = wire.replace(old, '"total_tokens":999', 1)
        code = "usage_accounting"
    elif fault == "failed_terminal":
        if protocol == "chat":
            wire = wire.replace(
                '"finish_reason":"tool_calls"', '"finish_reason":"aborted"'
            )
        else:
            wire = (FIXTURES / protocol / "failed.sse").read_text()
        code = "TERMINAL_STATE"
    result = run(
        m,
        lambda r: httpx.Response(
            200, content=wire, headers={"content-type": "text/event-stream"}
        ),
    )
    if code:
        failing_assertion(result, code)
    else:
        assert result.exit_code == 0


@pytest.mark.parametrize(
    "kind,payload,assertion_id",
    [
        ("tool_forbidden", response(None, [tool()]), "TOOL_PROHIBITION"),
        ("tool_required", response(), "TOOL_REQUIRED"),
        (
            "tool_required",
            response(None, [tool(arguments="{bad}")]),
            "TOOL_ARGUMENTS_INVALID_JSON",
        ),
    ],
)
def test_tool_fault_inventory_reaches_report(kind, payload, assertion_id):
    m = manifest(oracle={"kind": kind})
    failing_assertion(run(m, lambda r: httpx.Response(200, json=payload)), assertion_id)


def test_premature_eof_reaches_report():
    m = manifest(oracle={"kind": "structure"})
    m = rehash_manifest(
        m.model_copy(update={"cases": [m.cases[0].model_copy(update={"stream": True})]})
    )
    wire = (FIXTURES / "chat" / "missing-terminal.sse").read_bytes()
    failing_assertion(
        run(
            m,
            lambda r: httpx.Response(
                200, content=wire, headers={"content-type": "text/event-stream"}
            ),
        ),
        "MISSING_TERMINAL",
    )


def test_replay_corruptions_reach_report(monkeypatch):
    from deepseek_provider_verifier import runner

    original = runner._request

    def corrupt(*args, **kwargs):
        request = original(*args, **kwargs)
        for item in request.get("messages", []):
            item.pop("reasoning_content", None)
            if item.get("role") == "tool":
                item["tool_call_id"] = "wrong-id"
        return request

    monkeypatch.setattr(runner, "_request", corrupt)
    m = manifest(
        max_requests=2,
        oracle={"kind": "conversation", "value": "42", "continue_tools": True},
    )

    def handler(request):
        followup = any(
            i.get("role") == "tool" for i in json.loads(request.content)["messages"]
        )
        return httpx.Response(
            200,
            json=response("42")
            if followup
            else response(None, [tool()], "original reasoning"),
        )

    result = run(m, handler)
    failing_assertion(result, "HISTORY_REPLAY")
    failing_assertion(result, "TOOL_RESULT_PAIRING")
