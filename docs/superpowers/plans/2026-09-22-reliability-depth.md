# Reliability and Depth Suites Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Add opt-in repeatability, long-workflow, schema, and large-input/output suites that produce reproducible, honestly bounded measurements for self-hosted endpoints.

**Architecture:** Preserve the current manifest and journal format. Expand new versioned catalog descriptors into existing CaseTemplate/Case records, place validated opt-in semantics in oracle metadata, and use focused evaluators plus derived analysis reports. Reuse protocol assembly, credential handling, evidence integrity, resume, and paired comparison.

**Tech Stack:** Python 3.11+, existing httpx, Pydantic, jsonschema, pytest, and Ruff; standard-library statistics/math; no new mandatory dependency.

**Spec:** `docs/superpowers/specs/2026-09-22-reliability-depth-design.md`, approved by the user on 2026-09-22. A copy accompanies this plan as `reliability-depth-design-2026-09-22.md`.

**Workspace:** `/Users/keru/workspace/deepseek-provider-verifier-compatibility`, branch `feat/reliability-depth-suites`. PR #3 has merged; base the new PR on `main` at `e325026` or its verified descendant.

**Execution recommendation:** Native execution in this session, task by task, with one independent whole-branch review. The tasks share catalog metadata, assertion semantics, and reporting interfaces, so a single implementer avoids repeated handoffs. The user selected native execution in this session.

## Global Constraints

- Work under `~/workspace`; preserve MIT licensing and private repository visibility.
- No Co-Authored-By trailers. Sign commits with the existing contributor identity.
- Write failing tests before runtime changes. Keep synthetic tests offline.
- Do not commit credentials, raw provider output, reasoning traces, or live journals.
- Preserve the old profiles, datasets, manifest hashes, and replayed verdicts.
- Python 3.11 and 3.14 CI must pass; avoid new mandatory runtime dependencies.
- New fixtures are project-policy tests, not newly observed official calibration.
- Live recovery/load, parameter effects, caching, vision, and files are outside this change.
- Develop and verify offline first. Larger live workloads require a reviewed plan showing explicit resource ceilings; no live traffic is part of design preparation.

At execution start, confirm the branch is clean, run `uv sync --locked`, and verify
the existing offline baseline before adding the first failing test.

Do not alter defaults in records participating in canonical manifest hashes. Do not edit historical catalogs/profiles to add these suites. Validate every new metadata discriminator and reject unknown versions before network access. Keep all new suites at concurrency one and zero retries in shipped examples.

## Review Focus

1. **Interrupted and resumed trials:** an earlier failed/cancelled reservation must remain in first-request accounting; re-running a trial must not manufacture a known first-attempt contract PASS. Task 2 owns these tests.
2. **Branch state and repeated call IDs:** replay must use its declared parent, restore parent options, exclude siblings, and match results by exact call ID. Task 3 owns these tests.
3. **Schemas with indirect or recursive references:** no remote retrieval, recursion blowup, boolean-as-integer coercion, or union ambiguity may yield a false PASS. Task 4 owns these tests.
4. **Non-ASCII payloads and one oversized stream chunk:** byte guards must use actual serialization and bound retained bytes before appending/decoding. Task 5 owns these tests.
5. **Valid but short output, missing/contradictory usage, and context overclaims:** task correctness is separate from limit utilization; no missing measurement may certify a boundary. Task 6 owns these tests.

## Delivery and suite inventory

One feature branch, seven independently testable tasks, followed by one review-ready PR. Tasks 1–2 deliver priority 1; task 3 delivers priority 2; task 4 delivers priority 3; tasks 5–6 deliver priority 5; task 7 verifies distribution and regression preservation.

| Preset | Catalog IDs | Variants per endpoint, both protocols | Planned requests per endpoint |
| --- | --- | ---: | ---: |
| `repeatability` | R01–R20; five repetitions | 200 | 250 |
| `repeatability-expanded` | R21–R40; five repetitions | 800 | 1,000 |
| `workflows-small` | W01, W04, W06, W07 | 32 | 160 |
| `workflows-full` | W01–W07 | 56 | 384 |
| `schemas` | S01–S64 | 128, including 32 Chat output-schema skips | 128 ceiling; 96 requests without early failures |
| `sizes-small` | L01, L02, L05, L06, L09, L10 | 24 | 48 |
| `sizes-large` | L01–L11 | 44 | 92 |

These counts assume all selected endpoints expose both protocols. Examples name one `candidate`; profiles reserve twice the per-endpoint ceiling so an operator can explicitly select a reference and candidate together. Output ceilings are computed from materialized cases, not hand-maintained estimates. No preset includes another preset implicitly.

## File and interface map

New modules are focused on one responsibility:

| File | Responsibility |
| --- | --- |
| `depth_catalog.py` | Expand versioned depth descriptors and authored prompts into CaseTemplates |
| `depth_metadata.py` | Validate depth oracle metadata, request/response byte limits, and deployment-limit declarations |
| `reliability.py` | Pure grouped counts, per-prompt intervals, and derived report data |
| `workflows.py` | Evaluate exact per-step tool/history/answer expectations |
| `schema_cases.py` | Authored schema fixtures and bounded local-only validation |
| `size_cases.py` | Deterministic sized text, numbered-record oracle, and usage/boundary measurements |

