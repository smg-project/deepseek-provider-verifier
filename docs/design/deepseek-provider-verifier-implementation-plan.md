# DeepSeek Provider Verifier Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans` to implement this plan task by task after the design is accepted. Use subagents only when separately authorized. Steps use checkbox syntax for tracking.

**Goal:** Build a standalone, evidence-producing verifier for DeepSeek deployments exposing Chat Completions and Responses APIs.

**Architecture:** A versioned contract and case manifest drive a bounded HTTP runner. Protocol adapters retain raw evidence and produce normalized observations, pure assertions score those observations, and reporters expose API, quality, and availability results separately.

**Tech Stack:** Python 3.11+, uv, HTTPX, Pydantic, jsonschema, pytest, and Ruff; standard-library argparse, TOML parsing, and XML reporting.

**Spec:** [DeepSeek Provider Verifier — proposed design](deepseek-provider-verifier-design.md).

**Status:** Tasks 1–5 implemented and reviewed through `351a0c2`; Task 6 integrates scoped official Flash calibration, CI, and release checks. The original repository baseline was `5c8154da3d7d1a6accc575785f186907b2e61254` (MIT license only). Candidate/direct-engine/SMG live comparison is explicitly deferred by operator choice. Final branch review and private PR are separate; no release publication or visibility change is authorized here.

## Global Constraints

- The proposed scope is DeepSeek first, with a reusable execution core.
- Chat Completions and Responses, each with streaming and non-streaming coverage.
- Deterministic local mock tools. The verifier never executes arbitrary model-generated commands.
- Retries default to zero.
- A successful HTTP response alone cannot upgrade a feature from accepted to behaviorally verified.
- A required endpoint feature returning unsupported behavior fails its requirement. Auto-discovery cannot turn it into a skip.
- No universal “98% passes” rule is assumed.
- Nothing is uploaded automatically.
- Python 3.11+; preserve the repository's existing MIT license and private visibility.
- Do not add Co-Authored-By trailers to commits.
- Default smoke runs have at most 40 outbound attempts per endpoint, at most 4 requests per conversation, concurrency 1, and a 300-second case deadline.
- Output-token ceilings are explicit in the selected preset: 512 for non-thinking smoke and 4096 for thinking smoke.

## Repository layout

```text
deepseek-provider-verifier/
  pyproject.toml
  uv.lock
  LICENSE
  README.md
  CONTRIBUTING.md
  .gitignore
  configs/providers.example.toml
  profiles/deepseek-api-2026-09-21.json
  cases/chat.jsonl
  cases/responses.jsonl
  cases/behavior.jsonl
  schemas/profile.schema.json
  schemas/case.schema.json
  schemas/manifest.schema.json
  schemas/result.schema.json
  src/deepseek_provider_verifier/
    __init__.py
    cli.py
    records.py
    config.py
    planner.py
    transport.py
    sse.py
    protocols/chat.py
    protocols/responses.py
    assertions.py
    mock_tools.py
    runner.py
    evidence.py
    comparison.py
    reports.py
  tests/
    test_planner.py
    test_transport.py
    test_sse.py
    test_protocols.py
    test_runner.py
    test_assertions.py
    test_evidence.py
    test_comparison.py
    test_cli_reports.py
    fixtures/chat/
    fixtures/responses/
  docs/
    contract-sources.md
    case-catalog.md
    metrics.md
    reproducibility.md
  .github/workflows/test.yml
  .github/workflows/live.yml
```

Keep production modules small and organized around the boundaries above. No plugin-discovery framework, database, web server, SMG runtime dependency, or automatic model downloader is needed.

## Shared records and interfaces

Use Pydantic records for input validation and stable JSON serialization. Export JSON Schemas from those same records; do not maintain two independent validation implementations. Each serialized record includes `schema_version: 1`. Store additive unknown server fields in raw evidence, not as unknown fields in project configuration.

