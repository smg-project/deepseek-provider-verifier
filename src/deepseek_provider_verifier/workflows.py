"""Bounded workflow recipes and evaluation against declared parent edges."""

import copy
from collections import Counter

from .assertions import has_execution_error
from .catalog import content_hash
from .compatibility import _positive
from .json_utils import strict_json_loads
from .mock_tools import execute_tool, tool_definition
from .records import AssertionResult, CaseResult, MetricObservation


def workflow_steps(descriptor, protocol):
    def call(a, b):
        return {"name": "add_integers", "arguments": {"a": a, "b": b}}

    def step(content, expect, request=None, **extras):
        return {
            "kind": "user",
            "content": content,
            "expect": expect,
            "request": request or {},
            **extras,
        }

    initial = {
        "tools": [tool_definition("add_integers", protocol)],
        "tool_choice": "auto",
    }
    shape = descriptor["shape"]
    if shape == "chain":
        steps = []
        total = 1
        for i in range(descriptor["depth"]):
            steps.append(
                step(
                    f"Use add_integers with a={total} and b={i + 1}. This round must consume the previous tool result; make exactly this one call.",
                    {"tools": [call(total, i + 1)]},
                    initial if i == 0 else {},
                )
            )
            total += i + 1
        steps.append(
            step(
                "Return only the final integer from the last tool result. No more calls.",
                {"text": str(total)},
                {"tool_choice": "none"},
            )
        )
        return steps
    if shape == "parallel":
        calls = [call(i + 1, 2 * (i + 1)) for i in range(descriptor["width"])]
        pairs = ", ".join(
            f"({i + 1}, {2 * (i + 1)})" for i in range(descriptor["width"])
        )
        return [
            step(
                f"Make exactly {descriptor['width']} parallel add_integers calls for these operand pairs: {pairs}.",
                {"tools": calls},
                initial,
            ),
            step(
                "Return the results in the original operand-pair order as comma-separated integers without spaces. No more calls.",
                {
                    "text": ",".join(
                        str(3 * (i + 1)) for i in range(descriptor["width"])
                    )
                },
                {"tool_choice": "none"},
            ),
        ]
    if shape == "branch":
        return [
            step(
                "Use add_integers to add 1 and 2 for the shared prefix.",
                {"tools": [call(1, 2)]},
                {**initial, "temperature": 0.7},
            ),
            step(
                "For branch A, use the shared result as a and add b=4 with add_integers.",
                {"tools": [call(3, 4)]},
                {"temperature": 0.2},
            ),
            step(
                "Return only the branch A integer result.",
                {"text": "7"},
                {"tool_choice": "none"},
            ),
            step(
                "For branch B, use the shared result as a and add b=10 with add_integers.",
                {"tools": [call(3, 10)]},
                replay_from=0,
            ),
            step(
                "Return only the branch B integer result.",
                {"text": "13"},
                {"tool_choice": "none"},
            ),
        ]
    updates = [
        ("color", "amber"),
        ("count", "2"),
        ("port", "harbor"),
        ("color", "violet"),
        ("count", "7"),
        ("port", "station"),
        ("color", "cobalt"),
    ]
    return [
        step(
            f"Set {key} to {value}, replacing its earlier value. Reply only ack.",
            {"text": "ack"},
        )
        for key, value in updates
    ] + [
        step(
            "Return the latest color,count,port values in that order, comma-separated without spaces.",
            {"text": "cobalt,7,station"},
        )
    ]


def _normalize_tool_outputs(history):
    """Tool output ordering is irrelevant within a contiguous result batch."""
    normalized = []
    batch = []
    for item in [*history, None]:
        if isinstance(item, dict) and (
            item.get("role") == "tool" or item.get("type") == "function_call_output"
        ):
            batch.append(item)
        else:
            normalized.extend(sorted(batch, key=content_hash))
            batch = []
            if item is not None:
                normalized.append(item)
    return normalized


def _parent_request(case, observations, index):
    recipe = case.steps[index]
    parent = recipe.get("replay_from", index - 1)
    prior = observations[parent]
    key = "messages" if case.protocol == "chat" else "input"
    expected = copy.deepcopy(prior.request_payload)
    history = expected[key]
    items = (
        prior.assistant_messages
        if case.protocol == "chat"
        else (prior.raw_response or {}).get("output", [])
    )
    history.extend(copy.deepcopy(items))
    for t in prior.tools.values():
        result = execute_tool(t.name, strict_json_loads(t.arguments))
        history.append(
            {"role": "tool", "tool_call_id": t.call_id, "content": str(result)}
            if case.protocol == "chat"
            else {
                "type": "function_call_output",
                "call_id": t.call_id,
                "output": str(result),
            }
        )
    if recipe.get("kind") == "user" and recipe.get("content") is not None:
        history.append({"role": "user", "content": recipe["content"]})
    expected.update(copy.deepcopy(recipe.get("request", {})))
    actual = copy.deepcopy(observations[index].request_payload)
    expected[key] = _normalize_tool_outputs(expected[key])
    actual[key] = _normalize_tool_outputs(actual.get(key, []))
    return expected == actual