All modules live under `src/deepseek_provider_verifier/`. Existing modules change only at their integration points: `catalog.py`, `assertions.py`, `runner.py`, `transport.py`, `reports.py`, `cli.py`, and validators in `records.py`.

Metadata for new cases lives under `case.oracle["depth"]`:

```json
{
  "version": 1,
  "family": "repeatability.instruction",
  "resource_limits": {
    "max_request_bytes": 8388608,
    "max_response_bytes": 8388608
  }
}
```

A family's semantic oracle still uses `oracle.kind`: existing kinds for repeatability; `workflow`, `schema_probe`, `large_input`, or `large_output` for later tasks. Typed metadata validators are separate from serialized legacy records. Case IDs identify cases; they do not determine family, sizes, or expected answers.

The new profile is `profiles/deepseek-depth-2026-09-22-v1.json`. Rules are explicitly project-policy, with deterministic fixture gates enabled where applicable and no invented official reference statuses. Case scope must work with arbitrary self-hosted model aliases.

---

## Task 1: Repeatability catalog and bounded presets

**Files:**
- Create: `src/deepseek_provider_verifier/depth_catalog.py`, `depth_metadata.py`
- Create: `cases/depth-prompts.jsonl`, `cases/depth-descriptors.json`
- Create: `profiles/deepseek-depth-2026-09-22-v1.json`
- Create: `configs/depth-repeatability.example.toml`, `configs/depth-repeatability-expanded.example.toml`
- Modify: `catalog.py`, `records.py`
- Test: `tests/test_depth_catalog.py`, `tests/test_depth_metadata.py`

**Interfaces:**
- `expand_depth_cases(case_ids: list[str], protocols: list[str]) -> list[CaseTemplate]`
- `validate_depth_oracle(oracle: dict) -> None` raises ValueError on invalid depth metadata and is a no-op when depth metadata is absent.
- `load_cases()` keeps its C01–C24 default. Explicit IDs append selected depth templates, without materializing unselected large fixtures.
- In `tests/test_depth_catalog.py`, define `depth_manifest(suite: str, repetitions: int = 1) -> Manifest` for later tests. It loads the committed depth config/profile, changes run.suite/repetitions, selects the preset IDs, and calls build_manifest.

- [x] **Step 1: Write the failing contract tests.**

```python
from deepseek_provider_verifier.catalog import load_cases


def test_repeatability_starter_is_bounded_and_varied():
    m = depth_manifest("repeatability", repetitions=5)
    assert len(m.cases) == 200
    assert m.request_ceiling == 250
    assert len({c.prompt_id for c in m.cases}) == 20
    assert all(c.mode == "non_thinking" and not c.stream for c in m.cases)
    assert {c.repetition for c in m.cases} == set(range(5))


def test_expanded_matrix_and_legacy_selection():
    m = depth_manifest("repeatability-expanded", repetitions=5)
    assert len(m.cases) == 800
    assert m.request_ceiling == 1000
    assert {(c.mode, c.stream) for c in m.cases} == {
        ("thinking", False), ("thinking", True),
        ("non_thinking", False), ("non_thinking", True),
    }
    assert {c.id for c in load_cases()} == {f"C{i:02d}" for i in range(1, 25)}
```

Add tests for duplicate prompt IDs, changed prompt hashes, absent requested templates,
unknown metadata version, bool/negative resource limits, and family values that are
not nonempty strings. Check that selecting R01 does not invoke size generation.

- [x] **Step 2: Run the new tests and verify failures are missing-feature failures.**

```sh
uv run --locked pytest tests/test_depth_catalog.py tests/test_depth_metadata.py -q
```

- [x] **Step 3: Add the descriptors, builder, and presets.**

R01–R05 are exact-text tasks with intended words amber/violet/cobalt/silver/green.
R06–R10 require `lookup_fixture`, using the existing harbor/orchard/station data
with five authored instructions and exact intended key arguments. Do not change
the existing tool registry or its schema to produce more keys.
Use `tool_choice="auto"` for the repeatability tool fixtures in both modes; the
authored prompt and oracle require the intended call. This measures tool-trigger
behavior without introducing the known thinking/forced-choice restriction.
R11–R15 require `add_integers` with distinct pairs `(17,25)`, `(6,8)`, `(9,4)`,
`(12,31)`, and `(24,18)`, followed by an exact final sum. R16–R20 produce typed
objects with differing labels/counts. Chat requests JSON-object output; Responses
requests typed JSON Schema. Responses uses strict=true for this known baseline.

R21–R40 reuse the same twenty prompt IDs and semantic fixtures while expanding
mode/stream axes. Starter and expanded presets select different template IDs;
no new preset-axis fields are added to ProfilePreset. Each prompt has a validated
BehavioralPrompt content hash and `dataset_version="depth-v1"`.

```python
# Metadata is carried explicitly through CaseTemplate into the hashed manifest.
oracle["depth"] = {
    "version": 1,
    "family": family,
    "resource_limits": {
        "max_request_bytes": 8 * 1024 * 1024,
        "max_response_bytes": 8 * 1024 * 1024,
    },
}
```

