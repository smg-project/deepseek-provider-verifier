"""Pure compatibility acceptance; original case verdicts are never rewritten."""

from collections import Counter
from importlib.resources import files
from pathlib import Path

from .acceptance_records import (
    FAMILY_FACETS,
    FUNCTIONAL,
    AcceptancePolicy,
    AcceptanceResult,
    Facet,
    case_family,
)
from .catalog import content_hash
from .comparison import _validate_run
from .records import Manifest, RunResult
from .runner import validate_manifest


def load_policy(path: Path | str) -> AcceptancePolicy:
    path = Path(path)
    if path.is_file():
        return AcceptancePolicy.model_validate_json(path.read_text())
    if path.parent != Path("."):
        raise FileNotFoundError(2, "Policy file not found", str(path))
    name = path.name if path.suffix else path.name + ".json"
    bundled = files("deepseek_provider_verifier").joinpath("policies", name)
    if bundled.is_file():
        return AcceptancePolicy.model_validate_json(bundled.read_text())
    return AcceptancePolicy.model_validate_json(
        (Path(__file__).resolve().parents[2] / "policies" / name).read_text()
    )


def assess_run(
    manifest: Manifest,
    run: RunResult,
    facets: list[Facet],
    policy: AcceptancePolicy,
    scorer_revision: str,
) -> AcceptanceResult:
    validate_manifest(manifest)
    _validate_run(run, manifest)
    policy = AcceptancePolicy.model_validate(policy.model_dump(mode="json"))
    if Counter(r.status for r in run.case_results) != Counter(run.counts):
        raise ValueError("Acceptance source counts do not match case results")
    cases = {c.id: c for c in manifest.cases}
    results = {(r.endpoint, r.case_id): r for r in run.case_results}
    selectors = {(s.family, s.facet): s.required for s in policy.selectors}
    expected = set()
    for endpoint in manifest.endpoints:
        for case in manifest.cases:
            family = case_family(case)
            for name in FAMILY_FACETS[family]:
                if (family, name) not in selectors:
                    raise ValueError(
                        "Selected case has no acceptance policy classification"
                    )
                expected.add((endpoint, case.id, name))
    indexed = {}
    for facet in facets:
        key = (facet.endpoint, facet.case_id, facet.name)
        if key in indexed or key not in expected:
            raise ValueError("Duplicate or unexpected acceptance facet")
        source = results.get(key[:2])
        if source is not None and not set(facet.evidence_hashes) <= set(
            source.attempt_refs
        ):
            raise ValueError("Acceptance facet refers to unrelated evidence")
        indexed[key] = facet
    required = []
    diagnostic = []
    reasons = []
    integrity = run.report.integrity if run.report else "unavailable"
    if integrity != "verified":
        reasons.append("Verified source evidence is required for acceptance")
    if not run.complete or any(
        not r.completed or r.status == "ERROR" for r in run.case_results
    ):
        reasons.append("Source execution is incomplete or contains an execution error")
    if len(results) != len(manifest.cases) * len(manifest.endpoints):
        reasons.append("Required source trial results are missing")
    if set(indexed) != expected:
        reasons.append("Required facet coverage is missing")
    for key in sorted(expected):
        endpoint, case_id, name = key
        case = cases[case_id]
        facet = indexed.get(key)
        if facet is None:
            facet = Facet(
                endpoint=endpoint,
                case_id=case_id,
                name=name,
                status="INCONCLUSIVE",
                source_assertion_ids=[],
                evidence_hashes=[],
                reason="Expected facet was not derived",
            )
        gate = selectors[case_family(case), name]
        if gate:
            required.append(facet)
            if facet.status in ("ERROR", "INCONCLUSIVE"):
                reasons.append("A mandatory facet is unmeasured or errored")
            if facet.status == "SKIP" and case.oracle.get("applicable") is not False:
                reasons.append(
                    "Mandatory work was skipped without declared inapplicability"
                )
        else:
            diagnostic.append(facet)
    for endpoint in manifest.endpoints:
        for protocol in manifest.budgets.protocols:
            controls = [
                f
                for f in required
                if f.endpoint == endpoint
                and cases[f.case_id].protocol == protocol
                and f.name in FUNCTIONAL
                and f.status != "SKIP"
            ]
            if not controls:
                reasons.append(
                    "Each endpoint/protocol needs an applicable functional control"
                )
    if reasons:
        verdict, code = "INCONCLUSIVE", 2
    elif any(f.status == "FAIL" for f in required):
        verdict, code = "FAIL", 1
    else:
        verdict, code = "PASS", 0
    uncertified = sorted(
        {
            f"{f.endpoint}:{f.case_id}:{f.name}"
            for f in facets
            if f.name in ("capability", "schema", "visible") and f.status != "PASS"
        }
    )
    return AcceptanceResult(
        source_manifest_hash=manifest.manifest_hash,
        source_scorer_revision=manifest.scorer_revision,
        policy_hash=content_hash(policy.model_dump(mode="json")),
        policy=policy,
        scorer_revision=scorer_revision,
        integrity=integrity,
        verdict=verdict,
        exit_code=code,
        required_facets=required,
        diagnostic_facets=diagnostic,
        uncertified_capabilities=uncertified,
        reasons=list(dict.fromkeys(reasons)),
    )
