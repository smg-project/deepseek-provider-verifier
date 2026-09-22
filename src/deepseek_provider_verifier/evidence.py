"""Append-only, hash-chained evidence; torn bytes are retained and accounted for."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from .capture import redact
from .catalog import content_hash
from .records import Attempt, CaseResult, ResumeState


def _scan(path: Path) -> tuple[list[dict], list[dict], str]:
    if not path.exists():
        return [], [], "0" * 64
    data = path.read_bytes()
    lines = data.splitlines(keepends=True)
    records, incomplete = [], []
    previous = "0" * 64
    offset = 0
    pending = None
    for index, raw in enumerate(lines):
        try:
            envelope = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            if pending is not None:
                raise ValueError("Interior evidence corruption")
            pending = {
                "offset": offset,
                "length": len(raw.rstrip(b"\r\n")),
                "sha256": hashlib.sha256(raw.rstrip(b"\r\n")).hexdigest(),
                "disposition": "retained_torn_tail",
            }
            incomplete.append(pending)
            if index != len(lines) - 1:
                # Only an immediately following hashed disposition can acknowledge it.
                try:
                    following = json.loads(lines[index + 1])
                    if (
                        following["record"].get("kind") != "torn_tail"
                        or following["record"].get("tail") != pending
                    ):
                        raise ValueError("Interior evidence corruption")
                except (ValueError, KeyError, TypeError) as exc:
                    raise ValueError("Interior evidence corruption") from exc
            offset += len(raw)
            continue
        if not isinstance(envelope, dict) or set(envelope) != {
            "record",
            "previous_hash",
            "record_hash",
        }:
            raise ValueError("Invalid evidence hash envelope")
        expected = content_hash(
            {"record": envelope["record"], "previous_hash": previous}
        )
        if envelope["previous_hash"] != previous or envelope["record_hash"] != expected:
            raise ValueError("Evidence hash mismatch")
        previous = expected
        record = envelope["record"]
        if pending is not None:
            if record.get("kind") != "torn_tail" or record.get("tail") != pending:
                raise ValueError("Missing torn-tail disposition")
            pending = None
        records.append(record)
        offset += len(raw)
    return records, incomplete, previous


def _write(handle, record: dict, previous: str) -> str:
    value = {"record": record, "previous_hash": previous}
    digest = content_hash(value)
    handle.write(
        (
            json.dumps(
                value | {"record_hash": digest},
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )
            + "\n"
        ).encode()
    )
    return digest


def append_record(path: Path, record: dict) -> str:
    """Flush before returning the durable envelope hash. Caller redacts secret values.

    Structural credential-key redaction is an additional defense. One runner owns
    each evidence directory; concurrent writers to the same path are unsupported.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    records, incomplete, previous = _scan(path)
    needs_disposition = bool(
        incomplete
        and (
            not records
            or records[-1].get("kind") != "torn_tail"
            or records[-1].get("tail") != incomplete[-1]
        )
    )
    # A previously acknowledged tail may have later valid records.
    acknowledged = [r.get("tail") for r in records if r.get("kind") == "torn_tail"]
    needs_disposition = needs_disposition and incomplete[-1] not in acknowledged
    with path.open("ab") as handle:
        if path.stat().st_size and not path.read_bytes().endswith(b"\n"):
            handle.write(b"\n")
        if needs_disposition:
            previous = _write(
                handle, {"kind": "torn_tail", "tail": incomplete[-1]}, previous
            )
        digest = _write(handle, redact(record), previous)
        handle.flush()
        os.fsync(handle.fileno())
    return digest


def load_resume_state(path: Path, manifest_hash: str) -> ResumeState:
    paths = (
        [path / "attempts.jsonl", path / "results.jsonl"] if path.is_dir() else [path]
    )
    attempts, results, incomplete = [], [], []
    starts = {}
    for artifact in paths:
        records, torn, _ = _scan(artifact)
        if (
            not records
            or records[0].get("kind") != "manifest"
            or records[0].get("manifest_hash") != manifest_hash
        ):
            raise ValueError("Resume manifest hash mismatch or missing header")
        incomplete.extend([t | {"artifact": artifact.name} for t in torn])
        for record in records[1:]:
            if record.get("kind") == "attempt_start":
                key = (record["endpoint"], record["case_id"], record["attempt_number"])
                if key in starts:
                    raise ValueError("Duplicate attempt reservation")
                starts[key] = record
            elif record.get("kind") == "attempt":
                attempt = Attempt.model_validate(record["attempt"])
                if (
                    content_hash(
                        attempt.model_dump(mode="json", exclude={"evidence_hash"})
                    )
                    != attempt.evidence_hash
                ):
                    raise ValueError("Attempt evidence hash mismatch")
                attempts.append(attempt)
            elif record.get("kind") == "result":
                results.append(CaseResult.model_validate(record["result"]))
    completed_attempts = {(a.endpoint, a.case_id, a.attempt_number) for a in attempts}
    if len(completed_attempts) != len(attempts):
        raise ValueError("Duplicate attempt identity")
    incomplete.extend(
        record | {"disposition": "interrupted_attempt"}
        for key, record in starts.items()
        if key not in completed_attempts
    )
    latest = {(r.endpoint, r.case_id): r for r in results}
    if path.is_dir():
        refs = {a.evidence_hash for a in attempts}
        if any(ref not in refs for result in results for ref in result.attempt_refs):
            raise ValueError("Result references missing attempt evidence")
    completed = [f"{r.endpoint}:{r.case_id}" for r in latest.values() if r.completed]
    return ResumeState(
        manifest_hash=manifest_hash,
        completed_case_ids=completed,
        prior_attempts=attempts,
        results=list(latest.values()),
        incomplete_records=incomplete,
    )


def atomic_json(path: Path, value: dict) -> None:
    """Replace a complete JSON document atomically after flush; input must be safe."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, ensure_ascii=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def checkpoint_artifacts(directory: Path) -> None:
    """Seal durable prefixes so deletion of complete tail records is detectable."""
    artifacts = {}
    for name in ("attempts.jsonl", "results.jsonl"):
        data = (directory / name).read_bytes()
        artifacts[name] = {
            "length": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    atomic_json(
        directory / "evidence-index.json", {"schema_version": 1, "artifacts": artifacts}
    )


def validate_checkpoint(directory: Path) -> None:
    path = directory / "evidence-index.json"
    if not path.exists():
        return  # An interrupted first trial may precede the first checkpoint.
    index = json.loads(path.read_text())
    for name, entry in index["artifacts"].items():
        if name not in ("attempts.jsonl", "results.jsonl"):
            raise ValueError("Unknown checkpoint artifact")
        data = (directory / name).read_bytes()
        if (
            len(data) < entry["length"]
            or hashlib.sha256(data[: entry["length"]]).hexdigest() != entry["sha256"]
        ):
            raise ValueError("Resume artifact checkpoint mismatch")
