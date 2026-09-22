"""Canonical JSON, Markdown, and JUnit reports from validated result records."""

from __future__ import annotations

import html
import json
import os
import re
import tempfile
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET

from .evidence import atomic_json, load_resume_state, validate_checkpoint
from .records import (
    AttemptMetric,
    ComparisonResult,
    Manifest,
    RunReportContext,
    RunResult,
)

_STATUSES = ("PASS", "FAIL", "ERROR", "SKIP", "INCONCLUSIVE")
_XML_FORBIDDEN = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


def exit_status(result: RunResult) -> int:
    """Return the honest process status for a run, with incomplete taking priority."""

    if not result.complete:
        return 2
    if result.report is None:
        return result.exit_code
    required = set(result.report.required_case_ids)
    selected = [item for item in result.case_results if item.case_id in required]
    expected_required = len(required) * len(result.report.endpoints)
    if len(selected) != expected_required or not selected or not result.enabled_gates:
        return 2
    if any(
        not item.completed or item.status in ("ERROR", "INCONCLUSIVE")
        for item in selected
    ):
        return 2
    if any(item.status == "FAIL" for item in selected):
        return 1
    return 0


def render_report(result: RunResult | ComparisonResult, format: str) -> str:
    """Render a result without consulting credentials or raw evidence bodies."""

    format = format.lower()
    if format == "json":
        value = result.model_dump(mode="json")
        if isinstance(result, RunResult):
            value["counts"] = _run_counts(result)
            value["exit_code"] = exit_status(result)
        return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    if format == "markdown":
        return (
            _run_markdown(result)
            if isinstance(result, RunResult)
            else _comparison_markdown(result)
        )
    if format == "junit":
        return (
            _run_junit(result)
            if isinstance(result, RunResult)
            else _comparison_junit(result)
        )
    raise ValueError(f"Unknown report format: {format}")


def comparison_exit_status(result: ComparisonResult) -> int:
    """Return comparison status without upgrading report-only observations."""

    if not result.comparable:
        return 2
    if result.policy is None:
        return 0
    if result.quality_gate == "FAIL":
        return 1
    if result.quality_gate != "PASS":
        return 2
    return 0


def load_run_evidence(directory: Path) -> tuple[Manifest, RunResult]:
    """Load a stored run and validate every available integrity artifact."""

    directory = Path(directory)
    from .runner import validate_manifest

    manifest = Manifest.model_validate_json((directory / "manifest.json").read_text())
    validate_manifest(manifest)
    run = RunResult.model_validate_json((directory / "summary.json").read_text())
    if run.manifest_hash != manifest.manifest_hash:
        raise ValueError("Run summary manifest hash mismatch")
    _run_counts(run)
    if (directory / "evidence-index.json").exists():
        validate_checkpoint(directory)
    journals = [directory / "attempts.jsonl", directory / "results.jsonl"]
    integrity = "summary-only"
    if any(path.exists() for path in journals):
        if not all(path.exists() for path in journals):
            raise ValueError("Incomplete evidence journal set")
        state = load_resume_state(directory, manifest.manifest_hash)
        summary_results = {
            (item.endpoint, item.case_id): item.model_dump(mode="json")
            for item in run.case_results
        }
        journal_results = {
            (item.endpoint, item.case_id): item.model_dump(mode="json")
            for item in state.results
        }
        if summary_results != journal_results:
            raise ValueError("Run aggregate results do not match evidence journals")
        expected_identities = {
            (endpoint, case.id)
            for endpoint in manifest.endpoints
            for case in manifest.cases
        }
        expected_complete = (
            set(journal_results) == expected_identities
            and all(item.completed for item in state.results)
            and not any(
                record.get("disposition") == "retained_torn_tail"
                for record in state.incomplete_records
            )
        )
        if run.complete != expected_complete:
            raise ValueError(
                "Run aggregate completion does not match evidence journals"
            )
        if run.resume_dispositions != state.incomplete_records:
            raise ValueError("Run aggregate interruption state does not match journals")
        cases = {case.id: case for case in manifest.cases}
        projected = [_attempt_projection(attempt) for attempt in state.prior_attempts]
        projected.extend(
            _reservation_projection(record, cases)
            for record in state.incomplete_records
            if record.get("disposition") == "interrupted_attempt"
        )

        # Reservations and completed attempts occupy separate journal projections;
        # resume appends new attempts after historical reservations in the summary.
        def attempt_order(item):
            return item.endpoint, item.case_id, item.attempt_number

        if run.attempt_metrics and sorted(
            run.attempt_metrics, key=attempt_order
        ) != sorted(projected, key=attempt_order):
            raise ValueError("Run aggregate attempts do not match evidence journals")
        usage = Counter(item.endpoint for item in projected)
        if {name: usage[name] for name in manifest.endpoints} != run.budget_usage:
            raise ValueError(
                "Run aggregate budget usage does not match evidence journals"
            )
        if not run.attempt_metrics:
            run = run.model_copy(update={"attempt_metrics": projected})
        integrity = "verified"
    context = RunReportContext(
        run_id=manifest.run_id,
        created_at=manifest.created_at,
        profile=manifest.profile_snapshot.id if manifest.profile_snapshot else None,
        profile_hash=manifest.profile_hash,
        dataset_hash=manifest.dataset_hash,
        endpoints={
            name: endpoint.model for name, endpoint in manifest.endpoints.items()
        },
        required_case_ids=[case.id for case in manifest.cases if case.required],
        evidence_links={
            f"{result.endpoint}:{result.case_id}": [
                f"attempts.jsonl#sha256-{ref}" for ref in result.attempt_refs
            ]
            for result in run.case_results
        },
        integrity=integrity,
    )
    run = run.model_copy(update={"report": context})
    expected_exit = exit_status(run)
    if run.exit_code != expected_exit:
        raise ValueError("Run aggregate exit status is inconsistent")
    if run.enabled_gates != manifest.gates:
        raise ValueError("Run aggregate enabled gates do not match manifest")
    return manifest, run


