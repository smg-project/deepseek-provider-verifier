# DeepSeek Provider Verifier

DeepSeek Provider Verifier (`dpv`) is a community-owned, MIT-licensed project for collecting reproducible evidence from DeepSeek-compatible HTTP providers. It is independent of DeepSeek and provider vendors. A passing result establishes only the behavior observed for the declared endpoint, model label, profile, dataset, and capture date. It does not authenticate model weights, prove quantization, certify a vendor, or predict production-load performance.

## Quickstart

Install the project with Python 3.11 or newer, then inspect the bundled smoke workload without credentials or network traffic:

```sh
uv sync
uv run dpv plan --config configs/providers.example.toml
```

`plan` expands the cases and prints missing environment-variable names, request ceilings, output-token ceilings, concurrency, and deadlines. It never creates an HTTP client. The bundled smoke preset is fixed at 17 possible requests per protocol, 34 per endpoint, 68 total across the two example endpoints, zero retries, concurrency one, and at most four requests in a conversation. These are hard ceilings rather than cost predictions. `dpv` makes no monetary estimate unless an operator independently supplies pricing.

For a live run, copy the example configuration, set only the named environment variable, select the endpoint explicitly, and choose a new output directory:

```sh
cp configs/providers.example.toml providers.toml
export DEEPSEEK_API_KEY='set-this-outside-shell-history'
uv run dpv run --config providers.toml --endpoint reference --out runs/reference
```

Credentials are read from environment variables, never command-line values, manifests, reports, or evidence records. Existing evidence directories are refused unless `--resume` is explicit and the newly planned workload matches the stored manifest.

## Four-request offline fixture example

The installed distribution includes `configs/offline-fixture.example.toml`, `profiles/offline-confirmation.example.json`, and a small standard-library fixture provider. The profile selects C01 for Chat and Responses in streaming and non-streaming modes: exactly four requests to one endpoint. C02, C17, and C20 attach assertions to those requests without increasing the budget.

From a source checkout, start the explicitly synthetic provider in the background, run the example, and wait for it to stop automatically after its fourth request:

```sh
python -m deepseek_provider_verifier.synthetic_fixture --max-requests 4 &
FIXTURE_PID=$!
trap 'kill "$FIXTURE_PID" 2>/dev/null || true' EXIT
dpv plan \
  --config configs/offline-fixture.example.toml \
  --profile profiles/offline-confirmation.example.json
dpv run \
  --config configs/offline-fixture.example.toml \
  --profile profiles/offline-confirmation.example.json \
  --endpoint fixture \
  --out runs/offline-confirmation
wait "$FIXTURE_PID"
trap - EXIT
```

When only the wheel is installed, extract the packaged example configuration and omit `--profile`; `dpv` resolves `offline-confirmation.example` from the installed package:

```sh
python -c 'from importlib.resources import files; print(files("deepseek_provider_verifier").joinpath("configs", "offline-fixture.example.toml").read_text(), end="")' > offline-fixture.toml
python -m deepseek_provider_verifier.synthetic_fixture --max-requests 4 &
FIXTURE_PID=$!
trap 'kill "$FIXTURE_PID" 2>/dev/null || true' EXIT
dpv plan --config offline-fixture.toml
dpv run --config offline-fixture.toml --endpoint fixture --out offline-run
wait "$FIXTURE_PID"
trap - EXIT
```

Stop an unfinished fixture with `kill "$FIXTURE_PID"`. The fixture accepts only the two documented API paths and the model label `synthetic-fixture-model`; it is not a general proxy or mock service.

This example verifies only the explicitly labeled synthetic fixture contract. Its PASS is not official calibration and says nothing about a DeepSeek service or model identity. To repeat cases or expand the matrix, copy the custom profile, raise its per-protocol, per-endpoint, and total ceilings deliberately, set `run.repetitions`, and review the plan before running. The planner rejects configurations whose repeats exceed the declared profile budget.

## Compare and render stored evidence

Run multiple endpoints together by repeating `--endpoint`; `execution_order = "paired"` interleaves the selected endpoints case by case:

```sh
dpv run --config providers.toml \
  --endpoint reference --endpoint candidate \
  --out runs/paired
dpv compare runs/paired runs/paired \
  --reference-endpoint reference \
  --candidate-endpoint candidate \
  --model-map deepseek-flash=internal-flash-alias \
  --out runs/comparison
dpv report runs/comparison --format markdown
```

Different served model labels require an explicit `--model-map REFERENCE=CANDIDATE` or matching `contract_model` declarations. This mapping expresses operator intent; it does not authenticate weights. Add `--quality-policy policy.json` to enable quality gates. Without a policy, quality metrics remain descriptive and are labeled `INCONCLUSIVE (report only)`. Insufficient repeated prompt evidence or an unknown model release also prevents a quality PASS.

Each run stores `manifest.json`, hash-chained `attempts.jsonl` and `results.jsonl`, `evidence-index.json`, authoritative `summary.json`, derived `summary.md`, and `junit.xml`. Comparison loading validates the manifest, every available journal and checkpoint, aggregate counts, completion state, budget usage, and evidence references. Legacy records without an explicit HTTP completion marker remain unavailable; an HTTP status alone is never treated as proof that an exchange completed.