Use single requests except R11–R15/R31–R35, which require two and use bounded
continuation. Use output caps 512 for non-thinking and 4096 for thinking. Preset
request caps equal the table; per-protocol caps are half, total caps are twice.
Keep five repetitions in the two example configs and zero retries.

- [x] **Step 4: Verify generated data and hash preservation.**

```sh
uv run --locked pytest tests/test_depth_catalog.py tests/test_depth_metadata.py tests/test_planner.py tests/test_calibration.py tests/test_compatibility_catalog.py -q
uv run dpv plan --config configs/depth-repeatability.example.toml
```

Assert all twenty prompts have distinct content hashes, four family groups with
five distinct prompt IDs each, deterministic expansion, and exact planned counts.

- [x] **Step 5: Commit the independently usable catalog.**

```sh
git add src/deepseek_provider_verifier/depth_catalog.py src/deepseek_provider_verifier/depth_metadata.py src/deepseek_provider_verifier/catalog.py src/deepseek_provider_verifier/records.py cases/depth-prompts.jsonl cases/depth-descriptors.json profiles/deepseek-depth-2026-09-22-v1.json configs/depth-repeatability.example.toml configs/depth-repeatability-expanded.example.toml tests/test_depth_catalog.py tests/test_depth_metadata.py
git commit -s -m "Add bounded repeatability fixtures and presets"
```

## Task 2: Reliability analysis and report integration

**Files:**
- Create: `src/deepseek_provider_verifier/reliability.py`
- Modify: `reports.py`, `cli.py`, `docs/metrics.md`
- Test: `tests/test_reliability.py`, `tests/test_cli_reports.py`

**Interfaces:**
- `wilson_interval(successes: int, total: int, confidence: float = 0.95) -> tuple[float, float] | None`
- `summarize_trials(statuses: list[str | None]) -> dict` (None means missing planned result).
- `build_reliability(manifest: Manifest, result: RunResult) -> dict`
- `render_reliability_markdown(analysis: dict) -> str`
- Add optional keyword-only `manifest: Manifest | None = None` to render_report and write_report_bundle; existing calls retain their behavior.

- [x] **Step 1: Write failing denominator, uncertainty, and round-trip tests.**

```python
import pytest
from deepseek_provider_verifier.reliability import summarize_trials, wilson_interval


def test_errors_and_missing_work_do_not_disappear():
    row = summarize_trials(["PASS", "FAIL", "ERROR", "INCONCLUSIVE", "SKIP", None])
    assert row["planned"] == 6
    assert row["counts"]["MISSING"] == 1
    assert row["failure_rate"] == {"numerator": 1, "denominator": 2, "value": 0.5}


def test_no_observations_is_not_perfect_reliability():
    assert wilson_interval(0, 0) is None
    assert summarize_trials(["ERROR", None])["failure_rate"]["value"] is None
    low, high = wilson_interval(0, 5)
    assert low == pytest.approx(0)
    assert 0.43 < high < 0.44
```

Using the existing `manifest`, `run`, and `response` helpers from test_runner,
create a five-repetition depth-tagged case with responses PASS/PASS/FAIL/PASS/FAIL.
Assert one prompt, five repetitions, two failures, failure rate 0.4, and a mixed
within-prompt outcome. Add ERROR and SKIP runs to prove they do not become model
failures. Reject duplicate or extraneous result identities before aggregation.

Port the existing interrupted-reservation/resume fixtures from test_runner into
analysis assertions: initial interrupted requests remain unsuccessful initial
HTTP requests after recovery. A completed trial reused on resume is not counted
twice. If a whole trial was rerun and its original contract assessment is not
recoverable from retained result records, report first-contract status unavailable;
do not replace it with the later PASS. HTTP first/eventual accounting still uses
the lowest retained attempt number per logical request.

- [x] **Step 2: Run tests and inspect the expected failures.**

```sh
uv run --locked pytest tests/test_reliability.py tests/test_cli_reports.py -q
```

- [x] **Step 3: Implement pure counts and rendering.**

Use the standard Wilson formula, with validation rejecting negative counts,
counts above total, booleans as counts, and confidence outside (0,1):

```python
from math import sqrt
from statistics import NormalDist


def wilson_interval(successes, total, confidence=0.95):
    if type(successes) is not int or type(total) is not int:
        raise ValueError("counts must be integers")
    if not 0 <= successes <= total or not 0 < confidence < 1:
        raise ValueError("invalid counts or confidence")
    if total == 0:
        return None
    z = NormalDist().inv_cdf((1 + confidence) / 2)
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    half = z * sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return max(0.0, center - half), min(1.0, center + half)
```

Build groups from endpoint metadata and Case fields plus `oracle.depth.family`.
Do not parse the case ID. Include explicit per-prompt rows; apply Wilson only to
per-prompt measured binary outcomes, not to pooled correlated variants. Keep the
existing prompt-cluster bootstrap for candidate/reference comparisons unchanged.
HTTP aggregation follows docs/metrics.md, including durable unmatched reservations.