def load_stored_result(path: Path) -> RunResult | ComparisonResult:
    """Load a run directory, comparison directory, or standalone summary."""

    path = Path(path)
    if path.is_dir() and (path / "manifest.json").exists():
        return load_run_evidence(path)[1]
    source = path / "summary.json" if path.is_dir() else path
    value = json.loads(source.read_text())
    if "manifest_hash" in value:
        if path.is_dir():
            raise ValueError("Run directory is missing manifest.json")
        result = RunResult.model_validate(value)
        _run_counts(result)
        if result.report:
            result = result.model_copy(
                update={
                    "report": result.report.model_copy(
                        update={"integrity": "summary-only"}
                    )
                }
            )
        return result
    result = ComparisonResult.model_validate(value)
    if result.report:
        result = result.model_copy(
            update={
                "report": result.report.model_copy(update={"integrity": "summary-only"})
            }
        )
    return result


def write_report_bundle(
    directory: Path,
    result: RunResult | ComparisonResult,
    *,
    allow_existing: bool = False,
) -> None:
    """Write the canonical aggregate and its two derived renderings atomically."""

    directory = Path(directory)
    if directory.exists() and not allow_existing and any(directory.iterdir()):
        raise ValueError(
            f"Output directory already exists and is not empty: {directory}"
        )
    directory.mkdir(parents=True, exist_ok=True)
    canonical = json.loads(render_report(result, "json"))
    atomic_json(directory / "summary.json", canonical)
    _atomic_text(directory / "summary.md", render_report(result, "markdown"))
    _atomic_text(directory / "junit.xml", render_report(result, "junit"))


def _atomic_text(path: Path, value: str) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _attempt_projection(attempt) -> AttemptMetric:
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


def _reservation_projection(record: dict, cases: dict) -> AttemptMetric:
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


def _run_counts(result: RunResult) -> dict[str, int]:
    actual = Counter(item.status for item in result.case_results)
    for status in _STATUSES:
        declared = result.counts.get(status, 0)
        if declared != actual[status]:
            raise ValueError(f"Run summary count mismatch for {status}")
    if set(result.counts) - set(_STATUSES):
        raise ValueError("Run summary contains an unknown status category")
    return {status: actual[status] for status in _STATUSES}


def _md(value: object) -> str:
    text = " ".join(str(value).splitlines())
    text = html.escape(text, quote=True)
    for character in ("\\", "|", "[", "]", "(", ")", "!"):
        text = text.replace(character, "\\" + character)
    return text


def _date(value) -> str:
    return (
        value.isoformat().replace("+00:00", "Z") if value is not None else "unavailable"
    )


