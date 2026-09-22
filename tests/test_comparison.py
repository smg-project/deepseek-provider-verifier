"""Reference/candidate comparisons preserve denominators and uncertainty."""

from __future__ import annotations

import pytest
from test_runner import manifest

from deepseek_provider_verifier.comparison import compare_runs
from deepseek_provider_verifier.records import (
    AttemptMetric,
    ComparisonPolicy,
    MetricObservation,
    RunResult,
)


def run_result(m, endpoint, values, *, attempts=()):
    cases = []
    for case, value in zip(m.cases, values, strict=True):
        metrics = []
        for name, observed in value.items():
            metrics.append(
                MetricObservation(
                    name=name,
                    value=observed,
                    scored=observed is not None,
                    denominator=1 if observed is not None else 0,
                )
            )
        cases.append(
            {
                "case_id": case.id,
                "endpoint": endpoint,
                "prompt_id": case.prompt_id,
                "repetition": case.repetition,
                "status": "INCONCLUSIVE",
                "assertions": [],
                "metric_observations": metrics,
                "attempt_refs": [],
            }
        )
    return RunResult(
        manifest_hash=m.manifest_hash,
        complete=True,
        case_results=cases,
        counts={"INCONCLUSIVE": len(cases)},
        budget_usage={endpoint: len(attempts)},
        enabled_gates=[],
        exit_code=2,
        attempt_metrics=list(attempts),
    )


def endpoint_manifest(
    *, prompts=2, repetitions=1, release="synthetic-v1", max_requests=1, retries=0
):
    m = manifest(
        count=prompts,
        repetitions=repetitions,
        max_requests=max_requests,
        retries=retries,
    )
    endpoint = m.endpoints["candidate"].model_copy(update={"model_release": release})
    cases = [
        case.model_copy(update={"prompt_id": case.template_id}) for case in m.cases
    ]
    return m.model_copy(update={"endpoints": {"candidate": endpoint}, "cases": cases})


def policy(**updates):
    values = {
        "allowed_drops": {"task_success": 0.1},
        "minimum_distinct_prompts": 2,
        "minimum_repetitions": 1,
        "confidence_level": 0.9,
        "bootstrap_samples": 500,
        "bootstrap_seed": 19,
    }
    return ComparisonPolicy(**(values | updates))


def test_zero_denominator_is_unavailable_not_perfect():
    m = endpoint_manifest()
    empty = run_result(m, "candidate", [{}, {}])

    result = compare_runs(empty, empty, (m, m), policy=None)

    schema = result.metrics["schema_accuracy"]
    assert schema.value is None
    assert schema.denominator == 0
    assert schema.unavailable == 2
    assert result.quality_gate is None


def test_end_to_end_uses_every_planned_trial_and_preserves_missingness():
    m = endpoint_manifest(prompts=3)
    reference = run_result(
        m,
        "candidate",
        [
            {"end_to_end_success": 1.0},
            {"end_to_end_success": 1.0},
            {},
        ],
    )
    candidate = run_result(
        m,
        "candidate",
        [
            {"end_to_end_success": 1.0},
            {"end_to_end_success": 0.0},
            {},
        ],
    )

    result = compare_runs(reference, candidate, (m, m), policy=None)

    metric = result.metrics["end_to_end_success"]
    assert (metric.reference_numerator, metric.reference_denominator) == (2, 3)
    assert (metric.numerator, metric.denominator) == (1, 3)
    assert metric.reference_unavailable == 1
    assert metric.unavailable == 1