The sidecar contains schema_version, manifest_hash, a hash of source result data,
confidence method/assumptions, group rows, and per-prompt rows. It is derived,
not authoritative. On `dpv report`, recompute from `load_run_evidence`; never trust
a stale sidecar. Keep the canonical summary.json and evidence-index unchanged.
CLI run passes its manifest into report-bundle generation; CLI report loads the
manifest for run evidence while retaining the comparison-report path. Markdown
appends reliability analysis only when a supplied manifest has depth-tagged cases.

- [x] **Step 4: Verify analysis, resume, corruption detection, and legacy rendering.**

```sh
uv run --locked pytest tests/test_reliability.py tests/test_cli_reports.py tests/test_comparison.py tests/test_evidence.py tests/test_runner.py -q
```

Tampering with reliability.json must not alter regenerated output or exit status.
Changing the authoritative journal must still fail integrity validation. Old
JSON/Markdown/JUnit rendering without a manifest remains byte-compatible in the
existing golden tests. Statistical summaries never change the verification exit code.

- [x] **Step 5: Commit reporting.**

```sh
git add src/deepseek_provider_verifier/reliability.py src/deepseek_provider_verifier/reports.py src/deepseek_provider_verifier/cli.py docs/metrics.md tests/test_reliability.py tests/test_cli_reports.py
git commit -s -m "Report repeated trial failure rates and uncertainty"
```

## Task 3: Long, parallel, and branching workflows

**Files:**
- Create: `src/deepseek_provider_verifier/workflows.py`
- Modify: `depth_catalog.py`, `depth_metadata.py`, `runner.py`, `assertions.py`, depth descriptors/profile
- Create: `configs/depth-workflows.example.toml`
- Test: `tests/test_workflows.py`

**Interfaces:**
- `evaluate_workflow(case: Case, observations: list[Observation], rules: list[Rule]) -> CaseResult`
- Each recipe adds `expect` with either `{"tools":[{"name":...,"arguments":...}]}` or `{"text":...}`.
- Parent is the preceding recipe unless `replay_from` declares a prior recipe index. New workflow metadata requires one response per recipe and bounded_steps=true.

- [x] **Step 1: Add failing workflow tests.**

```python
from test_depth_catalog import depth_manifest


def test_workflow_bounds_are_exact():
    small = depth_manifest("workflows-small")
    full = depth_manifest("workflows-full")
    assert (len(small.cases), small.request_ceiling) == (32, 160)
    assert (len(full.cases), full.request_ceiling) == (56, 384)
    assert max(c.max_requests for c in full.cases) == 17
```

Construct deterministic provider fixtures using protocol_response and
stream_response from the compatibility tests. Execute complete chains and assert
that the exact preceding sum is present in the next call's arguments. For the
branch fixture, vary a request option on the first branch, then assert the second
branch restores the parent's value before applying its own override.

Inject faults separately: final answer without any tool rounds; one missing round;
wrong but schema-valid arithmetic arguments; extra call; duplicate ID; wrong tool
result ID; reversed parallel result order; mutated reasoning; sibling output in a
branch; redirect with a valid body; service error after an earlier answer failure.
Reversed ordering alone must pass when IDs and call argument multisets are correct.

- [x] **Step 2: Verify the new tests fail before implementation.**

```sh
uv run --locked pytest tests/test_workflows.py -q
```

- [x] **Step 3: Implement the recipe evaluator and branch state restoration.**

Use these exact workload shapes:

| ID | Shape | Maximum requests |
| --- | --- | ---: |
| W01/W02/W03 | 4/8/16 successive add_integers rounds, then final answer | 5/9/17 |
| W04/W05 | 4/16 parallel add_integers calls in one response, then exact result list | 2/2 |
| W06 | Shared add(1,2); branch A add(3,4) then answer 7; branch B from step 0 add(3,10) then answer 13 | 5 |
| W07 | Seven fact introduction/update turns, then exact recall of the latest values | 8 |

All seven cases have both mode and stream axes on both protocols. The first chain
sum is 1; zero-based round i adds i+1, so depth d ends at `1 + d*(d+1)//2`. The final expected sum is authored from those fixture
values, not taken from model output. Parallel fixtures use distinct operand pairs
and validate the complete argument multiset using canonical JSON representations.

For workflow branches, save request option state at each recipe. On replay_from,
restore the parent's pre-response history and options, append the parent's original
assistant items and registered tool outputs, then apply the branch recipe's user
message and request overrides. Keep existing P01 mutation behavior unchanged.

Evaluate each response structurally via the existing positive-response checks,
using an isolated structure oracle; apply exact per-step expectations separately.
Validate complete outgoing history against the declared parent edge, including
unchanged reasoning and matched tool outputs. Do not apply the old adjacent-row
history check to branches. A known execution error anywhere in the observed graph
wins over a functional failure. Missing steps never pass.

Emit workflow depth/width and completed-round counts as assertion observations;
retain existing per-call tool schema metrics and one final task-success metric.
For thinking variants, separately count nonempty returned reasoning rounds. A
workflow without at least two such rounds cannot establish accumulated reasoning
replay; mark that facet INCONCLUSIVE instead of treating empty placeholders as
proof. Its tool/history task observations remain available.

