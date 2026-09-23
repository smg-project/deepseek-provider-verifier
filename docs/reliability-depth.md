# Reliability and depth suites

These opt-in suites exercise self-hosted DeepSeek-compatible deployments using
project-authored deterministic fixtures. They extend the compatibility probes;
existing smoke, compatibility, and dated official calibration profiles are unchanged.
The depth rules are executable project-policy gates, not official observations.
They apply to the operator's served model alias without claiming weight identity.

## Select and inspect a workload

Copy one example, set the endpoint URL/model/release and authentication, inspect
the plan, and choose the endpoint explicitly when executing:

```sh
cp configs/depth-repeatability.example.toml depth.toml
dpv plan --config depth.toml
dpv run --config depth.toml --endpoint candidate --out runs/depth-candidate
dpv report runs/depth-candidate --format markdown
```

The default endpoint in these examples is a local placeholder. For authentication,
replace `auth_none = true` with `api_key_env = "CANDIDATE_API_KEY"` and set that
variable outside the config. The installed wheel bundles all examples; extract one
without a source checkout:

```sh
python -c 'from importlib.resources import files; print(files("deepseek_provider_verifier").joinpath("configs", "depth-repeatability.example.toml").read_text(), end="")' > depth.toml
```

All examples use profile `deepseek-depth-2026-09-22-v1`, serial execution, and no
retries. Counts below are for one endpoint with both protocols. Each profile
reserves twice the endpoint request budget for explicitly paired reference and
candidate runs. Repetitions consume real request budget.

| Preset | Example config | Trials | Request ceiling per endpoint |
| --- | --- | ---: | ---: |
| repeatability | depth-repeatability.example.toml | 200 | 250 |
| repeatability-expanded | depth-repeatability-expanded.example.toml | 800 | 1,000 |
| workflows-small | depth-workflows.example.toml | 32 | 160 |
| workflows-full | same config; change suite | 56 | 384 |
| schemas | depth-schemas.example.toml | 128 | 128; 96 sent if all applicable cases complete |
| sizes-small | depth-sizes.example.toml | 24 | 48 |
| sizes-large | depth-sizes-large.example.toml | 44 | 92 |

`plan` prints request/output-token ceilings plus derived byte bounds. It makes no
HTTP requests. Larger presets are explicitly selected; planning repeatability does
not construct any large input text. Counts are workload ceilings, never statistical
denominators. Copy the profile and deliberately adjust budgets if changing repeats.

## Repeatability

R01–R20 contain five distinct prompts for each of instruction following, required
fixture lookup, arithmetic tool continuation, and structured JSON. The starter
runs five repeats in non-thinking, nonstream mode. R21–R40 reuse the same prompt
identities and add thinking and streaming axes. Chat uses JSON-object output;
Responses uses typed JSON Schema. Tool requests use auto choice, while their
oracles require the intended calls and exact arguments.

Each run adds derived `reliability.json` and Markdown analysis. Groups identify
endpoint/model, family, protocol, mode, and stream, with individual prompt rows.
Counts preserve PASS, FAIL, ERROR, INCONCLUSIVE, SKIP, and missing work. Conditional
failure is FAIL/(PASS+FAIL). Zero observations yield null, not perfect reliability.
Errors remain visible outside that conditional rate. First/eventual HTTP rates
use actual logical requests and retain earlier failed or interrupted reservations.
A restarted trial with an unrecoverable original assessment has unavailable first
contract status. Reusing a completed trial does not double-count it.

Per-prompt 95% Wilson intervals assume independent repeated trials. Different
prompt variants are not pooled into a misleading independent-sample interval.
Five repeats provide a small descriptive sample; there is no new automatic quality
gate. Cross-endpoint comparison retains the existing prompt-cluster bootstrap.
Reports recompute from validated manifests/results, never from a trusted sidecar.
See [metric denominators](metrics.md).

## Long conversations and tools

W01/W02/W03 exercise 4/8/16 tool rounds plus a final answer. W04/W05 exercise 4/16
parallel calls and a final result list. W06 forks two deterministic branches from
a shared tool result. W07 updates and recalls facts across eight turns. All cover
both protocols, thinking settings, and stream settings.

Each response must satisfy its own exact call/answer expectation. History includes
the declared parent's original assistant output, reasoning, and matched tool
results. Branches restore parent request settings and exclude sibling history.
Call/result order may vary within a parallel batch; call IDs and arguments must
remain correct. Only registered local arithmetic/lookup functions execute.
The 16-call parallel case allows 2,048 non-thinking output tokens per request;
512 tokens truncated valid call batches during official live testing. Other
non-thinking workflow cases retain their 512-token cap.

At least two nonempty returned reasoning rounds are needed to measure accumulated
reasoning replay. Otherwise that facet is inconclusive even if the task succeeded.
Cases stop at their request bounds or deadline; they never grow unbounded chains.

## Schema coverage

S01–S64 cover required objects, optional fields present/absent, null and nonnull
values, enum/const, array bounds, numeric/string bounds, nesting 2/4/8, anyOf/oneOf,
and local references. Each feature has tool/output targets with strict omitted
or true. Chat's 32 output-schema combinations are explicit SKIPs. Required-object
tool/strict-omitted is mandatory; the Responses required-object output forms are
also mandatory. Advanced subsets may reject with 400/422.

