# Official-baseline Compatibility Acceptance Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans` to implement this plan task by task after the design is accepted. Complete an independent review before delivery. The user approved this plan for execution.

**Goal:** Make the recommended compatibility acceptance pass the official API under the same meaningful rules applied to self-hosted endpoints, while preserving strict failures and uncertainty.

**Architecture:** Keep canonical run evidence and existing run/report behavior intact. Add a pure acceptance evaluator, explicit versioned policies, separate acceptance reports, and verify/assess CLI entry points. Separate retrieval correctness, raw formatting, total output budget, and visible-token measurement.

**Tech Stack:** Existing Python >=3.11, Pydantic, HTTPX, jsonschema, pytest, Ruff, and uv. No new runtime dependencies.

**Spec:** `docs/superpowers/specs/2026-09-22-official-compatibility-design.md`; user-facing copy: `official-compatibility-design.md` alongside this file.

## Global constraints

- Both official and self-hosted endpoints use the same policy; no hostname/model-label PASS bypass.
- Preserve historical run records, hashes, original assertions, and current legacy command behavior.
- No hidden inference retries, post-hoc threshold tuning, or replacement of failed verification runs.
- Reject malformed protocol data and incorrect mandatory functional results.
- Keep absent measurements unknown; distinguish optional capability findings from certified support.
- Preserve MIT and repository privacy; no credentials/raw live evidence in commits; DCO sign-offs without co-author trailers.
- This planning step makes no live calls. At execution, use the user's existing authorization and generate the bounded workload before live calibration.

## File structure

| File | Responsibility |
| --- | --- |
| `src/deepseek_provider_verifier/acceptance_records.py` (new) | Validated policy, facet, and acceptance result types; independent of historical record schemas |
| `src/deepseek_provider_verifier/acceptance.py` (new) | Pure policy evaluation, coverage accounting, verdict/exit calculation |
| `src/deepseek_provider_verifier/acceptance_facets.py` (new) | Derive narrowly scoped facets from verified existing evidence without rewriting CaseResult |
| `src/deepseek_provider_verifier/acceptance_reports.py` (new) | Acceptance JSON, Markdown, JUnit from one result |
| `src/deepseek_provider_verifier/cli.py` | Add verify and assess; preserve run/report/compare |
| `policies/official-compatible-v1.json` (new) | Published default compatibility contract and optional-capability classification |
| `policies/strict-contract-v1.json` (new) | Strict assessment with schema/format assertions blocking |
| `profiles/deepseek-verification-2026-09-22-v1.json` (new) | Combine explicit existing core controls with depth probes; separate compact and full-stress presets |
| `configs/self-hosted-verify.example.toml` (new) | Bounded, meaningful core workload usable against either provider |
| `tests/test_acceptance*.py` (new) | Policy, facets, command/report agreement, adversarial endpoints |
| `scripts/check_release.py`, `pyproject.toml` | Bundle policies/configs and exercise installed CLI |
| `README.md`, `docs/self-hosted-compatibility.md`, `docs/reliability-depth.md` | Explain exact certification scope and recommended commands |

No refactoring of unrelated protocol adapters, transport, or statistical comparison.

## Shared interfaces

New types belong to `acceptance_records.py`. All use strict Pydantic validation; case and assertion identities are explicit rather than wildcard-waived.

```python
class Facet(BaseModel):
    endpoint: str
    case_id: str
    name: str
    status: Literal["PASS", "FAIL", "ERROR", "INCONCLUSIVE", "SKIP"]
    source_assertion_ids: list[str]
    evidence_hashes: list[str]
    reason: str

class GateSelector(BaseModel):
    family: str
    facet: str
    required: bool

class AcceptancePolicy(BaseModel):
    version: Literal[1]
    id: str
    mode: Literal["compatibility", "strict"]
    selectors: list[GateSelector]

class AcceptanceResult(BaseModel):
    version: Literal[1]
    source_manifest_hash: str
    policy_hash: str
    scorer_revision: str
    integrity: Literal["verified", "summary-only", "unavailable"]
    verdict: Literal["PASS", "FAIL", "INCONCLUSIVE"]
    exit_code: Literal[0, 1, 2]
    required_facets: list[Facet]
    diagnostic_facets: list[Facet]
    uncertified_capabilities: list[str]
    reasons: list[str]
```

Public functions introduced by the tasks:

