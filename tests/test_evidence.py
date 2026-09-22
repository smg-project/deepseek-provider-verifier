import json

import pytest


def _legacy_attempt():
    from deepseek_provider_verifier.catalog import content_hash

    value = {
        "schema_version": 1,
        "prompt_id": "C01",
        "retry": 0,
        "capture": {},
        "case_id": "C01.chat.non_thinking.nonstream",
        "endpoint": "reference",
        "step": 0,
        "repetition": 0,
        "attempt_number": 1,
        "request_hash": "b" * 64,
        "request": {},
        "status_code": 200,
        "timings": {"total_seconds": 0.1},
        "response": {},
        "events": [],
        "error": None,
    }
    return value | {"evidence_hash": content_hash(value)}


def test_append_hashes_redacted_records_and_detects_tampering(tmp_path):
    from deepseek_provider_verifier.evidence import append_record, load_resume_state

    path = tmp_path / "attempts.jsonl"
    h = append_record(
        path, {"kind": "manifest", "manifest_hash": "a" * 64, "api_key": "secret"}
    )
    assert len(h) == 64
    assert "secret" not in path.read_text()
    assert load_resume_state(path, "a" * 64).manifest_hash == "a" * 64
    path.write_text(path.read_text().replace('"kind":"manifest"', '"kind":"changed"'))
    with pytest.raises(ValueError, match="hash"):
        load_resume_state(path, "a" * 64)


def test_resume_rejects_manifest_and_records_torn_tail_without_removing_bytes(tmp_path):
    from deepseek_provider_verifier.evidence import append_record, load_resume_state

    path = tmp_path / "attempts.jsonl"
    append_record(path, {"kind": "manifest", "manifest_hash": "a" * 64})
    with pytest.raises(ValueError, match="manifest"):
        load_resume_state(path, "b" * 64)
    with path.open("ab") as handle:
        handle.write(b'{"kind":"attempt",')
    before = path.read_bytes()
    state = load_resume_state(path, "a" * 64)
    assert state.incomplete_records[0]["disposition"] == "retained_torn_tail"
    append_record(path, {"kind": "note", "reason": "resume"})
    assert path.read_bytes().startswith(before)
    again = load_resume_state(path, "a" * 64)
    assert len(again.incomplete_records) == 1


def test_interior_corruption_is_not_silently_dropped(tmp_path):
    from deepseek_provider_verifier.evidence import append_record, load_resume_state

    path = tmp_path / "results.jsonl"
    append_record(path, {"kind": "manifest", "manifest_hash": "a" * 64})
    with path.open("ab") as handle:
        handle.write(b'bad\n{"not":"an envelope"}\n')
    with pytest.raises(ValueError):
        load_resume_state(path, "a" * 64)


def test_atomic_json_replaces_complete_document(tmp_path):
    from deepseek_provider_verifier.evidence import atomic_json

    path = tmp_path / "summary.json"
    atomic_json(path, {"complete": False})
    atomic_json(path, {"complete": True})
    assert json.loads(path.read_text()) == {"complete": True}
    assert list(tmp_path.iterdir()) == [path]


def test_secret_bearing_url_components_are_removed_even_without_a_known_key(tmp_path):
    from deepseek_provider_verifier.evidence import append_record

    path = tmp_path / "events.jsonl"
    append_record(
        path,
        {
            "url": "https://user:opaque-password@host.test/path?token=opaque-query#opaque-fragment"
        },
    )
    assert "opaque-" not in path.read_text()


def test_legacy_attempt_hash_uses_original_payload_before_additive_defaults(tmp_path):
    from deepseek_provider_verifier.evidence import append_record, load_resume_state

    path = tmp_path / "attempts.jsonl"
    append_record(path, {"kind": "manifest", "manifest_hash": "a" * 64})
    append_record(path, {"kind": "attempt", "attempt": _legacy_attempt()})

    state = load_resume_state(path, "a" * 64)

    assert len(state.prior_attempts) == 1
    assert state.prior_attempts[0].status_code == 200
    assert state.prior_attempts[0].http_exchange_completed is None


def test_legacy_attempt_hash_still_rejects_tampered_original_payload(tmp_path):
    from deepseek_provider_verifier.evidence import append_record, load_resume_state

    attempt = _legacy_attempt()
    attempt["status_code"] = 201
    path = tmp_path / "attempts.jsonl"
    append_record(path, {"kind": "manifest", "manifest_hash": "a" * 64})
    append_record(path, {"kind": "attempt", "attempt": attempt})

    with pytest.raises(ValueError, match="Attempt evidence hash mismatch"):
        load_resume_state(path, "a" * 64)