def evaluate_workflow(case, observations, rules) -> CaseResult:
    if not observations:
        return CaseResult(
            case_id=case.id,
            endpoint="unbound",
            prompt_id=case.prompt_id,
            repetition=case.repetition,
            status="INCONCLUSIVE",
            assertions=[],
            metric_observations=[
                MetricObservation(
                    name="task_success",
                    scored=False,
                    reason="No observations; planned trial remains unscored",
                )
            ],
            attempt_refs=[],
        )
    assertions = []
    metrics = []

    def check(id, ok, reason, observed=None):
        assertions.append(
            AssertionResult(
                id=id,
                status="INCONCLUSIVE" if ok is None else "PASS" if ok else "FAIL",
                reason=reason,
                observed=observed,
                rule_ids=[r.id for r in rules],
                gating=bool(rules)
                and all(r.gating and r.maturity == "calibrated" for r in rules),
            )
        )

    error = False
    for index, obs in enumerate(observations):
        structural = case.model_copy(
            update={"steps": [{"kind": "continue"}], "oracle": {"kind": "structure"}}
        )
        result = _positive(structural, [obs], rules)
        assertions.extend(
            a.model_copy(update={"id": f"step.{index}.{a.id}"})
            for a in result.assertions
        )
        metrics.extend(result.metric_observations)
        error |= (
            result.status == "ERROR"
            or obs.status_code == 402
            or has_execution_error(obs)
        )
        if index >= len(case.steps):
            continue
        expectation = case.steps[index]["expect"]
        if "tools" in expectation:
            try:
                actual = Counter(
                    content_hash(
                        {"name": t.name, "arguments": strict_json_loads(t.arguments)}
                    )
                    for t in obs.tools.values()
                )
                wanted = Counter(content_hash(c) for c in expectation["tools"])
                valid = actual == wanted
            except (ValueError, RecursionError):
                valid = False
            check(
                f"step.{index}.EXACT_CALLS",
                valid,
                "Complete authored tool argument multiset required",
            )
        else:
            check(
                f"step.{index}.EXACT_ANSWER",
                not obs.tools and obs.text.strip() == expectation["text"],
                "Exact authored step answer without extra calls",
            )
        if index:
            try:
                valid = _parent_request(case, observations, index)
            except (ValueError, TypeError, KeyError, IndexError, RecursionError):
                valid = False
            check(
                f"step.{index}.PARENT_HISTORY",
                valid,
                "History, reasoning, matched call outputs and options must derive from the declared parent",
            )
    complete = len(observations) == len(case.steps)
    check(
        "WORKFLOW_COMPLETE",
        complete,
        "Every bounded recipe needs one response",
        {
            "planned_steps": len(case.steps),
            "observed_steps": len(observations),
            "tool_rounds": sum(bool(o.tools) for o in observations),
            "max_parallel_calls": max((len(o.tools) for o in observations), default=0),
        },
    )
    task_ok = complete and not error and all(a.status == "PASS" for a in assertions)
    metrics.append(
        MetricObservation(
            name="task_success",
            value=float(task_ok),
            scored=not error,
            reason="Workflow execution error" if error else None,
        )
    )
    if case.mode == "thinking":
        count = sum(bool(o.reasoning.strip()) for o in observations)
        check(
            "ACCUMULATED_REASONING",
            True if count >= 2 else None,
            "At least two nonempty reasoning rounds are needed to measure accumulated replay",
            count,
        )
    status = (
        "ERROR"
        if error
        else "FAIL"
        if any(a.status == "FAIL" for a in assertions)
        else "INCONCLUSIVE"
        if not assertions
        or any(a.status == "INCONCLUSIVE" for a in assertions)
        or not rules
        or any(r.maturity != "calibrated" for r in rules)
        else "PASS"
    )
    return CaseResult(
        case_id=case.id,
        endpoint=observations[0].endpoint if observations else "unbound",
        prompt_id=case.prompt_id,
        repetition=case.repetition,
        status=status,
        assertions=assertions,
        metric_observations=metrics,
        attempt_refs=[],
    )
