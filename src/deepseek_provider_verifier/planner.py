"""Pure, network-free expansion of case templates into an execution manifest."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from .assertions import rule_applies
from .records import Case, CaseTemplate, Config, Manifest, PlanBudgets, Profile


def build_manifest(
    config: Config, profile: Profile, cases: list[CaseTemplate]
) -> Manifest:
    """Validate and expand a bounded workload without resolving credentials."""

    if config.run.profile != profile.id:
        raise ValueError(
            f"configured profile {config.run.profile!r} does not match {profile.id!r}"
        )
    try:
        preset = profile.presets[config.run.suite]
    except KeyError as exc:
        raise ValueError(f"unknown profile suite: {config.run.suite}") from exc
    if config.run.retries > preset.max_retries:
        raise ValueError("configured retries exceed the selected suite request budget")
    if config.run.concurrency > preset.concurrency:
        raise ValueError("configured concurrency exceeds the selected suite budget")

    rules_by_id = {rule.id: rule for rule in profile.rules}
    templates_by_key: dict[tuple[str, str], CaseTemplate] = {}
    for template in cases:
        key = (template.protocol, template.id)
        if key in templates_by_key:
            raise ValueError(
                f"duplicate case ID template: {template.id}.{template.protocol}"
            )
        templates_by_key[key] = template
        if template.protocol not in config.run.protocols:
            raise ValueError(f"case {template.id} uses an unselected protocol")
        unknown_rules = set(template.rule_ids) - set(rules_by_id)
        if unknown_rules:
            raise ValueError(
                f"case {template.id} references unknown rule(s): {sorted(unknown_rules)}"
            )
        if any(
            rules_by_id[rule_id].protocol != template.protocol
            for rule_id in template.rule_ids
        ):
            raise ValueError(
                f"case {template.id} references a rule for another protocol"
            )

    selection_ids = [*preset.case_ids, *preset.attached_assertion_case_ids]
    if selection_ids:
        expected_keys = {
            (protocol, case_id)
            for protocol in config.run.protocols
            for case_id in selection_ids
        }
        actual_keys = set(templates_by_key)
        missing_keys = expected_keys - actual_keys
        if missing_keys:
            raise ValueError(
                "missing preset template(s): "
                + ", ".join(_template_key(key) for key in sorted(missing_keys))
            )
        extra_keys = actual_keys - expected_keys
        if extra_keys:
            raise ValueError(
                "unexpected preset template(s): "
                + ", ".join(_template_key(key) for key in sorted(extra_keys))
            )
        request_templates = [
            templates_by_key[(protocol, case_id)]
            for protocol in config.run.protocols
            for case_id in preset.case_ids
        ]
        attachment_templates = [
            templates_by_key[(protocol, case_id)]
            for protocol in config.run.protocols
            for case_id in preset.attached_assertion_case_ids
        ]
    else:
        request_templates = cases
        attachment_templates = []

    expanded: list[Case] = []
    seen_case_ids: set[str] = set()
    for template in request_templates:
        if template.max_requests > preset.max_requests_per_conversation:
            raise ValueError(
                f"case {template.id} exceeds the per-conversation request budget"
            )
        for mode in template.modes:
            allowed_tokens = preset.mode_max_output_tokens.get(mode)
            requested_tokens = template.max_output_tokens[mode]
            if allowed_tokens is None or requested_tokens > allowed_tokens:
                raise ValueError(
                    f"case {template.id} exceeds the {mode} output token budget"
                )
            for stream in template.streams:
                case_id = _case_id(template, mode, stream)
                if case_id in seen_case_ids:
                    raise ValueError(f"duplicate case ID: {case_id}")
                seen_case_ids.add(case_id)
                expanded.append(
                    Case(
                        id=case_id,
                        prompt_id=template.prompt_id or template.id,
                        dataset_version=template.dataset_version,
                        protocol=template.protocol,
                        template_id=template.id,
                        mode=mode,
                        stream=stream,
                        rule_ids=template.rule_ids,
                        attached_assertion_case_ids=[],
                        steps=template.steps,
                        required=template.required,
                        max_requests=template.max_requests,
                        max_output_tokens=requested_tokens,
                        oracle=template.oracle,
                    )
                )

    for attachment in attachment_templates:
        attached = False
        for mode in attachment.modes:
            for stream in attachment.streams:
                attachment_case_id = _case_id(attachment, mode, stream)
                matching_indexes = [
                    index
                    for index, case in enumerate(expanded)
                    if case.protocol == attachment.protocol
                    and case.mode == mode
                    and case.stream == stream
                ]
                if not matching_indexes:
                    continue
                attached = True
                for index in matching_indexes:
                    case = expanded[index]
                    expanded[index] = case.model_copy(
                        update={
                            "rule_ids": _ordered_union(
                                case.rule_ids, attachment.rule_ids
                            ),
                            "attached_assertion_case_ids": [
                                *case.attached_assertion_case_ids,
                                attachment_case_id,
                            ],
                            "required": case.required or attachment.required,
                        }
                    )
        if not attached:
            raise ValueError(
                f"attached assertion {attachment.id} has no matching request variant"
            )

    if config.run.repetitions > 1:
        expanded = [
            case.model_copy(
                update={"id": f"{case.id}.r{repetition + 1}", "repetition": repetition}
            )
            for case in expanded
            for repetition in range(config.run.repetitions)
        ]

    retry_multiplier = config.run.retries + 1
    requests_by_protocol: Counter[str] = Counter()
    for case in expanded:
        requests_by_protocol[case.protocol] += case.max_requests * retry_multiplier
    for protocol, request_count in requests_by_protocol.items():
        if request_count > preset.max_requests_per_protocol:
            raise ValueError(
                f"{protocol} request budget exceeded: {request_count} > "
                f"{preset.max_requests_per_protocol}"
            )

    requests_per_endpoint = sum(requests_by_protocol.values())
    endpoint_limit = min(
        preset.max_requests_per_endpoint, config.run.max_attempts_per_endpoint
    )
    if requests_per_endpoint > endpoint_limit:
        raise ValueError(
            f"per-endpoint request budget exceeded: {requests_per_endpoint} > {endpoint_limit}"
        )
    request_ceiling = requests_per_endpoint * len(config.endpoints)
    if request_ceiling > preset.max_total_requests:
        raise ValueError(
            f"aggregate request budget exceeded: {request_ceiling} > "
            f"{preset.max_total_requests}"
        )

    output_token_ceiling = sum(
        case.max_output_tokens * case.max_requests * retry_multiplier
        for case in expanded
    ) * len(config.endpoints)
    gates = sorted(
        {
            rules_by_id[rule_id].assertion_id
            for case in expanded
            if case.required
            for rule_id in case.rule_ids
            if rules_by_id[rule_id].gating and rule_applies(rules_by_id[rule_id], case)
        }
    )
    budgets = PlanBudgets(
        repetitions=config.run.repetitions,
        execution_order=config.run.execution_order,
        suite=config.run.suite,
        protocols=config.run.protocols,
        max_requests_per_protocol=preset.max_requests_per_protocol,
        max_requests_per_endpoint=preset.max_requests_per_endpoint,
        max_total_requests=preset.max_total_requests,
        max_requests_per_conversation=preset.max_requests_per_conversation,
        max_retries=preset.max_retries,
        retries=config.run.retries,
        max_concurrency=preset.concurrency,
        concurrency=config.run.concurrency,
        max_attempts_per_endpoint=config.run.max_attempts_per_endpoint,
        case_deadline_seconds=preset.case_deadline_seconds,
        mode_max_output_tokens=preset.mode_max_output_tokens,
        case_ids=preset.case_ids,
        attached_assertion_case_ids=preset.attached_assertion_case_ids,
    )
    profile_hash = _hash(profile.model_dump(mode="json"))
    dataset_hash = _hash([case.model_dump(mode="json") for case in expanded])
    comparable = {
        "profile_hash": profile_hash,
        "dataset_hash": dataset_hash,
        "scorer_revision": config.run.scorer_revision,
        "endpoints": {
            name: endpoint.model_dump(mode="json")
            for name, endpoint in sorted(config.endpoints.items())
        },
        "cases": [case.model_dump(mode="json") for case in expanded],
        "budgets": budgets.model_dump(mode="json"),
        "request_ceiling": request_ceiling,
        "output_token_ceiling": output_token_ceiling,
        "gates": gates,
    }
    return Manifest(
        profile_snapshot=profile,
        run_id=str(uuid.uuid4()),
        created_at=datetime.now(UTC),
        profile_hash=profile_hash,
        dataset_hash=dataset_hash,
        scorer_revision=config.run.scorer_revision,
        endpoints=config.endpoints,
        cases=expanded,
        budgets=budgets,
        request_ceiling=request_ceiling,
        output_token_ceiling=output_token_ceiling,
        gates=gates,
        manifest_hash=_hash(comparable),
    )


def _case_id(template: CaseTemplate, mode: str, stream: bool) -> str:
    stream_label = "stream" if stream else "nonstream"
    return f"{template.id}.{template.protocol}.{mode}.{stream_label}"


def _template_key(key: tuple[str, str]) -> str:
    protocol, case_id = key
    return f"{case_id}.{protocol}"


def _ordered_union(left: list[str], right: list[str]) -> list[str]:
    return list(dict.fromkeys([*left, *right]))


def _hash(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
