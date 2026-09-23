"""Read-only reconstruction of hash-verified captured attempts for assessment."""

import base64
import hashlib
from pathlib import Path

from .capture import AttemptPayload
from .evidence import load_resume_state
from .reports import load_run_evidence
from .runner import _assemble


def scorer_revision():
    """Bind assessment to the installed Python sources, independent of checkout paths."""
    root = Path(__file__).parent
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        digest.update(
            str(path.relative_to(root)).encode() + b"\0" + path.read_bytes() + b"\0"
        )
    return "sha256:" + digest.hexdigest()


def load_observations(directory):
    manifest, run = load_run_evidence(directory)
    groups = {}
    if run.report.integrity != "verified":
        return manifest, run, groups
    state = load_resume_state(directory, manifest.manifest_hash)
    attempts = {a.evidence_hash: a for a in state.prior_attempts}
    cases = {c.id: c for c in manifest.cases}
    for result in run.case_results:
        # Retry outcomes are already retained in the canonical attempts and metrics.
        # Reconstruct the same final per-step trajectory the runner evaluated.
        selected = {}
        unavailable = False
        for ref in result.attempt_refs:
            attempt = attempts[ref]
            if (attempt.endpoint, attempt.case_id) != (result.endpoint, result.case_id):
                raise ValueError("Acceptance attempt identity mismatch")
            prior = selected.get(attempt.step)
            if prior is not None and attempt.retry <= prior.retry:
                raise ValueError("Acceptance attempt order mismatch")
            selected[attempt.step] = attempt
        values = []
        for step, attempt in sorted(selected.items()):
            if step != len(values):
                raise ValueError("Acceptance attempt step coverage mismatch")
            capture = AttemptPayload.model_validate(attempt.capture)
            if capture.body_omission_reason:
                unavailable = True
                break
            capture.raw_chunks = [base64.b64decode(capture.body_base64, validate=True)]
            capture.raw_json = capture.decoded_json
            obs, _ = _assemble(cases[result.case_id], capture)
            obs.endpoint = attempt.endpoint
            obs.request_payload = attempt.request
            if obs.model_dump(mode="json") != attempt.response:
                raise ValueError("Acceptance observation reassembly mismatch")
            values.append(obs)
        if not unavailable:
            groups[result.endpoint, result.case_id] = values
    return manifest, run, groups
