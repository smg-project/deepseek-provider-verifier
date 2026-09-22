# Task 1 report: contract profiles and network-free planner

Status: **DONE**

## Delivered behavior

- Added a Python 3.11+ package and `uv.lock` with Pydantic, HTTPX,
  jsonschema, pytest, and Ruff at bounded major versions.
- Added strict Pydantic records for configuration, profiles, rules, case
  templates, expanded cases, manifests, attempts, case results, and run
  results. Every serialized record contains `schema_version: 1` and rejects
  unknown fields.
- Serialized result outcomes use the canonical `PASS`, `FAIL`, `ERROR`,
  `SKIP`, and `INCONCLUSIVE` spellings.
- Added `load_config(path)`, `load_profile(path)`, and the pure
  `build_manifest(config, profile, cases)` transformation. The planner expands
  mode and streaming variants, validates rule/protocol references, enforces
  conversation, protocol, endpoint, aggregate, retry, concurrency, and token
  budgets, and hashes the comparable workload without timestamps or run IDs.
- Added an inspectable `PlanBudgets` record that retains the selected suite,
  protocols, actual retry/concurrency settings, operator endpoint-attempt cap,
  and profile ceilings in each manifest.
- Added the exact smoke preset: 17 requests per protocol, 34 per endpoint for
  both protocols, zero retries, four requests per conversation, concurrency
  one, a 300-second deadline, 512 non-thinking tokens, and 4096 thinking
  tokens. C02, C17, and C20 are identified as assertions attached to existing
  attempts and do not consume additional requests.
- Added a strict two-endpoint example using the approved `[run]` and
  `[endpoints.<name>]` TOML form.
- Added JSON Schemas generated from the Pydantic records rather than a second
  validation implementation.
- Added a versioned DeepSeek profile whose source-backed rules are
  `documented` for provenance and separately `diagnostic`/non-gating for
  calibration maturity. The profile preserves documented conflicts about
  Chat stream usage placement, thinking-mode tool choice, stateless Responses,
  and ignored Responses options as rule data.
- Captured official source URL, section, retrieval timestamp, and SHA-256 in
  each rule. Full third-party captures remain ignored and were not committed.
- Configured the wheel to contain the single authoritative profile, schemas,
  example config, and contract-source note as package resources.

No HTTP client is created by configuration loading or planning. No live
inference request was made. API-key environment variable names are preserved
for later credential resolution; environment values never enter a serialized
record or hash.

## TDD evidence

Initial RED:

```text
$ uv run pytest tests/test_planner.py -q
E   ModuleNotFoundError: No module named 'deepseek_provider_verifier.config'
1 error in 0.34s
```

This followed two packaging-scaffold corrections (missing readme and empty
forced-asset directories) and was the intended failure caused by the absent
planner implementation.

The budget-inspection self-review also used a separate RED/GREEN cycle:

```text
$ uv run pytest tests/test_planner.py::test_planner_counts_every_variant -q
E   AttributeError: 'ProfilePreset' object has no attribute 'suite'
1 failed in 0.08s

$ uv run pytest tests/test_planner.py::test_planner_counts_every_variant -q
1 passed in 0.06s
```

The shared result-status correction was also test-first:

```text
$ uv run pytest tests/test_planner.py::test_result_status_uses_canonical_spelling -q
E   Input should be 'pass', 'fail', 'inconclusive', 'error' or 'skipped'
1 failed in 0.07s

$ uv run pytest tests/test_planner.py::test_result_status_uses_canonical_spelling -q
1 passed in 0.05s
```

Final focused GREEN:

```text
$ uv run pytest tests/test_planner.py -q
20 passed in 0.07s
```

Final complete available suite:

```text
$ uv run pytest -q
20 passed in 0.07s
```

Additional verification:

```text
$ uv run ruff check src tests
All checks passed!

$ uv run ruff format --check src tests
5 files already formatted

$ uv build --out-dir /private/tmp/dpv-task1-dist-final-20260921
Successfully built deepseek_provider_verifier-0.1.0.tar.gz
Successfully built deepseek_provider_verifier-0.1.0-py3-none-any.whl
```

An isolated environment outside the source checkout loaded both packaged
assets through `importlib.resources` and printed:

```text
deepseek-api-2026-09-21 2 34
```

## Exported APIs

Functions:

```python
load_config(path: Path) -> Config
load_profile(path: Path) -> Profile
build_manifest(
    config: Config,
    profile: Profile,
    cases: list[CaseTemplate],
) -> Manifest
```

Records exported from `deepseek_provider_verifier`:

```text
Endpoint, RunSettings, Config, Rule, ProfilePreset, Profile, PlanBudgets,
CaseTemplate, Case, Manifest, Attempt, CaseResult, RunResult
```

Protocol names are exactly `chat` and `responses`; modes are exactly
`non_thinking` and `thinking`. Evidence provenance is one of `documented`,
`observed`, or `project-policy`; calibration maturity is `diagnostic` or
`calibrated`. A diagnostic rule cannot be a gate.
Result statuses are exactly `PASS`, `FAIL`, `ERROR`, `SKIP`, and
`INCONCLUSIVE`.

## Self-review notes

- The comparable `manifest_hash` excludes `run_id`, `created_at`, and the hash
  field itself. It includes the profile/dataset hashes, scorer revision,
  endpoint metadata without credential values, cases, actual/configured
  budgets, ceilings, and enabled gates.
- Retry accounting conservatively multiplies every possible conversation
  request and output-token allowance by `retries + 1`.
- The included smoke profile has an aggregate ceiling of 68, matching its two
  endpoint example. A different endpoint count or the full C01-C24 matrix
  requires an explicitly versioned larger preset/profile.
- Outcome policy for required diagnostic rules is intentionally left for the
  runner/result task; the records preserve enough maturity data to report such
  results as inconclusive rather than PASS.

No blocking concerns remain for Task 1.