| Record | Required fields |
| --- | --- |
| `Endpoint` | `name`, `base_url`, `model`, `model_release`, `api_key_env` or explicit `auth_none` |
| `Rule` | `id`, `protocol`, `conditions`, `expectation`, `assertion_id`, `source_url`, `source_section`, `retrieved_at`, `evidence_status`, `gating` |
| `CaseTemplate` | `id`, `protocol`, `modes`, `streams`, `rule_ids`, `steps`, `required`, `max_requests`, mode-specific `max_output_tokens`, `oracle` |
| `Case` | `id`, `protocol`, `template_id`, `mode`, `stream`, `rule_ids`, `steps`, `required`, `max_requests`, `max_output_tokens`, `oracle` |
| `Manifest` | `run_id`, `created_at`, `profile_hash`, `dataset_hash`, `scorer_revision`, `endpoints` without secrets, expanded `cases`, `budgets`, `request_ceiling`, `output_token_ceiling`, `gates`, `manifest_hash` |
| `Attempt` | `case_id`, `endpoint`, `step`, `repetition`, `attempt_number`, `request_hash`, redacted `request`, `status_code`, `timings`, `response`, `events`, `error`, `evidence_hash` |
| `CaseResult` | `case_id`, `endpoint`, `status`, `assertions`, `metric_observations`, `attempt_refs`, `reason` |
| `RunResult` | `manifest_hash`, `complete`, `case_results`, `counts`, `budget_usage`, `enabled_gates`, `exit_code` |

Timestamps and run IDs do not affect the comparable workload hash. Credentials never enter any serialized record. Keep protocol-specific response detail in tagged observations. A normalized text string alone is insufficient to assess streaming or tools.

## Task 1: Contract profile and a network-free planner

**Files:** `pyproject.toml`, `records.py`, `config.py`, `planner.py`, `profiles/deepseek-api-2026-09-21.json`, `schemas/*`, `configs/providers.example.toml`, `docs/contract-sources.md`, `tests/test_planner.py`.

**Consumes:** The proposed design, official source URLs, operator endpoint configuration, and explicit case records.

**Produces:** `load_config(path: Path) -> Config`; `load_profile(path: Path) -> Profile`; `build_manifest(config: Config, profile: Profile, cases: list[CaseTemplate]) -> Manifest`. `Config` contains `run` settings and named `Endpoint` records; `Profile` contains an ID, rules, model applicability, and preset definitions. No function in this task creates an HTTP client. The planner resolves each `CaseTemplate` into concrete `Case` records.

- [x] Create the Python package with the declared version floor and dependencies, plus a locked development environment.
- [x] Capture official contract sources with retrieval dates and provenance. The Chat reference capture and hash are available; unresolved assertions remain diagnostic. Record each conflict as a rule-level entry, not a comment buried in a test.
- [x] Define the shared records above and reject unknown configuration keys, duplicate case IDs, missing rule references, invalid protocol names, negative budgets, and conflicting authentication settings.
- [x] Add planner regression tests before implementing expansion. A cases/profile/config fixture is JSON/TOML loaded through the public record constructors; fixtures contain a single text case with two modes and two streaming variants.

```python
def test_planner_counts_every_variant(config, profile, one_text_template):
    manifest = build_manifest(config, profile, [one_text_template])
    assert len(manifest.cases) == 4
    assert manifest.request_ceiling == 4 * len(config.endpoints)
    assert manifest.output_token_ceiling == (2 * 512 + 2 * 4096) * len(config.endpoints)

def test_planner_rejects_budget_overflow(config, profile, oversized_cases):
    with pytest.raises(ValueError, match="request budget"):
        build_manifest(config, profile, oversized_cases)
```

- [x] Implement expansion as a pure transformation: expand declared variants, reject duplicate IDs, sum maximum conversation requests and allowed retries, check each endpoint and aggregate limits, then compute stable hashes. For a multi-step case, count every possible step even when a live run may terminate earlier.
- [x] Encode the design's exact smoke preset: at most 17 requests per protocol, 34 per endpoint for both protocols with no retries. C02/C17/C20 attach assertions to existing smoke attempts. Test these ceilings; the full matrix must fail under the smoke budget until a larger budget is explicitly configured.
- [x] Prove planning does not access the network by patching `socket.create_connection` to raise in the planner test process. Verify the plan output contains the API-key environment variable name, never its value.
- [x] Run `uv run pytest tests/test_planner.py -q`, then commit `feat: add versioned profiles and verification planner`.

