"""Descriptive and policy-gated paired deployment comparisons."""

from __future__ import annotations

import math
import random
from collections import defaultdict

from .records import (
    AttemptMetric,
    Case,
    CaseResult,
    ComparisonMetric,
    ComparisonPolicy,
    ComparisonResult,
    Manifest,
    ManifestDifference,
    MetricObservation,
    RunResult,
)

_QUALITY_METRICS = {
    "end_to_end_success",
    "task_success",
    "schema_accuracy",
    "tool_trigger_precision",
    "tool_trigger_recall",
    "tool_trigger_f1",
    "first_http_2xx_rate",
    "eventual_http_2xx_rate",
}


def _endpoint(manifest: Manifest, selected: str | None, side: str) -> str:
    if selected is None:
        if len(manifest.endpoints) != 1:
            raise ValueError(
                f"{side} endpoint selector is required for multi-endpoint run"
            )
        return next(iter(manifest.endpoints))
    if selected not in manifest.endpoints:
        raise ValueError(f"Unknown {side} endpoint selector: {selected}")
    return selected


def _validate_run(run: RunResult, manifest: Manifest) -> None:
    if run.manifest_hash != manifest.manifest_hash:
        raise ValueError("Run result does not belong to the supplied manifest")
    cases = {case.id: case for case in manifest.cases}
    if len(cases) != len(manifest.cases):
        raise ValueError("Duplicate manifest trial identity")
    seen: set[tuple[str, str]] = set()
    for result in run.case_results:
        identity = (result.endpoint, result.case_id)
        if identity in seen:
            raise ValueError(f"Duplicate result identity: {identity}")
        seen.add(identity)
        if result.endpoint not in manifest.endpoints or result.case_id not in cases:
            raise ValueError(f"Unexpected result identity: {identity}")
        case = cases[result.case_id]
        if (result.prompt_id, result.repetition) != (case.prompt_id, case.repetition):
            raise ValueError(
                "Result prompt/repetition metadata conflicts with manifest"
            )
    attempt_seen: set[tuple[str, str, int]] = set()
    for attempt in run.attempt_metrics:
        identity = (attempt.endpoint, attempt.case_id, attempt.attempt_number)
        if identity in attempt_seen:
            raise ValueError(f"Duplicate attempt metric identity: {identity}")
        attempt_seen.add(identity)
        if attempt.endpoint not in manifest.endpoints or attempt.case_id not in cases:
            raise ValueError(f"Unexpected attempt metric identity: {identity}")
        case = cases[attempt.case_id]
        if (attempt.prompt_id, attempt.repetition) != (
            case.prompt_id,
            case.repetition,
        ):
            raise ValueError(
                "Attempt prompt/repetition metadata conflicts with manifest"
            )
        if (
            attempt.step >= case.max_requests
            or attempt.retry > manifest.budgets.retries
        ):
            raise ValueError("Attempt step/retry metadata exceeds manifest bounds")


def _difference(
    differences: list[ManifestDifference],
    field: str,
    reference,
    candidate,
    reason: str,
    *,
    compatible: bool = False,
) -> None:
    if reference != candidate:
        differences.append(
            ManifestDifference(
                field=field,
                reference=reference,
                candidate=candidate,
                compatible=compatible,
                reason=reason,
            )
        )


