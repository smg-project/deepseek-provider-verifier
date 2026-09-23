# Self-hosted compatibility and official behavior

Use `configs/self-hosted.example.toml` to verify a self-hosted DeepSeek endpoint.
Edit its URL, served model label, release, and authentication. Select only the
protocols the deployment claims to expose; a selected protocol that rejects
ordinary supported requests fails its controls.

```sh
dpv plan --config configs/self-hosted.example.toml
dpv run --config configs/self-hosted.example.toml \
  --endpoint candidate --out runs/candidate-compatibility
dpv report runs/candidate-compatibility --format markdown
```

For authenticated deployments, replace `auth_none = true` with
`api_key_env = "CANDIDATE_API_KEY"` and set the credential outside the config.
No official credential or reference endpoint is needed to run these probes.
The default example uses the `compatibility` preset. Set `suite = "probes"`
to run only C08/C09 and P01–P10.

## Two scoring policies

| Profile | Meaning of a passing run |
| --- | --- |
| `deepseek-self-hosted-2026-09-22-v1` | Required controls and accepted responses satisfy their fixtures. The boundary probes may return HTTP 400/422 or valid output; differences from the reference are reported separately. |
| `deepseek-official-parity-2026-09-22-v1` | The same functional checks pass, and probes with observed references match the dated official HTTP status exactly. |

Set `run.profile` to choose a policy. The self-hosted profile is recommended for
self-hosted deployments. For example, working forced tool choice in thinking
mode is a valid extension under this policy. Stricter rejection of identifiers
with punctuation is a validation difference. Either differs from the official
reference and fails strict parity.

Acceptance alone never passes: response structure, stream completion, function
name and arguments, schema validity, and fixture answers still apply. A server
returning HTTP 400 to everything fails the valid controls. Only HTTP 400/422
qualify as boundary validation rejections. Authentication, payment, timeout,
rate-limit, server, and transport failures are execution errors; unrelated
client errors such as HTTP 404 remain failures.

JSON reports retain a nongating `COMPATIBILITY_OBSERVATION` with the candidate
status, reference status, reference date, reference basis, policy, and match
flag. Its PASS means the observation was recorded, not that the candidate
matched. Markdown shows MATCH/DIFFERENCE in a separate table alongside the
functional case verdict. Strict parity adds a gating
`OFFICIAL_BEHAVIOR_PARITY` assertion for observed references. JUnit and process
exit status follow the selected policy. A status match cannot override a
malformed or incorrect accepted response.

## Permanent probes

| Cases | Coverage and controls | Reference expectation |
| --- | --- | --- |
| P01 | One genuine reasoning/tool response, a preserved-history continuation, then a branch from the same setup with reasoning removed. Requires the intended tool arguments/result and correct final answers. Empty reasoning placeholders do not exercise the probe. | HTTP 200 with omission. Chat: nonstream and stream; Responses: nonstream. |
| P02–P04 | Valid identifier, a space, and punctuation; Chat `user_id` and Responses `user`. | HTTP 200, including the observed invalid-character acceptance. |
| P05–P06 | 512- and 513-character identifiers. | P05: documented valid boundary, **not live-observed**. P06: observed HTTP 400. |
| C08–C09 | Required and named function choice, both protocols, both thinking settings, nonstream and stream. Accepted calls must select `lookup_fixture` with `key="harbor"`. | HTTP 200 without thinking; HTTP 400 with thinking. |
| P07–P10 | Responses typed enum schemas and const-only property schemas, each with `strict=true` or strict omitted. Typed controls must work; optional const-only acceptance must satisfy the schema. | HTTP 200 except strict const-only, which returned HTTP 400. |

P01 makes three bounded requests. The omitted branch must match the preserved
request exactly except for removal of reasoning history; it cannot inherit the
control's final answer. Failed setup/control, absent genuine reasoning,
unexercised mutation, wrong tool arguments, or an extra unfinished tool call
cannot pass. Responses streaming omission is deferred until an official
reference for that variant has been captured.

P02–P10 are non-thinking, nonstream probes. P07–P10 are explicitly inapplicable
on Chat and appear as SKIP, without sending requests. The legacy C06/C23 Chat
skips also remain in the larger preset.

| Preset, both protocols, one endpoint | Trials including skips | Planned request ceiling | Per-endpoint hard cap |
| --- | ---: | ---: | ---: |
| `compatibility` | 99 | 123 | 126 |
| `probes` | 37 | 43 | 46 |

Conservative caps allow up to 63/23 requests per protocol and 252/92 across two
endpoints, respectively. Actual requests are lower for skips and early failures.
Both presets use zero retries, concurrency one, and at most four requests per
conversation. The plan prints the exact ceiling for selected protocols,
endpoints, and repetitions. Increasing repetitions requires explicitly raising
profile budgets; no hidden requests are added.

The larger preset includes the deterministic core C01–C24 except C10 (ambiguous
tool choice), C15 (replaced here by paired P01), and C24 (option effect remains
unmeasured). Historical profiles and the default `load_cases()` C01–C24 catalog
remain unchanged. They retain their original hashes and diagnostic behavior.

## Evidence scope

The [dated reference record](calibration/compatibility-reference-2026-09-22.json)
links the four findings to the September 22 official run: 262 requests across
`deepseek-flash` and `deepseek-v4-pro`, plus source-document and artifact hashes.
It distinguishes documentation discrepancies from documentation gaps. The
reference date is explicit because official behavior and model aliases can
change.