Capability labels are ACCEPTED_VALID, ACCEPTED_INVALID, REJECTED, and ERROR.
A permitted rejection can pass its observation contract but never contributes a
supported-feature or schema-validity score of one. Accepted responses must satisfy
the schema and intended fixture. `record_schema_fixture` is inspected, never run.
Passing an example does not establish that constrained decoding enforces every
schema keyword.

For official Chat strict-tool calibration, use `https://api.deepseek.com/beta`
as the endpoint base URL and select only the Chat protocol. DeepSeek documents
strict tools on that route; accepting `strict=true` on the standard URL does not
establish the same guarantee. Self-hosted endpoints should use their documented
route. See the [official tool guide](https://api-docs.deepseek.com/guides/tool_calls/)
and the [dated live findings](calibration/official-depth-2026-09-22.md).

Validation is local only: schemas/values are bounded to 64 KiB, 2,048 nodes, and
depth 32. Expanded reference traversal is bounded too. Remote/recursive references,
dynamic scopes, and regex patterns are outside this validator's bounded subset.

## Large inputs and outputs

L01–L04 contain exactly 16/64/256/1024 KiB of authored user text, including
instructions. L05–L08 distribute the same total sizes across four turns. Seeded,
varied filler surrounds facts near the beginning, middle, and end. All three facts
must be returned correctly. Literal UTF-8 bytes are not token estimates.

When a run includes input- or output-size probes, the CLI's HTTP read timeout
follows the manifest's per-case deadline (600 seconds in the shipped size presets), allowing
large input processing and nonstream generation to finish. The runner still
enforces that overall deadline;
connect/write timeouts and the 30-second read timeout of other suites are unchanged.

L09–L11 request numbered records with output caps of 1,024/4,096/16,384 tokens.
The requested number of records exceeds twice the token cap. Every complete record
must be correct, unique, and ordered. A partial last record requires an explicit
length terminal. An output boundary is exercised only with a valid nonempty
sequence and provider-reported visible generation of at least 90% of the requested
cap and a terminal explicitly reporting the output limit. Reported reasoning tokens
are excluded; omitted reasoning usage is unknown, never assumed zero. Short or
unmeasured generation is
inconclusive; malformed output or contradictory usage fails. These measurements
rely on provider-reported token usage, not an independently verified tokenizer.

Reports retain authored bytes, actual serialized request sizes/hashes, provider
usage, visible records/bytes, terminal state, and requested/declared utilization
separately. Correct input retrieval can pass with unknown context utilization.
A selected cap is not necessarily the deployment maximum.

For explicit deployment claims, copy the profile and add this object to each
applicable `chat.size` / `responses.size` rule's `conditions`:

```json
{"deployment_limits": {"candidate": {"context_tokens": 131072, "output_tokens": 16384}}}
```

These example values are operator declarations, not defaults or inferred model
specifications. Set values for your deployment and pass `--profile custom-profile.json`
to both plan and run. Endpoint names must match the config. Claims affect the
profile hash. A selected positive output cap above a declared maximum fails
preflight. A 400/422 with no established within-capacity claim is an inconclusive
REJECTED capacity observation; rejection within a declared output limit fails.

## Local limits and evidence

All new cases cap serialized requests and retained response bodies at 8 MiB each.
Requests are measured using actual HTTPX JSON bytes before any attempt is reserved.
The check includes growing history. Captures stop before retaining bytes beyond
the cap and close the stream. REQUEST_BYTE_LIMIT / RESPONSE_BYTE_LIMIT produce
incomplete ERROR results with exit 2, never a provider rejection or reached limit.
No prompts/history are silently shortened. Unsafe partial raw bodies follow the
existing redaction/omission rules.

Offline tests cover valid fixtures, seeded failures, streams, branches, resume,
byte bounds, schema boundaries, installed CLI runs, and historical replay. These
new depth fixtures were exercised against both official model aliases on
2026-09-22; the [live report](calibration/official-depth-2026-09-22.md) preserves
initial failures, targeted confirmations, and measurement limits. No self-hosted
candidate was supplied. Load, recovery under live concurrency, parameter effects,
caching, vision, and files remain separate work. Inspect a concrete plan before
choosing a live workload.

## Compatibility acceptance versus strict observations

The combined `deepseek-verification-2026-09-22-v1` profile keeps all existing
fixtures unchanged and composes core controls with depth probes. Use `dpv verify`
with `official-compatible-v1` for the recommended compatibility decision, and
`dpv assess ... --policy strict-contract-v1` for the strict view of the same
captures. See [acceptance workflow](self-hosted-compatibility.md#recommended-acceptance-workflow)
for the exact gates, diagnostic limitations and exit codes. Repeated default
runs are independent executions of the same frozen prompt/filler seeds; they
measure repeatability without claiming broad statistical coverage.

See the [official acceptance campaign](calibration/official-acceptance-2026-09-23.md)
for the installed-wheel default rounds and extended stress matrix. It preserves
all initial failures and the 600-second request timeout, with confirmations
labeled separately from the original validation runs.
