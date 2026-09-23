"""Separate acceptance artifacts derived from a single validated decision."""

import json
from collections import Counter
from xml.etree import ElementTree as ET

from .catalog import content_hash
from .reports import _XML_FORBIDDEN


def _text(value):
    return _XML_FORBIDDEN.sub("", str(value))


def _md(value):
    import html

    return html.escape(_text(value)).replace("|", "&#124;").replace("\n", " ")


def render_acceptance(result, format):
    if content_hash(result.policy.model_dump(mode="json")) != result.policy_hash:
        raise ValueError("Acceptance policy hash mismatch")
    if format == "json":
        return (
            json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
        )
    counts = dict(Counter(f.status for f in result.required_facets))
    diagnostics = dict(Counter(f.status for f in result.diagnostic_facets))
    if format == "markdown":
        lines = [
            f"# Acceptance: {result.verdict}",
            "",
            f"Policy: `{_md(result.policy.id)}` / `{result.policy_hash}`",
            f"Source manifest: `{result.source_manifest_hash}`",
            f"Scorer: `{result.scorer_revision}`",
            f"Integrity: **{result.integrity}**; exit: {result.exit_code}",
            f"Required coverage: {_md(counts)}",
            f"Diagnostics (report only): {_md(diagnostics)}",
            "Uncertified capabilities: "
            + (_md(", ".join(result.uncertified_capabilities)) or "none"),
            "",
            *[_md(r) for r in result.reasons],
            "",
            "| Endpoint / case | Facet | Scope | Raw status | Reason |",
            "| --- | --- | --- | --- | --- |",
        ]
        for scope, facets in [
            ("required", result.required_facets),
            ("diagnostic", result.diagnostic_facets),
        ]:
            for f in facets:
                lines.append(
                    "| "
                    + " | ".join(
                        _md(v)
                        for v in (
                            f"{f.endpoint}/{f.case_id}",
                            f.name,
                            scope,
                            f.status,
                            f.reason,
                        )
                    )
                    + " |"
                )
        return "\n".join(lines) + "\n"
    if format != "junit":
        raise ValueError("Unknown acceptance report format")
    # Only the aggregate acceptance testcase affects CI. Facets retain raw statuses
    # as properties; diagnostics are explicitly skipped, never recast as PASS.
    root = ET.Element(
        "testsuite",
        name="compatibility acceptance",
        tests=str(1 + len(result.diagnostic_facets)),
        failures=str(int(result.exit_code == 1)),
        errors=str(int(result.exit_code == 2)),
        skipped=str(len(result.diagnostic_facets)),
    )
    props = ET.SubElement(root, "properties")
    for name, value in [
        ("policy_id", result.policy.id),
        ("policy_hash", result.policy_hash),
        ("scorer_revision", result.scorer_revision),
        ("source_manifest_hash", result.source_manifest_hash),
        ("integrity", result.integrity),
        ("required_counts", counts),
    ]:
        ET.SubElement(props, "property", name=name, value=_text(value))
    test = ET.SubElement(root, "testcase", name="required acceptance gates")
    if result.exit_code:
        ET.SubElement(
            test,
            "failure" if result.exit_code == 1 else "error",
            message=result.verdict,
        ).text = _text("\n".join(result.reasons))
    ET.SubElement(test, "system-out").text = _text(
        json.dumps([f.model_dump(mode="json") for f in result.required_facets])
    )
    for f in result.diagnostic_facets:
        test = ET.SubElement(
            root,
            "testcase",
            name=_text(f"{f.endpoint}/{f.case_id}/{f.name}"),
            classname="report-only diagnostics",
        )
        ET.SubElement(test, "skipped", message=f"report only; raw status {f.status}")
        props = ET.SubElement(test, "properties")
        ET.SubElement(props, "property", name="raw_status", value=f.status)
        ET.SubElement(test, "system-out").text = _text(f.reason)
    return ET.tostring(root, encoding="unicode") + "\n"


def write_acceptance(directory, result, *, allow_existing=False):
    from pathlib import Path

    directory = Path(directory)
    files = {
        "acceptance.json": render_acceptance(result, "json"),
        "acceptance.md": render_acceptance(result, "markdown"),
        "acceptance.junit.xml": render_acceptance(result, "junit"),
    }
    if directory.exists() and not allow_existing and any(directory.iterdir()):
        raise ValueError("Acceptance output directory is not empty")
    if any((directory / name).exists() for name in files):
        raise ValueError("Acceptance artifacts already exist")
    directory.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (directory / name).write_text(text, encoding="utf-8")