**Acceptance:** A user can inspect precisely which checks and maximum attempts will run. An over-budget matrix fails before making a request. Unknown or unresolved requirements cannot silently become satisfied requirements.

## Task 2: Raw HTTP, SSE decoding, and protocol assembly

**Files:** `transport.py`, `sse.py`, `protocols/chat.py`, `protocols/responses.py`, `tests/test_transport.py`, `tests/test_sse.py`, `tests/test_protocols.py`, `tests/fixtures/{chat,responses}/*`.

**Consumes:** An `Endpoint`, a fully rendered request body, timeout/budget settings, and protocol name.

**Produces:** `send_request(client: httpx.AsyncClient, endpoint: Endpoint, path: str, payload: dict, secret: str | None) -> AttemptPayload`; `decode_sse(chunks: Iterable[bytes]) -> list[SSEEvent]`; `assemble_chat(events: list[SSEEvent]) -> Observation`; `assemble_responses(events: list[SSEEvent]) -> Observation`.

`AttemptPayload` holds HTTP status, response headers after redaction, timestamped byte chunks, decoded JSON when applicable, and transport errors. `SSEEvent` holds `event`, `data`, and source positions. `Observation` holds text/reasoning segments, tools keyed by identity, usage, terminal state, and structural violations. Implementations retain access to source evidence for each violation.

- [x] Write independent valid and faulty stream fixtures. Include a Unicode character split across byte chunks and two tool-call argument streams interleaved by index/ID. Expected decoded events and arguments are hand-authored, not generated by the implementation under test.

```python
def test_sse_preserves_fragmented_utf8():
    events = decode_sse([b'data: {"text":"\xe4', b'\xbd\xa0"}\r', b'\n\r\n'])
    assert events[0].data == '{"text":"你"}'

def test_eof_does_not_invent_success(chat_without_terminal):
    observed = assemble_chat(decode_sse(chat_without_terminal))
    assert observed.terminal_state == "missing"
    assert "MISSING_TERMINAL" in observed.violations
```

- [x] Implement incremental UTF-8 decoding and SSE framing, including CRLF, comments, multi-line data, and delimiters split across reads. Preserve an incomplete final frame as an error; do not append an invented terminator.
- [x] Implement protocol-specific assembly. Chat tool deltas join by tool index; Responses items join by item/output identity. Track terminal state, missing/duplicate identities, argument completion, and usage separately. Do not repair invalid JSON or relabel a failed terminal event.
- [x] Implement the HTTP layer with explicit timeouts, no automatic SDK retries, and exact path joining. Use one async client per endpoint/run. Capture first-event and first-meaningful-output timing with distinct definitions.
- [x] Inject 401, 429, 500, timeout, and partial-stream responses through HTTPX test transports. Assert no retry occurs when retries are zero, no credentials appear in persisted payloads, and headers/status survive normalization.
- [x] Run `uv run pytest tests/test_transport.py tests/test_sse.py tests/test_protocols.py -q`, then commit `feat: capture and validate chat and responses streams`.

**Acceptance:** Deliberately malformed streams are detected. Chunk boundaries do not change a valid result, and normalization does not erase transport or schema defects.

## Task 3: Case catalog, bounded conversations, and evidence

**Files:** `cases/*.jsonl`, `assertions.py`, `mock_tools.py`, `runner.py`, `evidence.py`, `tests/test_assertions.py`, `tests/test_runner.py`, `tests/test_evidence.py`, `docs/case-catalog.md`.

**Consumes:** `Manifest`, `Rule`, transport observations, and protocol adapters.

**Produces:** `evaluate_case(case: Case, observations: list[Observation], rules: list[Rule]) -> CaseResult`; `execute_manifest(manifest: Manifest, clients: dict[str, httpx.AsyncClient], secrets: dict[str, str]) -> RunResult`; `append_record(path: Path, record: dict) -> str`; `load_resume_state(path: Path, manifest_hash: str) -> ResumeState`.

