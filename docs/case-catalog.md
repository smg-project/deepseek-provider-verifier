# Versioned case catalog

The authoritative resources are `cases/chat.jsonl`, `cases/responses.jsonl`, and
`cases/behavior.jsonl`. The build includes these files as package resources; there
is no maintained duplicate under `src/`. `catalog.load_cases()` resolves prompt
references and validates records. `catalog.load_prompts()` validates content hashes.
Original prompts and intended answers are MIT-licensed, dataset `original-v1`;
no Kimi/MiniMax corpus is included. The authored variants cover tool-required,
tool-forbidden, ambiguous, nested-schema, and follow-up wording. These small
fixtures are diagnostics, not an adversarial fingerprint or a representative
quality benchmark.

Each template names source rules, protocol, modes, streaming variants, request
recipes, token limits, applicability, and a machine-readable oracle. Missing
required support remains a failure. A predeclared inapplicable case remains a
visible SKIP. Official source provenance and live calibration are independent:
the shipped profile is diagnostic, with no enabled release gates. Its observed
assertion PASS/FAIL measurements remain visible inside INCONCLUSIVE cases.

| ID | Probe and oracle | Request bound and applicability |
|---|---|---|
| C01 | Exact `amber`, required envelope, output, terminal | 1, non-thinking, stream and nonstream |
| C02 | Explicit thinking disabled; no reasoning channel or markers | Attachment in smoke, both stream settings |
| C03 | Thinking output with separated channels, no demanded thought text | 1, thinking, both stream settings |
| C04 | Remember an invented lantern color, replay history, recall `amber` | 2, both stream settings |
| C05 | Completed JSON object matching original parcel schema | 1, nonstream; truncation is INCONCLUSIVE |
| C06 | Declared parcel JSON schema | 1, Responses nonstream; Chat json_schema outside profile scope |
| C07 | `tool_choice=none`, zero calls | 1, non-thinking, both stream settings |
| C08 | Required valid call; documented Chat thinking rejection | 1, both modes and stream settings |
| C09 | Correct named function; documented Chat thinking rejection | 1, both modes and stream settings |
| C10 | Automatic selection on an intentionally ambiguous prompt | 1; observed trigger, no invented ground-truth label |
| C11 | Original nested arguments, strict schema and exact fixture values | 1, both stream settings; synthetic function is never executed |
| C12 | Two calls with distinct IDs, independently reconstructed arguments | 1, both stream settings |
| C13 | Registered addition result, original ID pairing, final `42` | At most 2, both stream settings |
| C14 | Thinking tool continuation, complete assistant replay, final `42` | At most 4, thinking nonstream |
| C15 | Deliberately omit reasoning from replay; observe negative status class | At most 2, thinking nonstream; project-policy diagnostic pending calibration |
| C16 | Multiple streamed calls assembled by identity | 1, streaming; interleaving order is not demanded of live generations |
| C17 | Protocol terminal and lifecycle | Streaming attachment in smoke |
| C18 | Same `amber` oracle for independent stream/nonstream generations | 1 each; never byte-for-byte generation equality |
| C19 | Eight-token limit; valid completed/incomplete terminal | 1 each; does not demand truncation |
| C20 | Nonnegative usage and arithmetic; documented Chat final placement | Streaming attachment in smoke |
| C21 | Invalid `messages`/`input` shape yields documented status class | 1 nonstream; model and generation bounds remain protected |
| C22 | Intentionally wrong result call ID, negative status class | At most 2 nonstream; diagnostic pending calibration |
| C23 | Responses full-history replay with `store=false` | 2 nonstream; Chat outside scope |
| C24 | Option acceptance versus demonstrated effect | 1 nonstream; acceptance cannot establish behavioral support |

C10 and C24 deliberately remain INCONCLUSIVE for behavioral verification even
when HTTP succeeds. Required/forbidden trigger labels come from C07 and the
required/named/nested cases; ambiguous prompts are excluded from precision/recall
labels. Repetition retains prompt identity for later cluster-aware comparison.

The exact smoke selection is C01, C03, C05, C07, C11, C13, C14, with C02, C17,
and C20 attached to compatible existing variants. This reserves 17 requests per
protocol, 34 per endpoint with both protocols. Every retry and repetition is
included in planning. A full matrix needs an explicitly larger preset.

# Execution and evidence API

- `evaluate_case(case, observations, rules) -> CaseResult`: typed assertion IDs,
  reasons, rule IDs, measured statuses, and gating flags. Observations bind HTTP
  status/error metadata explicitly; transport/assembly errors take precedence
  over partial successful output. Unknown assertions/oracles are inconclusive.
- `execute_manifest(manifest, clients, secrets, *, output_dir=None, resume=False)
  -> RunResult`: caller owns one HTTPX client per endpoint, without transport
  retries. Secrets are keyed by endpoint name and must never occur in manifest
  metadata. This function performs no credential discovery or live authorization.
- `load_cases(protocols=None, case_ids=None)`: use the selected preset's request
  and attachment IDs before calling `build_manifest`.
- `append_record(path, record) -> str`: fsynced, hash-chained JSONL envelope hash.
  Additional evidence must be credential-redacted before calling it; structural
  credential-key/URL redaction is also applied at this boundary.
- `load_resume_state(path, manifest_hash) -> ResumeState`: accepts an individual
  journal or directory. Directory loading cross-checks result-to-attempt hashes.