These new profiles are **project-policy contracts**, verified offline with
synthetic responses. Their `calibrated` rule maturity enables deterministic
project gates; it does not claim live calibration of the new profile composition
or any self-hosted endpoint. They apply to the operator-declared endpoint/model
label without silently downgrading unknown self-hosted aliases. To compare an
actual current reference and candidate, run both with the same profile and use
the existing comparison command and explicit model mapping.

The new recipes reuse authored prompts and combine paired control steps. They
are not presented as exact-request replay of every reference capture. P05 is
labeled `documented` and has no official-observation parity gate. Acceptance of
an identifier does not prove cache isolation; passing a schema fixture does not
prove constrained decoding; a PASS never authenticates model weights. Raw
responses, reasoning, and credentials are excluded from the committed record.

## Recommended follow-up order

1. **Repeatability:** multiple authored prompts and repeated runs for each model,
   protocol, and mode; report failure counts/rates, sample sizes, and confidence
   intervals. Preserve first-attempt and eventual outcomes. This is the next
   substantive feature to prioritize.
2. **Long conversations and tools:** progressively longer deterministic chains,
   branches and parallel calls, with accumulated history and reasoning.
3. **Schema coverage:** optional/null fields, unions, references, nesting, and
   supported strict-mode boundaries, each with valid and invalid controls.
4. **Live recovery and load:** bounded concurrency ramps, interrupted streams,
   cancellation, timeouts, retries, and evidence-preserving resume.
5. **Large inputs/outputs:** controlled ramps toward actual context and generation
   limits, with explicit token/request budgets.
6. **Parameter effects and caching:** repeated controlled comparisons rather than
   interpreting request acceptance as evidence of an effect.
7. **Vision and files:** prioritize earlier only for deployments claiming those
   capabilities; include fetches, OCR/charts, multiple images, and lifecycle edges.

Validate the **installed wheel and manual GitHub workflow in the next live run**
as a release check alongside repeatability. The current PR's packaged CLI check
is offline; no new live coverage is claimed for the workflow or these profiles.

## Recommended acceptance workflow

Use the versioned verification workload and the same policy for every provider:

```sh
dpv plan --config configs/self-hosted-verify.example.toml --policy official-compatible-v1
dpv verify --config configs/self-hosted-verify.example.toml --endpoint candidate \
  --policy official-compatible-v1 --out runs/candidate-verification
dpv assess runs/candidate-verification --policy strict-contract-v1 \
  --out runs/candidate-strict
```

Edit the example endpoint/model and configure authentication before live use. The
`verification` preset has a 210-request ceiling per endpoint, with both APIs,
thinking modes and stream modes where the selected fixtures support them. The
`full-stress` preset has an 888-request ceiling per endpoint and additionally
includes all depth schemas, long workflows, 1 MiB inputs and 16K output budgets.
The output-token ceiling and route are printed before `verify` sends traffic.
These are selected fixture sizes, not a claim about the model's maximum capacity.

`verify` retains the canonical strict `summary.json`, `summary.md`, `junit.xml`
and raw evidence. Its exit code and separate `acceptance.json`, `acceptance.md`,
`acceptance.junit.xml` follow the selected acceptance policy. Existing `run`,
`report` and `compare` behavior is unchanged. Raw strict failures can coexist
with compatibility acceptance PASS. Acceptance JUnit records diagnostic facets
as explicitly skipped/report-only testcases with their original statuses.

The default `official-compatible-v1` policy requires valid protocol envelopes,
complete streams, correct core tool results and histories, shallow structured
output, exact retrieval facts and exercised total output budgets. Exact-word
instruction observations, advanced schema capability, raw retrieval formatting
and visible-token measurement remain separately reported diagnostics. Advanced
schema rejection or invalid output never certifies support. Valid extra support
from a self-hosted provider earns a capability PASS; it need not reproduce an
official provider defect. Malformed tool JSON always fails protocol validation.

Plain-text retrieval may return exactly the expected object inside one complete
outer JSON fence. JSON-mode responses still require raw JSON. Duplicate members,
nonfinite numbers, excessive nesting/size, prose, repaired JSON or wrong facts
are rejected. Total output budget means consistent provider-reported generation
of at least 90% of the selected cap plus a valid numbered prefix and explicit
length terminal. If reasoning token details are absent, visible tokens remain
unknown; acceptance never assumes zero reasoning tokens.

`assess` is network-free and reconstructs observations from hash-verified
captures. It does not trust an existing acceptance sidecar. Missing captures,
partial runs, unverified summaries and execution errors cannot pass. Exit codes
are **0** for all required gates passing, **1** for required failures, and **2**
for invalid/incomplete/unmeasured required evidence. A source-content hash binds
the scorer; the policy snapshot and hash bind classification. CI can pin them with
`--expected-policy-hash` and `--expected-scorer-revision` on verify/assess.

To require an advanced capability, copy the compatibility policy, assign a new
ID, and set `schema.optional/capability` to `required: true`. All applicable
advanced probes then become mandatory. `strict-contract-v1` requires every
facet, including original strict verdicts and visible measurement. Mandatory
protocol and core functional gates cannot be waived. Both policies are independent
of hostnames and model labels. Chat Beta strict schema is a separate contract:
configure its explicit `/beta` route and select Chat only; standard-route results
do not establish Beta support.

Thinking workflows report accumulated reasoning separately from tool execution.
All exact calls, results and replay of emitted reasoning remain mandatory. If
fewer than two nonempty reasoning rounds are emitted, accumulated replay is
**INCONCLUSIVE**, even when the workflow succeeds. The default policy reports
this as an uncertified capability; strict mode or a policy requiring
`workflow.thinking/reasoning` cannot pass that unknown measurement.