`ResumeState` contains completed case IDs, prior attempts, incomplete records, and the validated manifest hash. Assertions return structured IDs and reasons, not only booleans. Mock tools expose `lookup_fixture(key: str) -> str` and `add_integers(a: int, b: int) -> int`, with fixed fixture values and strict argument validation.

- [x] Author templates C01–C24 from the design. Each case names its official rule or project policy, mode, applicability, bounded number of steps, and observable oracle. Exclude unverified rules from release gates while retaining them as visible diagnostics.
- [x] Author original behavioral prompts for tool-required, tool-forbidden, ambiguous, schema, and follow-up tasks. Record dataset version, content hash, intended answer, and licensing. Do not copy the Kimi/MiniMax corpus as an implicit shortcut.
- [x] Write failure-injection tests for lost reasoning history, mismatched call IDs, malformed arguments, ignored tool prohibition, and a required protocol returning 404. Test positive fixtures as well as negative ones.

```python
def test_required_missing_protocol_fails(required_responses_case, http_404_observation, rules):
    result = evaluate_case(required_responses_case, [http_404_observation], rules)
    assert result.status == "FAIL"

def test_malformed_arguments_are_not_repaired(tool_case, broken_json_observation, rules):
    result = evaluate_case(tool_case, [broken_json_observation], rules)
    assert result.status == "FAIL"
    assert any(a.id == "TOOL_ARGUMENTS_INVALID_JSON" for a in result.assertions)
```

- [x] Implement case execution as an explicit step loop. Before each outbound attempt, debit the attempt budget; before a follow-up, check the conversation-step and case-deadline limits. Retain original returned assistant items needed for replay. Only registered mock tools may be executed.
- [x] Make retry behavior explicit in records. A retry receives a new attempt number and consumes budget. Do not retry assertion failures; retain first-attempt metrics when transient HTTP retry policy is enabled.
- [x] Persist append-only JSONL attempts/results and hashes. Flush completed records before marking a case complete; use atomic replacement for manifests/summaries. Detect incomplete final lines on resume and record their disposition rather than quietly dropping them.
- [x] Add cancellation/budget tests proving queued attempts stop and the run becomes incomplete. Add a no-progress conversation fixture that must stop after four requests.
- [x] Add a resume test that rejects different profile/dataset hashes and does not repeat completed cases. Add sentinel-secret tests across every stored output, including error paths and URLs.
- [x] Run `uv run pytest tests/test_assertions.py tests/test_runner.py tests/test_evidence.py -q`, then commit `feat: add bounded verification cases and reproducible evidence`.

**Acceptance:** A candidate run yields a useful contract report. Missing support, exhausted budgets, and malformed output remain distinguishable; none becomes a passing subset.

## Task 4: Reference comparisons and uncertainty

**Files:** `comparison.py`, `tests/test_comparison.py`, `docs/metrics.md`, `docs/reproducibility.md`.

**Consumes:** Two manifests, `CaseResult` records, prompt/repetition identities, and an optional explicit quality-gate policy.

**Produces:** `compare_runs(reference: RunResult, candidate: RunResult, manifests: tuple[Manifest, Manifest], policy: ComparisonPolicy | None) -> ComparisonResult`.

`ComparisonPolicy` defines selected metrics, per-metric allowed drop, minimum distinct prompts, minimum repetitions, confidence level, and bootstrap seed. `ComparisonResult` records comparability, field-level manifest differences, per-category metrics and denominators, intervals, optional gate outcomes, and refusal/inconclusive reasons. With `policy=None`, quality comparison is descriptive only.

