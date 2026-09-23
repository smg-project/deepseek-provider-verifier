"""Acceptance views agree while canonical evidence and legacy commands stay intact."""

import hashlib
import json
from xml.etree import ElementTree as ET

import httpx
import pytest
from test_acceptance import api
from test_cli_reports import _write_fixture_inputs
from test_runner import manifest, response

from deepseek_provider_verifier import cli
from deepseek_provider_verifier.acceptance_records import FAMILY_FACETS, MANDATORY
from deepseek_provider_verifier.depth_catalog import depth_metadata
from deepseek_provider_verifier.runner import execute_manifest, rehash_manifest


def policy_file(tmp_path, strict=False):
    _, Policy, _ = api()
    p = Policy(
        version=1,
        id="test-strict" if strict else "test-compatible",
        mode="strict" if strict else "compatibility",
        selectors=[
            {"family": family, "facet": facet, "required": strict or facet in MANDATORY}
            for family, facets in FAMILY_FACETS.items()
            for facet in facets
        ],
    )
    path = tmp_path / ("strict.json" if strict else "compatible.json")
    path.write_text(p.model_dump_json())
    return path


@pytest.mark.parametrize(
    "behavior,exit_code", [("quality", 0), ("core", 1), ("interrupt", 2)]
)
def test_verify_and_assess_preserve_raw_verdicts(
    tmp_path, monkeypatch, capsys, behavior, exit_code
):
    m = manifest(count=2)
    cases = list(m.cases)
    cases[1] = cases[1].model_copy(
        update={
            "oracle": dict(
                cases[1].oracle, depth=depth_metadata("repeatability.instruction")
            )
        }
    )
    m = rehash_manifest(m.model_copy(update={"cases": cases}))
    monkeypatch.setattr(cli, "_manifest", lambda *args: m)
    config, profile = _write_fixture_inputs(tmp_path, 1)
    calls = 0

    def respond(request):
        nonlocal calls
        calls += 1
        if behavior == "interrupt":
            raise httpx.ReadError("interrupted")
        text = "wrong" if (behavior == "core" or calls == 2) else "amber"
        return httpx.Response(200, json=response(text))

    async def execute(m, secrets, out, resume):
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            return await execute_manifest(
                m,
                {"candidate": client},
                {"candidate": "fixture-placeholder"},
                output_dir=out,
            )

    monkeypatch.setattr(cli, "_execute", execute)
    monkeypatch.setenv("FIXTURE_KEY", "fixture-placeholder")
    out = tmp_path / "evidence"
    p = policy_file(tmp_path)
    args = [
        "verify",
        "--config",
        str(config),
        "--profile",
        str(profile),
        "--endpoint",
        "candidate",
        "--policy",
        str(p),
        "--out",
        str(out),
    ]
    assert cli.main(args) == exit_code
    result = json.loads((out / "acceptance.json").read_text())
    assert result["exit_code"] == exit_code
    junit = ET.parse(out / "acceptance.junit.xml").getroot()
    assert (int(junit.attrib["failures"]) > 0) == (exit_code == 1)
    assert (int(junit.attrib["errors"]) > 0) == (exit_code == 2)
    assert result["verdict"] in (out / "acceptance.md").read_text()
    if behavior == "quality":
        assert json.loads((out / "summary.json").read_text())["counts"]["FAIL"] == 1
        assert ET.parse(out / "junit.xml").getroot().find(".//failure") is not None
        assert cli.main(["report", str(out), "--format", "json"]) == 1
        assert any(f["status"] == "FAIL" for f in result["diagnostic_facets"])
    before = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in out.iterdir()
        if p.is_file()
    }
    assessed = tmp_path / "assessment"

    def no_network(*args):
        pytest.fail("offline assess used network")

    monkeypatch.setattr(cli, "_execute", no_network)
    assert (
        cli.main(["assess", str(out), "--policy", str(p), "--out", str(assessed)])
        == exit_code
    )
    assert before == {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in out.iterdir()
        if p.is_file()
    }
    assert json.loads((assessed / "acceptance.json").read_text()) == result
    assert (
        cli.main(
            [
                "assess",
                str(out),
                "--policy",
                str(p),
                "--expected-policy-hash",
                "wrong",
                "--out",
                str(tmp_path / "bad"),
            ]
        )
        == 2
    )
    assert not (tmp_path / "bad").exists()
    assert (
        cli.main(
            [
                "assess",
                str(out),
                "--policy",
                str(p),
                "--expected-scorer-revision",
                "wrong",
                "--out",
                str(tmp_path / "bad2"),
            ]
        )
        == 2
    )
    if behavior == "quality":
        assert (
            cli.main(
                [
                    "assess",
                    str(out),
                    "--policy",
                    str(policy_file(tmp_path, True)),
                    "--out",
                    str(tmp_path / "strict-out"),
                ]
            )
            == 1
        )


def test_unverified_summary_cannot_be_assessed_as_pass(tmp_path):
    from test_runner import run

    from deepseek_provider_verifier.reports import exit_status, write_report_bundle

    m = manifest()
    r = run(m, lambda req: httpx.Response(200, json=response()))
    r = r.model_copy(update={"report": cli._run_context(m, r, "summary-only")})
    r = r.model_copy(update={"exit_code": exit_status(r)})
    source = tmp_path / "summary-only"
    source.mkdir()
    (source / "manifest.json").write_text(m.model_dump_json())
    write_report_bundle(source, r, allow_existing=True)
    out = tmp_path / "assessed"
    assert (
        cli.main(
            [
                "assess",
                str(source),
                "--policy",
                str(policy_file(tmp_path)),
                "--out",
                str(out),
            ]
        )
        == 2
    )
    result = json.loads((out / "acceptance.json").read_text())
    assert result["integrity"] == "summary-only"
    assert result["verdict"] == "INCONCLUSIVE"
