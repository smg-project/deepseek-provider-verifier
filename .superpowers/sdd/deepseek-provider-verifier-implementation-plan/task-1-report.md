# Task 1 report: contract profiles and network-free planner

Status: **DONE**

## Delivered behavior

- Added a Python 3.11+ package and `uv.lock` with Pydantic, HTTPX,
  jsonschema, pytest, and Ruff at bounded major versions.
- Added strict Pydantic records for configuration, profiles, rules, case
  templates, expanded cases, manifests, attempts, case results, and run
  results. Every serialized record contains `schema_version: 1` and rejects
  unknown fields.
- Endpoint records accept only environment-variable identifiers for
  `api_key_env`, reject URL userinfo/query/fragment data, and hide rejected
  inputs from validation error strings.
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
  attempts and do not consume additional requests. Their concrete attachment
  IDs and rule IDs are materialized on every matching protocol/mode/stream
  variant.
- Named presets require exactly their request and attachment templates for
  every selected protocol. Empty preset case lists retain the caller-selected
  custom-workload behavior.
- Case-template steps are request recipes; `max_requests` must cover every
  declared step and may reserve bounded follow-up requests beyond them.
- Added a strict two-endpoint example using the approved `[run]` and
  `[endpoints.<name>]` TOML form.
- Added JSON Schemas generated from the Pydantic records rather than a second
  validation implementation.
- Added a versioned DeepSeek profile whose source-backed rules are
  `documented` for provenance and separately `diagnostic`/non-gating for
  calibration maturity. The profile preserves documented distinctions about
  Chat stream usage placement, thinking-mode tool choice, stateless Responses,
  and ignored Responses options as rule data.
- Aligned profile applicability and the reference example to the captured
  `deepseek-flash` and `deepseek-v4-pro` model IDs.
- Captured official source URL, section, retrieval timestamp, and SHA-256 in
  each rule. A committed bounded Chat capture contains one 14-word excerpt,
  its hash and selection context; full third-party captures remain ignored.
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

Review-fix RED for preset selection/attachments, credential metadata, and
step budgets:

```text
$ uv run pytest \
  tests/test_planner.py::test_planner_rejects_incomplete_named_preset \
  tests/test_planner.py::test_planner_rejects_extra_template_outside_named_preset \
  tests/test_planner.py::test_planner_attaches_assertions_without_adding_requests \
  tests/test_planner.py::test_endpoint_rejects_invalid_environment_variable_names \
  tests/test_planner.py::test_endpoint_rejects_secret_bearing_url_components \
  tests/test_planner.py::test_case_template_rejects_more_request_steps_than_budget -q
11 failed in 0.14s
```

Validation-error redaction then demonstrated the rejected environment values
were still present in exception strings:

```text
$ uv run pytest \
  tests/test_planner.py::test_endpoint_rejects_invalid_environment_variable_names \
  tests/test_planner.py::test_endpoint_rejects_secret_bearing_url_components -q
4 failed, 3 passed in 0.12s
```

Deep schema comparison also failed before regeneration:

```text
$ uv run pytest tests/test_planner.py -q
1 failed, 30 passed in 0.11s
```

Final focused GREEN:

```text
$ uv run pytest tests/test_planner.py -q
32 passed in 0.12s
```

Final complete available suite:

```text
$ uv run pytest -q
32 passed in 0.08s
```

Additional verification:

```text
$ uv run ruff check src tests
All checks passed!

$ uv run ruff format --check src tests
5 files already formatted

$ uv build --out-dir /private/tmp/dpv-task1-fix1-dist-20260921
Successfully built deepseek_provider_verifier-0.1.0.tar.gz
Successfully built deepseek_provider_verifier-0.1.0-py3-none-any.whl
```

An isolated environment outside the source checkout loaded both packaged
assets through `importlib.resources` and printed:

```text
['deepseek-flash', 'deepseek-v4-pro'] deepseek-flash True
```