def test_first_http_availability_uses_actual_initial_requests_not_request_ceiling():
    m = endpoint_manifest(prompts=1, max_requests=2, retries=1)
    attempts = [
        AttemptMetric(
            endpoint="candidate",
            case_id=m.cases[0].id,
            prompt_id=m.cases[0].prompt_id,
            repetition=0,
            step=0,
            retry=0,
            attempt_number=1,
            status_code=200,
            http_exchange_completed=True,
            error_type="INVALID_JSON",
        ),
        AttemptMetric(
            endpoint="candidate",
            case_id=m.cases[0].id,
            prompt_id=m.cases[0].prompt_id,
            repetition=0,
            step=1,
            retry=0,
            attempt_number=2,
            status_code=None,
            http_exchange_completed=False,
            error_type="ReadTimeout",
        ),
        AttemptMetric(
            endpoint="candidate",
            case_id=m.cases[0].id,
            prompt_id=m.cases[0].prompt_id,
            repetition=0,
            step=1,
            retry=1,
            attempt_number=3,
            status_code=200,
            http_exchange_completed=True,
        ),
    ]
    run = run_result(m, "candidate", [{}], attempts=attempts)

    result = compare_runs(run, run, (m, m), policy=None)

    first = result.metrics["first_http_2xx_rate"]
    eventual = result.metrics["eventual_http_2xx_rate"]
    assert (first.numerator, first.denominator) == (1, 2)
    assert (eventual.numerator, eventual.denominator) == (2, 2)
    assert result.metrics["http_retry_attempts"].numerator == 1
    assert first.unavailable == 0  # no planned HTTP allowance is fabricated


def test_latency_is_conditional_on_recorded_successful_http_attempts():
    m = endpoint_manifest(prompts=1, max_requests=2)
    attempts = [
        AttemptMetric(
            endpoint="candidate",
            case_id=m.cases[0].id,
            prompt_id=m.cases[0].prompt_id,
            repetition=0,
            step=0,
            retry=0,
            attempt_number=1,
            status_code=200,
            http_exchange_completed=True,
            timings={"headers_seconds": 0.2, "total_seconds": 0.8},
        ),
        AttemptMetric(
            endpoint="candidate",
            case_id=m.cases[0].id,
            prompt_id=m.cases[0].prompt_id,
            repetition=0,
            step=1,
            retry=0,
            attempt_number=2,
            status_code=503,
            http_exchange_completed=True,
            timings={"headers_seconds": 0.1, "total_seconds": 0.3},
        ),
    ]
    run = run_result(m, "candidate", [{}], attempts=attempts)

    result = compare_runs(run, run, (m, m), policy=None)

    headers = result.metrics["latency_headers_seconds"]
    assert (headers.value, headers.denominator, headers.unavailable) == (0.2, 1, 0)
    meaningful = result.metrics["latency_first_meaningful_output_seconds"]
    assert meaningful.value is None and meaningful.unavailable == 1


def test_legacy_attempt_without_completion_marker_is_unavailable_not_zero():
    m = endpoint_manifest(prompts=1)
    legacy = AttemptMetric(
        endpoint="candidate",
        case_id=m.cases[0].id,
        prompt_id=m.cases[0].prompt_id,
        repetition=0,
        step=0,
        retry=0,
        attempt_number=1,
        status_code=200,
        http_exchange_completed=None,
    )
    run = run_result(m, "candidate", [{}], attempts=[legacy])

    result = compare_runs(run, run, (m, m), policy=None)

    metric = result.metrics["first_http_2xx_rate"]
    assert metric.value is None
    assert metric.denominator == 0
    assert metric.unavailable == 1


def test_schema_accuracy_counts_each_emitted_call_and_unscored_calls():
    m = endpoint_manifest(prompts=2)
    reference = run_result(
        m,
        "candidate",
        [
            {"tool_argument_schema": 1.0},
            {"tool_argument_schema": 1.0},
        ],
    )
    candidate = run_result(
        m,
        "candidate",
        [
            {"tool_argument_schema": 1.0},
            {"tool_argument_schema": None},
        ],
    )

    result = compare_runs(reference, candidate, (m, m), policy=None)

    schema = result.metrics["schema_accuracy"]
    assert (schema.numerator, schema.denominator, schema.unavailable) == (1, 1, 1)
    assert schema.reference_denominator == 2


def test_tool_trigger_confusion_matrix_has_exact_oracle_denominators_and_interval():
    m = endpoint_manifest(prompts=4)
    observations = [
        {"tool_trigger": 1.0, "expected_tool_trigger": 1.0},
        {"tool_trigger": 1.0, "expected_tool_trigger": 1.0},
        {"tool_trigger": 0.0, "expected_tool_trigger": 0.0},
        {"tool_trigger": 0.0, "expected_tool_trigger": 0.0},
    ]
    reference = run_result(m, "candidate", observations)
    candidate = run_result(m, "candidate", observations)

    result = compare_runs(
        reference,
        candidate,
        (m, m),
        policy=policy(allowed_drops={"tool_trigger_f1": 0.05}),
    )

    metric = result.metrics["tool_trigger_f1"]
    assert metric.counts == {
        "true_positive": 2,
        "false_positive": 0,
        "true_negative": 2,
        "false_negative": 0,
    }
    assert (metric.numerator, metric.denominator, metric.unavailable) == (4, 4, 0)
    assert metric.lower_bound == 0 and metric.upper_bound == 0
    assert result.metric_gates["tool_trigger_f1"] == "PASS"