def _manifest_differences(
    reference: Manifest,
    candidate: Manifest,
    reference_endpoint: str,
    candidate_endpoint: str,
    model_mapping: dict[str, str] | None,
) -> list[ManifestDifference]:
    differences: list[ManifestDifference] = []
    for field in ("profile_hash", "dataset_hash", "scorer_revision", "gates"):
        _difference(
            differences,
            field,
            getattr(reference, field),
            getattr(candidate, field),
            f"{field} must match for a paired workload",
        )
    _difference(
        differences,
        "budgets",
        reference.budgets.model_dump(mode="json"),
        candidate.budgets.model_dump(mode="json"),
        "run budgets and repetition policy must match",
    )
    reference_cases = {case.id: case for case in reference.cases}
    candidate_cases = {case.id: case for case in candidate.cases}
    _difference(
        differences,
        "cases.identities",
        list(reference_cases),
        list(candidate_cases),
        "planned trial identities and order must match",
    )
    for case_id in sorted(reference_cases.keys() & candidate_cases.keys()):
        ref_case = reference_cases[case_id].model_dump(mode="json")
        cand_case = candidate_cases[case_id].model_dump(mode="json")
        for field in sorted(ref_case.keys() | cand_case.keys()):
            _difference(
                differences,
                f"cases[{case_id}].{field}",
                ref_case.get(field),
                cand_case.get(field),
                "planned trial, prompt, mode, output limit, and oracle fields must match",
            )
    ref = reference.endpoints[reference_endpoint]
    cand = candidate.endpoints[candidate_endpoint]
    _difference(
        differences,
        "endpoint.name",
        reference_endpoint,
        candidate_endpoint,
        "endpoint selectors explicitly pair these addresses",
        compatible=True,
    )
    _difference(
        differences,
        "endpoint.base_url",
        str(ref.base_url),
        str(cand.base_url),
        "provider addresses may differ when endpoint slices are declared",
        compatible=True,
    )
    _difference(
        differences,
        "endpoint.model_release",
        ref.model_release,
        cand.model_release,
        "intended checkpoint releases must match",
    )
    ref_model = ref.contract_model or ref.model
    cand_model = cand.contract_model or cand.model
    mapped = (
        ref_model == cand_model or (model_mapping or {}).get(ref_model) == cand_model
    )
    if ref.model != cand.model or ref_model != cand_model:
        differences.append(
            ManifestDifference(
                field="endpoint.model",
                reference={"served": ref.model, "contract": ref_model},
                candidate={"served": cand.model, "contract": cand_model},
                compatible=mapped,
                reason=(
                    "explicit contract_model/model_mapping pairs these labels"
                    if mapped
                    else "different model labels require an explicit mapping"
                ),
            )
        )
    return differences


def _observations(result: CaseResult | None, name: str) -> list[MetricObservation]:
    if result is None:
        return []
    return [metric for metric in result.metric_observations if metric.name == name]


def _finite_unit(metric: MetricObservation) -> float | None:
    if not metric.scored or metric.value is None or metric.denominator <= 0:
        return None
    if metric.denominator != 1:
        raise ValueError(
            f"Metric {metric.name!r} requires denominator one per retained observation"
        )
    if not math.isfinite(metric.value) or not 0 <= metric.value <= 1:
        raise ValueError(
            f"Metric {metric.name!r} must be a finite value from zero to one"
        )
    return metric.value


def _result_index(run: RunResult, endpoint: str) -> dict[str, CaseResult]:
    return {
        result.case_id: result
        for result in run.case_results
        if result.endpoint == endpoint
    }


def _case_values(
    cases: list[Case],
    results: dict[str, CaseResult],
    name: str,
    *,
    planned_denominator: bool = False,
    singleton_observation: bool = False,
) -> tuple[float, int, int, dict[str, list[float]]]:
    numerator = 0.0
    denominator = 0
    unavailable = 0
    values: dict[str, list[float]] = {}
    for case in cases:
        observed = _observations(results.get(case.id), name)
        if singleton_observation and len(observed) > 1:
            raise ValueError(f"Trial {case.id} has duplicate {name} observations")
        scored = [_finite_unit(item) for item in observed]
        scored = [item for item in scored if item is not None]
        if planned_denominator:
            denominator += 1
            value = scored[0] if len(scored) == 1 else 0.0
            if not scored:
                unavailable += 1
            elif len(scored) != 1:
                raise ValueError(f"Trial {case.id} has duplicate {name} observations")
            numerator += value
            values[case.id] = [value]
        elif scored:
            denominator += len(scored)
            numerator += sum(scored)
            values[case.id] = scored
            if len(scored) < len(observed):
                unavailable += len(observed) - len(scored)
        else:
            unavailable += max(1, len(observed))
    return numerator, denominator, unavailable, values


