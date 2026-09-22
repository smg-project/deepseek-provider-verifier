"""Opt-in functional compatibility and dated official-status comparisons.

A supported extension must satisfy the same positive oracle as a required
feature. Only explicit validation rejections (400/422) qualify as differences.
"""

from __future__ import annotations

import copy

from .assertions import _evaluate_standard, _mutation_exercised, has_execution_error
from .capture import Observation
from .records import AssertionResult, Case, CaseResult, Rule


def _positive(
    case: Case, observations: list[Observation], rules: list[Rule]
) -> CaseResult:
    oracle = {
        k: v
        for k, v in case.oracle.items()
        if k not in ("compatibility", "status_class", "status_class_by_mode")
    }
    if case.template_id in ("C08", "C09"):
        oracle["expected_arguments"] = {"key": "harbor"}
    result = _evaluate_standard(
        case.model_copy(update={"oracle": oracle}),
        observations,
        rules,
        enforce_official_tool_restriction=False,
    )

    if observations and any(
        not 200 <= (o.status_code or 0) < 300 for o in observations
    ):
        assertion = AssertionResult(
            id="SUCCESSFUL_HTTP_ACCEPTANCE",
            status="FAIL",
            reason="Accepted feature behavior requires successful 2xx HTTP responses",
            observed=[o.status_code for o in observations],
            rule_ids=[r.id for r in rules],
            gating=bool(rules)
            and all(r.gating and r.maturity == "calibrated" for r in rules),
        )
        result = result.model_copy(
            update={
                "assertions": [*result.assertions, assertion],
                "status": "FAIL" if result.status == "PASS" else result.status,
                "reason": result.reason or assertion.reason,
            }
        )
    unexpected = [
        t.name
        for o in observations
        for t in o.tools.values()
        if t.name
        not in {
            definition.get("function", definition).get("name")
            for definition in o.request_payload.get("tools", [])
            if isinstance(definition, dict)
        }
    ]
    if unexpected:
        assertion = AssertionResult(
            id="ADVERTISED_TOOL_ONLY",
            status="FAIL",
            reason="Accepted responses may call only functions advertised in that request",
            observed=unexpected,
            rule_ids=[r.id for r in rules],
            gating=bool(rules)
            and all(r.gating and r.maturity == "calibrated" for r in rules),
        )
        result = result.model_copy(
            update={
                "assertions": [*result.assertions, assertion],
                "status": "FAIL" if result.status == "PASS" else result.status,
                "reason": result.reason or assertion.reason,
            }
        )
    return result


def _status_result(
    case: Case,
    observations: list[Observation],
    rules: list[Rule],
    allow_rejection: bool,
) -> CaseResult:
    if any(o.status_code == 402 for o in observations):
        return CaseResult(
            case_id=case.id,
            endpoint=observations[0].endpoint,
            prompt_id=case.prompt_id,
            repetition=case.repetition,
            status="ERROR",
            reason="Payment or quota HTTP failure",
            assertions=[],
            metric_observations=[],
            attempt_refs=[],
        )
    if allow_rejection and observations and observations[-1].status_code in (400, 422):
        # Reuse standard transport/error-body/calibration checks. These boundary
        # probes explicitly permit both validation rejection and working support.
        rejected = case.model_copy(
            update={"steps": [{"kind": "continue"}], "oracle": {"status_class": 4}}
        )
        return _evaluate_standard(
            rejected, observations, rules, enforce_official_tool_restriction=False
        )
    return _positive(case, observations, rules)


