"""Deterministic byte-sized retrieval fixtures and measured generation boundaries."""

import hashlib

import httpx

from .compatibility import _status_result
from .json_utils import strict_json_loads
from .records import AssertionResult, MetricObservation
from .workflows import _parent_request


def _document(byte_count, seed, prefix, suffix):
    if (
        type(byte_count) is not int
        or type(seed) is not int
        or seed < 0
        or byte_count < 1024
    ):
        raise ValueError(
            "Size requires at least 1024 bytes and a nonnegative integer seed"
        )
    facts = {
        name: hashlib.sha256(f"{seed}:{name}".encode()).hexdigest()[:16]
        for name in ("begin", "middle", "end")
    }
    head = prefix + f"\nFACT begin={facts['begin']}\n"
    middle = f"\nFACT middle={facts['middle']}\n"
    tail = f"\nFACT end={facts['end']}\n" + suffix
    left = byte_count // 2 - len(head)
    right = byte_count - len(head) - left - len(middle) - len(tail)
    if min(left, right) < 0:
        raise ValueError("Requested size too small for complete fixture instructions")

    def filler(size, offset):
        lines = []
        length = 0
        i = offset
        while length < size:
            digest = hashlib.sha256(f"filler:{seed}:{i}".encode()).hexdigest()[:32]
            line = f"entry-{i:07d} value-{digest}\n"
            lines.append(line)
            length += len(line)
            i += 1
        return "".join(lines)[:size]

    return head + filler(left, 0) + middle + filler(right, 1000000) + tail, facts


def sized_text(byte_count: int, seed: int = 0) -> tuple[str, dict[str, str]]:
    return _document(
        byte_count,
        seed,
        "Read the data and retain the three FACT values. Ignore the filler entries.",
        "Return only a JSON object with keys begin, middle, end containing their FACT values.",
    )


def numbered_prefix(text: str, allow_partial: bool) -> tuple[bool, int]:
    pieces = text.split("\n")
    complete = pieces[:-1]
    for index, line in enumerate(complete, 1):
        if line != f"record-{index:06d}":
            return False, index - 1
    trailing = pieces[-1]
    if trailing and (
        not allow_partial
        or not f"record-{len(complete) + 1:06d}\n".startswith(trailing)
    ):
        return False, len(complete)
    return True, len(complete)


def size_template(descriptor, protocol):
    from .depth_catalog import depth_metadata
    from .records import CaseTemplate

    oracle = {"depth": depth_metadata(descriptor["family"])}
    if descriptor["shape"] == "output":
        cap = descriptor["output_tokens"]
        records = 2 * cap + 1
        steps = [
            {
                "kind": "user",
                "content": f"Write exactly {records} numbered records, one per line, starting record-000001 then record-000002 and continuing sequentially. Use six digits, end every record with a newline, and write no heading or explanation. Continue until all records are written.",
            }
        ]
        oracle.update(kind="large_output", requested_records=records)
    else:
        total = descriptor["input_bytes"]
        seed = descriptor["seed"]
        cap = 512
        if descriptor["shape"] == "single":
            content, facts = sized_text(total, seed)
            steps = [{"kind": "user", "content": content}]
        else:
            headers = [
                "Archive this data. Reply only ack; a later turn will request the facts.\n"
            ] * 3 + [
                "Read the final data, then return only a JSON object with keys begin, middle, end containing the remembered FACT values.\n"
            ]
            content, facts = _document(
                total - sum(len(h) for h in headers), seed, "", ""
            )
            # ASCII fixture data permits exact splitting without UTF-8 ambiguity.
            # Move cuts away from FACT lines to avoid splitting retrieval facts.
            cuts = [0]
            for i in range(1, 4):
                cut = len(content) * i // 4
                if content.rfind("\nFACT ", 0, cut) > content.rfind("\n", 0, cut - 1):
                    cut = content.index("\n", cut) + 1
                # At a middle FACT, keep its whole line in the following turn.
                begin = content.rfind("\n", 0, cut)
                if content[begin + 1 :].startswith("FACT "):
                    cut = begin + 1
                cuts.append(cut)
            cuts.append(len(content))
            steps = [
                {"kind": "user", "content": headers[i] + content[cuts[i] : cuts[i + 1]]}
                for i in range(4)
            ]
        oracle.update(
            kind="large_input",
            expected_facts=facts,
            authored_bytes=total,
            seed=seed,
            bounded_steps=True,
        )
    return CaseTemplate(
        id=descriptor["id"],
        prompt_id=descriptor["id"],
        dataset_version="depth-v1",
        protocol=protocol,
        modes=["non_thinking"],
        streams=[False, True],
        rule_ids=[f"{protocol}.size"],
        steps=steps,
        required=True,
        max_requests=len(steps),
        max_output_tokens={"non_thinking": cap},
        oracle=oracle,
    )