def _http_values(
    cases: list[Case], attempts: list[AttemptMetric], eventual: bool
) -> tuple[float, int, int, dict[str, list[float]]]:
    case_ids = {case.id for case in cases}
    chosen = [attempt for attempt in attempts if attempt.case_id in case_ids]
    by_request: dict[tuple[str, int], list[AttemptMetric]] = defaultdict(list)
    for attempt in chosen:
        by_request[(attempt.case_id, attempt.step)].append(attempt)
    values: dict[str, list[float]] = defaultdict(list)
    unknown_requests = 0
    for (case_id, _), request_attempts in by_request.items():
        candidates = (
            request_attempts
            if eventual
            else [min(request_attempts, key=lambda attempt: attempt.attempt_number)]
        )
        if not candidates:
            continue
        known = [
            attempt
            for attempt in candidates
            if attempt.http_exchange_completed is not None
        ]
        if not known:
            unknown_requests += 1
            continue
        available = any(
            attempt.http_exchange_completed
            and attempt.status_code is not None
            and 200 <= attempt.status_code < 300
            for attempt in known
        )
        values[case_id].append(float(available))
    flat = [value for trial in values.values() for value in trial]
    started_cases = {attempt.case_id for attempt in chosen}
    return (
        sum(flat),
        len(flat),
        len(case_ids - started_cases) + unknown_requests,
        dict(values),
    )


def _trigger_values(
    cases: list[Case], results: dict[str, CaseResult]
) -> tuple[dict[str, int], dict[str, list[tuple[int, int]]], int]:
    counts = {
        "true_positive": 0,
        "false_positive": 0,
        "true_negative": 0,
        "false_negative": 0,
    }
    values: dict[str, list[tuple[int, int]]] = {}
    unavailable = 0
    for case in cases:
        actual = _observations(results.get(case.id), "tool_trigger")
        expected = _observations(results.get(case.id), "expected_tool_trigger")
        actual_value = _finite_unit(actual[0]) if len(actual) == 1 else None
        expected_value = _finite_unit(expected[0]) if len(expected) == 1 else None
        if actual_value is None or expected_value is None:
            unavailable += 1
            continue
        pair = (round(actual_value), round(expected_value))
        values[case.id] = [pair]
        label = {
            (1, 1): "true_positive",
            (1, 0): "false_positive",
            (0, 0): "true_negative",
            (0, 1): "false_negative",
        }[pair]
        counts[label] += 1
    return counts, values, unavailable


def _trigger_counts(observations: list[tuple[int, int]]) -> dict[str, int]:
    counts = {
        "true_positive": 0,
        "false_positive": 0,
        "true_negative": 0,
        "false_negative": 0,
    }
    labels = {
        (1, 1): "true_positive",
        (1, 0): "false_positive",
        (0, 0): "true_negative",
        (0, 1): "false_negative",
    }
    for observation in observations:
        counts[labels[observation]] += 1
    return counts


def _rate(counts: dict[str, int], kind: str) -> tuple[float | None, int, int]:
    tp, fp, fn = (
        counts["true_positive"],
        counts["false_positive"],
        counts["false_negative"],
    )
    if kind == "precision":
        denominator, numerator = tp + fp, tp
    elif kind == "recall":
        denominator, numerator = tp + fn, tp
    else:
        denominator, numerator = 2 * tp + fp + fn, 2 * tp
    return (numerator / denominator if denominator else None), numerator, denominator


def _quantile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    position = probability * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _paired_ratio_values(
    cases: list[Case],
    reference: dict[str, list[float]],
    candidate: dict[str, list[float]],
) -> tuple[dict[str, list[tuple[float, int, float, int, int]]], int, int]:
    clusters: dict[str, list[tuple[float, int, float, int, int]]] = defaultdict(list)
    missing_reference = 0
    missing_candidate = 0
    for case in cases:
        ref_values = reference.get(case.id, [])
        cand_values = candidate.get(case.id, [])
        if not ref_values:
            missing_reference += 1
        if not cand_values:
            missing_candidate += 1
        if ref_values and cand_values and case.prompt_id is not None:
            clusters[case.prompt_id].append(
                (
                    sum(ref_values),
                    len(ref_values),
                    sum(cand_values),
                    len(cand_values),
                    case.repetition,
                )
            )
    return dict(clusters), missing_reference, missing_candidate


