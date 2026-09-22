"""Pure, typed observable assertions; calibration never erases measurements."""

from __future__ import annotations

import itertools
from typing import Any

from jsonschema import Draft202012Validator

from .capture import Observation
from .json_utils import strict_json_loads
from .mock_tools import TOOL_SCHEMAS, execute_tool
from .records import AssertionResult, Case, CaseResult, MetricObservation, Rule


def rule_applies(rule: Rule, case: Case) -> bool:
    """Supported scope keys: case_ids, mode/modes, stream/streams, models, model_releases.

    Model and release scope are checked by the runner using operator-declared
    contract_model (falling back to model) and model_release metadata. Other condition keys are descriptive, never automatic gate expansion.
    """
    condition = rule.conditions
    ids = {
        case.template_id,
        *(i.split(".")[0] for i in case.attached_assertion_case_ids),
    }
    return (
        rule.protocol == case.protocol
        and (not condition.get("case_ids") or bool(ids & set(condition["case_ids"])))
        and condition.get("mode", "any") in ("any", case.mode)
        and ("modes" not in condition or case.mode in condition["modes"])
        and ("stream" not in condition or case.stream == condition["stream"])
        and ("streams" not in condition or case.stream in condition["streams"])
    )


def evaluate_case(
    case: Case, observations: list[Observation], rules: list[Rule]
) -> CaseResult:
    applicable = [r for r in rules if r.id in case.rule_ids and rule_applies(r, case)]
    assertions: list[AssertionResult] = []
    metric: list[MetricObservation] = []

    def check(
        id: str,
        ok: bool | None,
        reason: str,
        observed: Any = None,
        source: list[Rule] | None = None,
    ):
        linked = applicable if source is None else source
        assertions.append(
            AssertionResult(
                id=id,
                status="INCONCLUSIVE" if ok is None else "PASS" if ok else "FAIL",
                reason=reason,
                observed=observed,
                rule_ids=[r.id for r in linked],
                gating=bool(linked)
                and all(r.gating and r.maturity == "calibrated" for r in linked),
            )
        )

    endpoint = observations[0].endpoint if observations else "unbound"
    base = {
        "case_id": case.id,
        "endpoint": endpoint,
        "prompt_id": case.prompt_id,
        "repetition": case.repetition,
        "attempt_refs": [],
        "assertions": assertions,
        "metric_observations": metric,
    }

    def finish(status: str, reason: str | None = None) -> CaseResult:
        return CaseResult(
            **(base | {"assertions": assertions, "metric_observations": metric}),
            status=status,
            reason=reason,
        )

    if case.oracle.get("applicable") is False:
        check("APPLICABILITY", None, case.oracle["reason"])
        return finish("SKIP", case.oracle["reason"])
    if not observations:
        return finish("INCONCLUSIVE", "No observations; planned trial remains unscored")
    expected_status = case.oracle.get("status_class")
    restriction = (
        case.protocol == "chat"
        and case.mode == "thinking"
        and case.template_id in ("C08", "C09")
    )
    error_body_rules = [r for r in applicable if r.assertion_id == "error_body_json"]
    for o in observations:
        status_only_rejection = (
            o.status_code is not None
            and o.status_code >= 400
            and (expected_status or restriction)
            and not error_body_rules
        )
        if (
            o.transport_error
            and o.transport_error.get("type") == "INVALID_JSON"
            and not status_only_rejection
        ):
            check(
                "RESPONSE_BODY_FORMAT",
                False,
                "Fully received body is not valid response JSON",
                o.transport_error,
            )
    errors = [o.transport_error for o in observations if has_execution_error(o)]
    if errors:
        assertions.append(
            AssertionResult(
                id="TRANSPORT_OR_RUNNER_ERROR",
                status="ERROR",
                reason="Transport or assembly failed; partial output retained",
                observed=errors,
            )
        )
        return finish("ERROR", "Transport or runner failure")
    statuses = [o.status_code for o in observations if o.status_code is not None]
    if any(s in (401, 403, 408, 429) or s >= 500 for s in statuses):
        return finish(
            "ERROR", "Authentication, rate limit, or infrastructure HTTP failure"
        )
    if expected_status or restriction:
        mutation_steps = [
            (index, step["mutation"])
            for index, step in enumerate(case.steps)
            if step.get("mutation")
        ]
        if mutation_steps:
            target, mutation = mutation_steps[0]
            setup_ok = (
                target > 0
                and len(observations) > target
                and all(
                    o.status_code is not None
                    and 200 <= o.status_code < 300
                    and not o.transport_error
                    and not o.violations
                    and o.terminal_state == "completed"
                    for o in observations[:target]
                )
            )
            check(
                "NEGATIVE_PROBE_SETUP",
                setup_ok,
                "Negative continuation requires successful setup at the designated prior step",
            )
            mutated = setup_ok and _mutation_exercised(
                case, observations[target - 1], observations[target], mutation
            )
            check(
                "NEGATIVE_PROBE_MUTATION",
                mutated,
                "Required tool/reasoning prerequisites and the designated history mutation must be present",
            )
            rejected = (
                setup_ok
                and mutated
                and observations[target].status_code is not None
                and observations[target].status_code // 100 == expected_status
            )
        else:
            rejected = bool(statuses) and statuses[-1] // 100 == (expected_status or 4)
        check(
            "EXPECTED_HTTP_REJECTION",
            rejected,
            "Expected documented status class",
            statuses,
        )
    elif any(s >= 400 for s in statuses):
        check(
            "REQUIRED_PROTOCOL_SUPPORTED",
            False,
            "Required request or endpoint was rejected",
            statuses,
        )
        # Missing required support is a failure even before behavioral calibration.
        return finish(
            "FAIL" if case.required else "INCONCLUSIVE",
            "Required endpoint/feature rejected",
        )
    else:
        for o in observations:
            if isinstance(o.raw_response, dict) and not case.stream:
                raw = o.raw_response
                fields_ok = isinstance(raw.get("id"), str) and bool(raw.get("id"))
                if case.protocol == "chat":
                    fields_ok = (
                        fields_ok
                        and raw.get("object") == "chat.completion"
                        and isinstance(raw.get("model"), str)
                        and type(raw.get("created")) is int
                    )
                    fields_ok = fields_ok and all(
                        isinstance(choice.get("message"), dict)
                        and choice["message"].get("role") == "assistant"
                        for choice in raw.get("choices", [])
                        if isinstance(choice, dict)
                    )
                else:
                    fields_ok = fields_ok and raw.get("object") == "response"
                check(
                    "RESPONSE_ENVELOPE",
                    fields_ok,
                    "Required response identity and typed envelope fields must be present",
                )
            for violation in o.violations:
                check(
                    violation,
                    False,
                    "Protocol structural violation",
                    [s.model_dump(mode="json") for s in o.evidence.get(violation, [])],
                )
            check(
                "TERMINAL_STATE",
                o.terminal_state in ("completed", "incomplete")
                if case.oracle.get("allow_truncation")
                or case.oracle.get("kind") in ("json_object", "schema")
                else o.terminal_state == "completed",
                "Completed protocol terminal required",
                o.terminal_state,
            )
            if case.mode == "thinking":
                check(
                    "REASONING_CHANNEL_SEPARATION",
                    "<think>" not in o.text and "</think>" not in o.text,
                    "Reasoning markers must not leak into visible content",
                )
            call_ids = [t.call_id for t in o.tools.values()]
            if call_ids:
                check(
                    "TOOL_CALL_IDS",
                    all(call_ids) and len(call_ids) == len(set(call_ids)),
                    "Tool call IDs must be present and distinct",
                    call_ids,
                )
            for t in o.tools.values():
                if case.oracle.get("continue_tools"):
                    check(
                        "TOOL_NOT_REGISTERED",
                        t.name in TOOL_SCHEMAS,
                        "Only registered deterministic mock functions may execute",
                        t.name,
                    )
                try:
                    args = strict_json_loads(t.arguments)
                except (ValueError, RecursionError):
                    check(
                        "TOOL_ARGUMENTS_INVALID_JSON",
                        False,
                        "Arguments must be valid original JSON; never repaired",
                    )
                    metric.append(
                        MetricObservation(name="tool_argument_schema", value=0.0)
                    )
                    continue
                object_arguments = isinstance(args, dict)
                check(
                    "TOOL_ARGUMENTS_OBJECT",
                    object_arguments,
                    "Function arguments must be an object",
                )
                schema = case.oracle.get("argument_schema", TOOL_SCHEMAS.get(t.name))
                if schema:
                    schema_valid = object_arguments and Draft202012Validator(
                        schema
                    ).is_valid(args)
                    check(
                        "TOOL_ARGUMENTS_SCHEMA",
                        schema_valid,
                        "Arguments satisfy advertised schema",
                        args,
                    )
                    metric.append(
                        MetricObservation(
                            name="tool_argument_schema", value=float(schema_valid)
                        )
                    )
                else:
                    metric.append(
                        MetricObservation(
                            name="tool_argument_schema",
                            value=None,
                            denominator=0,
                            scored=False,
                            reason="No declared argument schema for emitted tool",
                        )
                    )
                if "expected_arguments" in case.oracle:
                    check(
                        "TOOL_ARGUMENTS_VALUES",
                        args == case.oracle["expected_arguments"],
                        "Arguments match authored fixture",
                        args,
                    )
        _check_history(case, observations, check)
        if case.oracle.get("continue_tools"):
            exercised = (
                any(
                    _tool_exchange(case, previous, current)
                    for previous, current in itertools.pairwise(observations)
                )
                and not observations[-1].tools
            )
            check(
                "TOOL_CONTINUATION_EXERCISED",
                exercised,
                "A valid intended tool call, matching result, and subsequent assistant response must be observed",
            )
        final = observations[-1]
        tools = [t for o in observations for t in o.tools.values()]
        kind = case.oracle.get("kind")
        if kind in ("exact_text", "conversation", "consistency"):
            expected = case.oracle.get("value")
            success = final.text.strip() == expected if expected is not None else None
            check(
                "TASK_SUCCESS",
                success,
                "Completed answer matches the original fixture",
                final.text,
            )
            metric.append(
                MetricObservation(
                    name="task_success",
                    value=float(success) if success is not None else None,
                    scored=success is not None,
                )
            )
        elif kind in ("json_object", "schema"):
            try:
                value = strict_json_loads(final.text)
                valid = isinstance(value, dict) and Draft202012Validator(
                    case.oracle.get("schema", {"type": "object"})
                ).is_valid(value)
            except (ValueError, RecursionError):
                valid = False
            check(
                "OUTPUT_SCHEMA",
                None if final.terminal_state == "incomplete" else valid,
                "Completed output must satisfy the declared JSON schema",
            )
            metric.append(
                MetricObservation(
                    name="schema_validity",
                    value=float(valid),
                    scored=final.terminal_state != "incomplete",
                )
            )
        elif kind == "tool_forbidden":
            check(
                "TOOL_PROHIBITION",
                not tools,
                "No tool calls when tool use is forbidden",
            )
        elif kind in ("tool_required", "named_tool", "nested_tool", "multiple_tools"):
            check(
                "TOOL_REQUIRED",
                len(tools) >= case.oracle.get("min_calls", 1),
                "Required number of tool calls observed",
            )
            if case.oracle.get("name"):
                check(
                    "TOOL_NAME",
                    bool(tools) and all(t.name == case.oracle["name"] for t in tools),
                    "Selected advertised function name",
                )
        elif kind == "ambiguous":
            check(
                "AMBIGUOUS_TRIGGER",
                None,
                "Tool choice is intentionally ambiguous; report trigger without a forced label",
                bool(tools),
            )
        elif kind == "accepted_option":
            check(
                "OPTION_ACCEPTANCE",
                True,
                "Request accepted; behavioral effect has not been demonstrated",
            )
            check(
                "OPTION_EFFECT",
                None,
                "Acceptance alone does not demonstrate an option's effect",
            )
        if kind in (
            "tool_required",
            "named_tool",
            "nested_tool",
            "multiple_tools",
            "tool_forbidden",
            "ambiguous",
        ):
            expected_trigger = None if kind == "ambiguous" else kind != "tool_forbidden"
            metric.extend(
                [
                    MetricObservation(name="tool_trigger", value=float(bool(tools))),
                    MetricObservation(
                        name="expected_tool_trigger",
                        value=None
                        if expected_trigger is None
                        else float(expected_trigger),
                        scored=expected_trigger is not None,
                    ),
                ]
            )
        if kind not in {
            "exact_text",
            "conversation",
            "consistency",
            "json_object",
            "schema",
            "tool_forbidden",
            "tool_required",
            "named_tool",
            "nested_tool",
            "multiple_tools",
            "ambiguous",
            "accepted_option",
            "structure",
        }:
            check("UNKNOWN_ORACLE", None, "No evaluator is registered for this oracle")
        for r in applicable:
            aid = r.assertion_id
            if "output_present" in aid:
                check(
                    aid,
                    any(o.text or o.reasoning or o.tools for o in observations),
                    r.expectation,
                    source=[r],
                )
            elif "thinking_disabled" in aid:
                check(
                    aid,
                    all(
                        not o.reasoning
                        and "<think>" not in o.text
                        and "</think>" not in o.text
                        for o in observations
                    ),
                    r.expectation,
                    source=[r],
                )
            elif "stream_terminal" in aid:
                check(
                    aid,
                    all(o.terminal_state == "completed" for o in observations),
                    r.expectation,
                    source=[r],
                )
            elif "usage" in aid:
                check(
                    aid,
                    all(_valid_usage(o.usage, case.protocol) for o in observations),
                    r.expectation,
                    source=[r],
                )
                if "placement" in aid:
                    check(
                        "USAGE_FINAL_CONTENT",
                        all(_chat_usage_placement(o) for o in observations),
                        r.expectation,
                        source=[r],
                    )
            elif aid == "responses_stateless_replay":
                check(
                    aid,
                    len(observations) > 1
                    and all(
                        o.request_payload.get("store") is False for o in observations
                    ),
                    r.expectation,
                    source=[r],
                )
            elif aid == "responses_ignored_options":
                check(
                    aid,
                    None,
                    "Documented ignored options cannot be verified from HTTP acceptance",
                    source=[r],
                )
            elif aid not in ("case_oracle", "chat_thinking_tool_choice_rejected"):
                check(aid, None, "No evaluator is registered for this rule", source=[r])
    if not applicable or any(r.maturity != "calibrated" for r in applicable):
        return finish(
            "INCONCLUSIVE",
            "Applicable rules require calibration; observed assertions retained",
        )
    if any(a.status == "FAIL" for a in assertions):
        return finish("FAIL", "Observable assertion failed")
    if any(a.status == "INCONCLUSIVE" for a in assertions) or not assertions:
        return finish("INCONCLUSIVE", "Insufficient observable evidence")
    return finish("PASS")


