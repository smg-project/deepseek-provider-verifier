# Reliability and depth suites — proposed design

Date: 2026-09-22
Status: Written design for review; product implementation has not started.
Base: compatibility PR #3, commit 27f8f1c76cfdfc31ab2e3b6235747ae56e2b4736.

## Intended outcome

The user selected priorities 1, 2, 3, and 5: repeatability, long conversations and
tools, schema coverage, and large inputs/outputs. The repository serves operators
testing self-hosted DeepSeek-compatible endpoints. Success means reproducible
workloads, verifiable task results, explicit failure denominators, and evidence
that distinguishes unsupported features, functional failures, execution errors,
and unmeasured limits.

Implement these as four independently selectable suites, in that order, sharing
the existing planner, runner, protocol assemblers, evidence journals, and reports.
Use one feature branch with separately reviewable commits. Do not enlarge the
existing smoke, compatibility, or historical calibration presets.

## Constraints carried forward

- Work under `~/workspace`; preserve MIT licensing and private repository visibility.
- No Co-Authored-By trailers. Sign commits with the existing contributor identity.
- Write failing tests before runtime changes. Keep synthetic tests offline.
- Do not commit credentials, raw provider output, reasoning traces, or live journals.
- Preserve the old profiles, datasets, manifest hashes, and replayed verdicts.
- Python 3.11 and 3.14 CI must pass; avoid new mandatory runtime dependencies.
- New fixtures are project-policy tests, not newly observed official calibration.
- Live recovery/load, parameter effects, caching, vision, and files are outside this change.
- Develop and verify offline first. Larger live workloads require a reviewed plan
  showing explicit resource ceilings; no live traffic is part of design preparation.

## Approach and alternatives

Recommended: extend the current planner and runner with versioned opt-in catalogs,
small focused evaluators, and derived reliability reports. Existing support for
repetitions, prompt identities, registered mock tools, branching replay, JSON
Schema validation, and prompt-cluster comparisons supplies the foundation.

A single expanded default matrix would obscure cost and combine unrelated failure
rates. A separate benchmark framework would duplicate authentication, capture,
resume, and integrity validation. Neither is needed for this scope.

## 1. Repeatability

### Workloads

Author five genuinely distinct fixtures for each of four behavior families:
exact instruction following, required fixture lookup, arithmetic tool continuation,
and structured JSON output. Vary task data as well as wording. Keep stable family
and prompt IDs, dataset versions, exact intended outputs, and content hashes.
Random fixture generation is unnecessary; any generated data uses a recorded seed.

The starter configuration uses five repetitions per prompt, non-thinking,
nonstream, on both selected protocols: 200 trials per endpoint. The arithmetic
continuations need two requests, yielding a 250-request ceiling per endpoint.
A separate expanded configuration enables thinking and streaming as well:
800 trials and a 1,000-request ceiling per endpoint. These are opt-in choices,
not an automatic escalation. Defaults use zero retries and serial execution.

Chat structured-output fixtures use its existing JSON-object contract; Responses
uses typed JSON Schema fixtures. Reports identify the distinction. Repetitions
retain their current stable `.rN` identities and remain resumable individually.

### Reporting

Produce one derived row per endpoint/model, behavior family, protocol, mode, and
stream setting, with per-prompt rows underneath. Record planned, started, completed,
PASS, FAIL, ERROR, INCONCLUSIVE, and SKIP counts; distinct prompts and repetitions;
first-attempt and eventual results; and actual request/retry counts.

Keep denominators explicit:

- Conditional contract failure rate: FAIL / (PASS + FAIL); zero denominator is null.
- End-to-end success: retain the existing planned-trial denominator and unavailable
  accounting, including execution errors and unstarted work.
- HTTP availability: retain actual logical-request denominators, independently of
  whether an expected HTTP rejection passed its verification oracle.
- Within-prompt instability: show prompts containing both observed successes and
  failures, separately from prompts affected only by execution errors.