def _bootstrap(
    clusters: dict[str, list[tuple[float, int, float, int, int]]],
    policy: ComparisonPolicy,
) -> tuple[float, float] | None:
    prompt_ids = sorted(clusters)
    if len(prompt_ids) < policy.minimum_distinct_prompts:
        return None
    if any(
        len({repetition for *_, repetition in clusters[prompt_id]})
        < policy.minimum_repetitions
        for prompt_id in prompt_ids
    ):
        return None
    rng = random.Random(policy.bootstrap_seed)
    differences = []
    for _ in range(policy.bootstrap_samples):
        sample = [rng.choice(prompt_ids) for _ in prompt_ids]
        observations = [item for prompt_id in sample for item in clusters[prompt_id]]
        reference_numerator = sum(item[0] for item in observations)
        reference_denominator = sum(item[1] for item in observations)
        candidate_numerator = sum(item[2] for item in observations)
        candidate_denominator = sum(item[3] for item in observations)
        differences.append(
            candidate_numerator / candidate_denominator
            - reference_numerator / reference_denominator
        )
    alpha = (1 - policy.confidence_level) / 2
    return _quantile(differences, alpha), _quantile(differences, 1 - alpha)


def _paired_trigger_clusters(
    cases: list[Case],
    reference: dict[str, list[tuple[int, int]]],
    candidate: dict[str, list[tuple[int, int]]],
) -> tuple[dict[str, list[tuple[tuple[int, int], tuple[int, int], int]]], int, int]:
    clusters = defaultdict(list)
    missing_reference = 0
    missing_candidate = 0
    for case in cases:
        ref = reference.get(case.id)
        cand = candidate.get(case.id)
        if not ref:
            missing_reference += 1
        if not cand:
            missing_candidate += 1
        if ref and cand and case.prompt_id is not None:
            clusters[case.prompt_id].append((ref[0], cand[0], case.repetition))
    return dict(clusters), missing_reference, missing_candidate


def _bootstrap_trigger(
    clusters: dict[str, list[tuple[tuple[int, int], tuple[int, int], int]]],
    kind: str,
    policy: ComparisonPolicy,
) -> tuple[float, float] | None:
    prompt_ids = sorted(clusters)
    if len(prompt_ids) < policy.minimum_distinct_prompts:
        return None
    if any(
        len({repetition for _, _, repetition in clusters[prompt_id]})
        < policy.minimum_repetitions
        for prompt_id in prompt_ids
    ):
        return None
    rng = random.Random(policy.bootstrap_seed)
    differences = []
    for _ in range(policy.bootstrap_samples):
        sample = [rng.choice(prompt_ids) for _ in prompt_ids]
        items = [item for prompt_id in sample for item in clusters[prompt_id]]
        reference_value = _rate(_trigger_counts([item[0] for item in items]), kind)[0]
        candidate_value = _rate(_trigger_counts([item[1] for item in items]), kind)[0]
        if reference_value is not None and candidate_value is not None:
            differences.append(candidate_value - reference_value)
    if not differences:
        return None
    alpha = (1 - policy.confidence_level) / 2
    return _quantile(differences, alpha), _quantile(differences, 1 - alpha)


def _comparison_metric(
    reference_summary: tuple[float, int, int, dict[str, list[float]]],
    candidate_summary: tuple[float, int, int, dict[str, list[float]]],
    cases: list[Case],
    policy: ComparisonPolicy | None,
) -> ComparisonMetric:
    ref_num, ref_den, ref_missing, ref_values = reference_summary
    cand_num, cand_den, cand_missing, cand_values = candidate_summary
    ref_value = ref_num / ref_den if ref_den else None
    cand_value = cand_num / cand_den if cand_den else None
    clusters, missing_ref_pairs, missing_cand_pairs = _paired_ratio_values(
        cases, ref_values, cand_values
    )
    interval = _bootstrap(clusters, policy) if policy else None
    paired = sum(len(items) for items in clusters.values())
    repetitions = min(
        (len({item[4] for item in items}) for items in clusters.values()), default=0
    )
    return ComparisonMetric(
        value=cand_value,
        numerator=cand_num,
        denominator=cand_den,
        unavailable=cand_missing,
        reference_value=ref_value,
        reference_numerator=ref_num,
        reference_denominator=ref_den,
        reference_unavailable=ref_missing,
        difference=(
            cand_value - ref_value
            if cand_value is not None and ref_value is not None
            else None
        ),
        lower_bound=interval[0] if interval else None,
        upper_bound=interval[1] if interval else None,
        confidence_level=policy.confidence_level if interval and policy else None,
        bootstrap_seed=policy.bootstrap_seed if interval and policy else None,
        paired_observations=paired,
        paired_distinct_prompts=len(clusters),
        paired_repetitions=repetitions,
        missing_reference=missing_ref_pairs,
        missing_candidate=missing_cand_pairs,
    )