def _run_markdown(result: RunResult) -> str:
    counts = _run_counts(result)
    context = result.report
    lines = [
        "# Verification run",
        "",
        f"- Verdict: `{exit_status(result)}`",
        f"- Complete: `{str(result.complete).lower()}`",
        f"- Run: `{_md(context.run_id if context else 'unavailable')}`",
        f"- Date: `{_md(_date(context.created_at) if context else 'unavailable')}`",
        f"- Profile: `{_md(context.profile if context and context.profile else 'unavailable')}`",
        f"- Profile hash: `{_md(context.profile_hash if context and context.profile_hash else 'unavailable')}`",
        f"- Dataset hash: `{_md(context.dataset_hash if context and context.dataset_hash else 'unavailable')}`",
        f"- Integrity: `{_md(context.integrity if context else 'unavailable')}`",
        "",
        "## Endpoints and models",
        "",
        "| Endpoint | Model |",
        "| --- | --- |",
    ]
    endpoints = (
        context.endpoints.items()
        if context
        else ((name, "unavailable") for name in result.budget_usage)
    )
    lines.extend(f"| {_md(name)} | {_md(model)} |" for name, model in endpoints)
    lines.extend(["", "## Status counts", "", "| Status | Count |", "| --- | ---: |"])
    lines.extend(f"| {status} | {counts[status]} |" for status in _STATUSES)
    lines.extend(
        [
            "",
            "## Required gates",
            "",
            *(f"- `{_md(gate)}`" for gate in result.enabled_gates),
            *(["- `none`"] if not result.enabled_gates else []),
            "",
            "## Cases",
            "",
            "| Case | Endpoint | Status | Required | Evidence | Detail |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    required = set(context.required_case_ids if context else [])
    links = context.evidence_links if context else {}
    for item in result.case_results:
        reasons = [assertion.reason for assertion in item.assertions]
        if item.reason:
            reasons.append(item.reason)
        evidence = (
            ", ".join(
                f"[{index + 1}]({_md(link)})"
                for index, link in enumerate(
                    links.get(
                        f"{item.endpoint}:{item.case_id}", links.get(item.case_id, [])
                    )
                )
            )
            or "unavailable"
        )
        lines.append(
            f"| {_md(item.case_id)} | {_md(item.endpoint)} | {item.status} | "
            f"{'unavailable' if context is None else 'yes' if item.case_id in required else 'no'} | {evidence} | {_md('; '.join(reasons))} |"
        )
    differences = [
        (item, assertion.observed)
        for item in result.case_results
        for assertion in item.assertions
        if assertion.id == "COMPATIBILITY_OBSERVATION"
    ]
    if differences:
        lines.extend(
            [
                "",
                "## Compatibility observations",
                "",
                "Status agreement alone does not establish functional correctness; see the case verdict above.",
                "",
                "| Case | Endpoint | Observed HTTP | Reference HTTP | Reference date | Basis | Comparison |",
                "| --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for item, observed in differences:
            lines.append(
                f"| {_md(item.case_id)} | {_md(item.endpoint)} | {observed['status_code']} | {observed['reference_status']} | {_md(observed['reference_date'])} | {_md(observed['reference_basis'])} | {'MATCH' if observed['matches_reference'] else 'DIFFERENCE'} |"
            )
    return "\n".join(lines) + "\n"


def _comparison_counts(result: ComparisonResult) -> dict[str, int]:
    supplied = result.report.status_counts if result.report else {}
    if set(supplied) - set(_STATUSES):
        raise ValueError("Comparison summary contains an unknown status category")
    return {status: supplied.get(status, 0) for status in _STATUSES}


def _comparison_verdict(result: ComparisonResult) -> str:
    if result.report and result.report.quality_verdict:
        return (
            "INCONCLUSIVE (report only)"
            if result.report.quality_verdict == "REPORT_ONLY"
            else result.report.quality_verdict
        )
    if result.policy is None:
        return "INCONCLUSIVE (report only)"
    return result.quality_gate or "INCONCLUSIVE"


def _comparison_markdown(result: ComparisonResult) -> str:
    context = result.report
    counts = _comparison_counts(result)
    lines = [
        "# Verification comparison",
        "",
        f"- Quality gate | {_comparison_verdict(result)}",
        f"- Comparable: `{str(result.comparable).lower()}`",
        f"- Date: `{_md(_date(context.created_at) if context else 'unavailable')}`",
        f"- Profile: `{_md(context.profile if context and context.profile else 'unavailable')}`",
        f"- Profile hash: `{_md(context.profile_hash if context and context.profile_hash else 'unavailable')}`",
        f"- Dataset hash: `{_md(context.dataset_hash if context and context.dataset_hash else 'unavailable')}`",
        f"- Cases: `{context.case_count if context else 'unavailable'}`",
        f"- Required cases: `{context.required_case_count if context else 'unavailable'}`",
        f"- Integrity: `{_md(context.integrity if context else 'unavailable')}`",
        "",
        "## Endpoints and models",
        "",
        "| Endpoint | Model |",
        "| --- | --- |",
    ]
    models = context.endpoint_models.items() if context else ()
    lines.extend(f"| {_md(name)} | {_md(model)} |" for name, model in models)
    lines.extend(["", "## Status counts", "", "| Status | Count |", "| --- | ---: |"])
    lines.extend(f"| {status} | {counts[status]} |" for status in _STATUSES)
    lines.extend(["", "## Required gates", ""])
    lines.extend(
        f"- `{_md(gate)}`" for gate in (context.enabled_gates if context else [])
    )
    if not context or not context.enabled_gates:
        lines.append("- `none` (quality observations are report only)")
    lines.extend(
        [
            "",
            "## Metrics",
            "",
            "| Metric | Reference | Candidate | Difference | Gate |",
            "| --- | ---: | ---: | ---: | --- |",
        ]
    )
    for name, metric in result.metrics.items():
        lines.append(
            f"| {_md(name)} | {_number(metric.reference_value)} | {_number(metric.value)} | "
            f"{_number(metric.difference)} | {result.metric_gates.get(name, 'REPORT_ONLY')} |"
        )
    lines.extend(
        [
            "",
            "## Metric populations and uncertainty",
            "",
            "| Metric | Reference numerator/denominator | Reference unavailable | Candidate numerator/denominator | Candidate unavailable | Paired observations / prompts / repetitions | Missing reference / candidate | Difference interval | Confidence | Bootstrap seed |",
            "| --- | ---: | ---: | ---: | ---: | --- | --- | --- | ---: | ---: |",
        ]
    )
    for name, metric in result.metrics.items():
        lines.append(
            f"| {_md(name)} | {_number(metric.reference_numerator)}/{metric.reference_denominator} | {metric.reference_unavailable} | "
            f"{_number(metric.numerator)}/{metric.denominator} | {metric.unavailable} | "
            f"{metric.paired_observations} / {metric.paired_distinct_prompts} / {metric.paired_repetitions} | "
            f"{metric.missing_reference} / {metric.missing_candidate} | "
            f"{_number(metric.lower_bound)} to {_number(metric.upper_bound)} | "
            f"{_number(metric.confidence_level)} | {metric.bootstrap_seed if metric.bootstrap_seed is not None else 'unavailable'} |"
        )
    lines.extend(["", "## Comparison policy", ""])
    if result.policy:
        policy = result.policy
        lines.extend(
            [
                f"- Minimum distinct prompts: {policy.minimum_distinct_prompts}",
                f"- Minimum repetitions: {policy.minimum_repetitions}",
                f"- Confidence level: {_number(policy.confidence_level)}",
                f"- Bootstrap samples: {policy.bootstrap_samples}",
                f"- Bootstrap seed: {policy.bootstrap_seed}",
                *[
                    f"- Allowed drop for {_md(name)}: {_number(margin)}"
                    for name, margin in policy.allowed_drops.items()
                ],
            ]
        )
    else:
        lines.append("- unavailable (report only)")
    if context and context.evidence_links:
        lines.extend(
            ["", "## Evidence", "", "| Case | Stored evidence |", "| --- | --- |"]
        )
        for case_id, links in context.evidence_links.items():
            rendered = ", ".join(
                f"[{index + 1}]({_md(link)})" for index, link in enumerate(links)
            )
            lines.append(f"| {_md(case_id)} | {rendered} |")
    if result.reasons:
        lines.extend(
            ["", "## Reasons", "", *[f"- {_md(reason)}" for reason in result.reasons]]
        )
    return "\n".join(lines) + "\n"


def _number(value: float | None) -> str:
    return "unavailable" if value is None else f"{value:.6g}"


def _xml(value: object) -> str:
    return _XML_FORBIDDEN.sub("�", str(value))


def _run_junit(result: RunResult) -> str:
    counts = _run_counts(result)
    suite = ET.Element(
        "testsuite",
        name="deepseek-provider-verifier",
        tests=str(sum(counts.values())),
        failures=str(counts["FAIL"]),
        errors=str(counts["ERROR"] + counts["INCONCLUSIVE"]),
        skipped=str(counts["SKIP"]),
    )
    properties = ET.SubElement(suite, "properties")
    context = result.report
    _property(properties, "complete", str(result.complete).lower())
    _property(properties, "exit_code", exit_status(result))
    _property(
        properties, "date", _date(context.created_at) if context else "unavailable"
    )
    _property(
        properties,
        "profile",
        context.profile if context and context.profile else "unavailable",
    )
    _property(
        properties,
        "profile_hash",
        context.profile_hash if context and context.profile_hash else "unavailable",
    )
    _property(
        properties,
        "dataset_hash",
        context.dataset_hash if context and context.dataset_hash else "unavailable",
    )
    _property(properties, "integrity", context.integrity if context else "unavailable")
    for name, model in context.endpoints.items() if context else ():
        _property(properties, f"endpoint.{name}.model", model)
    for status, count in counts.items():
        _property(properties, f"count.{status}", count)
    for gate in result.enabled_gates:
        _property(properties, "required_gate", gate)
    for item in result.case_results:
        case = ET.SubElement(
            suite, "testcase", name=_xml(item.case_id), classname=_xml(item.endpoint)
        )
        detail = "; ".join(
            [
                *(a.reason for a in item.assertions),
                *([item.reason] if item.reason else []),
            ]
        )
        if item.status == "FAIL":
            ET.SubElement(
                case, "failure", message=_xml(detail or "required assertion failed")
            )
        elif item.status in ("ERROR", "INCONCLUSIVE"):
            ET.SubElement(case, "error", message=_xml(detail or item.status))
        elif item.status == "SKIP":
            ET.SubElement(case, "skipped", message=_xml(detail or "skipped"))
        item_links = (
            context.evidence_links.get(
                f"{item.endpoint}:{item.case_id}",
                context.evidence_links.get(item.case_id, []),
            )
            if context
            else []
        )
        if item_links:
            ET.SubElement(case, "system-out").text = _xml("\n".join(item_links))
    return ET.tostring(suite, encoding="unicode", xml_declaration=True) + "\n"


def _comparison_junit(result: ComparisonResult) -> str:
    report_only = result.policy is None
    metrics = {
        name: "REPORT_ONLY"
        if report_only
        else result.metric_gates.get(name, "REPORT_ONLY")
        for name in result.metrics
    }
    suite = ET.Element(
        "testsuite",
        name="deepseek-provider-verifier-comparison",
        tests=str(len(metrics)),
        failures=str(sum(value == "FAIL" for value in metrics.values())),
        errors=str(sum(value == "INCONCLUSIVE" for value in metrics.values())),
        skipped=str(sum(value == "REPORT_ONLY" for value in metrics.values())),
    )
    properties = ET.SubElement(suite, "properties")
    context = result.report
    _property(properties, "quality_gate", _comparison_verdict(result))
    _property(properties, "comparable", str(result.comparable).lower())
    _property(
        properties, "date", _date(context.created_at) if context else "unavailable"
    )
    _property(
        properties,
        "profile",
        context.profile if context and context.profile else "unavailable",
    )
    _property(
        properties,
        "profile_hash",
        context.profile_hash if context and context.profile_hash else "unavailable",
    )
    _property(
        properties,
        "dataset_hash",
        context.dataset_hash if context and context.dataset_hash else "unavailable",
    )
    _property(
        properties, "case_count", context.case_count if context else "unavailable"
    )
    _property(properties, "integrity", context.integrity if context else "unavailable")
    _property(
        properties,
        "required_case_count",
        context.required_case_count if context else "unavailable",
    )
    _property(
        properties,
        "policy",
        json.dumps(result.policy.model_dump(mode="json"), sort_keys=True)
        if result.policy
        else "unavailable",
    )
    for name, model in context.endpoint_models.items() if context else ():
        _property(properties, f"endpoint.{name}.model", model)
    for status, count in _comparison_counts(result).items():
        _property(properties, f"count.{status}", count)
    for gate in context.enabled_gates if context else ():
        _property(properties, "required_gate", gate)
    for name, verdict in metrics.items():
        case = ET.SubElement(suite, "testcase", name=_xml(name), classname="quality")
        metric = result.metrics.get(name)
        if metric:
            ET.SubElement(case, "system-out").text = _xml(
                json.dumps(metric.model_dump(mode="json"), sort_keys=True)
            )
        if verdict == "FAIL":
            ET.SubElement(case, "failure", message="quality gate failed")
        elif verdict == "INCONCLUSIVE":
            ET.SubElement(
                case,
                "error",
                message=_xml(
                    "; ".join(
                        reason
                        for reason in result.reasons
                        if reason.startswith(name + ":")
                    )
                    or "quality gate inconclusive"
                ),
            )
        elif verdict == "REPORT_ONLY":
            ET.SubElement(case, "skipped", message="report-only observation")
    if context and context.evidence_links:
        evidence = [
            f"{case_id}\t{link}"
            for case_id, links in context.evidence_links.items()
            for link in links
        ]
        ET.SubElement(suite, "system-out").text = _xml("\n".join(evidence))
    return ET.tostring(suite, encoding="unicode", xml_declaration=True) + "\n"


def _property(parent, name: str, value: object) -> None:
    ET.SubElement(parent, "property", name=_xml(name), value=_xml(value))
