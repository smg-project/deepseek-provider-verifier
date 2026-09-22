"""Bounded offline-testable execution with explicit retries and durable evidence."""

from __future__ import annotations

import asyncio
import copy
import re
import time
from collections import Counter
from pathlib import Path

import httpx

from .assertions import evaluate_case, has_execution_error, rule_applies
from .capture import AttemptPayload, Observation, redact
from .catalog import content_hash
from .evidence import (
    append_record,
    atomic_json,
    checkpoint_artifacts,
    load_resume_state,
    validate_checkpoint,
)
from .json_utils import strict_json_loads
from .mock_tools import execute_tool
from .protocols.chat import assemble_chat, assemble_chat_json
from .protocols.responses import assemble_responses, assemble_responses_json
from .records import (
    Attempt,
    AttemptMetric,
    Case,
    CaseResult,
    Manifest,
    MetricObservation,
    ResumeState,
    RunResult,
)
from .sse import decode_sse
from .transport import send_request


def _comparable(manifest: Manifest) -> dict:
    return manifest.model_dump(
        mode="json",
        exclude={
            "schema_version",
            "profile_snapshot",
            "run_id",
            "created_at",
            "manifest_hash",
        },
    )


def rehash_manifest(manifest: Manifest) -> Manifest:
    """Recompute comparable hashes after explicit offline workload/profile edits.

    This does not validate budgets. Execution always validates again before I/O.
    """
    if manifest.profile_snapshot is None:
        raise ValueError("Manifest requires profile snapshot")
    value = manifest.model_copy(
        update={
            "profile_hash": content_hash(
                manifest.profile_snapshot.model_dump(mode="json")
            ),
            "dataset_hash": content_hash(
                [c.model_dump(mode="json") for c in manifest.cases]
            ),
        }
    )
    return value.model_copy(update={"manifest_hash": content_hash(_comparable(value))})


def validate_manifest(manifest: Manifest) -> None:
    # model_copy bypasses validation; revalidate all nested records at the boundary.
    Manifest.model_validate(manifest.model_dump(mode="json"))
    expected = rehash_manifest(manifest)
    if any(
        getattr(expected, field) != getattr(manifest, field)
        for field in ("profile_hash", "dataset_hash", "manifest_hash")
    ):
        raise ValueError("Manifest/profile/dataset hash mismatch")
    b = manifest.budgets
    profile = manifest.profile_snapshot
    preset = profile.presets[b.suite]
    for key in (
        "max_requests_per_protocol",
        "max_requests_per_endpoint",
        "max_total_requests",
        "max_requests_per_conversation",
        "max_retries",
        "case_deadline_seconds",
        "mode_max_output_tokens",
        "case_ids",
        "attached_assertion_case_ids",
    ):
        if getattr(b, key) != getattr(preset, key):
            raise ValueError(f"Manifest budget differs from profile: {key}")
    if (
        b.max_concurrency != preset.concurrency
        or b.concurrency > b.max_concurrency
        or b.retries > b.max_retries
    ):
        raise ValueError("Manifest concurrency/retry budget invalid")
    if len({c.id for c in manifest.cases}) != len(manifest.cases):
        raise ValueError("Duplicate trial IDs")
    if any(name != endpoint.name for name, endpoint in manifest.endpoints.items()):
        raise ValueError("Endpoint identity mismatch")
    rules = {r.id: r for r in profile.rules}
    counts = Counter()
    tokens = 0
    for c in manifest.cases:
        if (
            len(c.steps) > c.max_requests
            or c.max_requests > b.max_requests_per_conversation
            or c.protocol not in b.protocols
            or c.max_output_tokens > b.mode_max_output_tokens.get(c.mode, 0)
            or c.repetition >= b.repetitions
        ):
            raise ValueError("Case exceeds declared workload bounds")
        if any(
            rid not in rules or rules[rid].protocol != c.protocol for rid in c.rule_ids
        ):
            raise ValueError("Case references unknown/mismatched rule")
        counts[c.protocol] += c.max_requests * (b.retries + 1)
        tokens += c.max_output_tokens * c.max_requests * (b.retries + 1)
    requests = sum(counts.values()) * len(manifest.endpoints)
    if (
        requests != manifest.request_ceiling
        or tokens * len(manifest.endpoints) != manifest.output_token_ceiling
        or any(v > b.max_requests_per_protocol for v in counts.values())
        or sum(counts.values())
        > min(b.max_requests_per_endpoint, b.max_attempts_per_endpoint)
        or requests > b.max_total_requests
    ):
        raise ValueError("Manifest request/token ceiling or budget mismatch")
    gates = sorted(
        {
            rules[rid].assertion_id
            for c in manifest.cases
            if c.required
            for rid in c.rule_ids
            if rules[rid].gating and rule_applies(rules[rid], c)
        }
    )
    if gates != manifest.gates:
        raise ValueError("Manifest gate mismatch")