def _count_metric(reference: int, candidate: int) -> ComparisonMetric:
    return ComparisonMetric(
        value=float(candidate),
        numerator=float(candidate),
        denominator=1,
        unavailable=0,
        reference_value=float(reference),
        reference_numerator=float(reference),
        reference_denominator=1,
        reference_unavailable=0,
        difference=float(candidate - reference),
    )


def _latency_metric(
    reference: list[AttemptMetric],
    candidate: list[AttemptMetric],
    field: str,
) -> ComparisonMetric:
    def summarize(
        attempts: list[AttemptMetric],
    ) -> tuple[float | None, float, int, int]:
        successful = [
            attempt
            for attempt in attempts
            if attempt.http_exchange_completed
            and attempt.status_code is not None
            and 200 <= attempt.status_code < 300
        ]
        values = [
            attempt.timings[field] for attempt in successful if field in attempt.timings
        ]
        total = sum(values)
        return (
            (total / len(values) if values else None),
            total,
            len(values),
            len(successful) - len(values),
        )

    ref_value, ref_total, ref_denominator, ref_unavailable = summarize(reference)
    cand_value, cand_total, cand_denominator, cand_unavailable = summarize(candidate)
    return ComparisonMetric(
        value=cand_value,
        numerator=cand_total,
        denominator=cand_denominator,
        unavailable=cand_unavailable,
        reference_value=ref_value,
        reference_numerator=ref_total,
        reference_denominator=ref_denominator,
        reference_unavailable=ref_unavailable,
        difference=(
            cand_value - ref_value
            if cand_value is not None and ref_value is not None
            else None
        ),
    )