- [x] **Step 4: Verify workflow execution and old continuation behavior.**

```sh
uv run --locked pytest tests/test_workflows.py tests/test_runner.py tests/test_compatibility_policy.py tests/test_compatibility_execution.py -q
```

Run Chat and Responses fixtures through real assemblers for nonstream and SSE,
including fragmented/interleaved arguments. Preserve historical C13–C16 and P01
requests/verdicts. Test early stop at the declared deadline/request bound.

- [x] **Step 5: Commit workflows.**

```sh
git add src/deepseek_provider_verifier/workflows.py src/deepseek_provider_verifier/depth_catalog.py src/deepseek_provider_verifier/depth_metadata.py src/deepseek_provider_verifier/runner.py src/deepseek_provider_verifier/assertions.py cases/depth-descriptors.json profiles/deepseek-depth-2026-09-22-v1.json configs/depth-workflows.example.toml tests/test_workflows.py
git commit -s -m "Verify long tool chains and branching histories"
```

## Task 4: Bounded schema capability matrix

**Files:**
- Create: `src/deepseek_provider_verifier/schema_cases.py`
- Modify: `depth_catalog.py`, `depth_metadata.py`, `assertions.py`, depth descriptors/profile
- Create: `configs/depth-schemas.example.toml`
- Test: `tests/test_schema_cases.py`

**Interfaces:**
- `validate_local_schema(schema: dict) -> None`
- `schema_matches(schema: dict, value: object) -> bool`
- `evaluate_schema_probe(case: Case, observations: list[Observation], rules: list[Rule]) -> CaseResult`
- Schema oracle fields: target=`tool`/`output`, schema, expected_value, strict boolean or omitted, required_support boolean, and depth family metadata.

- [x] **Step 1: Write failing local-reference and semantic tests.**

```python
import pytest
from deepseek_provider_verifier.schema_cases import validate_local_schema, schema_matches


def test_remote_and_recursive_references_are_rejected():
    with pytest.raises(ValueError):
        validate_local_schema({"$ref": "https://example.invalid/schema"})
    with pytest.raises(ValueError):
        validate_local_schema({"$defs": {"self": {"$ref": "#/$defs/self"}}, "$ref": "#/$defs/self"})


def test_null_optional_and_numeric_types_are_distinct():
    schema = {"type": "object", "properties": {"n": {"type": ["integer", "null"]}},
              "additionalProperties": False}
    validate_local_schema(schema)
    assert schema_matches(schema, {})
    assert schema_matches(schema, {"n": None})
    assert not schema_matches(schema, {"n": True})
    assert not schema_matches(schema, {"extra": 1})
```

Add positive and invalid-output fixtures for each feature listed below. Block
network access during schema validation. Include unresolved JSON pointers,
oneOf matching two branches, optional keys under strict mode, 400/422 rejection,
401/402/429/500 service errors, malformed accepted JSON, and schema-valid but
fixture-incorrect values. Baseline rejection must fail; optional subset rejection
must be visible without claiming the feature works.

- [x] **Step 2: Run schema tests to confirm missing functionality.**

```sh
uv run --locked pytest tests/test_schema_cases.py -q
```

- [x] **Step 3: Implement the 16-feature by 4-target/strict matrix.**

Feature order: required object, optional absent, optional present, nullable null,
nullable nonnull, enum, const, array bounds, numeric bounds, string bounds,
nesting depth 2, depth 4, depth 8, anyOf, oneOf, local reference. For feature index
f=0..15, IDs S(4*f+1)..S(4*f+4) are tool/strict-omitted, tool/strict-true,
output/strict-omitted, output/strict-true. Both protocols have a template for every
ID; Chat output-schema templates are explicit inapplicable SKIPs. All are
non-thinking, nonstream, single-request cases.

Baseline required-object tool/strict-omitted is mandatory on both protocols;
required-object Responses output variants are mandatory. Other subset variants
allow explicit 400/422 rejection with a capability observation. Tool strict=true
uses the function strict field; Responses output strict uses text.format.strict.
Use one advertised `record_schema_fixture` function with the fixture schema and
no tool execution/continuation. Validate its name and exact argument value.

Traverse schemas before constructing Draft202012Validator: maximum 64 KiB
serialized schema, 2,048 schema nodes, and depth 32; refs must be resolvable local
JSON pointers; reject reference cycles and remote IDs/refs. Validate schema syntax
with Draft202012Validator.check_schema. Bound candidate value size/node count before
validation and collect only enough validation detail to classify the result.

Emit capability labels ACCEPTED_VALID, ACCEPTED_INVALID, REJECTED, ERROR. An
optional rejection can pass the probe's observation contract but must never
produce a supported-feature or schema-validity score of 1. Accepted output must
satisfy both schema and intended fixture. No new official status expectation is added.

- [x] **Step 4: Verify matrix inventory and all seeded schema faults.**

```sh
uv run --locked pytest tests/test_schema_cases.py tests/test_assertions.py tests/test_compatibility_policy.py tests/test_depth_catalog.py -q
```