def _request(case: Case, endpoint, recipe: dict, history: list, options: dict) -> dict:
    content = recipe.get("content")
    if recipe.get("kind") == "user" and content is not None:
        history.append({"role": "user", "content": content})
    options.update(copy.deepcopy(recipe.get("request", {})))
    key = "messages" if case.protocol == "chat" else "input"
    payload = {key: copy.deepcopy(history), **copy.deepcopy(options)}
    # Explicit malformed input recipes are allowed; execution identity/caps are not.
    payload.update(model=endpoint.model, stream=case.stream)
    if case.protocol == "chat":
        payload.update(
            max_tokens=case.max_output_tokens,
            thinking={"type": "enabled" if case.mode == "thinking" else "disabled"},
        )
        payload.pop("max_completion_tokens", None)
        payload.pop("max_output_tokens", None)
        if case.mode == "thinking":
            payload["reasoning_effort"] = "high"
        else:
            payload.pop("reasoning_effort", None)
    else:
        payload.update(
            max_output_tokens=case.max_output_tokens,
            reasoning={"effort": "high" if case.mode == "thinking" else "none"},
            store=False,
        )
        payload.pop("max_tokens", None)
        payload.pop("max_completion_tokens", None)
    return payload


def _replay(
    case: Case,
    observation: Observation,
    history: list,
    execute: bool,
    mutation: str | None,
) -> None:
    items = (
        observation.assistant_messages
        if case.protocol == "chat"
        else (observation.raw_response or {}).get("output", [])
    )
    if not isinstance(items, list) or not items:
        raise ValueError("Original assistant items unavailable for replay")
    replay = copy.deepcopy(items)
    if mutation == "omit_reasoning":
        for item in replay:
            item.pop("reasoning_content", None)
        replay = [item for item in replay if item.get("type") != "reasoning"]
        if replay == items:
            raise ValueError("No returned reasoning exists for the negative mutation")
    history.extend(replay)
    if execute:
        for tool in observation.tools.values():
            if observation.violations or not tool.complete or not tool.call_id:
                raise ValueError("Invalid tool call cannot be executed")
            arguments = strict_json_loads(tool.arguments)
            result = execute_tool(tool.name, arguments)
            call_id = (
                "intentionally-unmatched"
                if mutation == "wrong_call_id"
                else tool.call_id
            )
            history.append(
                {"role": "tool", "tool_call_id": call_id, "content": str(result)}
                if case.protocol == "chat"
                else {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": str(result),
                }
            )


def _assemble(case: Case, capture: AttemptPayload) -> tuple[Observation, list]:
    events = []
    try:
        if (
            case.stream
            and capture.status_code is not None
            and 200 <= capture.status_code < 300
        ):
            events = decode_sse(capture.raw_chunks)
            obs = (assemble_chat if case.protocol == "chat" else assemble_responses)(
                events
            )
        else:
            obs = (
                assemble_chat_json
                if case.protocol == "chat"
                else assemble_responses_json
            )(capture.raw_json)
    except Exception as exc:  # noqa: BLE001 -- evidence boundary must retain unexpected failures
        # Keep a successful HTTP capture even when assembly itself fails.
        obs = Observation(protocol=case.protocol)
        capture.error = capture.error or {
            "type": "ASSEMBLY_ERROR",
            "exception": type(exc).__name__,
        }
    obs.status_code = capture.status_code
    obs.transport_error = capture.error
    return obs, events


