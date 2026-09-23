"""Independent semantic facets derived from verified protocol observations."""

import json
import re

from .acceptance_records import FAMILY_FACETS, Facet, case_family
from .assertions import rule_applies
from .catalog import content_hash
from .compatibility import _status_result
from .schema_cases import MAX_BYTES, _bounded_json, schema_matches
from .size_cases import _length_terminal, numbered_prefix, usage_measurement
from .workflows import _parent_request


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON member")
        result[key] = value
    return result


def _nonfinite(value):
    raise ValueError("Nonfinite JSON number")


def bounded_json(text, *, allow_fence=False):
    """One complete JSON value, optionally enclosed in one whole Markdown fence."""
    if len(text.encode()) > MAX_BYTES:
        raise ValueError("JSON byte limit exceeded")
    if allow_fence:
        match = re.fullmatch(r"\s*```(?:json)?\r?\n([\s\S]*?)\r?\n```\s*", text)
        if match:
            text = match[1]
    value = json.loads(
        text, object_pairs_hook=_unique_object, parse_constant=_nonfinite
    )
    _bounded_json(value)
    return value


def _status(statuses):
    return next((s for s in ("ERROR", "FAIL", "INCONCLUSIVE") if s in statuses), "PASS")


def _protocol(case, observations, rules):
    statuses = []
    special = case_family(case) in ("core", "contract") and (
        case.oracle.get("status_class")
        or case.oracle.get("status_class_by_mode")
        or case.oracle.get("compatibility")
        or case.template_id in ("C08", "C09")
    )
    if special:
        from .assertions import evaluate_case

        statuses.append(evaluate_case(case, observations, rules).status)
    optional = case_family(case) == "schema.optional"
    if not special and observations:
        structural = case.model_copy(
            update={
                "steps": [{"kind": "continue"}],
                "oracle": {
                    "kind": "structure",
                    "allow_truncation": case.oracle.get("kind") == "large_output",
                },
            }
        )
        # Ordinary replay assertions consume the whole trajectory. Branching
        # workflows check their declared parent edges in the functional facet.
        groups = (
            [[obs] for obs in observations]
            if case.oracle.get("kind") == "workflow"
            else [observations]
        )
        statuses.extend(
            _status_result(structural, group, rules, optional).status
            for group in groups
        )
    for obs in observations:
        for tool in obs.tools.values():
            try:
                if not isinstance(bounded_json(tool.arguments), dict):
                    raise TypeError("Arguments must be an object")
            except (ValueError, TypeError, RecursionError):
                statuses.append("FAIL")
    return _status(statuses) if statuses else "INCONCLUSIVE"


def _input(case, observations):
    history = len(observations) == len(case.steps) and not any(
        o.tools for o in observations
    )
    for index in range(1, len(observations)):
        try:
            history &= _parent_request(case, observations, index)
        except (KeyError, IndexError, ValueError, TypeError):
            history = False
    history &= all(o.text.strip() == "ack" for o in observations[:-1])
    final = observations[-1]
    payload = final.request_payload
    structured = (
        payload.get("response_format", {}).get("type", "text") != "text"
        or payload.get("text", {}).get("format", {}).get("type", "text") != "text"
    )
    result = {}
    for name, allow in [("retrieval", not structured), ("format", False)]:
        try:
            value = bounded_json(final.text, allow_fence=allow)
            valid = content_hash(value) == content_hash(case.oracle["expected_facts"])
        except (ValueError, TypeError, RecursionError):
            valid = False
        result[name] = "PASS" if valid and (history or name == "format") else "FAIL"
    return result


def _output(case, observations):
    final = observations[-1]
    valid, count = numbered_prefix(final.text, _length_terminal(final))
    valid &= count > 0 and len(observations) == 1 and not final.tools
    usage = usage_measurement(final, case.max_output_tokens)
    total = usage["output_tokens"]
    if not valid or usage["state"] == "invalid":
        budget = "FAIL"
    elif (
        total is not None
        and total >= 0.9 * case.max_output_tokens
        and _length_terminal(final)
    ):
        budget = "PASS"
    else:
        budget = "INCONCLUSIVE"
    return {
        "functional": "PASS" if valid else "FAIL",
        "budget": budget,
        "visible": "PASS"
        if usage["state"] == "valid"
        else "FAIL"
        if usage["state"] == "invalid"
        else "INCONCLUSIVE",
    }


def _schema(case, observations):
    final = observations[-1]
    if final.status_code in (400, 422):
        return "FAIL"  # Explicit rejection is not certified support, even if optional.
    try:
        if case.oracle["target"] == "tool":
            tools = list(final.tools.values())
            if len(tools) != 1 or tools[0].name != "record_schema_fixture":
                return "FAIL"
            value = bounded_json(tools[0].arguments)
        else:
            if final.tools:
                return "FAIL"
            value = bounded_json(final.text)
        return (
            "PASS"
            if (
                schema_matches(case.oracle["schema"], value)
                and content_hash(value) == content_hash(case.oracle["expected_value"])
            )
            else "FAIL"
        )
    except (ValueError, TypeError, RecursionError):
        return "FAIL"


REASONS = {
    "protocol": "HTTP status, response envelope, terminal events and original tool JSON must satisfy the selected protocol contract",
    "functional": "The authored functional control and its complete tool/history associations must succeed",
    "quality": "Exact instruction-following observation; original strict result retained",
    "schema": "Required shallow schema and authored value must match",
    "capability": "Advanced capability is certified only by a valid accepted response; rejection establishes no support",
    "retrieval": "Exact facts and complete history; plain-text output may use one whole outer JSON fence",
    "format": "Original output must be strict raw JSON with the expected facts",
    "budget": "Valid nonempty numbered output, explicit length terminal, and consistent reported total generation at least 90% of selected cap",
    "visible": "Visible token measurement requires a valid explicit reasoning-token breakdown; absent detail remains unknown",
    "raw_contract": "Unchanged canonical verdict, including all original strict assertions",
}


def derive_facets(manifest, run, observations):
    """Project evidence into explicit dimensions without rewriting canonical results."""
    cases = {c.id: c for c in manifest.cases}
    facets = []
    for result in run.case_results:
        case = cases[result.case_id]
        names = FAMILY_FACETS[case_family(case)]
        values = observations.get((result.endpoint, case.id), [])
        statuses = dict.fromkeys(names, result.status)
        statuses["raw_contract"] = result.status
        if case.oracle.get("applicable") is False:
            statuses = dict.fromkeys(names, "SKIP")
        elif not values:
            statuses = dict.fromkeys(names, "INCONCLUSIVE")
            statuses["raw_contract"] = result.status
        else:
            rules = [
                r
                for r in manifest.profile_snapshot.rules
                if r.id in case.rule_ids and rule_applies(r, case)
            ]
            statuses["protocol"] = _protocol(case, values, rules)
            if case_family(case) == "size.input":
                statuses.update(_input(case, values))
            elif case_family(case) == "size.output":
                statuses.update(_output(case, values))
            elif case_family(case).startswith("schema."):
                statuses[
                    "schema" if case_family(case) == "schema.core" else "capability"
                ] = _schema(case, values)
        for name in names:
            facets.append(
                Facet(
                    endpoint=result.endpoint,
                    case_id=case.id,
                    name=name,
                    status=statuses[name],
                    source_assertion_ids=[a.id for a in result.assertions],
                    evidence_hashes=result.attempt_refs,
                    reason=REASONS[name],
                )
            )
    return facets
