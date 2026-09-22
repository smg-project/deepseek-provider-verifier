"""Reproduce the September 22 calibration offline from private local evidence.

No credentials or network transport are created. Raw captures are never copied
into the output. Original artifacts, exact requests, and scored results must all
match the committed, content-free calibration record.
"""

import argparse
import asyncio
import base64
import hashlib
import json
from collections import Counter
from pathlib import Path

import httpx

from deepseek_provider_verifier.catalog import load_cases
from deepseek_provider_verifier.config import load_config, load_profile
from deepseek_provider_verifier.evidence import load_resume_state
from deepseek_provider_verifier.planner import build_manifest
from deepseek_provider_verifier.reports import load_run_evidence
from deepseek_provider_verifier.runner import execute_manifest

ROOT = Path(__file__).resolve().parents[1]


async def replay(evidence_root: Path) -> dict:
    record = json.loads(
        (ROOT / "docs/calibration/official-both-models-2026-09-22.json").read_text()
    )

    def verify_originals():
        for name, digest in record["artifact_sha256"].items():
            if (
                hashlib.sha256((evidence_root / name).read_bytes()).hexdigest()
                != digest
            ):
                raise ValueError(f"Original artifact changed: {name}")

    verify_originals()
    groups = {}
    for source, provenance in record["sources"].items():
        path = evidence_root / source
        original, stored = load_run_evidence(path)
        if (
            original.manifest_hash != provenance["manifest_hash"]
            or stored.report.integrity != "verified"
        ):
            raise ValueError(f"Invalid source evidence: {source}")
        for attempt in load_resume_state(path, original.manifest_hash).prior_attempts:
            groups.setdefault((source, attempt.endpoint, attempt.case_id), []).append(
                attempt
            )
    config = load_config(ROOT / "configs/official-full.example.toml")
    config = config.model_copy(
        update={
            "endpoints": {
                k: endpoint.model_copy(update={"api_key_env": None, "auth_none": True})
                for k, endpoint in config.endpoints.items()
            }
        }
    )
    profile = load_profile(ROOT / f"profiles/{record['profile']}.json")
    manifest = build_manifest(config, profile, load_cases())
    if (
        manifest.dataset_hash != record["dataset_hash"]
        or manifest.profile_hash != record["profile_hash"]
    ):
        raise ValueError("Current dataset/profile differs from calibrated version")
    trials = {(t["endpoint"], t["case_id"]): t for t in record["trials"]}
    expected = []
    for endpoint in manifest.endpoints:
        for case in manifest.cases:
            trial = trials[endpoint, case.id]
            if case.oracle.get("applicable") is False:
                if trial["status"] != "SKIP":
                    raise ValueError("Applicability differs from calibration")
                continue
            captures = groups[trial["source"], endpoint, case.id]
            if [a.request_hash for a in captures] != trial["request_hashes"]:
                raise ValueError("Request provenance differs from calibration")
            if [a.evidence_hash for a in captures] != trial["evidence_hashes"]:
                raise ValueError("Response provenance differs from calibration")
            expected.extend(captures)
    index = 0
    mismatches = []

    def handler(request):
        nonlocal index
        if index >= len(expected):
            mismatches.append("Unexpected extra request")
            raise ValueError(mismatches[-1])
        attempt = expected[index]
        if json.loads(request.content) != attempt.request:
            mismatches.append(
                f"Request mismatch: {attempt.endpoint}/{attempt.case_id}/{attempt.step}"
            )
            raise ValueError(mismatches[-1])
        index += 1
        return httpx.Response(
            attempt.status_code,
            content=base64.b64decode(attempt.capture["body_base64"]),
            headers={
                "content-type": "text/event-stream"
                if attempt.request["stream"] and attempt.status_code < 400
                else "application/json"
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await execute_manifest(
            manifest, {k: client for k in manifest.endpoints}, {}
        )
    if mismatches or index != len(expected) or not result.complete:
        raise ValueError(f"Incomplete exact-request replay: {mismatches}")
    if {(t.endpoint, t.case_id) for t in result.case_results} != set(trials):
        raise ValueError("Trial inventory differs from calibration")
    for trial in result.case_results:
        recorded = trials[trial.endpoint, trial.case_id]
        if (
            trial.status != recorded["status"]
            or dict(Counter(a.status for a in trial.assertions))
            != recorded["assertion_counts"]
        ):
            raise ValueError(f"Result differs: {trial.endpoint}/{trial.case_id}")
    summary = {
        "live_calls": 0,
        "exact_requests_matched": True,
        "mocked_requests": index,
        "counts": result.counts,
        "exit_code": result.exit_code,
        "observed_assertion_counts": dict(
            Counter(a.status for t in result.case_results for a in t.assertions)
        ),
    }
    if summary != record["offline_replay"]:
        raise ValueError("Replay summary differs from calibration")
    verify_originals()
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(replay(args.evidence_root)), indent=2))