def test_unknown_release_reports_metrics_but_quality_gate_is_inconclusive():
    m = endpoint_manifest(release="unknown")
    run = run_result(
        m,
        "candidate",
        [{"task_success": 1.0}, {"task_success": 1.0}],
    )

    result = compare_runs(run, run, (m, m), policy=policy())

    assert result.metrics["task_success"].value == 1.0
    assert result.quality_gate == "INCONCLUSIVE"
    assert any("checkpoint" in reason.lower() for reason in result.reasons)


def test_model_alias_requires_explicit_mapping_for_descriptive_comparison():
    reference_manifest = endpoint_manifest()
    candidate_manifest = endpoint_manifest()
    candidate_endpoint = candidate_manifest.endpoints["candidate"].model_copy(
        update={"model": "served-alias"}
    )
    candidate_manifest = candidate_manifest.model_copy(
        update={"endpoints": {"candidate": candidate_endpoint}}
    )
    reference = run_result(reference_manifest, "candidate", [{}, {}])
    candidate = run_result(candidate_manifest, "candidate", [{}, {}])

    refused = compare_runs(
        reference, candidate, (reference_manifest, candidate_manifest), policy=None
    )
    mapped = compare_runs(
        reference,
        candidate,
        (reference_manifest, candidate_manifest),
        policy=None,
        model_mapping={"fixture-model": "served-alias"},
    )

    assert not refused.comparable
    assert mapped.comparable
    assert any(d.field == "endpoint.model" for d in mapped.manifest_differences)


def test_multi_endpoint_run_requires_selectors_and_supports_paired_slice():
    m = manifest(count=2, endpoints=2)
    values = [{"task_success": 1.0}, {"task_success": 1.0}]
    reference_slice = run_result(m, "reference", values)
    candidate_slice = run_result(m, "candidate", values)
    paired = reference_slice.model_copy(
        update={
            "case_results": [
                *reference_slice.case_results,
                *candidate_slice.case_results,
            ]
        }
    )

    with pytest.raises(ValueError, match="endpoint selector"):
        compare_runs(paired, paired, (m, m), policy=None)

    compared = compare_runs(
        paired,
        paired,
        (m, m),
        policy=None,
        reference_endpoint="reference",
        candidate_endpoint="candidate",
    )
    assert compared.reference_endpoint == "reference"
    assert compared.candidate_endpoint == "candidate"


def test_duplicate_or_conflicting_trial_identity_is_rejected():
    m = endpoint_manifest(prompts=2)
    run = run_result(m, "candidate", [{}, {}])
    duplicate = run.model_copy(
        update={"case_results": [*run.case_results, run.case_results[0]]}
    )
    conflict = run.model_copy(
        update={
            "case_results": [
                run.case_results[0].model_copy(update={"prompt_id": "relabeled"}),
                run.case_results[1],
            ]
        }
    )

    with pytest.raises(ValueError, match="Duplicate result identity"):
        compare_runs(duplicate, run, (m, m), policy=None)
    with pytest.raises(ValueError, match="prompt/repetition"):
        compare_runs(conflict, run, (m, m), policy=None)

    duplicate_manifest = m.model_copy(update={"cases": [m.cases[0], m.cases[0]]})
    with pytest.raises(ValueError, match="Duplicate manifest trial identity"):
        compare_runs(run, run, (duplicate_manifest, m), policy=None)

    unexpected_attempt = AttemptMetric(
        endpoint="candidate",
        case_id=m.cases[0].id,
        prompt_id=m.cases[0].prompt_id,
        repetition=m.cases[0].repetition,
        step=m.cases[0].max_requests,
        retry=0,
        attempt_number=1,
    )
    bad_attempt_run = run.model_copy(update={"attempt_metrics": [unexpected_attempt]})
    with pytest.raises(ValueError, match="step/retry"):
        compare_runs(bad_attempt_run, run, (m, m), policy=None)