Assert 128 planned trials, 32 Chat applicability skips, and 96 actual requests
for a fixture provider with no early failures. Exercise optional rejection and
accepted-invalid fixtures separately in JSON/Markdown/JUnit results.

- [x] **Step 5: Commit schema coverage.**

```sh
git add src/deepseek_provider_verifier/schema_cases.py src/deepseek_provider_verifier/depth_catalog.py src/deepseek_provider_verifier/depth_metadata.py src/deepseek_provider_verifier/assertions.py cases/depth-descriptors.json profiles/deepseek-depth-2026-09-22-v1.json configs/depth-schemas.example.toml tests/test_schema_cases.py
git commit -s -m "Add schema keyword and strict-subset probes"
```

## Task 5: Enforce request and capture byte budgets

**Files:**
- Modify: `depth_metadata.py`, `runner.py`, `transport.py`, `cli.py`
- Test: `tests/test_resource_limits.py`, `tests/test_transport.py`, `tests/test_runner.py`

**Interfaces:**
- `resource_limits(case: Case) -> tuple[int | None, int | None]` returns request/response caps; old cases return `(None, None)`.
- Add keyword-only `max_response_bytes: int | None = None` to send_request.
- Use httpx Request construction to inspect the exact outgoing JSON bytes before reserving/sending an attempt.
- `plan_resource_summary(manifest: Manifest) -> dict` produces derived byte ceilings outside the hashed legacy Manifest record.

- [x] **Step 1: Add failing preflight and oversized-chunk tests.**

```python
import httpx
import pytest
from test_runner import manifest, run
from deepseek_provider_verifier.runner import rehash_manifest


def test_oversize_request_is_not_sent_or_debited():
    m = manifest(steps=[{"kind": "user", "content": "你" * 100}])
    case = m.cases[0]
    oracle = {**case.oracle, "depth": {"version": 1, "family": "limits",
              "resource_limits": {"max_request_bytes": 128, "max_response_bytes": 1024}}}
    m = rehash_manifest(m.model_copy(update={"cases": [case.model_copy(update={"oracle": oracle})]}))
    def forbidden(request):
        pytest.fail("An oversized request reached the transport")
    result = run(m, forbidden)
    assert result.exit_code == 2
    assert result.budget_usage.get("candidate", 0) == 0
    assert result.case_results[0].reason == "REQUEST_BYTE_LIMIT"
```

Test a response arriving as one chunk larger than its cap, many chunks exactly at
the cap, multibyte text split across the cap, unfinished SSE at the cap, and a
complete valid response exactly at the cap. Assert the stream closes, retained
raw bytes do not exceed the cap, interrupted delivery is not marked complete,
and reports/redaction do not expose a secret from the partial body.

- [x] **Step 2: Verify failure before changing transport.**

```sh
uv run --locked pytest tests/test_resource_limits.py -q
```

- [x] **Step 3: Implement guards without changing legacy request serialization.**

Before debiting a request, measure `httpx.Request("POST", url, json=payload).content`.
Do not send or reserve a request that exceeds its cap; emit REQUEST_BYTE_LIMIT and
an incomplete execution result. Keep the original `json=payload` transport path
so historical wire JSON behavior remains unchanged. Test measured size against
captured request.content using both ASCII and non-ASCII payloads.

During streaming, check `offset + len(chunk)` before retaining a chunk or feeding
it to SSEDecoder. Retain at most the remaining byte allowance, stop reading/close
transport, and emit RESPONSE_BYTE_LIMIT with http_exchange_completed=false. Do
not finalize an unfinished SSE event as completed. Preserve safe partial evidence
using the current redaction rules and mark the case ERROR/incomplete with exit 2.

Plan output includes per-request/capture caps, exact authored input bytes, and
conservative aggregate byte ceilings computed from caps and request ceilings.
These are outside the legacy manifest schema; the case metadata containing the
caps is already hashed. Runtime checks include accumulated multi-turn history.

- [x] **Step 4: Verify limits and transport regressions.**

```sh
uv run --locked pytest tests/test_resource_limits.py tests/test_transport.py tests/test_runner.py tests/test_release_faults.py tests/test_task3_review_fixes.py -q
```

Verify an old case without depth metadata emits the same requests and result as
before. A local byte cap must not be reported as a server rejection or as a
successfully reached generation limit.

- [x] **Step 5: Commit resource controls.**

```sh
git add src/deepseek_provider_verifier/depth_metadata.py src/deepseek_provider_verifier/runner.py src/deepseek_provider_verifier/transport.py src/deepseek_provider_verifier/cli.py tests/test_resource_limits.py tests/test_transport.py tests/test_runner.py
git commit -s -m "Bound outgoing payloads and retained response captures"
```

## Task 6: Deterministic input ramps and measured output boundaries

**Files:**
- Create: `src/deepseek_provider_verifier/size_cases.py`
- Modify: `depth_catalog.py`, `depth_metadata.py`, `assertions.py`, `reliability.py`, depth descriptors/profile
- Create: `configs/depth-sizes.example.toml`, `configs/depth-sizes-large.example.toml`
- Test: `tests/test_size_cases.py`