- `validate_manifest(manifest)`: recomputes profile/dataset/workload hashes,
  ceilings, gate selections, and workload limits before sending requests.
- `rehash_manifest(manifest)`: explicit offline editing helper; recomputes hashes,
  not validation. It does not authorize a live run.

`RunSettings.repetitions` defaults to 1. Expanded trial IDs gain `.r1`, `.r2`, etc.
only when repetitions exceed 1; `prompt_id` stays stable and `repetition` is
zero-based. An endpoint plus concrete case ID identifies a planned trial.
`execution_order="sequential"` is endpoint-major; `"paired"` is case-major, so
matching endpoint trials run adjacent to one another. Actual concurrency is 1,
reported separately from the configured concurrency ceiling. This is a bounded
diagnostic runner, not a load or performance certification tool.

Every outbound request, including retry, is debited before sending and has an
fsynced `attempt_start` reservation when persistence is enabled. A request counts
toward both conversation and endpoint/run bounds. Explicit request recipes retain
history even for assistant turns with no tool calls. Tool continuation executes
only `lookup_fixture` and `add_integers`, with exact argument types and keys.
Unknown or malformed calls are never repaired or executed. Model identity,
stream setting, thinking mode, and output ceilings override recipe options.

Retries default to zero. An explicit retry allowance permits recovery from listed
transient HTTP statuses (408, 429, 500, 502, 503, 504) and transport failures;
assertion failures are never retried. `first_attempt_status` evaluates the first
attempt of each conversation step; `eventual_status` evaluates the eventual
conversation. All attempted timings remain in `Attempt.timings`, with retry and
step indices. On resumed incomplete trials, previous attempt references remain
linked, but in-memory conversations restart. First-attempt status for a restarted
trial describes that resumed conversation; original measurements remain in the
prior attempt evidence. Infrastructure failures without recovery, cancellation,
deadlines, and exhausted budgets leave the run incomplete. Unstarted planned
trials remain explicit results rather than disappearing from denominators.

Per-trial metric observations include availability, end-to-end measured success,
fixture task success, trigger/expected trigger, and conditional schema validity
where relevant. Each uses an explicit denominator and scored flag. Diagnostic
maturity never erases a measured success or failure. Neither request ceilings nor
conversation-step counts are independent task-quality sample sizes.

# Rule calibration scope

Executable condition keys are `case_ids`, `mode` (`any`, `thinking`,
`non_thinking`), `modes`, `stream`, `streams`, `models`, and `model_releases`. Case IDs include
attached template IDs. `models` matches the endpoint's explicit `contract_model`,
or its `model` if no mapping was declared. For example, a locally served alias can
set `contract_model="deepseek-flash"`. This is operator-declared applicability,
not weight authentication. `model_releases` separately matches operator-supplied
release metadata. A Flash calibration can therefore use `models=["deepseek-flash"]`
while the release is unknown, without calibrating V4 or every unknown checkpoint.
A mismatch in either declared scope downgrades that rule to diagnostic. Additional keys (for example
`documented_distinction`) are descriptive. Calibration tooling must populate
explicit scope keys and retain exact source/profile snapshots; a generic
calibrated rule intentionally applies to its full declared scope. No applicable
rule means INCONCLUSIVE, never an implicit gate.

# Durable artifacts and resume

The runner writes `manifest.json`, `attempts.jsonl`, `results.jsonl`,
`evidence-index.json`, and `summary.json`; later reporting code produces Markdown
and JUnit views. Manifests and summaries are atomically replaced. Journals are
append-only and flushed before a completed result is recorded. The evidence
index seals journal prefixes after each result, detecting deletion of previously
completed tail records as well as altered record hashes.

Resume requires matching manifest/profile/dataset hashes and valid artifact
chains/checkpoints. It skips completed endpoint/case trials. Interrupted request
reservations still consume budget and retain their attempt numbers. A rerun gets
a new number if budget remains. Torn trailing bytes are retained; subsequent
appends add a hashed disposition record. Unassigned torn evidence remains visible
in `RunResult.resume_dispositions` and conservatively prevents a complete PASS.
Interior corruption is rejected. One runner owns an evidence directory; concurrent
writers are unsupported.

All persisted hashes cover redacted representations. Raw assistant items remain
intact only in memory for replay. The owning capture redacts observation/event
records before persistence. URL userinfo, query, and fragment components are
removed from structured evidence. A body containing secret-bearing URLs is
omitted with a reason when arbitrary raw escaping prevents reliable replacement.
Unexpected assembler errors retain the successful transport capture and become
ERROR. No evidence is automatically uploaded, and no live inference was used to
validate this implementation.

Review-hardened evidence boundaries: a fully received non-JSON response records a
`RESPONSE_BODY_FORMAT` violation while retaining its decoding metadata. A decisive
required HTTP 404 remains FAIL; malformed HTTP 200 is an observed format failure
(or an INCONCLUSIVE enclosing case until calibration). Actual transport,
assembler, and parser resource/depth failures remain ERROR. Unparseable or
interrupted raw bodies, including malformed streaming bodies, are omitted from
persistence with a reason; their originals remain available only in memory.

C13/C14 require an observed valid intended fixture call, its matching returned
result, and a subsequent assistant response. Merely answering `42` can satisfy
the separate answer metric but cannot pass the continuation contract. C15/C22
bind rejection to the designated mutated continuation after successful setup;
rejection of the setup request cannot pass the negative probe. C15 also requires
returned reasoning to exist before its omission can exercise the mutation.