- [x] Define exact metric denominators in `docs/metrics.md`: end-to-end success over all planned case/repetition trials, first-attempt HTTP 2xx rate over actual logical requests, conditional schema accuracy over emitted tool calls, and tool-trigger confusion matrix over cases with an explicit trigger oracle. Zero denominators produce unavailable values, never 100%.
- [x] Add manifest mismatch tests for model release, profile, dataset, thinking mode, output limit, and scorer revision. Endpoint/model label differences pass only when covered by declared addressing/mapping rules. Unknown checkpoint identity permits exploratory reporting but disables equivalence gating.

```python
def test_unknown_checkpoint_cannot_receive_quality_pass(unknown_release_pair, strict_policy):
    comparison = compare_runs(*unknown_release_pair, policy=strict_policy)
    assert comparison.quality_gate == "INCONCLUSIVE"

def test_empty_metric_is_not_perfect(no_tool_calls_pair):
    comparison = compare_runs(*no_tool_calls_pair, policy=None)
    assert comparison.metrics["schema_accuracy"].value is None
    assert comparison.metrics["schema_accuracy"].denominator == 0
```

In these fixtures, `*_pair` is a tuple of reference run, candidate run, and the two manifests, matching the public signature.

- [x] Compute first-attempt and eventual metrics separately and retain provider errors in end-to-end results. A conditional quality chart must also display the number of unavailable/unscored responses.
- [x] Implement prompt-cluster bootstrap intervals for repeated behavioral observations. Resample independent prompt IDs with replacement, retaining each prompt's repetitions; compute candidate-minus-reference differences on paired prompt clusters. Report the bootstrap seed and confidence level.
- [x] Implement an optional non-inferiority verdict: with enough evidence, PASS only when the lower confidence bound is above the negative allowed-drop margin; FAIL when the upper bound is below it; otherwise INCONCLUSIVE. Margins and sample floors are explicit operator policy. Test threshold boundary equality as inconclusive, not pass.
- [x] Add synthetic tests for a clear regression, a narrow acceptable difference, wide uncertainty, correlated repetitions, and unequal missingness. Use fixed seeds and cases with analytically obvious ordering, not exact snapshots of every random quantile.
- [x] Run `uv run pytest tests/test_comparison.py -q`, then commit `feat: compare deployment behavior with explicit uncertainty`.

**Acceptance:** The comparison cannot certify mismatched releases or manufacture confidence from repeated copies of one prompt. Descriptive reporting works without a quality acceptance policy.

## Task 5: CLI and consistent reports

**Files:** `cli.py`, `reports.py`, `tests/test_cli_reports.py`, `README.md`, `CONTRIBUTING.md`, `.gitignore`, `pyproject.toml` CLI entry point.

**Consumes:** Config, planner, runner, comparison, and versioned result records.

**Produces:** `dpv plan`, `dpv run`, `dpv compare`, and `dpv report`; `render_report(result: RunResult | ComparisonResult, format: str) -> str`; `exit_status(result: RunResult) -> int`.

- [x] Add argparse command interfaces matching the design. `plan` never sends traffic. `run` requires a named configured endpoint; `compare` reads stored runs; `report` only renders stored evidence.
- [x] Add black-box CLI tests using subprocesses and local fixture servers. Verify `--help`, missing config, invalid protocol, absent credentials, request budgets, interruption, and output directory behavior. Do not put literal credentials in command-line arguments.
- [x] Implement `summary.json` as the authoritative aggregate and render Markdown/JUnit from it. Every summary displays endpoint/model/profile/dataset, actual case counts, all status categories, gates enabled, dates, and links to per-case evidence.
- [x] Implement exit precedence: incomplete/configuration/error/inconclusive required gate gives 2; otherwise required failures give 1; otherwise 0. Optional report-only quality observations do not imply a quality PASS.

```python
def test_incomplete_run_cannot_exit_success(incomplete_run):
    assert exit_status(incomplete_run) == 2

def test_reports_keep_skipped_and_failed_counts(mixed_run):
    markdown = render_report(mixed_run, "markdown")
    assert "SKIP" in markdown
    assert "FAIL" in markdown
    assert "Required gates" in markdown
```