Provide 95% Wilson intervals for per-prompt repeated binary outcomes, labeled with
sample sizes and the independent-trials assumption. Repeats are not new distinct
prompts. Cross-endpoint uncertainty continues to use the existing seeded
prompt-cluster bootstrap, with its minimum prompt/repetition requirements. Do not
add a new automatic quality gate or imply that five repeats establish production
reliability. Keep unmeasured outcomes outside conditional confidence calculations
and visible in their own counts.

Generate reliability summaries from validated manifests and results, not from
parsing case names or raw model output. Add a versioned, derived
`reliability.json` sidecar and a Markdown section without changing old journal
records or canonical manifest serialization. Loading older runs remains supported.

## 2. Long conversations and tools

Add explicit chain depths of 4, 8, and 16 completed tool rounds, each followed by a
final answer. Add parallel-call widths of 4 and 16, a shared-prefix workflow with
two deterministic branches, and multi-turn fact updates/recall. Exercise Chat and
Responses, thinking and non-thinking, and streamed and nonstreamed replies.

Keep the only executable tools in the deterministic local registry. Authored
step expectations specify tool name, exact arguments, the expected tool result,
and final answer. A chain step must consume the previous result; merely producing
the final number does not demonstrate a completed chain. Parallel calls must have
distinct IDs and the complete expected argument multiset; call order is irrelevant.

Branch continuations must originate from the declared earlier step, preserve that
branch's complete history/reasoning, and exclude output from its sibling. Validate
history against the actual parent edge, not simply the previous recorded response.
Check continuation settings as well as messages when branching.

Use a separate workflow evaluator so the current one-step `expected_arguments`
contract and adjacent-history checks keep their historical meaning. Missing or
extra calls, wrong IDs, stale arguments, missing results, mutated reasoning, early
final answers, or exhausted bounds cannot pass. Each workflow has an explicit
maximum request count and deadline; a malformed model response cannot grow the
workflow indefinitely.

Provide small and full depth presets. The small preset selects depth 4, width 4,
and the branch/recall controls. The full preset additionally selects depths 8/16
and width 16. The planner calculates and displays every request and output-token
ceiling before execution; existing four-request conversation caps remain unchanged.

## 3. Schema coverage

Create a matrix covering required/optional properties, explicit null, scalar enums
and const, arrays with item/count constraints, numeric/string boundaries, nested
objects at depths 2/4/8, anyOf/oneOf unions, and local `$defs`/`$ref` references.
Pair strict=true with strict omitted on Responses. Exercise tool argument schemas
on both protocols and final-output schemas where the protocol exposes them.

For each fixture, store its schema and explicit allowed answer/arguments. Validate
schemas themselves before planning. Runtime validation allows only local schema
references; remote retrieval is prohibited. Limit nesting and validator work so
schemas cannot trigger unbounded recursion or resource consumption.

Required baseline fixtures must be accepted and correct. Advanced subset probes
report ACCEPTED_VALID, ACCEPTED_INVALID, REJECTED, or ERROR at the capability level.
Unsupported advanced keywords are explicit observations under the self-hosted
policy, while invalid output from an accepted request is a functional failure.
Strict official parity is enabled only for cases with recorded official evidence;
new schema behavior is not assigned an invented official expectation.

Offline tests include invalid outputs at each boundary and malformed schema
controls, including missing required keys, wrong union branches, null/type
confusion, and unresolved references. Live valid-output examples establish only
that those examples satisfied the schema, not that constrained decoding enforced
all of its semantics.

## 5. Large inputs and outputs

### Inputs

Use reproducible text sizes of 16 KiB, 64 KiB, 256 KiB, and 1 MiB. Place distinct
retrieval facts near the beginning, middle, and end, with a deterministic expected
answer that requires all three. A separate long-history variant distributes facts
across turns. Record literal UTF-8 size, payload hash, provider-reported input
usage, and the correctness of each retrieval target.