The final `True` confirms the bounded Chat capture is present in the installed
wheel.

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
- All committed generated schemas are compared structurally with the current
  runtime models during the focused suite.
- The included smoke profile has an aggregate ceiling of 68, matching its two
  endpoint example. A different endpoint count or the full C01-C24 matrix
  requires an explicitly versioned larger preset/profile.
- Outcome policy for required diagnostic rules is intentionally left for the
  runner/result task; the records preserve enough maturity data to report such
  results as inconclusive rather than PASS.

No blocking concerns remain for Task 1.

## Fix round 1

The first scoped review identified four important contract gaps and one schema
coverage gap. This round addressed each finding:

1. **Credential-free endpoint metadata.** `api_key_env` now accepts only a
   portable environment-variable identifier. Endpoint URLs reject userinfo,
   query strings, and fragments before they can enter a manifest. Pydantic
   error strings hide rejected input values so invalid credential-like data is
   not echoed by later CLI error handling.
2. **Preset selection and attached assertions.** A named preset with nonempty
   case lists requires exactly every request and attachment template for each
   selected protocol. Missing and extra templates fail planning. C02, C17, and
   C20 expand as assertion attachments on matching protocol/mode/stream
   variants while adding zero outbound requests.
3. **Step/request accounting.** Every current `steps` entry is defined as a
   request recipe. `max_requests` must be at least the number of steps; a larger
   value is the bounded follow-up allowance used in ceilings.
4. **Reviewable Chat provenance.** The committed bounded capture records the
   official URL, retrieval timestamp, captured-page SHA-256, excerpt SHA-256,
   selection context, and one 14-word excerpt. It remains diagnostic source
   provenance, not observed endpoint evidence. The full third-party page stays
   ignored.
5. **Generated-schema parity.** The focused suite now compares every committed
   schema object with `model_json_schema()` for every `Record` subclass and
   checks that the filename sets are identical.

The same pass aligned the profile and example reference endpoint with the
captured `deepseek-flash` / `deepseek-v4-pro` model IDs, corrected seed rule
case applicability, described source-defined differences as documented
distinctions, and made Chat usage placement unconditional as documented.

### Exported attachment semantics

`Case.attached_assertion_case_ids` contains concrete IDs such as
`C17.chat.non_thinking.stream`. For a named preset, attachment templates are
matched to outbound cases by protocol, mode, and stream. Their `rule_ids` are
merged in stable order onto each matched `Case`, and their `required` flag can
promote that case to required. Attachment templates do not contribute steps,
requests, retry allowance, or output-token allowance to plan ceilings. An
attachment variant with no outbound match fails planning. Presets whose
request and attachment ID lists are both empty retain caller-selected custom
workloads.

### Fix-round verification

```text
$ uv run pytest \
  tests/test_planner.py::test_planner_rejects_incomplete_named_preset \
  tests/test_planner.py::test_planner_rejects_extra_template_outside_named_preset \
  tests/test_planner.py::test_planner_attaches_assertions_without_adding_requests \
  tests/test_planner.py::test_endpoint_rejects_invalid_environment_variable_names \
  tests/test_planner.py::test_endpoint_rejects_secret_bearing_url_components \
  tests/test_planner.py::test_case_template_rejects_more_request_steps_than_budget -q
11 failed in 0.14s

$ uv run pytest tests/test_planner.py -q
32 passed in 0.12s

$ uv run pytest -q
32 passed in 0.08s

$ uv run ruff check src tests
All checks passed!

$ uv run ruff format --check src tests
5 files already formatted

$ uv build --out-dir /private/tmp/dpv-task1-fix1-dist-20260921
Successfully built deepseek_provider_verifier-0.1.0.tar.gz
Successfully built deepseek_provider_verifier-0.1.0-py3-none-any.whl
```

The isolated installed-wheel check returned:

```text
['deepseek-flash', 'deepseek-v4-pro'] deepseek-flash True
```

This verifies the installed profile targets, example reference target, and
presence of the bounded capture. No live inference or remote publication was
performed during the fix round.
