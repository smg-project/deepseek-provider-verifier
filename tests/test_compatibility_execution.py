"""Execute the complete new probes preset with deterministic HTTP/SSE fixtures."""

import copy
import json
from pathlib import Path

import httpx
import pytest
from test_runner import run
from test_task3_review_fixes import protocol_response

from deepseek_provider_verifier.catalog import load_cases
from deepseek_provider_verifier.config import load_config, load_profile
from deepseek_provider_verifier.planner import build_manifest

ROOT = Path(__file__).resolve().parents[1]


def stream_response(protocol, body):
    if protocol == "chat":
        message = copy.deepcopy(body["choices"][0]["message"])
        for index, tool in enumerate(message.get("tool_calls", [])):
            tool["index"] = index
        base = {k: body[k] for k in ("id", "created", "model")}
        base["object"] = "chat.completion.chunk"
        events = [
            {
                **base,
                "choices": [{"index": 0, "delta": message, "finish_reason": None}],
            },
            {
                **base,
                "choices": [
                    {
                        "index": 0,
                        "delta": {},
                        "finish_reason": body["choices"][0]["finish_reason"],
                    }
                ],
                "usage": body["usage"],
            },
        ]
        content = (
            "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"
        )
    else:
        events = []

        def emit(kind, **fields):
            events.append(dict(type=kind, sequence_number=len(events), **fields))

        emit(
            "response.created",
            response={"id": body["id"], "object": "response", "status": "in_progress"},
        )
        for index, item in enumerate(body["output"]):
            initial = copy.deepcopy(item)
            if item["type"] == "function_call":
                initial["arguments"] = ""
            else:
                initial["content"] = []
            emit("response.output_item.added", output_index=index, item=initial)
            common = {"output_index": index, "item_id": item["id"]}
            if item["type"] == "function_call":
                emit(
                    "response.function_call_arguments.delta",
                    **common,
                    delta=item["arguments"],
                )
                emit(
                    "response.function_call_arguments.done",
                    **common,
                    arguments=item["arguments"],
                )
            else:
                for ci, part in enumerate(item["content"]):
                    emit(
                        "response.content_part.added",
                        **common,
                        content_index=ci,
                        part={"type": part["type"], "text": ""},
                    )
                    emit(
                        f"response.{part['type']}.delta",
                        **common,
                        content_index=ci,
                        delta=part["text"],
                    )
                    emit(
                        f"response.{part['type']}.done",
                        **common,
                        content_index=ci,
                        text=part["text"],
                    )
                    emit(
                        "response.content_part.done",
                        **common,
                        content_index=ci,
                        part=part,
                    )
            emit("response.output_item.done", output_index=index, item=item)
        emit("response.completed", response=body)
        content = "".join(
            f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events
        )
    return httpx.Response(
        200, content=content, headers={"content-type": "text/event-stream"}
    )


def probe_manifest(policy):
    config = load_config(ROOT / "configs/self-hosted.example.toml")
    profile = load_profile(ROOT / f"profiles/deepseek-{policy}-2026-09-22-v1.json")
    config = config.model_copy(
        update={
            "run": config.run.model_copy(
                update={"suite": "probes", "profile": profile.id}
            )
        }
    )
    return build_manifest(
        config, profile, load_cases(case_ids=profile.presets["probes"].case_ids)
    )


def provider(kind):
    def handle(request):
        p = json.loads(request.content)
        protocol = (
            "chat" if request.url.path.endswith("chat/completions") else "responses"
        )
        history = p["messages" if protocol == "chat" else "input"]
        thinking = (
            p.get("thinking", {}).get("type") == "enabled"
            or p.get("reasoning", {}).get("effort") == "high"
        )
        identifier = p.get("user_id", p.get("user", ""))
        fmt = p.get("text", {}).get("format", {})
        forced = p.get("tool_choice") not in (None, "auto")
        continuation = any(
            i.get("role") == "tool" or i.get("type") == "function_call_output"
            for i in history
        )
        preserved = any(
            i.get("reasoning_content") or i.get("type") == "reasoning" for i in history
        )
        reject = (
            len(identifier) > 512
            or (kind == "official" and thinking and forced)
            or (
                kind == "official"
                and fmt.get("strict")
                and "const" in fmt["schema"]["properties"]["label"]
            )
            or (kind == "self-hosted" and any(c in identifier for c in " !"))
            or (kind == "self-hosted" and continuation and not preserved)
        )
        if kind == "reject-everything" or reject:
            return httpx.Response(400, json={"error": "synthetic validation rejection"})
        if fmt:
            text = '{"label":"amber","count":3}'
        elif continuation:
            text = "42"
        else:
            text = "amber"
        call = bool(p.get("tools")) and not continuation
        body = protocol_response(protocol, text=text, call=call, reasoning=thinking)
        if call and forced:
            item = (
                body["choices"][0]["message"]["tool_calls"][0]["function"]
                if protocol == "chat"
                else body["output"][-1]
            )
            item.update(name="lookup_fixture", arguments='{"key":"harbor"}')
        return (
            stream_response(protocol, body)
            if p["stream"]
            else httpx.Response(200, json=body)
        )

    return handle


@pytest.mark.parametrize("policy", ["self-hosted", "official-parity"])
@pytest.mark.parametrize("behavior", ["official", "self-hosted", "reject-everything"])
def test_full_probe_matrix(policy, behavior, tmp_path):
    m = probe_manifest(policy)
    result = run(m, provider(behavior), tmp_path)
    expected = (
        "FAIL"
        if behavior == "reject-everything"
        or (policy == "official-parity" and behavior == "self-hosted")
        else "PASS"
    )
    assert result.complete
    assert result.exit_code == (1 if expected == "FAIL" else 0), [
        (
            r.case_id,
            r.status,
            r.reason,
            [a.id for a in r.assertions if a.status != "PASS"],
        )
        for r in result.case_results
        if r.status not in ("PASS", "SKIP")
    ]
    assert result.counts.get("ERROR", 0) == 0
    assert result.counts.get("INCONCLUSIVE", 0) == 0
    assert result.counts.get("SKIP", 0) == 4
    assert result.budget_usage["candidate"] <= m.request_ceiling
    differences = [
        a
        for r in result.case_results
        for a in r.assertions
        if a.id == "COMPATIBILITY_OBSERVATION" and not a.observed["matches_reference"]
    ]
    assert bool(differences) == (behavior != "official")
