# Metrics and denominators

The verifier reports contract results, measured task quality, and HTTP delivery as
separate sections. It never combines them into a single score. A diagnostic rule
can leave contract certification inconclusive while still producing a scored task
observation. Conversely, a completed HTTP response can be contract-invalid.

## Population terms

A **planned trial** is one manifest case variant at one repetition for one selected
endpoint. The request ceiling is only a resource cap. It is not a metric
denominator, and unused continuation allowance is not a failed request.

A **logical HTTP request** is identified by endpoint, trial, and conversation step.
Its initial attempt is the lowest retained attempt number for that step, including
attempts from before a resume. A retry is any later outbound attempt for the same
step. An unmatched durable attempt reservation is retained as an unavailable
attempt; it cannot disappear on resume.

## Trial and task metrics

`end_to_end_success` has one denominator unit for every planned trial. Its
numerator contains trials whose measured required assertions completed and passed.
Not-started, execution-error, missing, and unscored trials remain in the denominator
as zero and are also reported in `unavailable`. A correctly exercised negative
probe whose expected result is HTTP 4xx can succeed as a verification trial. That
does not rewrite its literal HTTP status into 2xx availability.

`task_success` is conditional on a scored authored-answer observation. Its
denominator is the number of scored observations. Missing or unscored planned
trials are shown in `unavailable`; a zero denominator produces `value: null`, never
100 percent. This metric consumes `TASK_SUCCESS` measurements, not the enclosing
contract status, so an uncalibrated diagnostic rule cannot create a false
behavioral failure or quality PASS.

## HTTP availability and latency

`first_http_2xx_rate` is successful fully received 2xx HTTP exchanges divided
by actual initial logical HTTP requests. The initial attempt is the first-ever
retained attempt, not the first attempt after a restart. `INVALID_JSON`,
`JSON_DEPTH_LIMIT`, and post-receipt assembly findings do not erase a fully
received HTTP 200. An interrupted read, timeout, cancelled reservation, missing
status, or non-2xx status contributes zero. Planned trials that never start are
reported separately in `unavailable`; unused request allowance is ignored.

`eventual_http_2xx_rate` uses the same actual logical-request denominator and
counts a request when any retained initial or retry attempt completes with 2xx.
`http_attempts` and `http_retry_attempts` show the literal outbound counts. Expected
4xx negative probes remain in both first/eventual denominators and score zero for
this 2xx metric even when the verification trial succeeds.

Latency means are conditional on fully received 2xx attempts with that timing
present. The report includes headers, first SSE event, first meaningful output, and
total time separately. Its denominator is the count with a recorded timing;
successful exchanges missing that timing are reported as unavailable. Chunk count
is never treated as token throughput.

## Tool metrics

`schema_accuracy` is valid emitted tool calls divided by emitted calls with a
declared argument schema. Every call has its own observation. Malformed JSON,
non-object arguments, and schema-invalid arguments contribute zero. Calls without
a declared schema are unscored and displayed in `unavailable`; cases with no tool
call do not fabricate a call. A zero scored-call denominator is unavailable.
Formatted final JSON output uses the separate `schema_validity` observation.

Tool-trigger precision, recall, and F1 use only trials with an explicit trigger
oracle. Ambiguous cases are unscored. The report retains true-positive,
false-positive, true-negative, and false-negative counts. The exact denominators
are `TP + FP` for precision, `TP + FN` for recall, and `2TP + FP + FN` for F1.
Any zero denominator produces an unavailable value.

## Paired uncertainty and quality gates

Behavioral differences are candidate minus reference. The bootstrap resamples
independent prompt IDs with replacement and retains all case variants and
repetitions belonging to each sampled prompt. It uses only paired scored
observations and reports excluded reference/candidate observations, paired prompt
count, retained repetition count, confidence level, and seed. Duplicate result
identities are rejected. Repeating one prompt cannot satisfy the minimum distinct
prompt floor.

A quality policy explicitly lists each higher-is-better metric's allowed drop,
minimum distinct prompts (at least two), minimum repetitions, confidence level,
bootstrap sample count, and seed. PASS requires the lower confidence bound to be
strictly greater than `-allowed_drop`. FAIL requires the upper bound to be strictly
less. Equality at either boundary, insufficient pairing, unknown checkpoint
identity, or an incompatible workload is INCONCLUSIVE. With no policy, comparison
is descriptive and has no quality verdict.