def _unstarted(case: Case, endpoint: str, reason: str) -> CaseResult:
    return CaseResult(
        case_id=case.id,
        endpoint=endpoint,
        prompt_id=case.prompt_id,
        repetition=case.repetition,
        status="INCONCLUSIVE",
        assertions=[],
        metric_observations=[
            MetricObservation(name="task_success", scored=False, reason=reason),
            MetricObservation(name="available", value=0),
        ],
        attempt_refs=[],
        reason=reason,
        completed=False,
    )


def _attempt_metric(attempt: Attempt) -> AttemptMetric:
    return AttemptMetric(
        endpoint=attempt.endpoint,
        case_id=attempt.case_id,
        prompt_id=attempt.prompt_id,
        repetition=attempt.repetition,
        step=attempt.step,
        retry=attempt.retry,
        attempt_number=attempt.attempt_number,
        status_code=attempt.status_code,
        timings=attempt.timings,
        http_exchange_completed=attempt.http_exchange_completed,
        error_type=attempt.error.get("type") if attempt.error else None,
    )


def _reservation_metric(record: dict, cases: dict[str, Case]) -> AttemptMetric:
    case = cases[record["case_id"]]
    return AttemptMetric(
        endpoint=record["endpoint"],
        case_id=case.id,
        prompt_id=record.get("prompt_id", case.prompt_id),
        repetition=record.get("repetition", case.repetition),
        step=record.get("step", 0),
        retry=record.get("retry", 0),
        attempt_number=record["attempt_number"],
        http_exchange_completed=False,
        error_type="INTERRUPTED_RESERVATION",
        interrupted_reservation=True,
    )


