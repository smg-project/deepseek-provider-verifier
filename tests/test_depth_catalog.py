"""Opt-in repeatability inventory, provenance, and legacy isolation."""

from pathlib import Path

import pytest

from deepseek_provider_verifier.catalog import load_cases
from deepseek_provider_verifier.config import load_config, load_profile
from deepseek_provider_verifier.planner import build_manifest

ROOT = Path(__file__).resolve().parents[1]


def depth_manifest(suite, repetitions=1):
    config = load_config(ROOT / "configs/depth-repeatability.example.toml")
    profile = load_profile(ROOT / "profiles/deepseek-depth-2026-09-22-v1.json")
    preset = profile.presets[suite]
    config = config.model_copy(
        update={
            "run": config.run.model_copy(
                update={
                    "suite": suite,
                    "repetitions": repetitions,
                    "max_attempts_per_endpoint": preset.max_requests_per_endpoint,
                }
            )
        }
    )
    return build_manifest(config, profile, load_cases(case_ids=preset.case_ids))


def test_repeatability_starter_is_bounded_and_varied():
    m = depth_manifest("repeatability", 5)
    assert len(m.cases) == 200
    assert m.request_ceiling == 250
    assert len({c.prompt_id for c in m.cases}) == 20
    assert all(c.mode == "non_thinking" and not c.stream for c in m.cases)
    assert {c.repetition for c in m.cases} == set(range(5))
    families = {c.oracle["depth"]["family"] for c in m.cases}
    assert len(families) == 4
    assert all(
        len({c.prompt_id for c in m.cases if c.oracle["depth"]["family"] == f}) == 5
        for f in families
    )
    assert len({c.steps[0]["prompt_hash"] for c in m.cases}) == 20


def test_expanded_matrix_and_legacy_selection():
    m = depth_manifest("repeatability-expanded", 5)
    assert len(m.cases) == 800
    assert m.request_ceiling == 1000
    assert {(c.mode, c.stream) for c in m.cases} == {
        ("thinking", False),
        ("thinking", True),
        ("non_thinking", False),
        ("non_thinking", True),
    }
    assert {c.id for c in load_cases()} == {f"C{i:02d}" for i in range(1, 25)}
    assert depth_manifest("repeatability-expanded", 5).dataset_hash == m.dataset_hash


def test_selected_repeatability_is_lazy_and_uses_auto_tools():
    templates = load_cases(case_ids=["R01", "R06", "R31"])
    assert len(templates) == 6
    assert all(c.max_requests <= 2 for c in templates)
    for c in templates:
        if c.id != "R01":
            assert c.steps[0]["request"]["tool_choice"] == "auto"
    assert sum(len(str(c.model_dump())) for c in templates) < 20000


def test_absent_requested_template_fails_closed():
    with pytest.raises(ValueError, match="missing|absent|Unknown"):
        load_cases(case_ids=["R99"])


@pytest.mark.parametrize("defect", ["duplicate", "hash"])
def test_depth_prompt_provenance_is_validated(tmp_path, monkeypatch, defect):
    from deepseek_provider_verifier import depth_catalog

    source = (ROOT / "cases/depth-prompts.jsonl").read_text()
    if defect == "duplicate":
        source += source.splitlines()[0] + "\n"
    else:
        source = source.replace("amber", "changed", 1)
    path = tmp_path / "prompts.jsonl"
    path.write_text(source)
    original = depth_catalog._resource
    monkeypatch.setattr(
        depth_catalog,
        "_resource",
        lambda n: path if n == "depth-prompts.jsonl" else original(n),
    )
    with pytest.raises(ValueError, match="hash|Duplicate"):
        load_cases(case_ids=["R01"])
