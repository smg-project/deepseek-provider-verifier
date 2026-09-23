# Official-baseline compatibility acceptance — proposed design

Status: accepted for execution by the user; live calls follow the authorized bounded plan.

## Objective

The default self-hosted verifier acceptance should pass a healthy official DeepSeek deployment under the same published policy used for a candidate. Passing certifies the selected compatibility contract. It does not certify perfect instruction following, every optional schema feature, model-weight identity, or maximum context capacity.

The requested success criterion is official API acceptance. The design assumption is that this means a clearly named default compatibility verdict, rather than rewriting every underlying observation to PASS. Strict document conformance remains separately selectable and can reveal official defects.

## Current evidence and implementation

The inspected checkout is `c46ddb9c0430d22d9e3dfc7fe1646afede699c23`. Its live calibration contains 2,068 requests across `deepseek-flash`, `deepseek-v4-pro`, Chat, and Responses. Remaining observations include four instruction failures, eight standard-route nested-schema failures, two Pro Beta strict-schema failures, and 27 fenced-JSON formatting failures with correct retrieved facts. Tool-budget and timeout defects were fixed and confirmed. Chat visible-output token measurements remain unknown when reasoning usage is absent.

`reports.exit_status()` currently gates on whole required-case statuses. `reports._run_junit()` also renders whole-case failures. Changing only an assertion's `gating` flag therefore cannot reliably implement an observational quality check. Existing canonical records and stored evidence validation also depend on the old exit behavior. This plan adds a separate, explicit acceptance result instead of silently changing those historical meanings.

## Alternatives

1. **Recommended: a compatibility acceptance result plus preserved diagnostics.** Correct mismatched measurement goals, keep a mandatory functional/protocol core, and show quality, advanced capabilities, and strict-document findings separately. Both provider types use identical rules.
2. **Make all existing strict cases green.** Requires fixing behavior inside the official service or weakening/removing observations. The repository cannot promise this outcome.
3. **Whitelist the official hostname or automatically accept observed failures.** Would allow false positives and encourage a candidate to reproduce official defects. Excluded.

## Behavior

The recommended command becomes `dpv verify`: execute a planned suite, preserve the existing canonical run bundle, and produce `acceptance.json`, `acceptance.md`, and `acceptance.junit.xml`. `dpv assess` performs the same acceptance calculation offline from a verified run bundle. Existing `dpv run`, `dpv report`, and historical JUnit/JSON semantics remain available.

The policy name is explicit in the plan, terminal result, acceptance files, and CI configuration. Compatibility assessment is the recommended default for new self-hosted acceptance workflows; strict-document assessment is an explicit option. Existing configs do not silently acquire different exit codes.

| Dimension | Compatibility acceptance | Strict-document / diagnostic result |
| --- | --- | --- |
| Response envelope, SSE framing/completion, tool IDs, history association | Mandatory | Mandatory |
| Core functional controls: actual tool execution, correct results, shallow schemas | Mandatory | Mandatory |
| Selected request fails authentication, billing, rate limiting, server/transport, or byte/deadline limits | Incomplete/error, never PASS | Error |
| Optional documented validation rejection | Only allowed for explicitly declared boundary/capability probes | Retain observed rejection and feature-not-certified state |
| Repeated exact-word instruction performance | Report failure count/rate, not a transport-compatibility gate | Original task failures retained |
| Large-input fact retrieval | Require all exact facts and complete valid history | Plain-JSON formatting scored separately |
| Advanced nesting, optional fields, schema unions/references | Explicit optional-capability observations; never imply support on invalid output | Strict schema mismatch fails |
| Output budget exhausted | Valid generated sequence, consistent provider-reported total output usage near selected cap, explicit limit terminal | Visible-token measurement remains unknown if its breakdown is absent |

A passing default report must state which advanced capabilities were not certified. A candidate that produces valid deep schemas receives a better capability result; it is never required to reproduce an official failure. If an operator explicitly requires a capability such as strict nested schemas, its failure becomes blocking for either provider.

## Specific treatment of observed failures