def test_aggregate_observation_denominator_cannot_inflate_independent_coverage():
    m = endpoint_manifest(prompts=2)
    run = run_result(m, "candidate", [{"task_success": 1.0}, {}])
    inflated = run.case_results[0].model_copy(
        update={
            "metric_observations": [
                MetricObservation(name="task_success", value=1.0, denominator=20)
            ]
        }
    )
    inflated_run = run.model_copy(
        update={"case_results": [inflated, run.case_results[1]]}
    )

    with pytest.raises(ValueError, match="denominator one"):
        compare_runs(inflated_run, run, (m, m), policy=None)


def test_paired_prompt_bootstrap_detects_clear_regression():
    m = endpoint_manifest(prompts=4, repetitions=2)
    reference = run_result(m, "candidate", [{"task_success": 1.0} for _ in m.cases])
    candidate = run_result(m, "candidate", [{"task_success": 0.0} for _ in m.cases])

    result = compare_runs(reference, candidate, (m, m), policy=policy())

    metric = result.metrics["task_success"]
    assert metric.paired_distinct_prompts == 4
    assert metric.paired_observations == 8
    assert metric.upper_bound < -0.1
    assert result.metric_gates["task_success"] == "FAIL"
    assert result.quality_gate == "FAIL"


def test_narrow_acceptable_difference_passes_explicit_margin():
    m = endpoint_manifest(prompts=4)
    reference = run_result(
        m,
        "candidate",
        [{"task_success": value} for value in (1.0, 1.0, 1.0, 0.0)],
    )
    candidate = run_result(
        m,
        "candidate",
        [{"task_success": value} for value in (1.0, 1.0, 1.0, 0.0)],
    )

    result = compare_runs(reference, candidate, (m, m), policy=policy())

    assert result.metrics["task_success"].lower_bound == 0
    assert result.metric_gates["task_success"] == "PASS"
    assert result.quality_gate == "PASS"


def test_wide_uncertainty_is_inconclusive():
    m = endpoint_manifest(prompts=4)
    reference = run_result(
        m,
        "candidate",
        [{"task_success": value} for value in (1.0, 0.0, 1.0, 0.0)],
    )
    candidate = run_result(
        m,
        "candidate",
        [{"task_success": value} for value in (0.0, 1.0, 1.0, 0.0)],
    )

    result = compare_runs(reference, candidate, (m, m), policy=policy())

    metric = result.metrics["task_success"]
    assert metric.lower_bound < -0.1 < metric.upper_bound
    assert result.metric_gates["task_success"] == "INCONCLUSIVE"


def test_prompt_cluster_bootstrap_retains_correlated_repetitions():
    m = endpoint_manifest(prompts=2, repetitions=4)
    reference_values = [1.0] * 4 + [0.0] * 4
    candidate_values = [0.0] * 4 + [1.0] * 4
    reference = run_result(
        m,
        "candidate",
        [{"task_success": value} for value in reference_values],
    )
    candidate = run_result(
        m,
        "candidate",
        [{"task_success": value} for value in candidate_values],
    )

    result = compare_runs(
        reference,
        candidate,
        (m, m),
        policy=policy(minimum_repetitions=4),
    )

    metric = result.metrics["task_success"]
    assert metric.paired_observations == 8
    assert metric.paired_distinct_prompts == 2
    assert metric.paired_repetitions == 4
    assert metric.lower_bound == -1.0 and metric.upper_bound == 1.0
    assert result.metric_gates["task_success"] == "INCONCLUSIVE"


def test_noninferiority_boundary_equality_is_inconclusive():
    m = endpoint_manifest(prompts=3)
    reference = run_result(m, "candidate", [{"task_success": 1.0} for _ in m.cases])
    candidate = run_result(m, "candidate", [{"task_success": 0.9} for _ in m.cases])

    result = compare_runs(reference, candidate, (m, m), policy=policy())

    assert result.metrics["task_success"].lower_bound == pytest.approx(-0.1)
    assert result.metric_gates["task_success"] == "INCONCLUSIVE"