def usage_measurement(observation, cap):
    usage = observation.usage
    if not isinstance(usage, dict):
        return {
            "state": "missing",
            "input_tokens": None,
            "output_tokens": None,
            "reasoning_tokens": None,
            "visible_output_tokens": None,
        }
    chat = observation.protocol == "chat"
    ik, ok, dk = (
        ("prompt_tokens", "completion_tokens", "completion_tokens_details")
        if chat
        else ("input_tokens", "output_tokens", "output_tokens_details")
    )
    fields = [usage.get(ik), usage.get(ok), usage.get("total_tokens")]
    details = usage.get(dk, {})
    reasoning = details.get("reasoning_tokens") if isinstance(details, dict) else None
    missing_reasoning = isinstance(details, dict) and "reasoning_tokens" not in details
    valid_counts = all(type(n) is int and n >= 0 for n in fields)
    if valid_counts:
        incoming, outgoing, total = fields
        valid_counts = total == incoming + outgoing and outgoing <= cap
    valid = valid_counts and type(reasoning) is int and reasoning >= 0
    if valid:
        incoming, outgoing, total = fields
        valid = total == incoming + outgoing and reasoning <= outgoing <= cap
    return {
        "state": "valid"
        if valid
        else "missing"
        if valid_counts and missing_reasoning
        else "invalid",
        "input_tokens": fields[0] if type(fields[0]) is int else None,
        "output_tokens": fields[1] if type(fields[1]) is int else None,
        "reasoning_tokens": reasoning if type(reasoning) is int else None,
        "visible_output_tokens": fields[1] - reasoning if valid else None,
    }


def _length_terminal(obs):
    if obs.terminal_state != "incomplete":
        return False
    if obs.protocol == "chat":
        return bool(obs.finish_reasons) and all(
            v == "length" for v in obs.finish_reasons.values()
        )
    details = (obs.raw_response or {}).get("incomplete_details")
    return isinstance(details, dict) and details.get("reason") == "max_output_tokens"