```python
def load_policy(path: Path) -> AcceptancePolicy: ...
def derive_facets(manifest: Manifest, run: RunResult,
                  observations: dict[tuple[str, str], list[Observation]]) -> list[Facet]: ...
def assess_run(manifest: Manifest, run: RunResult,
               facets: list[Facet], policy: AcceptancePolicy,
               scorer_revision: str) -> AcceptanceResult: ...
def render_acceptance(result: AcceptanceResult, format: str) -> str: ...
```

`Observation` is the existing normalized protocol observation type from `capture.py`. `assess_run` is network-free. A summary without verified captures cannot manufacture a new semantic facet; it yields incomplete acceptance when that facet is required.

## Review focus

1. A malicious policy moves all useful checks to diagnostics: reject empty mandatory coverage, never return PASS on zero applicable gates. Task 1.
2. A broken endpoint sends 200 with arbitrary prose or 400 to everything: preserve mandatory core functional/validation controls. Tasks 1 and 5.
3. A code-fenced object has duplicate keys, wrong facts, appended prose, multiple objects, or excessive nesting: reject; no arbitrary extraction or repair. Task 2.
4. CLI exits 0 while the CI reporter renders diagnostic failures as acceptance failures, or vice versa: render all acceptance views from one result and preserve the raw report separately. Task 3.
5. A stale/wrong policy, unsupported variant, unverified summary, or missing required result is treated as a passing baseline: verify hashes, coverage, and evidence; return incomplete. Tasks 1, 3, and 5.

## Task 1 — Pure acceptance model and fail-closed policy

**Files:** create acceptance_records.py, acceptance.py, tests/test_acceptance.py.

**Consumes:** existing Manifest, RunResult, case identities, and assertion statuses. **Produces:** AcceptancePolicy, Facet, AcceptanceResult, load_policy, assess_run.

- [ ] Write failing tests for the following complete decision table, using small authored manifest/run fixtures. Test fixture helper `assessment_fixture` returns a valid matched manifest, run, facet list, and policy with one mandatory core response gate; each test changes only the named condition.

```python
@pytest.mark.parametrize("condition, expected", [
    ("all_required_pass", ("PASS", 0)),
    ("diagnostic_quality_fail", ("PASS", 0)),
    ("required_schema_fail", ("FAIL", 1)),
    ("required_wrong_tool_result", ("FAIL", 1)),
    ("required_measurement_unknown", ("INCONCLUSIVE", 2)),
    ("transport_error", ("INCONCLUSIVE", 2)),
    ("required_result_missing", ("INCONCLUSIVE", 2)),
    ("summary_only_new_facet", ("INCONCLUSIVE", 2)),
])
def test_acceptance_decision(condition, expected):
    m, r, facets, policy = assessment_fixture(condition)
    result = assess_run(m, r, facets, policy, "test-scorer")
    assert (result.verdict, result.exit_code) == expected
```

- [ ] Run `uv run pytest tests/test_acceptance.py -q`; confirm tests fail for missing behavior.
- [ ] Implement policy validation: known families/facets only; no duplicate selectors; nonempty mandatory protocol and functional controls; missing/unmapped selected assertions are errors, not silently ignored. Hash canonical policy content using the existing content-hash helper.
- [ ] Implement the decision order: validate source and coverage → incomplete/error means exit 2 → mandatory failed facet means exit 1 → all applicable required facets pass means exit 0. Diagnostics never change their recorded raw statuses.
- [ ] Test model/endpoint renaming invariance, reject empty gates/unknown selectors, and verify source results remain byte-equivalent before/after assessment.
- [ ] Rerun focused tests; commit with `git commit -s`.

## Task 2 — Correct measurement facets without changing historical scoring

**Files:** create acceptance_facets.py and tests/test_acceptance_facets.py; reuse json_utils.py, schema_cases.py, size_cases.py helpers without changing their legacy defaults.

**Consumes:** normalized Observation, case oracle, existing strict schema validator and strict JSON parser. **Produces:** derive_facets with explicit protocol, core function, retrieval, raw format, total-budget, visible-measurement, quality, and optional-capability facets.

- [ ] Add failing retrieval tests using the exact acceptance boundaries below. `retrieval_fixture` authors a large_input case with expected value `{"begin":"a","middle":"b","end":"c"}` and one completed observation; `facet_by_name` selects the named derived facet.