The 16/64 KiB tiers form the small size preset. The 256 KiB/1 MiB tiers require the
explicit large preset. Byte sizes are never labeled as token counts. If the
operator supplies a context-token limit for the declared deployment, show measured
utilization using available provider usage. Otherwise context-limit utilization is
unavailable. Passing a retrieval fixture below the claimed limit must not become
a claim that the context boundary was exercised.

### Outputs

Request a deterministic sequence of numbered records with output caps of 1,024,
4,096, and 16,384 tokens. The requested sequence is longer than the cap is expected
to permit. Validate all complete records for order, uniqueness, and content. Permit
a trailing partial record only when the protocol proves length truncation.

The small preset selects caps 1,024/4,096; the large preset adds 16,384. Start with
non-thinking mode to separate visible generation from reasoning consumption.
Support both protocols and both streaming settings.

Record requested cap, provider-reported output usage, visible record/byte counts,
finish reason, and terminal completeness. Claim a requested output boundary was
exercised only with measured usage at least 90% of that cap and a valid sequence.
Early completion, missing usage, and the verifier's own capture limit must remain
separate outcomes. A request accepted with `max_tokens=16384` is not evidence that
16,384 tokens were generated. The declared deployment maximum remains a separate
operator-supplied value; reaching a selected cap is not necessarily reaching that
maximum.

### Resource controls

Add explicit limits for outgoing request bytes and captured response bytes,
enforced before sending and while reading. Include deterministic input size and
conservative request/output ceilings in the plan. Keep defaults bounded: at most
8 MiB per serialized request and 8 MiB per captured response for these suites.
Longer workflows must respect their request limit as accumulated history grows.
Stop with an explicit execution-limit outcome and retain partial evidence if a
bound is reached. Do not convert a local limit into a provider failure.

Large suites are excluded from smoke/repeatability defaults. Examples remain
serial and use zero retries. They never silently truncate prompts or discard
history to fit a limit. Exceeding a declared deployment limit can be tested only
by a separately labeled negative case; it cannot contaminate positive-limit rates.

## Integration boundaries

- Catalog expansion: new versioned prompt/case data loaded only by explicit presets.
- Planning: deterministic expansion and resource preflight; no network or credentials.
- Execution: reuse durable attempt/resume accounting; add bounded per-step workflow
  expectations and request/capture guards without changing legacy defaults.
- Evaluation: focused workflow, schema, and size evaluators alongside existing
  assertions; preserve separate task correctness and feature-acceptance observations.
- Reporting: derived reliability/capability/size sections with denominators and
  provenance. Report rendering makes no network requests.
- Distribution: bundle new catalogs/profiles/examples, and verify planning and
  synthetic execution through a fresh installed wheel outside the checkout.

Use free-form case metadata only with explicit validation; do not put ambiguous
semantics into IDs. Any unavoidable persisted schema change must be versioned and
have a legacy-loading test before it is accepted into the implementation plan.

## Acceptance criteria and delivery order

1. Repeatability suite produces stable workloads and correct denominators for
   successes, failures, errors, skips, missing work, retries, and resumed runs.
   Seeded synthetic intermittent failures reproduce the expected per-prompt rates.
2. Long workflows detect each injected history/tool defect, including streamed,
   interleaved parallel calls and sibling-branch contamination.
3. Schema matrix rejects seeded invalid outputs and labels accepted/rejected
   subsets correctly without remote schema resolution.
4. Size fixtures measure actual payload/output evidence and distinguish provider
   behavior from local bounds, missing usage, and early completion.
5. Full offline tests, lint, Python 3.11/3.14 CI, packaged CLI checks, and exact
   historical calibration replay pass. Independent review has no blocking finding.
6. Prepare a review-ready PR with separate commits for the four deliverables and
   an explicit table of offline coverage versus any subsequent live observations.

The next stage is a written implementation plan against this reviewed design.
