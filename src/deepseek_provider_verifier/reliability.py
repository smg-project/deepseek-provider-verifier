"""Content-free, derived repeated-trial analysis; never an additional quality gate."""

from collections import Counter, defaultdict
from math import sqrt
from statistics import NormalDist

from .catalog import content_hash
from .comparison import _case_values, _http_values, _validate_run
from .records import Manifest, RunResult

STATUSES = ("PASS", "FAIL", "ERROR", "INCONCLUSIVE", "SKIP", "MISSING")


def wilson_interval(
    successes: int, total: int, confidence: float = 0.95
) -> tuple[float, float] | None:
    if type(successes) is not int or type(total) is not int:
        raise ValueError("counts must be integers")
    if not 0 <= successes <= total or not 0 < confidence < 1:
        raise ValueError("invalid counts or confidence")
    if total == 0:
        return None
    z = NormalDist().inv_cdf((1 + confidence) / 2)
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    half = z * sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return max(0.0, center - half), min(1.0, center + half)


def _rate(numerator, denominator, unavailable=None):
    result = {
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator if denominator else None,
    }
    if unavailable is not None:
        result["unavailable"] = unavailable
    return result


def summarize_trials(statuses: list[str | None]) -> dict:
    if any(s is not None and s not in STATUSES[:-1] for s in statuses):
        raise ValueError("Unknown trial status")
    counts = Counter(s or "MISSING" for s in statuses)
    return {
        "planned": len(statuses),
        "counts": {s: counts[s] for s in STATUSES},
        "failure_rate": _rate(counts["FAIL"], counts["PASS"] + counts["FAIL"]),
    }


def _first_contract(result, attempts):
    # Retry counters restart at zero when a whole trial is rerun. The retained
    # latest result cannot reconstruct the original conversation assessment.
    starts = Counter(a.step for a in attempts if a.retry == 0)
    if any(n > 1 for n in starts.values()) or any(
        a.interrupted_reservation for a in attempts
    ):
        return None
    return result.first_attempt_status if result else None


def _row(cases, results, attempts):
    ids = {c.id for c in cases}
    attempts = [a for a in attempts if a.case_id in ids]
    row = summarize_trials(
        [results[c.id].status if c.id in results else None for c in cases]
    )
    row["started"] = len({a.case_id for a in attempts})
    row["completed"] = sum(c.id in results and results[c.id].completed for c in cases)
    row["repetitions"] = len({c.repetition for c in cases})
    row["first_contract"] = summarize_trials(
        [
            _first_contract(
                results.get(c.id), [a for a in attempts if a.case_id == c.id]
            )
            for c in cases
        ]
    )
    n, d, missing, _ = _case_values(
        cases,
        {
            id: r
            for id, r in results.items()
            if r.completed and r.status not in ("ERROR", "SKIP")
        },
        "end_to_end_success",
        planned_denominator=True,
        singleton_observation=True,
    )
    row["end_to_end_success"] = _rate(n, d, missing)
    for eventual, name in [
        (False, "first_http_2xx_rate"),
        (True, "eventual_http_2xx_rate"),
    ]:
        n, d, missing, _ = _http_values(cases, attempts, eventual)
        row[name] = _rate(n, d, missing)
    row["http_attempts"] = len(attempts)
    row["http_retry_attempts"] = len(attempts) - len(
        {(a.case_id, a.step) for a in attempts}
    )
    return row