def _valid_usage(usage: Any, protocol: str) -> bool:
    fields = (
        ("prompt_tokens", "completion_tokens", "total_tokens")
        if protocol == "chat"
        else ("input_tokens", "output_tokens", "total_tokens")
    )
    if not isinstance(usage, dict) or not all(
        type(usage.get(k)) is int and usage[k] >= 0 for k in fields
    ):
        return False
    if usage[fields[0]] + usage[fields[1]] != usage["total_tokens"]:
        return False
    details = usage.get(
        "output_tokens_details", usage.get("completion_tokens_details", {})
    )
    return isinstance(details, dict) and all(
        type(v) is int and v >= 0 for v in details.values()
    )


def _chat_usage_placement(o: Observation) -> bool:
    objects = [v for v in o.raw_objects if isinstance(v, dict) and v.get("choices")]
    return bool(objects) and isinstance(objects[-1].get("usage"), dict)


def _check_history(case: Case, observations: list[Observation], check) -> None:
    for previous, current in itertools.pairwise(observations):
        history = current.request_payload.get(
            "messages" if case.protocol == "chat" else "input", []
        )
        if not isinstance(history, list):
            check("HISTORY_REPLAY", False, "Continuation history must be an array")
            continue
        originals = (
            previous.assistant_messages
            if case.protocol == "chat"
            else (previous.raw_response or {}).get("output", [])
        )
        check(
            "HISTORY_REPLAY",
            bool(originals) and all(item in history for item in originals),
            "Original assistant items including reasoning must be replayed unchanged",
        )
        if case.protocol == "chat":
            ids = [t.get("id") for m in originals for t in m.get("tool_calls", [])]
            results = [
                m.get("tool_call_id")
                for m in history
                if isinstance(m, dict) and m.get("role") == "tool"
            ]
        else:
            ids = [
                m.get("call_id") for m in originals if m.get("type") == "function_call"
            ]
            results = [
                m.get("call_id")
                for m in history
                if isinstance(m, dict) and m.get("type") == "function_call_output"
            ]
        check(
            "TOOL_RESULT_PAIRING",
            all(i in results for i in ids),
            "Tool results must reference original call IDs",
        )


