"""Fixed-budget manual CI config and content-free artifact projection.

This helper never reads credentials or sends requests. The workflow supplies only
one endpoint's separate environment credential to the subsequent dpv invocation.
"""

import argparse
import json
import os
from pathlib import Path

from deepseek_provider_verifier.records import Endpoint

PROFILES = {"deepseek-api-2026-09-21", "deepseek-flash-smoke-2026-09-21-v1"}
PROTOCOLS = {
    "chat": ["chat"],
    "responses": ["responses"],
    "both": ["chat", "responses"],
}
STATUSES = {"PASS", "FAIL", "ERROR", "SKIP", "INCONCLUSIVE"}


def config_text(env):
    endpoint = env["DPV_ENDPOINT"]
    profile = env["DPV_PROFILE"]
    if endpoint not in {"reference", "candidate"} or profile not in PROFILES:
        raise ValueError("Unsupported endpoint or profile")
    protocols = PROTOCOLS.get(env["DPV_PROTOCOLS"])
    if protocols is None:
        raise ValueError("Unsupported protocol selection")
    official = endpoint == "reference"
    target = Endpoint(
        name=endpoint,
        base_url="https://api.deepseek.com" if official else env["DPV_CANDIDATE_URL"],
        model="deepseek-flash" if official else env["DPV_CANDIDATE_MODEL"],
        contract_model="deepseek-flash",
        model_release="unknown",
        api_key_env="DEEPSEEK_API_KEY" if official else "CANDIDATE_API_KEY",
    )
    if target.base_url.scheme != "https":
        raise ValueError("Manual workflow requires HTTPS")
    # JSON basic strings/lists are valid TOML; no input is evaluated as shell text.
    values = {
        "profile": profile,
        "suite": "smoke",
        "protocols": protocols,
        "concurrency": 1,
        "max_attempts_per_endpoint": 34,
        "retries": 0,
        "repetitions": 1,
        "execution_order": "sequential",
        "scorer_revision": env["GITHUB_SHA"],
    }
    lines = ["[run]", *[f"{k} = {json.dumps(v)}" for k, v in values.items()]]
    lines += [f"\n[endpoints.{endpoint}]"]
    for key in [
        "name",
        "base_url",
        "model",
        "contract_model",
        "model_release",
        "api_key_env",
    ]:
        lines.append(f"{key} = {json.dumps(str(getattr(target, key)))}")
    return "\n".join(lines) + "\n"


def safe_summary(directory):
    """Deliberately omit arbitrary strings, reasons, observations and links."""
    summary = json.loads((directory / "summary.json").read_text())
    counts = summary["counts"]
    budget = summary["budget_usage"]
    digest = summary["manifest_hash"]
    if (
        not isinstance(counts, dict)
        or not set(counts) <= STATUSES
        or any(type(n) is not int or n < 0 for n in counts.values())
        or not isinstance(budget, dict)
        or not set(budget) <= {"reference", "candidate"}
        or any(type(n) is not int or not 0 <= n <= 34 for n in budget.values())
        or len(budget) != 1
        or type(summary["complete"]) is not bool
        or type(summary["exit_code"]) is not int
        or summary["exit_code"] not in (0, 1, 2)
        or not isinstance(digest, str)
        or len(digest) != 64
        or any(c not in "0123456789abcdef" for c in digest)
    ):
        raise ValueError("Invalid sanitized summary fields")
    return {
        "schema_version": 1,
        "artifact_kind": "sanitized-ci-summary",
        "manifest_hash": digest,
        "complete": summary["complete"],
        "exit_code": summary["exit_code"],
        "counts": counts,
        "attempts": sum(budget.values()),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "summarize"])
    args = parser.parse_args()
    if args.action == "prepare":
        text = config_text(os.environ)
        Path("runs").mkdir(exist_ok=True)
        Path("runs/live-provider.toml").write_text(text)
    else:
        result = safe_summary(Path("runs/live"))
        Path("runs/sanitized").mkdir(exist_ok=True)
        Path("runs/sanitized/summary.json").write_text(
            json.dumps(result, indent=2) + "\n"
        )


if __name__ == "__main__":
    main()