def build_reliability(manifest: Manifest, result: RunResult) -> dict:
    from .runner import validate_manifest

    validate_manifest(manifest)
    _validate_run(result, manifest)
    groups = []
    by_group = defaultdict(list)
    for c in manifest.cases:
        if "depth" in c.oracle:
            by_group[
                (c.oracle["depth"]["family"], c.protocol, c.mode, c.stream)
            ].append(c)
    for endpoint, config in manifest.endpoints.items():
        results = {r.case_id: r for r in result.case_results if r.endpoint == endpoint}
        attempts = [a for a in result.attempt_metrics if a.endpoint == endpoint]
        for (family, protocol, mode, stream), cases in sorted(by_group.items()):
            row = {
                "endpoint": endpoint,
                "model": config.model,
                "family": family,
                "protocol": protocol,
                "mode": mode,
                "stream": stream,
                **_row(cases, results, attempts),
            }
            by_prompt = defaultdict(list)
            for c in cases:
                by_prompt[c.prompt_id].append(c)
            prompts = []
            for prompt_id, prompt_cases in sorted(by_prompt.items()):
                p = {"prompt_id": prompt_id, **_row(prompt_cases, results, attempts)}
                p["unstable"] = p["counts"]["PASS"] > 0 and p["counts"]["FAIL"] > 0
                rate = p["failure_rate"]
                p["failure_interval"] = {
                    "method": "Wilson",
                    "confidence": 0.95,
                    "sample_size": rate["denominator"],
                    "bounds": wilson_interval(rate["numerator"], rate["denominator"]),
                }
                prompts.append(p)
            row.update(
                prompts=prompts,
                distinct_prompts=len(prompts),
                unstable_prompts=sum(p["unstable"] for p in prompts),
                error_affected_prompts=sum(p["counts"]["ERROR"] > 0 for p in prompts),
            )
            groups.append(row)
    return {
        "schema_version": 1,
        "manifest_hash": manifest.manifest_hash,
        "source_result_hash": content_hash(result.model_dump(mode="json")),
        "methods": {
            "interval": "95% Wilson per prompt and variant; assumes independent trials",
            "scope": "Conditional PASS/FAIL only; no pooled interval across prompt variants; descriptive, not a quality gate",
        },
        "groups": groups,
    }


def render_reliability_markdown(analysis: dict) -> str:
    from .reports import _md

    lines = [
        "",
        "## Reliability",
        "",
        analysis["methods"]["interval"] + ". " + analysis["methods"]["scope"] + ".",
        "",
        "| Endpoint / model / family / variant | Planned / started / completed | PASS / FAIL / ERROR / INCONCLUSIVE / SKIP / MISSING | Conditional failure | HTTP first / eventual | Requests / retries |",
        "| --- | --- | --- | --- | --- | --- |",
    ]

    def ratio(r):
        return (
            f"{r['numerator']:g}/{r['denominator']}"
            if r["value"] is not None
            else "unavailable"
        )

    for g in analysis["groups"]:
        label = _md(
            f"{g['endpoint']} / {g['model']} / {g['family']} / {g['protocol']} {g['mode']} stream={g['stream']}"
        )
        lines.append(
            f"| {label} | {g['planned']} / {g['started']} / {g['completed']} | "
            + " / ".join(str(g["counts"][s]) for s in STATUSES)
            + f" | {ratio(g['failure_rate'])} | {ratio(g['first_http_2xx_rate'])} / {ratio(g['eventual_http_2xx_rate'])} | {g['http_attempts']} / {g['http_retry_attempts']} |"
        )
        for p in g["prompts"]:
            bounds = p["failure_interval"]["bounds"]
            interval = (
                "unavailable" if bounds is None else f"{bounds[0]:.1%}–{bounds[1]:.1%}"
            )
            lines.append(
                f"| ↳ {_md(p['prompt_id'])}; {p['repetitions']} repetitions; mixed PASS/FAIL={p['unstable']} | {p['planned']} / {p['started']} / {p['completed']} | "
                + " / ".join(str(p["counts"][s]) for s in STATUSES)
                + f" | {ratio(p['failure_rate'])}; 95% {interval} | {ratio(p['first_http_2xx_rate'])} / {ratio(p['eventual_http_2xx_rate'])} | {p['http_attempts']} / {p['http_retry_attempts']} |"
            )
    return "\n".join(lines) + "\n"