async def execute_manifest(
    manifest: Manifest,
    clients: dict[str, httpx.AsyncClient],
    secrets: dict[str, str],
    *,
    output_dir: Path | None = None,
    resume: bool = False,
) -> RunResult:
    """Execute planned endpoint/case trials, serially within the concurrency cap.

    Clients remain caller-owned. ``sequential`` is endpoint-major; ``paired`` is
    case-major. No retries on successful malformed/incorrect model responses.
    Retry policy explicitly permits eventual recovery from transient HTTP/IO
    errors; first-attempt outcomes and timing records remain available.
    """
    validate_manifest(manifest)
    if set(clients) != set(manifest.endpoints):
        raise ValueError("One client per selected endpoint is required")
    for name, endpoint in manifest.endpoints.items():
        if not endpoint.auth_none and not secrets.get(name):
            raise ValueError("Missing endpoint credential")
    # Credentials cannot enter a workload hash through user-authored metadata.
    serialized = manifest.model_dump_json()
    if any(secret and secret in serialized for secret in secrets.values()):
        raise ValueError("Credential value occurs in manifest metadata")
    state = ResumeState(manifest_hash=manifest.manifest_hash)
    out = Path(output_dir) if output_dir else None
    if resume and out is None:
        raise ValueError("Resume requires an evidence directory")
    if out:
        out.mkdir(parents=True, exist_ok=True)
        if resume:
            saved = Manifest.model_validate_json((out / "manifest.json").read_text())
            validate_manifest(saved)
            if saved.manifest_hash != manifest.manifest_hash:
                raise ValueError("Resume manifest hash mismatch")
            validate_checkpoint(out)
            state = load_resume_state(out, manifest.manifest_hash)
        else:
            if any(
                (out / filename).exists()
                for filename in ("manifest.json", "attempts.jsonl", "results.jsonl")
            ):
                raise ValueError(
                    "Evidence exists; explicitly resume or select a new directory"
                )
            atomic_json(out / "manifest.json", manifest.model_dump(mode="json"))
            for filename in ("attempts.jsonl", "results.jsonl"):
                append_record(
                    out / filename,
                    {"kind": "manifest", "manifest_hash": manifest.manifest_hash},
                )
    usage = Counter({name: 0 for name in manifest.endpoints})
    cases_by_id = {case.id: case for case in manifest.cases}
    attempt_metrics = [_attempt_metric(attempt) for attempt in state.prior_attempts]
    protocol_usage = Counter()
    numbers = Counter()
    for a in state.prior_attempts:
        usage[a.endpoint] += 1
        c = next(c for c in manifest.cases if c.id == a.case_id)
        protocol_usage[(a.endpoint, c.protocol)] += 1
        numbers[(a.endpoint, a.case_id)] = max(
            numbers[(a.endpoint, a.case_id)], a.attempt_number
        )
    for pending in state.incomplete_records:
        if pending.get("disposition") == "interrupted_attempt":
            usage[pending["endpoint"]] += 1
            protocol_usage[(pending["endpoint"], pending["protocol"])] += 1
            numbers[(pending["endpoint"], pending["case_id"])] = max(
                numbers[(pending["endpoint"], pending["case_id"])],
                pending["attempt_number"],
            )
            attempt_metrics.append(_reservation_metric(pending, cases_by_id))
    results = {(r.endpoint, r.case_id): r for r in state.results if r.completed}
    jobs = (
        [(name, case) for name in manifest.endpoints for case in manifest.cases]
        if manifest.budgets.execution_order == "sequential"
        else [(name, case) for case in manifest.cases for name in manifest.endpoints]
    )
    cancelled = False
    for name, case in jobs:
        key = (name, case.id)
        if key in results:
            continue
        if cancelled:
            result = _unstarted(case, name, "RUN_CANCELLED")
        elif case.oracle.get("applicable") is False:
            result = evaluate_case(
                case, [], manifest.profile_snapshot.rules
            ).model_copy(update={"endpoint": name})
        else:
            endpoint = manifest.endpoints[name]
            rules = [
                r.model_copy(update={"maturity": "diagnostic", "gating": False})
                if (
                    r.conditions.get("models")
                    and (endpoint.contract_model or endpoint.model)
                    not in r.conditions["models"]
                )
                or (
                    r.conditions.get("model_releases")
                    and endpoint.model_release not in r.conditions["model_releases"]
                )
                or (
                    "calibrated_variants" in r.conditions
                    and re.sub(r"\.r[0-9]+$", "", case.id)
                    not in r.conditions["calibrated_variants"]
                )
                else r
                for r in manifest.profile_snapshot.rules
            ]
            observations, first_observations = [], []
            refs = [
                a.evidence_hash
                for a in state.prior_attempts
                if (a.endpoint, a.case_id) == key
            ]
            history, options = [], {}
            recipe_options = []
            deadline = time.monotonic() + manifest.budgets.case_deadline_seconds
            reason = None
            step = 0
            conversation_requests = 0
            recipe_index = 0
            while True:
                recipe = (
                    case.steps[recipe_index]
                    if recipe_index < len(case.steps)
                    else {"kind": "continue"}
                )
                payload = _request(case, endpoint, recipe, history, options)
                recipe_options.append(copy.deepcopy(options))
                for retry in range(manifest.budgets.retries + 1):
                    b = manifest.budgets
                    if (
                        usage[name]
                        >= min(b.max_attempts_per_endpoint, b.max_requests_per_endpoint)
                        or sum(usage.values())
                        >= min(b.max_total_requests, manifest.request_ceiling)
                        or protocol_usage[(name, case.protocol)]
                        >= b.max_requests_per_protocol
                    ):
                        reason = "ATTEMPT_BUDGET"
                        break
                    if conversation_requests >= b.max_requests_per_conversation:
                        reason = "CONVERSATION_LIMIT"
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        reason = "CASE_DEADLINE"
                        break
                    # Debit before the request, including cancellation and retries.
                    usage[name] += 1
                    protocol_usage[(name, case.protocol)] += 1
                    numbers[key] += 1
                    conversation_requests += 1
                    safe_payload = redact(payload, secrets.get(name))
                    if out:
                        append_record(
                            out / "attempts.jsonl",
                            {
                                "kind": "attempt_start",
                                "endpoint": name,
                                "protocol": case.protocol,
                                "case_id": case.id,
                                "prompt_id": case.prompt_id,
                                "repetition": case.repetition,
                                "step": step,
                                "retry": retry,
                                "attempt_number": numbers[key],
                                "request_hash": content_hash(safe_payload),
                            },
                        )
                    try:
                        async with asyncio.timeout(remaining):
                            capture = await send_request(
                                clients[name],
                                endpoint,
                                "chat/completions"
                                if case.protocol == "chat"
                                else "responses",
                                payload,
                                secrets.get(name),
                            )
                    except (TimeoutError, asyncio.CancelledError) as exc:
                        reason = (
                            "RUN_CANCELLED"
                            if isinstance(exc, asyncio.CancelledError)
                            else "CASE_DEADLINE"
                        )
                        cancelled = isinstance(exc, asyncio.CancelledError)
                        capture = AttemptPayload(error={"type": reason})
                        capture._secret = secrets.get(name)
                    except Exception as exc:  # noqa: BLE001 -- evidence boundary must retain unexpected failures
                        capture = AttemptPayload(
                            error={
                                "type": "RUNNER_ERROR",
                                "exception": type(exc).__name__,
                            }
                        )
                        capture._secret = secrets.get(name)
                    obs, events = _assemble(case, capture)
                    obs.endpoint, obs.request_payload = name, copy.deepcopy(payload)
                    data = {
                        "case_id": case.id,
                        "prompt_id": case.prompt_id,
                        "endpoint": name,
                        "step": step,
                        "repetition": case.repetition,
                        "retry": retry,
                        "attempt_number": numbers[key],
                        "request_hash": content_hash(safe_payload),
                        "request": safe_payload,
                        "status_code": capture.status_code,
                        "timings": {
                            k: v for k, v in capture.timings.items() if v is not None
                        },
                        "response": capture.safe_evidence(obs.model_dump(mode="json")),
                        "events": capture.safe_evidence(
                            [e.model_dump(mode="json") for e in events]
                        ),
                        "error": capture.error,
                        "http_exchange_completed": capture.http_exchange_completed,
                        "capture": capture.model_dump(mode="json"),
                    }
                    attempt = Attempt(**data, evidence_hash="0" * 64)
                    attempt = attempt.model_copy(
                        update={
                            "evidence_hash": content_hash(
                                attempt.model_dump(
                                    mode="json", exclude={"evidence_hash"}
                                )
                            )
                        }
                    )
                    if out:
                        append_record(
                            out / "attempts.jsonl",
                            {
                                "kind": "attempt",
                                "attempt": attempt.model_dump(mode="json"),
                            },
                        )
                    refs.append(attempt.evidence_hash)
                    attempt_metrics.append(_attempt_metric(attempt))
                    if retry == 0:
                        first_observations.append(obs)
                    transient = capture.status_code in (
                        408,
                        429,
                        500,
                        502,
                        503,
                        504,
                    ) or bool(
                        capture.error
                        and capture.error.get("type")
                        in (
                            "ReadTimeout",
                            "ConnectTimeout",
                            "ReadError",
                            "ConnectError",
                            "RemoteProtocolError",
                        )
                    )
                    if reason or not transient or retry == b.retries:
                        observations.append(obs)
                        break
                if reason:
                    break
                if (
                    observations[-1].transport_error
                    or (observations[-1].status_code or 200) >= 400
                ):
                    break
                obs = observations[-1]
                if obs.violations:
                    break
                follow_tools = bool(obs.tools and case.oracle.get("continue_tools"))
                follow_recipe = recipe_index + 1 < len(case.steps)
                if case.oracle.get("bounded_steps") and not follow_recipe:
                    break
                if not follow_tools and not follow_recipe:
                    break
                if step + 1 >= case.max_requests:
                    reason = "CONVERSATION_LIMIT"
                    break
                mutation = (
                    case.steps[recipe_index + 1].get("mutation")
                    if follow_recipe
                    else None
                )
                try:
                    if follow_recipe and "replay_from" in case.steps[recipe_index + 1]:
                        source = case.steps[recipe_index + 1]["replay_from"]
                        obs = observations[source]
                        if case.oracle.get("kind") == "workflow":
                            options = copy.deepcopy(recipe_options[source])
                        history = copy.deepcopy(
                            obs.request_payload[
                                "messages" if case.protocol == "chat" else "input"
                            ]
                        )
                        follow_tools = bool(
                            obs.tools and case.oracle.get("continue_tools")
                        )
                    _replay(case, obs, history, follow_tools, mutation)
                except (ValueError, TypeError, RecursionError):
                    # Invalid calls remain observed contract failures, never executed.
                    result = evaluate_case(case, observations, rules)
                    if not any(a.status == "FAIL" for a in result.assertions):
                        reason = "INVALID_TOOL_EXECUTION"
                    break
                step += 1
                recipe_index += 1
            result = evaluate_case(case, observations, rules).model_copy(
                update={"endpoint": name, "attempt_refs": refs}
            )
            if reason:
                result = result.model_copy(
                    update={
                        "status": "ERROR"
                        if reason
                        in ("RUN_CANCELLED", "CASE_DEADLINE", "INVALID_TOOL_EXECUTION")
                        else "INCONCLUSIVE",
                        "completed": False,
                        "reason": reason,
                    }
                )
            if result.status == "ERROR":
                result = result.model_copy(update={"completed": False})
            available = bool(
                observations
                and not has_execution_error(observations[-1])
                and 200 <= (observations[-1].status_code or 0) < 300
            )
            metrics = [
                *result.metric_observations,
                MetricObservation(name="available", value=float(available)),
                MetricObservation(
                    name="end_to_end_success",
                    value=float(
                        result.completed
                        and bool(result.assertions)
                        and all(a.status == "PASS" for a in result.assertions)
                    ),
                ),
            ]
            if not any(m.name == "task_success" for m in metrics):
                metrics.append(
                    MetricObservation(
                        name="task_success",
                        scored=False,
                        reason="No scorable final fixture answer",
                    )
                )
            first_status = (
                evaluate_case(case, first_observations, rules).status
                if first_observations
                else None
            )
            result = result.model_copy(
                update={
                    "first_attempt_status": first_status,
                    "eventual_status": result.status,
                    "metric_observations": metrics,
                }
            )
            result = CaseResult.model_validate(
                redact(result.model_dump(mode="json"), secrets.get(name))
            )
        results[key] = result
        if out:
            append_record(
                out / "results.jsonl",
                {"kind": "result", "result": result.model_dump(mode="json")},
            )
            checkpoint_artifacts(out)
    ordered = [results[(name, case.id)] for name, case in jobs]
    unresolved_tails = any(
        r.get("disposition") == "retained_torn_tail" for r in state.incomplete_records
    )
    complete = all(r.completed for r in ordered) and not unresolved_tails
    required = [results[(name, c.id)] for name, c in jobs if c.required]
    insufficient = (
        not required
        or not manifest.gates
        or any(r.status in ("ERROR", "INCONCLUSIVE") for r in required)
    )
    exit_code = (
        2
        if not complete or insufficient
        else 1
        if any(r.status == "FAIL" for r in required)
        else 0
    )
    run = RunResult(
        resume_dispositions=state.incomplete_records,
        manifest_hash=manifest.manifest_hash,
        complete=complete,
        case_results=ordered,
        counts=dict(Counter(r.status for r in ordered)),
        budget_usage=dict(usage),
        enabled_gates=manifest.gates,
        exit_code=exit_code,
        attempt_metrics=attempt_metrics,
    )
    if out:
        atomic_json(out / "summary.json", run.model_dump(mode="json"))
    return run