## Evidence and reference policy

Rules record their source URL, section, retrieval date, and optional source hash. Project-policy fixtures are labeled separately from documented provider behavior. Raw prompts, responses, and reasoning traces stay in local per-attempt evidence and are linked rather than copied into default summaries. Nothing is uploaded automatically.

Capture a fresh reference with the same profile, dataset, scorer revision, budgets, and date window as the candidate. Treat mutable model aliases as time-dependent labels: timestamp them, record the declared release, and remeasure them with the candidate. Never silently reuse an older alias baseline as current evidence.

Exit status `0` means all selected required gates completed and passed. Status `1` means completed evidence contains a required failure. Status `2` takes precedence for invalid setup, interruption, incomplete work, required execution errors, or an inconclusive required gate.

## Measured results

The September 22 follow-up tested official `deepseek-flash` and
`deepseek-v4-pro`: **696 inference requests** across both protocols, plus three
metadata/file-lifecycle requests. The frozen live run used merged revision
`a97566f`; the request and expectation corrections below were verified offline.

Select `deepseek-official-full-2026-09-22-v1` with the supplied two-model config:

```sh
uv run dpv plan --config configs/official-full.example.toml
```

The `full` preset means all shipped C01–C24 variants: 86 trials per model and a
212-request total ceiling. Exact-request replay of **198 saved responses** with
the corrected fixtures yields **154 PASS, 14 INCONCLUSIVE, 4 SKIP**, with 786
passing assertions and zero failed assertions. This fix made no additional live
calls. Both models have the same result: 77 PASS, 7 INCONCLUSIVE, 2 SKIP.

The profile enables 41 rules only within their recorded model/variant scopes.
Ambiguous tool choice (C10), reasoning omission (C15), and Responses option
effects (C24) stay diagnostic; Chat C06/C23 are inapplicable. A full run therefore
returns **exit 2**, even when all calibrated assertions pass. This is not a claim
of complete API conformance. Supplementary instruction-following mismatches,
validation differences, budget exhaustion, and lack of demonstrated vision
remain recorded limitations; acceptance alone never establishes capability.
See the [two-model calibration record](docs/calibration/official-both-models-2026-09-22.json)
and [scope and replay instructions](docs/contract-sources.md#official-two-model-catalog-calibration-september-22).
Candidate deployments remain untested.

### Historical Flash smoke calibration

Version 0.1.0 is a release candidate with scoped official calibration. Select
`profile = "deepseek-flash-smoke-2026-09-21-v1"` to use the calibrated Flash smoke
rules. The example's `deepseek-api-2026-09-21` base profile remains diagnostic.

| Endpoint/model | Protocol and observed settings | Evidence and limits |
| --- | --- | --- |
| Official `deepseek-flash` | Chat: 12 smoke trials, 15 requests; selected thinking/non-thinking and stream/non-stream variants | Measured assertions passed; exact per-rule scope recorded |
| Official `deepseek-flash` | Responses: 12 smoke trials, 15 requests; same selected smoke matrix | Measured assertions passed; exact per-rule scope recorded |
| Official V4 Pro / other models | Not covered by this September 21 smoke run | V4 Pro follow-up is recorded separately above |
| Candidate / direct engine / SMG | No live coverage | Deferred by operator choice |
| Installed CLI at `8b12f73` | Official Flash C01, both protocols and stream settings, four requests | Separate live confirmation: 4 HTTP 200, 4 PASS, 23 passing assertions |
| Current scorer after final review fixes | Both protocols; synthetic fixtures and exact-request replay of the 34 captured responses | Offline verification only; no additional live calls |

The official observations comprise **30 HTTP 200 responses and 197 passing
measured assertions across 24 trials**, collected on September 21, 2026 at
20:09–20:10 America/Los_Angeles using runner `bc3a9cc`. Their original diagnostic
results remain INCONCLUSIVE. The new profile promotes only the 19 observed rules
within their enumerated variants; it does not imply full protocol conformance.
Usage option branches, reasoning-effort strength, unknown checkpoint identity,
and quality equivalence remain outside the claim. See [calibration provenance
and exact limitations](docs/contract-sources.md).

A [separate CLI confirmation](docs/calibration/official-flash-cli-confirmation-2026-09-21.json)
ran at **22:35:53–22:35:59 PDT on September 21** using immutable `8b12f73`,
with exit 0 and verified evidence integrity. Total September 21 session traffic was **34
requests**. This predates the final streaming-envelope fixes; those changes
were checked offline against synthetic corruption tests and both captured runs.
The original calibration record and hashes remain unchanged.

Offline CI tests Python 3.11 and 3.14, checks schemas against runtime records,
exercises valid and seeded-fault fixtures, and checks wheel/sdist contents plus
a fresh installed CLI outside the checkout. The [manual live workflow](.github/workflows/live.yml)
is separate, unscheduled, and requires operator-managed environment protection
and credentials. It uploads only an allowlisted content-free summary. See
[reproducibility and CI setup](docs/reproducibility.md).