```python
@pytest.mark.parametrize("text, expected", [
    ('{"begin":"a","middle":"b","end":"c"}', "PASS"),
    ('```json\n{"begin":"a","middle":"b","end":"c"}\n```', "PASS"),
    ('Answer: {"begin":"a","middle":"b","end":"c"}', "FAIL"),
    ('{"begin":"wrong","middle":"b","end":"c"}', "FAIL"),
    ('{"begin":"a","begin":"wrong","middle":"b","end":"c"}', "FAIL"),
])
def test_plain_text_retrieval(text, expected):
    m, r, obs = retrieval_fixture(text)
    assert facet_by_name(derive_facets(m, r, obs), "retrieval").status == expected
```

- [ ] Add tests proving a fence still fails the raw-format facet and fails the JSON-mode facet if JSON/structured output was requested. Test empty/multiple fences, trailing prose, NaN, malformed JSON, bounds, missing history and wrong acknowledgments.
- [ ] Add total-budget tests: complete valid sequence + explicit length terminal + consistent output usage at/above 90% of selected cap passes; normal stop, missing total usage, negative/inconsistent/over-cap usage, malformed sequence and truncation without proper terminal do not. Missing reasoning detail leaves visible measurement INCONCLUSIVE while total-budget evidence remains usable.
- [ ] Run focused tests and confirm RED. Implement a single-whole-fence parser with strict inner JSON parsing and the existing size/depth bounds; it must never repair content. Implement independent total-budget and visible measurement facets.
- [ ] Derive advanced schema findings using the unchanged schema validator. A compatibility policy may mark the feature optional; strict assessment and an operator-required capability still fail invalid arguments. Syntactically malformed tool arguments always fail the structural facet.
- [ ] Add synthetic versions of all observed failure classes. Historical captured results remain unchanged; any new fixture/prompt gets a new content hash and version.
- [ ] Run facet and original size/schema/reliability tests; commit with sign-off.

## Task 3 — Explicit acceptance commands and consistent reports

**Files:** create acceptance_reports.py, tests/test_acceptance_cli.py; modify cli.py; add policy examples.

**Consumes:** load_policy, derive_facets, assess_run. **Produces:** verify/assess commands and three consistent acceptance artifacts.

```text
dpv verify --config configs/self-hosted-verify.example.toml \
  --endpoint candidate --policy policies/official-compatible-v1.json \
  --out runs/candidate-verify

dpv assess runs/candidate-verify \
  --policy policies/strict-contract-v1.json --out runs/candidate-strict
```

- [ ] Write failing CLI tests using the synthetic provider: a non-gating quality failure leaves canonical summary FAIL and raw JUnit failure intact, but produces acceptance PASS/exit 0; a wrong core tool result produces acceptance FAIL/exit 1 in all views; an interrupted stream produces incomplete/exit 2.
- [ ] Implement verify as the existing planned execution/canonical evidence path followed by a pure assessment. Implement assess using verified evidence loading and observation reassembly. Do not infer permission to call the network during assess.
- [ ] Write `acceptance.json`, `acceptance.md`, and `acceptance.junit.xml` only after all renderings succeed. Do not replace canonical summary/JUnit or load a stale acceptance sidecar as source truth.
- [ ] Terminal and Markdown must display policy ID/hash, required coverage, diagnostic failures, uncertified capabilities, source revision, and integrity. JUnit must label diagnostics as report-only without representing their raw verdict as PASS; store raw status and reason in properties/output.
- [ ] Test policy-hash mismatch, unavailable capture, partial run, wrong scorer, explicit capability requirement, and no required gates. Test that legacy CLI exit codes and historical rendering are unchanged.
- [ ] Run CLI/report/replay tests; commit with sign-off.

## Task 4 — Freeze a meaningful default core and preserve stress coverage

**Files:** create policies/official-compatible-v1.json, policies/strict-contract-v1.json, profiles/deepseek-verification-2026-09-22-v1.json, configs/self-hosted-verify.example.toml, tests/test_acceptance_inventory.py; add versioned fixtures only where requests change.