def has_execution_error(observation: Observation) -> bool:
    """A proven body-syntax defect is measured output, not interrupted execution.

    Resource/depth limits and assembler failures are intentionally not classified
    as syntax errors. Original decoding metadata remains in observation/capture.
    """
    return bool(
        observation.transport_error
        and observation.transport_error.get("type") != "INVALID_JSON"
    )


def _assistant_items(case: Case, observation: Observation) -> list[dict]:
    if case.protocol == "chat":
        return observation.assistant_messages
    raw = observation.raw_response
    return raw.get("output", []) if isinstance(raw, dict) else []


def _tool_outputs(case: Case, observation: Observation) -> list[dict] | None:
    """Recompute registered fixture results; never dispatch an arbitrary function."""
    if not observation.tools or observation.violations:
        return None
    outputs, ids = [], set()
    advertised = {
        definition.get("function", definition).get("name")
        for step in case.steps
        for definition in step.get("request", {}).get("tools", [])
        if isinstance(definition, dict)
    }
    for call in observation.tools.values():
        if (
            not call.complete
            or not call.call_id
            or call.call_id in ids
            or (advertised and call.name not in advertised)
        ):
            return None
        ids.add(call.call_id)
        try:
            arguments = strict_json_loads(call.arguments)
            if (
                "expected_arguments" in case.oracle
                and arguments != case.oracle["expected_arguments"]
            ):
                return None
            result = str(execute_tool(call.name, arguments))
        except (ValueError, TypeError, RecursionError):
            return None
        outputs.append(
            {"role": "tool", "tool_call_id": call.call_id, "content": result}
            if case.protocol == "chat"
            else {
                "type": "function_call_output",
                "call_id": call.call_id,
                "output": result,
            }
        )
    return outputs