def _pair(
    case: Case, observations: list[Observation], rules: list[Rule], spec: dict
) -> CaseResult:
    control = _positive(case, observations[:2], rules)
    if control.status != "PASS":
        return control
    exercised = (
        len(observations) == 3
        and bool(observations[0].reasoning.strip())
        and _mutation_exercised(
            case, observations[0], observations[2], "omit_reasoning"
        )
    )
    if exercised:
        expected = copy.deepcopy(observations[1].request_payload)
        key = "messages" if case.protocol == "chat" else "input"
        if case.protocol == "chat":
            for item in expected[key]:
                item.pop("reasoning_content", None)
        else:
            expected[key] = [i for i in expected[key] if i.get("type") != "reasoning"]
        exercised = expected == observations[2].request_payload
    prerequisite = AssertionResult(
        id="REASONING_PAIR_EXERCISED",
        status="PASS" if exercised else "FAIL",
        reason="Preserved and omitted continuations must reuse the same real tool setup and differ only in reasoning history",
        rule_ids=[r.id for r in rules],
        gating=all(r.gating for r in rules),
    )
    if not exercised:
        return control.model_copy(
            update={
                "status": "FAIL",
                "reason": prerequisite.reason,
                "assertions": [*control.assertions, prerequisite],
            }
        )
    # The omitted branch follows the setup, not the control's assistant answer.
    final_case = case.model_copy(
        update={"oracle": {**case.oracle, "continue_tools": False}}
    )
    final = _status_result(final_case, observations[2:], rules, spec["allow_rejection"])
    no_tools = AssertionResult(
        id="OMITTED_CONTINUATION_FINISHED",
        status="FAIL" if observations[2].tools else "PASS",
        reason="Omitted-history continuation must finish without another tool call",
        rule_ids=[r.id for r in rules],
        gating=all(r.gating for r in rules),
    )
    status = (
        "FAIL" if final.status == "PASS" and observations[2].tools else final.status
    )
    return final.model_copy(
        update={
            "status": status,
            "assertions": [
                *control.assertions,
                prerequisite,
                *final.assertions,
                no_tools,
            ],
            "metric_observations": [
                *(m for m in control.metric_observations if m.name != "task_success"),
                *final.metric_observations,
            ],
        }
    )


def evaluate_compatibility(
    case: Case, observations: list[Observation], rules: list[Rule], policy: str
) -> CaseResult:
    spec = case.oracle.get("compatibility") or {
        "feature": "forced_tool_choice",
        "allow_rejection": case.mode == "thinking",
        "reference_status": 400 if case.mode == "thinking" else 200,
        "reference_date": "2026-09-22",
    }
    if any(
        has_execution_error(o)
        or o.status_code in (401, 402, 403, 408, 429)
        or (o.status_code or 0) >= 500
        for o in observations
    ):
        return _status_result(case, observations, rules, False)
    paired = spec["feature"] == "reasoning_pair"
    result = (
        _pair(case, observations, rules, spec)
        if paired
        else _status_result(case, observations, rules, spec["allow_rejection"])
    )
    # Missing prerequisites, failed controls, or incomplete exchange never count
    # as an observed compatibility difference.
    if (
        not observations
        or result.status == "ERROR"
        or (
            paired
            and not any(
                a.id == "REASONING_PAIR_EXERCISED" and a.status == "PASS"
                for a in result.assertions
            )
        )
        or has_execution_error(observations[-1])
    ):
        return result
    status = observations[-1].status_code
    if status is None:
        return result
    matches = status == spec["reference_status"]
    basis = spec.get("reference_basis", "observed")
    observed = {
        "reference_basis": basis,
        "feature": spec["feature"],
        "policy": policy,
        "status_code": status,
        "reference_status": spec["reference_status"],
        "reference_date": spec["reference_date"],
        "matches_reference": matches,
    }
    assertions = [
        *result.assertions,
        AssertionResult(
            id="COMPATIBILITY_OBSERVATION",
            status="PASS",
            gating=False,
            observed=observed,
            reason=f"{'MATCH' if matches else 'DIFFERENCE'}: observed HTTP {status}; {basis} reference HTTP {spec['reference_status']} ({spec['reference_date']})",
            rule_ids=[r.id for r in rules],
        ),
    ]
    if policy == "official_parity" and basis == "observed":
        assertions.append(
            AssertionResult(
                id="OFFICIAL_BEHAVIOR_PARITY",
                status="PASS" if matches else "FAIL",
                observed=observed,
                reason="Strict parity requires the dated official HTTP status as well as valid accepted behavior",
                gating=bool(rules)
                and all(r.gating and r.maturity == "calibrated" for r in rules),
                rule_ids=[r.id for r in rules],
            )
        )
        if result.status == "PASS" and not matches:
            result = result.model_copy(
                update={"status": "FAIL", "reason": "Official behavior differs"}
            )
    return result.model_copy(update={"assertions": assertions})