- [ ] Create the new verification profile by composing the existing core control rules and depth rules with explicit compact/full-stress case inventories. Preserve all old profiles; validate that the combined planner resolves every selected case without changing its request. Inventory every selected case/facet. Default mandatory coverage includes valid response envelopes, stream completion, functional lookup/arithmetic results, matched tool IDs/history, supported shallow structured output, and declared input retrieval/output-budget probes. A fake echo server or reject-everything server cannot pass.
- [ ] Keep exact-word repeatability and optional advanced schema dimensions in the workload as separately visible diagnostics. Freeze this category-based policy for both models; do not add exceptions keyed to their observed failed case IDs.
- [ ] Keep Beta Chat as an explicitly configured route/contract. Strict mode preserves the existing Pro nesting failures. If a candidate supplies valid arguments, record capability success without expecting the official malformed structure.
- [ ] Preserve v1 prompts as diagnostics. If a clearer default control prompt is needed, author a new version before calibration and keep it distinct from old evidence. Do not silently edit previously hashed prompt records.
- [ ] Add inventory tests for both protocols, modes and streams that the core claims; ensure unsupported combinations are declared and never counted as executed support. Verify every policy selector resolves and required coverage is nonempty on each endpoint.
- [ ] `dpv plan`/verify preflight must print separate mandatory/diagnostic counts, request/token ceilings, selected route, and policy hash. Commit the frozen policy before holdout verification.

## Task 5 — Offline falsification, live holdout, and installed release

**Files:** extend synthetic fixtures and scripts/check_release.py, pyproject.toml; update docs; add a sanitized dated calibration record after testing.

- [ ] Offline negative controls must fail acceptance: wrong required fact, wrong required tool result, missing tool association, malformed required schema, malformed/unfinished SSE, 400 to all controls, 500/429/401, and missing required observations. Parameterize both protocols and streaming where applicable.
- [ ] Replay all available verified official evidence without network traffic. This checks classification and detects code regressions; it is not independent proof that the newly frozen contract passes live. Keep the original six timeout errors unresolved in their historical records.
- [ ] Build wheel/sdist, install outside the checkout, and exercise verify/assess plus all three report formats. Bundle policies and new config, and test Python 3.11 and 3.14, matching the existing CI matrix. Run the complete offline suite once after implementation stabilizes.
- [ ] Preflight live runs with printed immutable workload manifests. Operational cap: at most **6,000 inference requests** across development calibration and final verification, serial execution, zero hidden retries. The output-token ceiling is the sum of the printed manifests and must be recorded before calls; reduce repeated stress workload rather than exceed the cap.
- [ ] Use development calibration to correct fixture or transport defects. Freeze prompts, classification and policies again after any change, then start independent verification from round one. Do not tune against a failed holdout and keep prior successful rounds as if the policy had not changed.
- [ ] Run both aliases with Chat and Responses for **three independent default-core/matrix rounds**. Use repeated new sessions and predetermined prompt/filler seeds. Extended 16-round/16-parallel workflows and the 1 MiB/16K size matrix run once after freezing, avoiding three repetitions of the most costly stress cases. Determine exact per-phase requests from the planner before execution.
- [ ] Require default acceptance PASS with zero mandatory failures and zero execution/incomplete errors in every verification round. Retain non-gating quality/capability observations, all first attempts and all failed rounds. If a mandatory check still fails, diagnose it; do not silently waive it to finish.
- [ ] Run at least the compact official verification through the installed wheel, with credentials only via authorized secret handling. The manual GitHub workflow remains a separate explicit action; do not provision repository secrets implicitly.
- [ ] Publish sanitized evidence-linked results and an example report showing compatibility PASS beside genuine diagnostic/strict failures. Update the recommended command and certification scope in README/docs. DCO-signed commits; independent review; a review-ready PR without merging.

## Definition of done

- [ ] Official aliases pass the frozen default policy in all three independent verification rounds and the selected full stress run.
- [ ] Controlled broken providers fail; better-than-official optional capabilities are not penalized.
- [ ] The strict report still exposes Pro's Beta schema discrepancy if reproduced; no raw finding is rewritten to PASS.
- [ ] Raw results and acceptance results are clearly distinguishable and reproducible offline.
- [ ] Full offline tests, installed-wheel verification, CI and DCO pass; no secrets/raw evidence committed.

## Self-review

The design requirements map to Tasks 1–5. All five review risks have explicit owning tests. No production code is changed by this plan. Proposed names and entry points are declared above; implementation must keep their signatures consistent. Live cost is bounded by manifests and the stated request ceiling, not an assumed token price or a guarantee of future provider behavior.