def _tool_exchange(case: Case, previous: Observation, current: Observation) -> bool:
    outputs = _tool_outputs(case, previous)
    history = current.request_payload.get(
        "messages" if case.protocol == "chat" else "input", []
    )
    originals = _assistant_items(case, previous)
    return bool(
        outputs
        and originals
        and isinstance(history, list)
        and all(item in history for item in [*originals, *outputs])
        and _assistant_items(case, current)
        and current.terminal_state == "completed"
    )


def _mutation_exercised(
    case: Case, previous: Observation, current: Observation, mutation: str
) -> bool:
    outputs = _tool_outputs(case, previous)
    originals = _assistant_items(case, previous)
    history = current.request_payload.get(
        "messages" if case.protocol == "chat" else "input", []
    )
    if not outputs or not originals or not isinstance(history, list):
        return False
    if mutation == "omit_reasoning":
        replay = [
            {k: v for k, v in item.items() if k != "reasoning_content"}
            for item in originals
            if item.get("type") != "reasoning"
        ]
        prerequisite = replay != originals
    elif mutation == "wrong_call_id":
        replay = originals
        key = "tool_call_id" if case.protocol == "chat" else "call_id"
        outputs = [item | {key: "intentionally-unmatched"} for item in outputs]
        prerequisite = True
    else:
        return False
    return prerequisite and all(item in history for item in [*replay, *outputs])
