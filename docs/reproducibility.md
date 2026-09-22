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