def evaluate_size_case(case, observations, rules):
    from .depth_metadata import deployment_limits

    endpoint = observations[0].endpoint if observations else "unbound"
    claims = deployment_limits(rules, endpoint)
    output = case.oracle["kind"] == "large_output"
    within = (
        output
        and claims.get("output_tokens") is not None
        and case.max_output_tokens <= claims["output_tokens"]
    )
    structural = case.model_copy(
        update={"oracle": {"kind": "structure", "allow_truncation": output}}
    )
    result = _status_result(structural, observations, rules, not within)
    assertions = list(result.assertions)
    metrics = list(result.metric_observations)

    def check(id, status, reason, observed=None, gating=True):
        assertions.append(
            AssertionResult(
                id=id,
                status=status,
                reason=reason,
                observed=observed,
                gating=gating
                and bool(rules)
                and all(r.gating and r.maturity == "calibrated" for r in rules),
                rule_ids=[r.id for r in rules],
            )
        )

    usage = (
        usage_measurement(observations[-1], case.max_output_tokens)
        if observations
        else {
            "state": "missing",
            "input_tokens": None,
            "output_tokens": None,
            "reasoning_tokens": None,
            "visible_output_tokens": None,
        }
    )
    visible = usage["visible_output_tokens"]
    # Missing reasoning detail does not invalidate independently checked input counts.
    input_tokens = (
        usage["input_tokens"] if usage["state"] in ("valid", "missing") else None
    )
    payloads = [
        httpx.Request("POST", "http://fixture.invalid", json=o.request_payload).content
        for o in observations
    ]
    measured = {
        "authored_input_bytes": sum(
            len(s.get("content", "").encode()) for s in case.steps
        ),
        "request_payload_bytes": [len(p) for p in payloads],
        "request_payload_sha256": [hashlib.sha256(p).hexdigest() for p in payloads],
        "provider_usage": usage,
        "requested_output_tokens": case.max_output_tokens,
        "declared_context_tokens": claims.get("context_tokens"),
        "declared_output_tokens": claims.get("output_tokens"),
        "context_utilization": input_tokens / claims["context_tokens"]
        if input_tokens is not None and claims.get("context_tokens")
        else None,
        "requested_output_utilization": visible / case.max_output_tokens
        if visible is not None
        else None,
        "declared_output_utilization": visible / claims["output_tokens"]
        if visible is not None and claims.get("output_tokens")
        else None,
        "visible_complete_records": None,
        "visible_output_bytes": len(observations[-1].text.encode())
        if observations
        else 0,
        "terminal_state": observations[-1].terminal_state
        if observations
        else "missing",
        "length_terminal": _length_terminal(observations[-1])
        if observations
        else False,
        "retrieval_correct": {},
    }
    rejected = bool(observations) and observations[-1].status_code in (400, 422)
    if result.status == "ERROR" or rejected or not observations:
        status = (
            "ERROR"
            if result.status == "ERROR"
            else "FAIL"
            if within and rejected
            else "INCONCLUSIVE"
        )
        check(
            "SIZE_CAPABILITY",
            status,
            "Rejection only establishes a capacity observation unless within an explicitly declared output limit",
            "ERROR" if status == "ERROR" else "REJECTED" if rejected else "UNOBSERVED",
        )
    else:
        task_ok = len(observations) == len(case.steps) and not any(
            o.tools for o in observations
        )
        for i in range(1, len(observations)):
            try:
                task_ok &= _parent_request(case, observations, i)
            except (KeyError, IndexError, ValueError, TypeError):
                task_ok = False
        if output:
            prefix, count = numbered_prefix(
                observations[-1].text, _length_terminal(observations[-1])
            )
            measured["visible_complete_records"] = count
            task_ok &= prefix and count > 0
        else:
            task_ok &= all(o.text.strip() == "ack" for o in observations[:-1])
            try:
                value = strict_json_loads(observations[-1].text)
            except (ValueError, RecursionError):
                value = None
            measured["retrieval_correct"] = {
                k: isinstance(value, dict) and value.get(k) == v
                for k, v in case.oracle["expected_facts"].items()
            }
            task_ok &= (
                isinstance(value, dict) and value == case.oracle["expected_facts"]
            )
        check(
            "SIZE_TASK",
            "PASS" if task_ok else "FAIL",
            "Exact retrieval or ordered nonempty numbered prefix, complete history, and no unexpected tools required",
        )
        metrics.append(MetricObservation(name="task_success", value=float(task_ok)))
        if usage["state"] == "invalid":
            check(
                "SIZE_USAGE_CONSISTENCY",
                "FAIL",
                "Provider usage must be nonnegative, internally consistent, and within the requested cap",
            )
        if output:
            boundary = bool(
                task_ok
                and result.status == "PASS"
                and usage["state"] == "valid"
                and _length_terminal(observations[-1])
                and visible >= 0.9 * case.max_output_tokens
            )
            check(
                "OUTPUT_BOUNDARY",
                "PASS" if boundary else "INCONCLUSIVE",
                "Requested output cap requires a length-limit terminal, valid visible generation, and provider-reported visible usage of at least 90% of the cap",
                {"exercised": boundary},
            )
            metrics.append(
                MetricObservation(
                    name="requested_output_utilization",
                    value=measured["requested_output_utilization"],
                    scored=usage["state"] == "valid",
                )
            )
        status = (
            "FAIL"
            if result.status == "FAIL" or any(a.status == "FAIL" for a in assertions)
            else "INCONCLUSIVE"
            if result.status != "PASS"
            or any(a.status == "INCONCLUSIVE" for a in assertions)
            else "PASS"
        )
    check(
        "SIZE_MEASUREMENTS",
        "PASS",
        "Literal bytes, provider-reported usage, and explicit deployment claims; unknown utilization remains unavailable",
        measured,
        gating=False,
    )
    return result.model_copy(
        update={
            "status": status,
            "assertions": assertions,
            "metric_observations": metrics,
        }
    )