def test_one_repeated_prompt_cannot_manufacture_confidence():
    m = endpoint_manifest(prompts=1, repetitions=4)
    reference = run_result(m, "candidate", [{"task_success": 1.0} for _ in m.cases])
    candidate = run_result(m, "candidate", [{"task_success": 1.0} for _ in m.cases])

    result = compare_runs(reference, candidate, (m, m), policy=policy())

    metric = result.metrics["task_success"]
    assert metric.paired_distinct_prompts == 1
    assert metric.lower_bound is None and metric.upper_bound is None
    assert result.metric_gates["task_success"] == "INCONCLUSIVE"


def test_unequal_missingness_stays_visible_in_conditional_pairing():
    m = endpoint_manifest(prompts=3)
    reference = run_result(
        m,
        "candidate",
        [
            {"task_success": 1.0},
            {"task_success": 1.0},
            {"task_success": None},
        ],
    )
    candidate = run_result(
        m,
        "candidate",
        [
            {"task_success": 1.0},
            {"task_success": None},
            {"task_success": 1.0},
        ],
    )

    result = compare_runs(reference, candidate, (m, m), policy=policy())

    metric = result.metrics["task_success"]
    assert metric.paired_observations == 1
    assert metric.missing_reference == 1
    assert metric.missing_candidate == 1
    assert metric.paired_distinct_prompts == 1
    assert result.metric_gates["task_success"] == "INCONCLUSIVE"


def test_workload_differences_are_field_level_and_refuse_comparison():
    reference_manifest = endpoint_manifest()
    candidate_manifest = reference_manifest.model_copy(
        update={"scorer_revision": "changed-scorer"}
    )
    reference = run_result(reference_manifest, "candidate", [{}, {}])
    candidate = run_result(candidate_manifest, "candidate", [{}, {}])

    result = compare_runs(
        reference, candidate, (reference_manifest, candidate_manifest), policy=None
    )

    assert not result.comparable
    assert any(d.field == "scorer_revision" for d in result.manifest_differences)


@pytest.mark.parametrize(
    ("change", "expected_field"),
    [
        ("profile_hash", "profile_hash"),
        ("dataset_hash", "dataset_hash"),
        ("release", "endpoint.model_release"),
        ("mode", ".mode"),
        ("output_limit", ".max_output_tokens"),
    ],
)
def test_required_workload_mismatches_are_reported_at_field_level(
    change, expected_field
):
    reference_manifest = endpoint_manifest()
    candidate_manifest = reference_manifest
    if change in ("profile_hash", "dataset_hash"):
        candidate_manifest = candidate_manifest.model_copy(update={change: "f" * 64})
    elif change == "release":
        endpoint = candidate_manifest.endpoints["candidate"].model_copy(
            update={"model_release": "different-release"}
        )
        candidate_manifest = candidate_manifest.model_copy(
            update={"endpoints": {"candidate": endpoint}}
        )
    else:
        case = candidate_manifest.cases[0].model_copy(
            update={
                "mode": "thinking"
                if change == "mode"
                else candidate_manifest.cases[0].mode,
                "max_output_tokens": 4096
                if change == "output_limit"
                else candidate_manifest.cases[0].max_output_tokens,
            }
        )
        candidate_manifest = candidate_manifest.model_copy(
            update={"cases": [case, *candidate_manifest.cases[1:]]}
        )
    reference = run_result(reference_manifest, "candidate", [{}, {}])
    candidate = run_result(candidate_manifest, "candidate", [{}, {}])

    result = compare_runs(
        reference, candidate, (reference_manifest, candidate_manifest), policy=None
    )

    assert not result.comparable
    assert any(
        difference.field == expected_field or difference.field.endswith(expected_field)
        for difference in result.manifest_differences
    )


def test_manifest_difference_order_is_canonical():
    reference_manifest = endpoint_manifest()
    changed = reference_manifest.cases[0].model_copy(
        update={"mode": "thinking", "max_output_tokens": 4096}
    )
    candidate_manifest = reference_manifest.model_copy(
        update={"cases": [changed, *reference_manifest.cases[1:]]}
    )
    reference = run_result(reference_manifest, "candidate", [{}, {}])
    candidate = run_result(candidate_manifest, "candidate", [{}, {}])

    result = compare_runs(
        reference, candidate, (reference_manifest, candidate_manifest), policy=None
    )

    case_fields = [
        difference.field
        for difference in result.manifest_differences
        if difference.field.startswith("cases[")
    ]
    assert case_fields == sorted(case_fields)
