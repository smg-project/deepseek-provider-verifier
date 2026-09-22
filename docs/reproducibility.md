# Reproducible comparisons

`compare_runs(reference, candidate, (reference_manifest, candidate_manifest),
policy)` is offline. It reads typed summaries and manifests; it never needs raw
request, response, reasoning, or SSE bodies.

## Comparable workload

The comparator checks profile and dataset hashes, scorer revision, gates, budgets,
trial identities, prompt and repetition identities, protocol, thinking mode,
streaming mode, request recipes, output limit, oracle, and intended model release.
Differences are returned as field-level records. Manifest hashes normally differ
between independently addressed runs because endpoint metadata participates in
the hash; hash equality is therefore not the workload test.

Endpoint names and URLs may differ and are recorded as compatible address
differences. Single-endpoint runs are selected automatically. A stored
multi-endpoint run requires selectors so Task 5 can expose the pairing directly:

```python
compare_runs(
    paired_run,
    paired_run,
    (manifest, manifest),
    policy=None,
    reference_endpoint="reference",
    candidate_endpoint="candidate",
)
```

Served model aliases require an explicit declaration. Set `contract_model` in the
endpoint manifest when the alias intentionally represents that contract family,
or pass a descriptive mapping independently of quality policy:

```python
compare_runs(
    reference,
    candidate,
    (reference_manifest, candidate_manifest),
    policy=None,
    model_mapping={"deepseek-flash": "internal-flash-alias"},
)
```

Mapping is addressing metadata, not proof of model weights. Both releases must be
known and equal before a quality-equivalence gate can PASS. `model_release =
"unknown"` still permits an exploratory report but forces the gate to
INCONCLUSIVE.

## Safe attempt projection and resume

Each `RunResult` contains `attempt_metrics`, a content-free projection with endpoint,
case/prompt/repetition, step, retry, monotonic attempt number, status, safe timings,
HTTP-exchange completion, error category, and interrupted-reservation marker. It
contains no request, response, event, text, tool arguments, or reasoning body.
`summary.json` persists the same projection.

On resume, the runner combines all prior completed attempts, unmatched reservations,
and new attempts. First-attempt availability always selects the earliest retained
attempt number for a logical request, so a restart cannot reset a failure into a
new first attempt. The eventual metric may count a later success, and retry/attempt
counts continue to expose the cost.

Evidence written before `http_exchange_completed` was added carries `null` for that
field when parsed by current records. The comparator excludes those logical
requests from the HTTP 2xx denominator and reports them as unavailable; it never
turns missing legacy metadata into a failed request. A stored-run loader may
reconstruct completion only from the append-only attempt journal and the documented
transport error categories. It must not infer completion from a summary's status
code alone.

## Repeated offline plans

The bundled `smoke` preset is fixed at 17 maximum requests per protocol, 34 per
endpoint, 68 total for two endpoints, and zero retries. Do not raise repetitions
or add the full C01-C24 matrix under that preset. Copy the profile under a new ID
and add a separately named preset with calculated bounds. For example, two
repetitions of the existing smoke selection need these minimum request ceilings:

```json
{
  "measurement-2x": {
    "max_requests_per_protocol": 34,
    "max_requests_per_endpoint": 68,
    "max_total_requests": 136,
    "max_requests_per_conversation": 4,
    "max_retries": 0,
    "concurrency": 1,
    "case_deadline_seconds": 300,
    "mode_max_output_tokens": {
      "non_thinking": 512,
      "thinking": 4096
    },
    "case_ids": ["C01", "C03", "C05", "C07", "C11", "C13", "C14"],
    "attached_assertion_case_ids": ["C02", "C17", "C20"]
  }
}
```

Point a separate config at the copied profile, select `suite =
"measurement-2x"`, set `repetitions = 2`, keep `retries = 0`, and set
`max_attempts_per_endpoint = 68`. The offline planner then expands and hashes the
workload before any credential resolution. A refusal reports the exceeded scope
(protocol, endpoint, aggregate, conversation, retry, concurrency, or token budget)
and its configured limit; fix the custom preset or reduce the requested matrix.
Never mutate the shipped smoke ceiling to make a larger run fit.

Store the two manifests, run summaries, comparison policy, bootstrap seed, scorer
revision, and verifier commit together. Do not reuse a mutable reference alias as a
later baseline without rerunning and timestamping it beside the candidate.

## Release artifact checks

From a clean checkout, use the committed lock and the supported CI interpreters
(Python 3.11 and 3.14):

```sh
uv sync --locked --python 3.14
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pytest -q
uv build
uv run --locked python scripts/check_release.py
```