### Fenced JSON in size tests

The retrieval facet accepts either a complete strict JSON object or exactly one complete outer Markdown code fence containing that object, with only whitespace outside. Use the existing strict JSON loader, enforce local size/depth bounds, reject duplicate keys and nonfinite values, and compare the complete object with the expected facts. Arbitrary prose extraction, merging objects, repairing brackets, coercing values, and accepting wrong facts are prohibited.

This normalization applies only to plain-text retrieval tasks. When the request explicitly enables JSON mode or structured output, raw valid JSON remains mandatory. [Chat JSON-output specification](https://api-docs.deepseek.com/api/create-chat-completion/). A separate format facet retains the existing failure for Markdown. Original evidence and case results are unchanged.

### Repeatability

Preserve all observed failures and first-attempt outcomes. Introduce clearer prompts only as a versioned dataset and retain the old prompts as regression diagnostics. Do not lower-case all answers, accept unrelated responses, use hidden retries, or choose a failure-rate threshold after observing the verification set. Keep meaningful mandatory functional controls so a well-formed but useless endpoint cannot pass.

### Nested schemas

The existing schema-validity oracle remains unchanged. The default contract certifies a declared core subset; advanced nesting is separately measured. Pro's Beta discrepancy remains a strict failure and an explicit capability limitation. No model-name or hostname exception changes its result. Advanced schema rejection is not feature support. Strict-mode routing is explicit: official Chat probes use `/beta`; no Beta Responses support is inferred. [Official strict-tools guide](https://api-docs.deepseek.com/guides/tool_calls/)

### Output limits

Separate reaching the provider-reported total generated-token budget from measuring visible output tokens. The total-budget facet still requires consistent integer usage, valid ordered output, and the appropriate terminal. Missing reasoning detail does not erase available total-token evidence and is never filled with zero. Missing or contradictory total usage cannot pass this facet. No selected 16K limit is presented as the model's maximum.

## Integrity and scope

- Policies are immutable, versioned, validated, and content-hashed. Assessment records bind the policy hash, source manifest hash, scorer revision, and evidence integrity.
- Policy validation rejects an empty mandatory gate set, unknown selectors, missing required coverage, and attempts to waive fundamental protocol or execution failures.
- New default acceptance does not read the source `exit_code` as its only input and does not rewrite it. The raw report and acceptance report have distinct names and scopes.
- Strict mode continues to fail the captured Pro Beta schema violations.
- No baseline is calibrated on its own final verification set. Freeze policy, prompts, and gate membership before independent live verification.
- Historical raw evidence remains local and ignored. Commit only authored fixtures and sanitized calibration summaries.
- Existing quality comparison retains its model-identity and sample-size limitations. Compatibility PASS is not statistical quality equivalence.

## Release acceptance

1. The full offline suite, regression replay, installed-wheel checks, and CI pass.
2. A frozen default compatibility policy passes three consecutive independent official core/matrix runs on both aliases and both APIs. Preserve every failed run; no retry-until-green selection.
3. Run the costly full long-workflow and large-input/output coverage once after the same policy is frozen, under a printed request/token budget.
4. Controlled broken endpoints still fail for malformed output envelopes, interrupted streams, wrong tool results/IDs, incorrect facts, invalid required schemas, and reject-everything behavior.
5. Strict assessment still detects the known schema and formatting findings; compatibility output visibly reports uncertified capabilities and quality failures.
6. Acceptance JSON, Markdown, JUnit, and command exit code agree. An incomplete or unmeasured required facet never produces exit 0.

A future official regression can legitimately fail these gates. The release goal is demonstrated acceptance of a frozen, meaningful contract, not an unconditional promise that every future official response will pass.

## References

- [Official live depth report](https://github.com/smg-project/deepseek-provider-verifier/blob/feat/reliability-depth-suites/docs/calibration/official-depth-2026-09-22.md)
- [Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)
- [Responses](https://api-docs.deepseek.com/api/create-response/)
- [Strict tools and Beta routing](https://api-docs.deepseek.com/guides/tool_calls/)