**Interfaces:**
- `sized_text(byte_count: int, seed: int = 0) -> tuple[str, dict[str, str]]` returns exact UTF-8-sized fixture text and its expected retrieval facts.
- `numbered_prefix(text: str, allow_partial: bool) -> tuple[bool, int]` validates `record-000001\nrecord-000002\n...` and returns valid/complete-record count.
- `evaluate_size_case(case: Case, observations: list[Observation], rules: list[Rule]) -> CaseResult`
- Optional per-endpoint deployment declarations live in the new size rules' free-form `conditions.deployment_limits`, keyed by endpoint name, with context_tokens/output_tokens positive integers. Empty by default; do not add Endpoint defaults or infer capacities from model names.

- [x] **Step 1: Write failing exact-size and generation-proof tests.**

```python
from deepseek_provider_verifier.size_cases import sized_text, numbered_prefix


def test_sized_text_is_exact_and_reproducible():
    first, facts = sized_text(16 * 1024, seed=7)
    second, again = sized_text(16 * 1024, seed=7)
    assert len(first.encode("utf-8")) == 16 * 1024
    assert first == second and facts == again
    assert set(facts) == {"begin", "middle", "end"}
    assert len(set(facts.values())) == 3


def test_record_prefix_detects_repeats_and_partial_records():
    assert numbered_prefix("record-000001\nrecord-000002\n", False) == (True, 2)
    assert numbered_prefix("record-000001\nrecord-000001\n", False)[0] is False
    assert numbered_prefix("record-000001\nrecord-000", True) == (True, 1)
    assert numbered_prefix("record-000001\nrecord-000", False)[0] is False
```

Generate a valid long prefix with reported output usage 3,700 of 4,096 and a
length terminal: boundary exercised. Then vary one condition: 100 reported tokens,
absent usage, reasoning-dominated usage, usage above the requested cap, wrong
sequence, empty visible output, unexpected tool call, redirect, local capture cap,
or completed EOS with a partial record. None may report an exercised output cap.

For inputs, corrupt only the beginning or middle fact in the answer to prove
that returning the final fact alone is insufficient. Test absent context metadata,
unknown usage, and differing endpoint declarations. No byte-to-token estimate
is used as a measurement. Declared output maxima below a selected positive cap
must fail planning instead of silently changing the request.

- [x] **Step 2: Confirm new size tests fail before implementation.**

```sh
uv run --locked pytest tests/test_size_cases.py -q
```

- [x] **Step 3: Build descriptors and evaluate actual measurements.**

L01–L04 are 16/64/256/1024 KiB single-user inputs. L05–L08 distribute the same
respective total authored byte sizes over four user turns; the first three
answers acknowledge receipt and the fourth retrieves all three facts. L09–L11
request numbered-record output with caps 1024/4096/16384. All use non-thinking
mode with stream false/true on both protocols. Input cases use a 512-token
response cap. The descriptor file stores sizes/seeds, not megabytes of padding;
expand only selected cases into the manifest.

Generate varied deterministic filler and three unique fact values from the seed.
Size the complete authored user content exactly, including fixture instructions;
fail if the requested byte count is too small. Place facts near the beginning,
middle, and end. Do not use a single repeated padding token or place the complete
answer in the final retrieval question.

For output prompts, request more than twice the token cap in numbered records,
so reaching the requested count cannot explain a short finish. A partial final
record is permitted only with a proven length/incomplete terminal. Require at
least one complete correct record and valid usage; for Chat subtract reported
reasoning_tokens when determining visible generation utilization. Apply the
corresponding Responses output_tokens_details accounting. Negative, inconsistent,
or above-cap usage is invalid evidence, not a boundary PASS.

Emit separate observations for task correctness, requested-cap utilization, and
declared-deployment-limit utilization. Input retrieval may PASS while the unknown
context-boundary measurement remains unavailable. Output undergeneration or
missing measurement leaves the boundary trial INCONCLUSIVE, retaining any valid
prefix/task observation. Known malformed output fails. A 400/422 from a size probe
with no established within-capacity claim is a REJECTED capacity observation,
not proof of protocol nonconformance; do not claim it passed a boundary. Known
within-declared-output-cap rejection is a functional failure. Transport/local
resource failures are ERROR and retain exit 2 precedence.

Add size/capability rows to the derived reliability report using assertion
observations already stored in CaseResult; do not reread raw response bodies for
reporting. A user may copy the depth profile and add deployment_limits explicitly;
the declared values and endpoint mapping become part of the profile hash.

- [x] **Step 4: Verify large descriptors, bounded execution, and exact inventories.**

```sh
uv run --locked pytest tests/test_size_cases.py tests/test_resource_limits.py tests/test_depth_catalog.py tests/test_reliability.py -q
```

Assert small preset = 24 trials/48 requests, large preset = 44 trials/92 requests.
Verify 1 MiB fixture generation offline, but use compact synthetic responses for
unit tests. Confirm the ordinary repeatability plan does not build or include
large strings and that all examples retain serial execution/zero retries.

- [x] **Step 5: Commit size probes.**