The full suite includes generated-schema equality checks and named report-level
fault detection. The release script inspects actual wheel and sdist entries,
including bundled cases/profiles/schemas, calibration provenance, lockfile,
docs, example configuration, and test fixtures. It installs the wheel and locked
runtime dependencies into a fresh temporary environment outside the checkout,
checks `dpv --help`, and runs the packaged four-request loopback example. Package
installation may access package indexes; verification HTTP traffic is exclusively
local. Nothing in offline CI receives inference credentials.

Seeded fault coverage includes lost reasoning history (`HISTORY_REPLAY`), wrong
call IDs (`TOOL_RESULT_PAIRING`), malformed arguments
(`TOOL_ARGUMENTS_INVALID_JSON`), mixed Chat tool indices (`CONFLICTING_TOOL_ID`)
and Responses item indices (`ITEM_INDEX_MISMATCH`), wrong usage arithmetic
(`usage_accounting` in the synthetic profile), ignored required/prohibited tool
choice (`TOOL_REQUIRED`, `TOOL_PROHIBITION`), swallowed failed terminals
(`TERMINAL_STATE`), and premature EOF (`MISSING_TERMINAL`). Valid fixtures must
also PASS. These fixture gates are synthetic policy, not extra live calibration.

## Optional manual live workflow

Only `workflow_dispatch` can start live verification, and the job runs only from
the repository's default branch. Configure the GitHub environment
`live-verification` with required reviewers and a default-branch deployment rule
before using it. **The repository files do not install those protection rules or
any secrets.** No evening calibration credential was copied into GitHub.

The official route requires that environment's `DEEPSEEK_API_KEY`, and its API
root and requested model are fixed to `https://api.deepseek.com` and
`deepseek-flash`. The candidate route requires its own `CANDIDATE_API_KEY`, a
HTTPS API root without userinfo/query/fragment, and its served alias. It explicitly
maps that alias to the Flash contract family and declares the release unknown.
There is no fallback between keys; missing selected credentials fail clearly.
Do not configure a secret for a route you do not intend to authorize.

Inputs select only the shipped diagnostic or calibrated Flash profile and
Chat/Responses/both. Inputs pass through environment variables into a validating
config writer, never raw shell interpolation. Every invocation selects one
endpoint with at most 17 requests per protocol / 34 overall, four per
conversation, zero retries, one repetition, concurrency one, 300-second case
deadlines, and 512/4096 non-thinking/thinking output-token ceilings. The job has a
15-minute wall-clock limit and no scheduled trigger. Candidate comparison stays
deferred until an operator chooses to run it separately under matched conditions.

The upload step accepts only `runs/sanitized/summary.json`: a separate allowlisted
projection of manifest hash, completion, exit code, status counts, and attempt
count. It deliberately omits arbitrary strings, prompt/response bodies, reasoning,
assertion observations, endpoint URLs, credentials, and raw evidence links. Raw
run data stays on the ephemeral runner and is not uploaded; the artifact cannot
reconstruct a full evidence bundle. Retention is seven days. A failed or
inconclusive run retains its nonzero status and never becomes a PASS because an
artifact upload succeeded.

## Calibration provenance retention

The committed [sanitized calibration record](calibration/official-flash-2026-09-21.json)
identifies the original runner and six hashed artifacts. It records only safe
metadata, original project-authored case identifiers, source URLs/hashes, and
settings. Preserve raw evidence privately for an authorized audit; hashes alone
do not let a third party independently reproduce the historical observations.
The [separate CLI confirmation record](calibration/official-flash-cli-confirmation-2026-09-21.json)
identifies `8b12f73` and its eight original artifact hashes plus wheel hash.
On September 21 at 22:35:53–22:35:59 PDT it made four official requests, all
HTTP 200, with four PASS trials, 23 passing assertions, exit 0 and verified
integrity. Total session traffic is 34. The operator authorized this session-only
after-window check; no persistent repository scheduling policy was changed.

The final review fixes, including stronger streaming-envelope checks, were not
live-tested. Offline replay sent each exact captured request to a MockTransport,
consumed the original sanitized response bytes in memory, and yielded 24 PASS
trials for the 30-request calibration under the scoped profile and four PASS
trials for the separate confirmation. All original artifact hashes were checked
before and after; neither source bundle was rewritten. This validates the new
scorer against historical observations, not a new provider measurement.
Candidate/direct-engine/SMG and V4 Pro remain untested. A later reference run is
a new, timestamped measurement, never a silent replacement of an original run.
