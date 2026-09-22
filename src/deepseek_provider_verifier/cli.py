"""Command-line interface for offline planning and auditable verification."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path

import httpx
from pydantic import ValidationError

from .catalog import load_cases
from .comparison import compare_runs
from .config import load_config, load_profile
from .planner import build_manifest
from .records import (
    ComparisonPolicy,
    ComparisonReportContext,
    Config,
    Manifest,
    Profile,
    RunReportContext,
)
from .reports import (
    comparison_exit_status,
    exit_status,
    load_run_evidence,
    load_stored_result,
    render_report,
    write_report_bundle,
)
from .runner import execute_manifest, validate_manifest
from .transport import endpoint_client


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dpv", description="DeepSeek Provider Verifier"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    plan = commands.add_parser("plan", help="expand and inspect a workload offline")
    _configuration_arguments(plan)
    plan.add_argument(
        "--endpoint", action="append", default=[], help="configured endpoint name"
    )

    run = commands.add_parser("run", help="execute named configured endpoints")
    _configuration_arguments(run)
    run.add_argument(
        "--endpoint",
        action="append",
        default=[],
        help="configured endpoint name; repeat for paired execution",
    )
    run.add_argument("--out", type=Path, required=True, help="new evidence directory")
    run.add_argument("--resume", action="store_true", help="resume matching evidence")

    compare = commands.add_parser("compare", help="compare two stored run results")
    compare.add_argument("reference", type=Path)
    compare.add_argument("candidate", type=Path)
    compare.add_argument("--reference-endpoint")
    compare.add_argument("--candidate-endpoint")
    compare.add_argument(
        "--model-map",
        action="append",
        default=[],
        metavar="REFERENCE=CANDIDATE",
        help="explicit comparable model-label mapping",
    )
    compare.add_argument(
        "--quality-policy", type=Path, help="JSON ComparisonPolicy for quality gating"
    )
    compare.add_argument("--out", type=Path, required=True, help="new report directory")

    report = commands.add_parser(
        "report", help="render stored evidence without traffic"
    )
    report.add_argument("evidence", type=Path)
    report.add_argument(
        "--format", choices=("json", "markdown", "junit"), default="markdown"
    )
    report.add_argument("--out", type=Path, help="new output file; stdout by default")
    return parser


def _configuration_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--profile",
        type=Path,
        help="custom profile JSON; the config still declares its profile ID",
    )


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            return _plan(args)
        if args.command == "run":
            return _run(args)
        if args.command == "compare":
            return _compare(args)
        return _report(args)
    except KeyboardInterrupt:
        print("dpv: interrupted", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"dpv: file not found: {Path(exc.filename).name}", file=sys.stderr)
        return 2
    except ValidationError:
        print("dpv: invalid configuration or stored record", file=sys.stderr)
        return 2
    except (json.JSONDecodeError, ValueError, OSError) as exc:
        print(f"dpv: {_safe_error(exc)}", file=sys.stderr)
        return 2


def _plan(args) -> int:
    config, profile = _load_configuration(args.config, args.profile)
    if args.endpoint:
        config = _select_endpoints(config, args.endpoint)
    manifest = _manifest(config, profile)
    missing = sorted(
        {
            endpoint.api_key_env
            for endpoint in manifest.endpoints.values()
            if endpoint.api_key_env and not os.environ.get(endpoint.api_key_env)
        }
    )
    print(
        json.dumps(
            {
                "schema_version": 1,
                "manifest": manifest.model_dump(mode="json"),
                "missing_prerequisites": missing,
            },
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def _run(args) -> int:
    if not args.endpoint:
        raise ValueError("run requires at least one --endpoint")
    config, profile = _load_configuration(args.config, args.profile)
    config = _select_endpoints(config, args.endpoint)
    planned = _manifest(config, profile)
    if args.resume:
        if not (args.out / "manifest.json").exists():
            raise ValueError("resume requires an existing manifest.json")
        saved = Manifest.model_validate_json((args.out / "manifest.json").read_text())
        validate_manifest(saved)
        if saved.manifest_hash != planned.manifest_hash:
            raise ValueError("resume workload does not match stored manifest")
        manifest = saved
    else:
        _require_new_directory(args.out)
        manifest = planned
    secrets = {}
    for name, endpoint in manifest.endpoints.items():
        secret = (
            os.environ.get(endpoint.api_key_env, "") if endpoint.api_key_env else ""
        )
        if endpoint.api_key_env and not secret:
            raise ValueError(
                f"missing credential environment variable: {endpoint.api_key_env}"
            )
        secrets[name] = secret
    result = asyncio.run(_execute(manifest, secrets, args.out, args.resume))
    context = _run_context(manifest, result, "verified")
    result = result.model_copy(update={"report": context})
    result = result.model_copy(update={"exit_code": exit_status(result)})
    write_report_bundle(args.out, result, allow_existing=True)
    print(args.out)
    return result.exit_code


async def _execute(manifest, secrets, output_dir, resume):
    timeout = httpx.Timeout(30.0, connect=10.0)
    async with AsyncExitStack() as stack:
        clients = {
            name: await stack.enter_async_context(endpoint_client(timeout=timeout))
            for name in manifest.endpoints
        }
        return await execute_manifest(
            manifest,
            clients,
            secrets,
            output_dir=output_dir,
            resume=resume,
        )


def _compare(args) -> int:
    _require_new_directory(args.out)
    reference_manifest, reference = load_run_evidence(args.reference)
    candidate_manifest, candidate = load_run_evidence(args.candidate)
    policy = (
        ComparisonPolicy.model_validate_json(args.quality_policy.read_text())
        if args.quality_policy
        else None
    )
    mapping = _model_mapping(args.model_map)
    result = compare_runs(
        reference,
        candidate,
        (reference_manifest, candidate_manifest),
        policy,
        reference_endpoint=args.reference_endpoint,
        candidate_endpoint=args.candidate_endpoint,
        model_mapping=mapping,
    )
    ref_name = result.reference_endpoint
    cand_name = result.candidate_endpoint
    counts = Counter(
        item.status
        for run, endpoint in ((reference, ref_name), (candidate, cand_name))
        for item in run.case_results
        if item.endpoint == endpoint
    )
    evidence = {}
    for case in reference_manifest.cases:
        links = []
        for directory, run, endpoint in (
            (args.reference, reference, ref_name),
            (args.candidate, candidate, cand_name),
        ):
            selected = next(
                (
                    item
                    for item in run.case_results
                    if item.endpoint == endpoint and item.case_id == case.id
                ),
                None,
            )
            if selected:
                links.extend(
                    str(directory / "attempts.jsonl") + f"#sha256-{ref}"
                    for ref in selected.attempt_refs
                )
        evidence[case.id] = links
    context = ComparisonReportContext(
        created_at=datetime.now(UTC),
        profile=(
            reference_manifest.profile_snapshot.id
            if reference_manifest.profile_snapshot
            else None
        ),
        profile_hash=reference_manifest.profile_hash,
        dataset_hash=reference_manifest.dataset_hash,
        endpoint_models={
            f"reference:{ref_name}": reference_manifest.endpoints[ref_name].model,
            f"candidate:{cand_name}": candidate_manifest.endpoints[cand_name].model,
        },
        case_count=len(reference_manifest.cases),
        required_case_count=sum(case.required for case in reference_manifest.cases),
        status_counts={
            status: counts[status]
            for status in ("PASS", "FAIL", "ERROR", "SKIP", "INCONCLUSIVE")
        },
        enabled_gates=reference_manifest.gates,
        evidence_links=evidence,
        integrity=(
            "verified"
            if reference.report
            and candidate.report
            and reference.report.integrity == candidate.report.integrity == "verified"
            else "summary-only"
        ),
        quality_verdict=result.quality_gate
        or ("REPORT_ONLY" if policy is None else "INCONCLUSIVE"),
        exit_code=comparison_exit_status(result),
    )
    result = result.model_copy(update={"report": context})
    write_report_bundle(args.out, result)
    print(args.out)
    return comparison_exit_status(result)


def _report(args) -> int:
    result = load_stored_result(args.evidence)
    rendered = render_report(result, args.format)
    if args.out:
        if args.out.exists():
            raise ValueError(f"Output path already exists: {args.out}")
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return (
        exit_status(result)
        if hasattr(result, "manifest_hash")
        else comparison_exit_status(result)
    )


def _load_configuration(
    config_path: Path, profile_path: Path | None
) -> tuple[Config, Profile]:
    config = load_config(config_path)
    if profile_path:
        profile = load_profile(profile_path)
    else:
        resource = files("deepseek_provider_verifier").joinpath(
            "profiles", f"{config.run.profile}.json"
        )
        if resource.is_file():
            profile = Profile.model_validate_json(resource.read_text())
        else:
            source = (
                Path(__file__).resolve().parents[2]
                / "profiles"
                / f"{config.run.profile}.json"
            )
            profile = load_profile(source)
    return config, profile


def _manifest(config: Config, profile: Profile):
    preset = profile.presets.get(config.run.suite)
    case_ids = (
        [*preset.case_ids, *preset.attached_assertion_case_ids] if preset else None
    )
    return build_manifest(config, profile, load_cases(config.run.protocols, case_ids))


def _select_endpoints(config: Config, selected: list[str]) -> Config:
    if len(selected) != len(set(selected)):
        raise ValueError("endpoint selections must be unique")
    unknown = set(selected) - set(config.endpoints)
    if unknown:
        raise ValueError("unknown configured endpoint")
    return config.model_copy(
        update={"endpoints": {name: config.endpoints[name] for name in selected}}
    )


def _model_mapping(values: list[str]) -> dict[str, str] | None:
    mapping = {}
    for value in values:
        reference, separator, candidate = value.partition("=")
        if not separator or not reference or not candidate or reference in mapping:
            raise ValueError("model mappings must be unique REFERENCE=CANDIDATE pairs")
        mapping[reference] = candidate
    return mapping or None


def _run_context(manifest, result, integrity):
    return RunReportContext(
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
            f"{item.endpoint}:{item.case_id}": [
                f"attempts.jsonl#sha256-{ref}" for ref in item.attempt_refs
            ]
            for item in result.case_results
        },
        integrity=integrity,
    )


def _require_new_directory(path: Path) -> None:
    if path.exists() and any(path.iterdir()):
        raise ValueError(f"Output directory already exists and is not empty: {path}")


def _safe_error(exc: Exception) -> str:
    message = str(exc)
    allowed = (
        "run requires",
        "missing credential environment variable:",
        "endpoint selections",
        "unknown configured endpoint",
        "request budget",
        "per-endpoint request budget",
        "aggregate request budget",
        "configured retries",
        "configured concurrency",
        "Output directory already exists",
        "Output path already exists",
        "resume requires",
        "resume workload",
        "Run aggregate",
        "Run summary",
        "Incomplete evidence",
        "Evidence hash",
        "Resume artifact",
        "model mappings",
        "reference endpoint selector",
        "candidate endpoint selector",
        "Unknown reference endpoint",
        "Unknown candidate endpoint",
    )
    if message.startswith(allowed):
        return message
    return "invalid configuration, evidence, or command input"


if __name__ == "__main__":
    raise SystemExit(main())