```sh
git add src/deepseek_provider_verifier/size_cases.py src/deepseek_provider_verifier/depth_catalog.py src/deepseek_provider_verifier/depth_metadata.py src/deepseek_provider_verifier/assertions.py src/deepseek_provider_verifier/reliability.py cases/depth-descriptors.json profiles/deepseek-depth-2026-09-22-v1.json configs/depth-sizes.example.toml configs/depth-sizes-large.example.toml tests/test_size_cases.py
git commit -s -m "Measure bounded large-input and output workloads"
```

## Task 7: Installed distribution, regression replay, documentation, and PR

**Files:**
- Modify: `README.md`, `docs/case-catalog.md`, `docs/metrics.md`, `scripts/check_release.py`, `pyproject.toml`
- Create: `docs/reliability-depth.md`
- Test: `tests/test_depth_integration.py`, installed CLI checks

**Interfaces:**
- Existing `dpv plan`, `dpv run`, `dpv report`, and `dpv compare` remain the user entry points.
- New examples select the named depth profile/preset; no new live workflow or automatic schedule is introduced.
- Installed smoke: select R01 on both protocols, two repetitions, non-thinking/nonstream; its authored answer is amber so the existing synthetic provider can serve all four requests.

- [x] **Step 1: Write failing integration and installed-release assertions.**

```python
from test_depth_catalog import depth_manifest


def test_depth_presets_are_bounded_opt_in_workloads():
    names = ["repeatability", "repeatability-expanded", "workflows-small",
             "workflows-full", "schemas", "sizes-small", "sizes-large"]
    for name in names:
        m = depth_manifest(name, repetitions=5 if name.startswith("repeatability") else 1)
        assert m.profile_snapshot.id == "deepseek-depth-2026-09-22-v1"
        assert m.budgets.retries == 0 and m.budgets.concurrency == 1
        assert all("depth" in c.oracle for c in m.cases)
```

Run each family through synthetic HTTP/SSE and the real runner. Seed one failure
per family and assert verdict, JSON observation, Markdown detail, and JUnit status
agree. Include a mixed-success repeatability run with an interrupted/resumed trial
and prove the derived report does not lose its earlier request failure.

- [x] **Step 2: Extend release checks and document limits.**

In scripts/check_release.py, load all seven presets using the freshly installed
wheel outside the checkout and verify the inventory table. Use a copied custom
profile selecting only R01 with sufficient budgets for two repetitions, then run
the existing installed synthetic HTTP provider for four requests. Assert the
installed CLI writes reliability.json, four PASS trials, four actual requests,
two distinct endpoint/protocol group rows, and one prompt per row. Regenerate its
Markdown report and compare the analysis. Keep the previous four-request legacy
fixture check as a separate compatibility test.

Bundle docs/reliability-depth.md alongside existing docs. Document exact command
examples, per-suite budgets, family semantics, confidence assumptions, optional
feature rejection, size units, local resource caps, deployment-limit declarations,
and offline-versus-live evidence. New fixtures have no official calibration claim.

- [x] **Step 3: Run complete verification once the implementation is stable.**

```sh
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pytest -q
uv build
uv run --locked python scripts/check_release.py
uv run --locked python scripts/replay_official_calibration.py --evidence-root /Users/keru/workspace/deepseek-provider-verifier/runs/official-full-20260922
git diff --check
```

Expected historical replay: 198 exact requests, 154 PASS / 14 INCONCLUSIVE /
4 SKIP, 786 passing assertions, identical recorded dataset/profile hashes, and
zero live calls. Existing compatibility profiles retain their 99/37 trial matrices.
Run the repository CI on Python 3.11 and 3.14. Re-run broader checks only after
code/data changes, failures, or a specific unresolved concern.

- [x] **Step 4: Independent whole-branch review, fix findings, then commit.**

Use requesting-code-review with a clean-context reviewer and the approved spec,
this plan, base/head commits, and explicit review focus above. Reproduce each
blocking finding in a failing test, fix it, and rerun relevant checks. Update this
plan's checkboxes only when each deliverable is actually complete.

```sh
git add README.md docs/case-catalog.md docs/metrics.md docs/reliability-depth.md scripts/check_release.py pyproject.toml tests/test_depth_integration.py
git commit -s -m "Document and verify installed reliability suites"
```

- [x] **Step 5: Open the review-ready PR and verify CI/DCO.**

Inspect staged files for credentials/raw runs, push the feature branch, and create
the PR against the current main. Describe the completed four suites, exact offline
coverage, historical replay, and remaining live calibration. Do not merge. Any
live run is a separate bounded execution after inspecting its manifest and
confirming the intended endpoints and current authorization.

## Self-review and handoff

The plan covers the four approved areas without adding load, caching, vision, or
file features. All new metadata has an owner and validator. Historical manifests
and journals remain unchanged; derived reports are recomputed from validated
sources. Each of the five review risks has explicit tests in its owning task.

Implementation, offline package verification, and independent whole-branch review are
complete. The review found one HTTP 402 classification defect; a regression test
failed on both protocols before the fix and all 695 tests pass afterward.
PR #4 is open against main. Python 3.11 and 3.14 CI and DCO passed on
implementation commit b4b1169; documentation-only completion updates remain subject
to the same automatic checks. No live calibration was performed for these suites.