- [x] Escape server-controlled text in Markdown and XML. Link optional raw evidence without embedding every prompt or reasoning trace in default summaries. Verify Unicode and XML control-character handling with fixtures.
- [x] Write README quickstart with an offline fixture example and explicit live setup. Include the community ownership statement, cost/budget behavior, source/provenance policy, and boundaries of verification claims. Describe fresh reference capture and the consequences of mutable model aliases.
- [x] Run `uv run pytest tests/test_cli_reports.py -q`, then commit `feat: expose verifier CLI and auditable reports`.

**Acceptance:** A user can plan, run one endpoint, compare two stored runs, and render reports without editing Python. All formats agree on counts and verdicts.

## Task 6: CI, live calibration, and release readiness

**Files:** `.github/workflows/test.yml`, `.github/workflows/live.yml`, `docs/contract-sources.md`, `docs/reproducibility.md`, `README.md`, and profile rules promoted after calibration.

**Consumes:** Completed CLI, fault fixtures, accepted design, and live endpoints/credentials supplied for execution.

**Produces:** A tested repository, an evidence-backed coverage statement, and a v0.1 release candidate in the existing private repository. Public package publication and repository visibility changes remain separate decisions; do not make the repository public as part of implementation.

- [x] Configure offline CI on Python 3.11 and the current supported stable Python chosen at implementation time. Lock dependencies, run Ruff and pytest, build wheel/sdist, and install the wheel in a fresh environment for `dpv --help` and an offline fixture run.
- [x] Configure a manually triggered live workflow with endpoint/profile inputs, a small fixed budget, protected credentials, and sanitized artifacts. Pull-request workflows must not use live provider credentials. No automatic scheduled paid runs in v0.1.
- [x] Run targeted official calibration for each prospective gating rule in both protocols. Record exact model alias, response metadata, source capture, settings, date, and observed result. If official behavior contradicts a source, preserve both and keep the rule diagnostic or issue a reviewed profile revision.
- [ ] Run the same required capability subset against a candidate deployment. If both routes are available, compare direct engine and SMG paths under matched conditions to localize gateway-only differences. A missing Responses implementation remains a declared coverage gap or required-feature failure, depending on the selected manifest. **Deferred by operator choice; not a blocker for official-only scope.**
- [x] Execute all seeded-fault fixtures and verify they trigger their intended assertion IDs. A test suite that accepts the valid fixture but misses a seeded corruption does not meet release acceptance.
- [x] Inspect generated evidence for secrets and misleading PASS/coverage claims. Confirm every referenced case and assertion exists and every released gate has documented provenance.
- [x] Run `uv run ruff check .`, `uv run pytest -q`, and `uv build`. After any live-driven rule change, rerun its affected offline tests and full required checks once.
- [x] Commit `ci: validate verifier contracts and release artifacts`. Publish only the tested combinations in the initial results table. Keep unsupported/unexercised combinations explicit.

**Acceptance:** A clean installation works; offline tests include both compliant and corrupted endpoints; actual live evidence supports the advertised subset. No claim is made for unavailable model/protocol combinations.

## Review checkpoints and implementation order

Review after Task 1 to validate contract ownership and scope; after Task 3 to assess the first working endpoint report; and after Task 6 for release readiness. Each task remains independently reviewable, and none requires unrelated SMG changes.

The critical dependency chain is contracts → transport/assembly → cases/runner → comparison → CLI/report integration → live calibration. Reporting record design can be refined early, but no need to start parallel development before these interfaces are agreed.

## Planning self-review

- Both requested API surfaces are covered, with distinct protocol assembly and rules.
- Gateway extensions have a separate scope from native DeepSeek fidelity.
- No universal quality threshold, model identity claim, or hidden retry is assumed.
- The case catalog includes positive behavior, negative requests, conversation replay, and streaming failures.
- Missing capabilities and incomplete runs cannot silently pass.
- Budget and secret handling appear in planner, runner, evidence, and CI tasks.
- The comparison includes model/settings compatibility and uncertainty, not exact-text matching.
- Release criteria require live calibration and seeded-failure detection.
- The repository name, MIT license, and private visibility match the user's existing repository. CLI/distribution naming and publication remain proposals.