def compare_runs(
    reference: RunResult,
    candidate: RunResult,
    manifests: tuple[Manifest, Manifest],
    policy: ComparisonPolicy | None = None,
    *,
    reference_endpoint: str | None = None,
    candidate_endpoint: str | None = None,
    model_mapping: dict[str, str] | None = None,
) -> ComparisonResult:
    """Compare declared endpoint slices without rereading raw request/response bodies."""

    reference_manifest, candidate_manifest = manifests
    _validate_run(reference, reference_manifest)
    _validate_run(candidate, candidate_manifest)
    ref_endpoint = _endpoint(reference_manifest, reference_endpoint, "reference")
    cand_endpoint = _endpoint(candidate_manifest, candidate_endpoint, "candidate")
    differences = _manifest_differences(
        reference_manifest,
        candidate_manifest,
        ref_endpoint,
        cand_endpoint,
        model_mapping,
    )
    comparable = all(difference.compatible for difference in differences)
    cases = reference_manifest.cases
    ref_results = _result_index(reference, ref_endpoint)
    cand_results = _result_index(candidate, cand_endpoint)
    ref_attempts = [a for a in reference.attempt_metrics if a.endpoint == ref_endpoint]
    cand_attempts = [
        a for a in candidate.attempt_metrics if a.endpoint == cand_endpoint
    ]
    metrics = {
        "end_to_end_success": _comparison_metric(
            _case_values(
                cases, ref_results, "end_to_end_success", planned_denominator=True
            ),
            _case_values(
                cases, cand_results, "end_to_end_success", planned_denominator=True
            ),
            cases,
            policy,
        ),
        "task_success": _comparison_metric(
            _case_values(
                cases, ref_results, "task_success", singleton_observation=True
            ),
            _case_values(
                cases, cand_results, "task_success", singleton_observation=True
            ),
            cases,
            policy,
        ),
        "schema_accuracy": _comparison_metric(
            _case_values(cases, ref_results, "tool_argument_schema"),
            _case_values(cases, cand_results, "tool_argument_schema"),
            cases,
            policy,
        ),
        "first_http_2xx_rate": _comparison_metric(
            _http_values(cases, ref_attempts, False),
            _http_values(cases, cand_attempts, False),
            cases,
            policy,
        ),
        "eventual_http_2xx_rate": _comparison_metric(
            _http_values(cases, ref_attempts, True),
            _http_values(cases, cand_attempts, True),
            cases,
            policy,
        ),
        "http_attempts": _count_metric(len(ref_attempts), len(cand_attempts)),
        "http_retry_attempts": _count_metric(
            sum(a.retry > 0 for a in ref_attempts),
            sum(a.retry > 0 for a in cand_attempts),
        ),
    }
    for timing in (
        "headers_seconds",
        "first_event_seconds",
        "first_meaningful_output_seconds",
        "total_seconds",
    ):
        metrics[f"latency_{timing}"] = _latency_metric(
            ref_attempts, cand_attempts, timing
        )
    ref_trigger, ref_trigger_values, ref_trigger_missing = _trigger_values(
        cases, ref_results
    )
    cand_trigger, cand_trigger_values, cand_trigger_missing = _trigger_values(
        cases, cand_results
    )
    trigger_clusters, missing_ref_trigger, missing_cand_trigger = (
        _paired_trigger_clusters(cases, ref_trigger_values, cand_trigger_values)
    )
    for kind in ("precision", "recall", "f1"):
        ref_value, ref_num, ref_den = _rate(ref_trigger, kind)
        cand_value, cand_num, cand_den = _rate(cand_trigger, kind)
        interval = (
            _bootstrap_trigger(trigger_clusters, kind, policy) if policy else None
        )
        metrics[f"tool_trigger_{kind}"] = ComparisonMetric(
            value=cand_value,
            numerator=cand_num,
            denominator=cand_den,
            unavailable=cand_trigger_missing,
            reference_value=ref_value,
            reference_numerator=ref_num,
            reference_denominator=ref_den,
            reference_unavailable=ref_trigger_missing,
            difference=(
                cand_value - ref_value
                if cand_value is not None and ref_value is not None
                else None
            ),
            lower_bound=interval[0] if interval else None,
            upper_bound=interval[1] if interval else None,
            confidence_level=policy.confidence_level if interval and policy else None,
            bootstrap_seed=policy.bootstrap_seed if interval and policy else None,
            paired_observations=sum(len(items) for items in trigger_clusters.values()),
            paired_distinct_prompts=len(trigger_clusters),
            paired_repetitions=min(
                (
                    len({item[2] for item in items})
                    for items in trigger_clusters.values()
                ),
                default=0,
            ),
            missing_reference=missing_ref_trigger,
            missing_candidate=missing_cand_trigger,
            counts=cand_trigger,
        )

    reasons = [
        difference.reason for difference in differences if not difference.compatible
    ]
    unknown_release = any(
        manifest.endpoints[endpoint].model_release.strip().lower() == "unknown"
        for manifest, endpoint in (
            (reference_manifest, ref_endpoint),
            (candidate_manifest, cand_endpoint),
        )
    )
    if unknown_release:
        reasons.append(
            "Unknown checkpoint identity disables quality-equivalence gating"
        )
    metric_gates = {}
    quality_gate = None
    if policy is not None:
        unsupported = set(policy.allowed_drops) - _QUALITY_METRICS
        if unsupported:
            raise ValueError(f"Unsupported quality metric(s): {sorted(unsupported)}")
        for name, margin in policy.allowed_drops.items():
            metric = metrics[name]
            if (
                not comparable
                or unknown_release
                or metric.lower_bound is None
                or metric.upper_bound is None
            ):
                verdict = "INCONCLUSIVE"
            elif metric.lower_bound > -margin and not math.isclose(
                metric.lower_bound, -margin, rel_tol=1e-12, abs_tol=1e-12
            ):
                verdict = "PASS"
            elif metric.upper_bound < -margin and not math.isclose(
                metric.upper_bound, -margin, rel_tol=1e-12, abs_tol=1e-12
            ):
                verdict = "FAIL"
            else:
                verdict = "INCONCLUSIVE"
            metric_gates[name] = verdict
        quality_gate = (
            "FAIL"
            if "FAIL" in metric_gates.values()
            else "PASS"
            if metric_gates and all(value == "PASS" for value in metric_gates.values())
            else "INCONCLUSIVE"
        )
    return ComparisonResult(
        comparable=comparable,
        reference_endpoint=ref_endpoint,
        candidate_endpoint=cand_endpoint,
        manifest_differences=differences,
        metrics=metrics,
        policy=policy,
        metric_gates=metric_gates,
        quality_gate=quality_gate,
        reasons=reasons,
    )
