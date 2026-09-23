# Official DeepSeek live verification — 2026-09-22

Completed 2,068 real inference requests against the official API (standard URL plus four Chat strict-schema requests on `/beta`), covering `deepseek-flash` and `deepseek-v4-pro`, Chat Completions and Responses. 1,198 trial executions (including targeted confirmations): **1067 PASS, 49 FAIL, 6 ERROR, 12 INCONCLUSIVE, 64 SKIP**.

## Results

Counts are PASS / FAIL / ERROR / INCONCLUSIVE / SKIP. Optional schema rejection can pass the observation check; it does not establish feature support.

| Suite | Flash | Pro | Live requests |
| --- | --- | --- | ---: |
| pilot | 2 / 0 / 0 / 0 / 0 | 2 / 0 / 0 / 0 / 0 | 4 |
| repeatability | 198 / 2 / 0 / 0 / 0 | 200 / 0 / 0 / 0 / 0 | 500 |
| repeatability-expanded | 158 / 2 / 0 / 0 / 0 | 160 / 0 / 0 / 0 / 0 | 400 |
| workflows-full | 52 / 4 / 0 / 0 / 0 | 52 / 4 / 0 / 0 / 0 | 760 |
| schemas | 95 / 1 / 0 / 0 / 32 | 89 / 7 / 0 / 0 / 32 | 192 |
| sizes-large | 27 / 10 / 2 / 5 / 0 | 18 / 17 / 4 / 5 / 0 | 183 |
| workflow-budget-confirmation | 4 / 0 / 0 / 0 / 0 | 4 / 0 / 0 / 0 / 0 | 16 |
| schema-beta-confirmation | 2 / 0 / 0 / 0 / 0 | 0 / 2 / 0 / 0 / 0 | 4 |
| size-timeout-confirmation | 1 / 0 / 0 / 1 / 0 | 1 / 0 / 0 / 1 / 0 | 4 |
| input-timeout-confirmation-pro | 0 / 0 / 0 / 0 / 0 | 2 / 0 / 0 / 0 / 0 | 5 |

## Findings

The initial run and later confirmations are separate observations. The totals retain earlier verifier-induced failures and timeouts; they are not a failure rate for the corrected verifier or a production reliability estimate.

### Model instruction and schema behavior

- **Repeatability:** Flash failed four trials across the two matrices; Pro passed all 360 of its trials. The two core failures occurred on the same exact-word prompt, once on Chat and once on Responses (each 1/5 failures for that prompt/API). One response refused the harmless instruction; another changed the required word's case. Expanded variants produced two unrelated answers. Correct outgoing prompts and settings were checked in the retained requests.
- **Nested schemas:** the standard-URL matrix produced eight invalid nested tool argument objects: one on Flash and seven on Pro. Each added an extra `child` level. These completed below the token cap, so they were not truncation failures. Some sent `strict=true`, but DeepSeek documents strict Chat tools under `/beta`; the standard-route failures alone do not establish a violation of that guarantee. [Official Tool Calls guide](https://api-docs.deepseek.com/guides/tool_calls/)
- **Schema rejections:** both models rejected five advanced strict Responses output-schema fixtures: optional fields absent/present, enum-only, const-only, and oneOf-only schemas. These count as passing capability observations because rejection is permitted for those fixtures; they do **not** count as supported schema features. Chat output-schema cases were intentionally skipped.
- **Large-input formatting:** all 27 initial input-task failures (10 Flash, 17 Pro) contained the correct requested facts inside Markdown code fences. Multiturn acknowledgments and history checks also passed for those cases. They remain failures because the fixture requires a plain JSON object. Offline fence removal was diagnostic only and did not change the scores. These observations do not show failed fact retrieval.

### Verifier corrections and confirmation

- **Parallel-call budget:** all eight initial non-thinking W05 failures hit the 512-token cap before completing the requested 16 tool calls. Only that fixture's non-thinking cap was raised to 2,048. Other workflow cases retained their budgets. The follow-up passed **8/8 trials** (16 requests), covering both models, APIs, and streaming settings.
- **Size timeouts:** the initial runner used a 30-second HTTP read timeout and recorded six execution errors: four non-streaming 16K-output cases and two large Chat input cases on Pro. Size suites now use the configured 600-second read budget while retaining the overall case deadline. All six errored trials were rerun: both Pro input trials passed, and all four output trials completed with correct numbered output at the 16,384-token cap. Responses passed both boundary checks; Chat remained INCONCLUSIVE on both models solely because the reasoning-token breakdown was absent. No confirmation had a transport error.
- **Strict tools on Beta:** Flash passed both depth-4 and depth-8 fixtures. Pro returned HTTP 200 but added an extra nesting level in **both** fixtures, despite `strict=true`, all object properties required, and `additionalProperties=false` throughout. The requests used `https://api.deepseek.com/beta/chat/completions`. Depth 4 became 5 levels; depth 8 became 9. Both returned complete tool calls using only 61/74 completion tokens. Independent review matched the raw bytes, decoded responses, and assembled arguments. This is an observed discrepancy with the documented strict-schema guarantee, limited to these two Pro examples; it does not establish a general failure rate. [Official Tool Calls guide](https://api-docs.deepseek.com/guides/tool_calls/)
- **Honest output measurements:** Chat omitted the reasoning-token breakdown needed to derive visible output tokens. The scorer now treats that measurement as unknown and requires an explicit output-limit terminal. Offline rescoring changed ten initial Chat output checks from PASS to INCONCLUSIVE; their task-format checks passed. Responses supplied the breakdown. No model output was changed or discarded.
- **Other reviewed corrections:** unexecuted workflows remain unscored; report render failures preserve previous files; retained attempt counts no longer claim to be exact outbound counts; installed fixture checks use an OS-assigned port without a reserve/rebind race. All 728 offline tests, lint/format checks, and fresh installed-wheel checks passed.

The largest completed provider-reported input was **523,980 tokens** on each model/API. The authored input ceiling was **1 MiB**, and the selected output ceiling was **16,384 tokens**. These are tested workloads, not verified maximum model capacities.

## Initial standard-route schema observations

| Model / API | Accepted valid | Accepted invalid | Rejected | Error |
| --- | ---: | ---: | ---: | ---: |
| flash / chat | 32 | 0 | 0 | 0 |
| flash / responses | 58 | 1 | 5 | 0 |
| pro / chat | 28 | 4 | 0 | 0 |
| pro / responses | 56 | 3 | 5 | 0 |

Chat output JSON Schema cases are intentionally skipped. Tool-argument schemas are exercised on both APIs.

## Initial size measurements

| Model / case | Status | Provider input tokens | Visible output tokens | Requested output cap | Length terminal |
| --- | --- | ---: | ---: | ---: | --- |
| flash / L01.chat.non_thinking.nonstream | FAIL | 8166 | unknown | 512 | False |
| flash / L01.chat.non_thinking.stream | FAIL | 8166 | unknown | 512 | False |
| flash / L02.chat.non_thinking.nonstream | PASS | 32730 | unknown | 512 | False |
| flash / L02.chat.non_thinking.stream | FAIL | 32730 | unknown | 512 | False |
| flash / L03.chat.non_thinking.nonstream | PASS | 130752 | unknown | 512 | False |
| flash / L03.chat.non_thinking.stream | PASS | 130752 | unknown | 512 | False |
| flash / L04.chat.non_thinking.nonstream | FAIL | 523980 | unknown | 512 | False |
| flash / L04.chat.non_thinking.stream | FAIL | 523980 | unknown | 512 | False |
| flash / L05.chat.non_thinking.nonstream | PASS | 8139 | unknown | 512 | False |
| flash / L05.chat.non_thinking.stream | PASS | 8139 | unknown | 512 | False |
| flash / L06.chat.non_thinking.nonstream | PASS | 32697 | unknown | 512 | False |
| flash / L06.chat.non_thinking.stream | PASS | 32697 | unknown | 512 | False |
| flash / L07.chat.non_thinking.nonstream | PASS | 130727 | unknown | 512 | False |
| flash / L07.chat.non_thinking.stream | PASS | 130727 | unknown | 512 | False |
| flash / L08.chat.non_thinking.nonstream | PASS | 523949 | unknown | 512 | False |
| flash / L08.chat.non_thinking.stream | PASS | 523949 | unknown | 512 | False |
| flash / L09.chat.non_thinking.nonstream | INCONCLUSIVE | 56 | unknown | 1024 | True |
| flash / L09.chat.non_thinking.stream | INCONCLUSIVE | 56 | unknown | 1024 | True |
| flash / L10.chat.non_thinking.nonstream | INCONCLUSIVE | 56 | unknown | 4096 | True |
| flash / L10.chat.non_thinking.stream | INCONCLUSIVE | 56 | unknown | 4096 | True |
| flash / L11.chat.non_thinking.nonstream | ERROR | unknown | unknown | 16384 | False |
| flash / L11.chat.non_thinking.stream | INCONCLUSIVE | 56 | unknown | 16384 | True |
| flash / L01.responses.non_thinking.nonstream | PASS | 8166 | 48 | 512 | False |
| flash / L01.responses.non_thinking.stream | PASS | 8166 | 48 | 512 | False |
| flash / L02.responses.non_thinking.nonstream | FAIL | 32730 | 46 | 512 | False |
| flash / L02.responses.non_thinking.stream | PASS | 32730 | 42 | 512 | False |
| flash / L03.responses.non_thinking.nonstream | FAIL | 130752 | 53 | 512 | False |
| flash / L03.responses.non_thinking.stream | FAIL | 130752 | 53 | 512 | False |
| flash / L04.responses.non_thinking.nonstream | FAIL | 523980 | 40 | 512 | False |
| flash / L04.responses.non_thinking.stream | FAIL | 523980 | 50 | 512 | False |
| flash / L05.responses.non_thinking.nonstream | PASS | 8139 | 38 | 512 | False |
| flash / L05.responses.non_thinking.stream | PASS | 8139 | 38 | 512 | False |
| flash / L06.responses.non_thinking.nonstream | PASS | 32697 | 37 | 512 | False |
| flash / L06.responses.non_thinking.stream | PASS | 32697 | 37 | 512 | False |
| flash / L07.responses.non_thinking.nonstream | PASS | 130727 | 39 | 512 | False |
| flash / L07.responses.non_thinking.stream | PASS | 130727 | 39 | 512 | False |
| flash / L08.responses.non_thinking.nonstream | PASS | 523949 | 36 | 512 | False |
| flash / L08.responses.non_thinking.stream | PASS | 523949 | 36 | 512 | False |
| flash / L09.responses.non_thinking.nonstream | PASS | 56 | 1024 | 1024 | True |
| flash / L09.responses.non_thinking.stream | PASS | 56 | 1024 | 1024 | True |
| flash / L10.responses.non_thinking.nonstream | PASS | 56 | 4096 | 4096 | True |
| flash / L10.responses.non_thinking.stream | PASS | 56 | 4096 | 4096 | True |
| flash / L11.responses.non_thinking.nonstream | ERROR | unknown | unknown | 16384 | False |
| flash / L11.responses.non_thinking.stream | PASS | 56 | 16384 | 16384 | True |
| pro / L01.chat.non_thinking.nonstream | FAIL | 8166 | unknown | 512 | False |
| pro / L01.chat.non_thinking.stream | FAIL | 8166 | unknown | 512 | False |
| pro / L02.chat.non_thinking.nonstream | FAIL | 32730 | unknown | 512 | False |
| pro / L02.chat.non_thinking.stream | PASS | 32730 | unknown | 512 | False |
| pro / L03.chat.non_thinking.nonstream | FAIL | 130752 | unknown | 512 | False |
| pro / L03.chat.non_thinking.stream | FAIL | 130752 | unknown | 512 | False |
| pro / L04.chat.non_thinking.nonstream | ERROR | unknown | unknown | 512 | False |
| pro / L04.chat.non_thinking.stream | FAIL | 523980 | unknown | 512 | False |
| pro / L05.chat.non_thinking.nonstream | PASS | 8139 | unknown | 512 | False |
| pro / L05.chat.non_thinking.stream | PASS | 8139 | unknown | 512 | False |
| pro / L06.chat.non_thinking.nonstream | PASS | 32697 | unknown | 512 | False |
| pro / L06.chat.non_thinking.stream | PASS | 32697 | unknown | 512 | False |
| pro / L07.chat.non_thinking.nonstream | PASS | 130727 | unknown | 512 | False |
| pro / L07.chat.non_thinking.stream | FAIL | 130727 | unknown | 512 | False |
| pro / L08.chat.non_thinking.nonstream | ERROR | unknown | unknown | 512 | False |
| pro / L08.chat.non_thinking.stream | PASS | 523949 | unknown | 512 | False |
| pro / L09.chat.non_thinking.nonstream | INCONCLUSIVE | 56 | unknown | 1024 | True |
| pro / L09.chat.non_thinking.stream | INCONCLUSIVE | 56 | unknown | 1024 | True |
| pro / L10.chat.non_thinking.nonstream | INCONCLUSIVE | 56 | unknown | 4096 | True |
| pro / L10.chat.non_thinking.stream | INCONCLUSIVE | 56 | unknown | 4096 | True |
| pro / L11.chat.non_thinking.nonstream | ERROR | unknown | unknown | 16384 | False |
| pro / L11.chat.non_thinking.stream | INCONCLUSIVE | 56 | unknown | 16384 | True |
| pro / L01.responses.non_thinking.nonstream | FAIL | 8166 | 52 | 512 | False |
| pro / L01.responses.non_thinking.stream | FAIL | 8166 | 52 | 512 | False |
| pro / L02.responses.non_thinking.nonstream | FAIL | 32730 | 51 | 512 | False |
| pro / L02.responses.non_thinking.stream | FAIL | 32730 | 51 | 512 | False |
| pro / L03.responses.non_thinking.nonstream | FAIL | 130752 | 53 | 512 | False |
| pro / L03.responses.non_thinking.stream | FAIL | 130752 | 53 | 512 | False |
| pro / L04.responses.non_thinking.nonstream | FAIL | 523980 | 50 | 512 | False |
| pro / L04.responses.non_thinking.stream | FAIL | 523980 | 50 | 512 | False |
| pro / L05.responses.non_thinking.nonstream | FAIL | 8139 | 52 | 512 | False |
| pro / L05.responses.non_thinking.stream | FAIL | 8139 | 52 | 512 | False |
| pro / L06.responses.non_thinking.nonstream | PASS | 32697 | 42 | 512 | False |
| pro / L06.responses.non_thinking.stream | PASS | 32697 | 37 | 512 | False |
| pro / L07.responses.non_thinking.nonstream | PASS | 130727 | 39 | 512 | False |
| pro / L07.responses.non_thinking.stream | PASS | 130727 | 39 | 512 | False |
| pro / L08.responses.non_thinking.nonstream | PASS | 523949 | 36 | 512 | False |
| pro / L08.responses.non_thinking.stream | PASS | 523949 | 41 | 512 | False |
| pro / L09.responses.non_thinking.nonstream | PASS | 56 | 1024 | 1024 | True |
| pro / L09.responses.non_thinking.stream | PASS | 56 | 1024 | 1024 | True |
| pro / L10.responses.non_thinking.nonstream | PASS | 56 | 4096 | 4096 | True |
| pro / L10.responses.non_thinking.stream | PASS | 56 | 4096 | 4096 | True |
| pro / L11.responses.non_thinking.nonstream | ERROR | unknown | unknown | 16384 | False |
| pro / L11.responses.non_thinking.stream | PASS | 56 | 16384 | 16384 | True |

## Nonpassing trials

| Suite | Model | Case | Status | Nonpassing assertions |
| --- | --- | --- | --- | --- |
| repeatability | flash | R05.chat.non_thinking.nonstream.r2 | FAIL | TASK_SUCCESS |
| repeatability | flash | R05.responses.non_thinking.nonstream.r2 | FAIL | TASK_SUCCESS |
| repeatability-expanded | flash | R25.chat.non_thinking.stream | FAIL | TASK_SUCCESS |
| repeatability-expanded | flash | R25.responses.non_thinking.nonstream | FAIL | TASK_SUCCESS |
| workflows-full | flash | W05.chat.non_thinking.nonstream | FAIL | step.0.INVALID_TOOL_ARGUMENTS, step.0.TERMINAL_STATE, step.0.TOOL_ARGUMENTS_INVALID_JSON, step.0.EXACT_CALLS, WORKFLOW_COMPLETE |
| workflows-full | flash | W05.chat.non_thinking.stream | FAIL | step.0.INVALID_TOOL_ARGUMENTS, step.0.TERMINAL_STATE, step.0.TOOL_ARGUMENTS_INVALID_JSON, step.0.EXACT_CALLS, WORKFLOW_COMPLETE |
| workflows-full | flash | W05.responses.non_thinking.nonstream | FAIL | step.0.INVALID_TOOL_ARGUMENTS, step.0.TERMINAL_STATE, step.0.TOOL_ARGUMENTS_INVALID_JSON, step.0.EXACT_CALLS, WORKFLOW_COMPLETE |
| workflows-full | flash | W05.responses.non_thinking.stream | FAIL | step.0.INVALID_TOOL_ARGUMENTS, step.0.TERMINAL_STATE, step.0.TOOL_ARGUMENTS_INVALID_JSON, step.0.EXACT_CALLS, WORKFLOW_COMPLETE |
| workflows-full | pro | W05.chat.non_thinking.nonstream | FAIL | step.0.INVALID_TOOL_ARGUMENTS, step.0.TERMINAL_STATE, step.0.TOOL_ARGUMENTS_INVALID_JSON, step.0.EXACT_CALLS, WORKFLOW_COMPLETE |
| workflows-full | pro | W05.chat.non_thinking.stream | FAIL | step.0.INVALID_TOOL_ARGUMENTS, step.0.TERMINAL_STATE, step.0.TOOL_ARGUMENTS_INVALID_JSON, step.0.EXACT_CALLS, WORKFLOW_COMPLETE |
| workflows-full | pro | W05.responses.non_thinking.nonstream | FAIL | step.0.INVALID_TOOL_ARGUMENTS, step.0.TERMINAL_STATE, step.0.TOOL_ARGUMENTS_INVALID_JSON, step.0.EXACT_CALLS, WORKFLOW_COMPLETE |
| workflows-full | pro | W05.responses.non_thinking.stream | FAIL | step.0.INVALID_TOOL_ARGUMENTS, step.0.TERMINAL_STATE, step.0.TOOL_ARGUMENTS_INVALID_JSON, step.0.EXACT_CALLS, WORKFLOW_COMPLETE |
| schemas | flash | S49.responses.non_thinking.nonstream | FAIL | SCHEMA_CAPABILITY |
| schemas | pro | S45.chat.non_thinking.nonstream | FAIL | SCHEMA_CAPABILITY |
| schemas | pro | S46.chat.non_thinking.nonstream | FAIL | SCHEMA_CAPABILITY |
| schemas | pro | S49.chat.non_thinking.nonstream | FAIL | SCHEMA_CAPABILITY |
| schemas | pro | S50.chat.non_thinking.nonstream | FAIL | SCHEMA_CAPABILITY |
| schemas | pro | S45.responses.non_thinking.nonstream | FAIL | SCHEMA_CAPABILITY |
| schemas | pro | S49.responses.non_thinking.nonstream | FAIL | SCHEMA_CAPABILITY |
| schemas | pro | S50.responses.non_thinking.nonstream | FAIL | SCHEMA_CAPABILITY |
| sizes-large | flash | L01.chat.non_thinking.nonstream | FAIL | SIZE_TASK |
| sizes-large | flash | L01.chat.non_thinking.stream | FAIL | SIZE_TASK |
| sizes-large | flash | L02.chat.non_thinking.stream | FAIL | SIZE_TASK |
| sizes-large | flash | L04.chat.non_thinking.nonstream | FAIL | SIZE_TASK |
| sizes-large | flash | L04.chat.non_thinking.stream | FAIL | SIZE_TASK |
| sizes-large | flash | L09.chat.non_thinking.nonstream | INCONCLUSIVE | OUTPUT_BOUNDARY |
| sizes-large | flash | L09.chat.non_thinking.stream | INCONCLUSIVE | OUTPUT_BOUNDARY |
| sizes-large | flash | L10.chat.non_thinking.nonstream | INCONCLUSIVE | OUTPUT_BOUNDARY |
| sizes-large | flash | L10.chat.non_thinking.stream | INCONCLUSIVE | OUTPUT_BOUNDARY |
| sizes-large | flash | L11.chat.non_thinking.nonstream | ERROR | TRANSPORT_OR_RUNNER_ERROR, SIZE_CAPABILITY |
| sizes-large | flash | L11.chat.non_thinking.stream | INCONCLUSIVE | OUTPUT_BOUNDARY |
| sizes-large | flash | L02.responses.non_thinking.nonstream | FAIL | SIZE_TASK |
| sizes-large | flash | L03.responses.non_thinking.nonstream | FAIL | SIZE_TASK |
| sizes-large | flash | L03.responses.non_thinking.stream | FAIL | SIZE_TASK |
| sizes-large | flash | L04.responses.non_thinking.nonstream | FAIL | SIZE_TASK |
| sizes-large | flash | L04.responses.non_thinking.stream | FAIL | SIZE_TASK |
| sizes-large | flash | L11.responses.non_thinking.nonstream | ERROR | TRANSPORT_OR_RUNNER_ERROR, SIZE_CAPABILITY |
| sizes-large | pro | L01.chat.non_thinking.nonstream | FAIL | SIZE_TASK |
| sizes-large | pro | L01.chat.non_thinking.stream | FAIL | SIZE_TASK |
| sizes-large | pro | L02.chat.non_thinking.nonstream | FAIL | SIZE_TASK |
| sizes-large | pro | L03.chat.non_thinking.nonstream | FAIL | SIZE_TASK |
| sizes-large | pro | L03.chat.non_thinking.stream | FAIL | SIZE_TASK |
| sizes-large | pro | L04.chat.non_thinking.nonstream | ERROR | TRANSPORT_OR_RUNNER_ERROR, SIZE_CAPABILITY |
| sizes-large | pro | L04.chat.non_thinking.stream | FAIL | SIZE_TASK |
| sizes-large | pro | L07.chat.non_thinking.stream | FAIL | SIZE_TASK |
| sizes-large | pro | L08.chat.non_thinking.nonstream | ERROR | TRANSPORT_OR_RUNNER_ERROR, SIZE_CAPABILITY |
| sizes-large | pro | L09.chat.non_thinking.nonstream | INCONCLUSIVE | OUTPUT_BOUNDARY |
| sizes-large | pro | L09.chat.non_thinking.stream | INCONCLUSIVE | OUTPUT_BOUNDARY |
| sizes-large | pro | L10.chat.non_thinking.nonstream | INCONCLUSIVE | OUTPUT_BOUNDARY |
| sizes-large | pro | L10.chat.non_thinking.stream | INCONCLUSIVE | OUTPUT_BOUNDARY |
| sizes-large | pro | L11.chat.non_thinking.nonstream | ERROR | TRANSPORT_OR_RUNNER_ERROR, SIZE_CAPABILITY |
| sizes-large | pro | L11.chat.non_thinking.stream | INCONCLUSIVE | OUTPUT_BOUNDARY |
| sizes-large | pro | L01.responses.non_thinking.nonstream | FAIL | SIZE_TASK |
| sizes-large | pro | L01.responses.non_thinking.stream | FAIL | SIZE_TASK |
| sizes-large | pro | L02.responses.non_thinking.nonstream | FAIL | SIZE_TASK |
| sizes-large | pro | L02.responses.non_thinking.stream | FAIL | SIZE_TASK |
| sizes-large | pro | L03.responses.non_thinking.nonstream | FAIL | SIZE_TASK |
| sizes-large | pro | L03.responses.non_thinking.stream | FAIL | SIZE_TASK |
| sizes-large | pro | L04.responses.non_thinking.nonstream | FAIL | SIZE_TASK |
| sizes-large | pro | L04.responses.non_thinking.stream | FAIL | SIZE_TASK |
| sizes-large | pro | L05.responses.non_thinking.nonstream | FAIL | SIZE_TASK |
| sizes-large | pro | L05.responses.non_thinking.stream | FAIL | SIZE_TASK |
| sizes-large | pro | L11.responses.non_thinking.nonstream | ERROR | TRANSPORT_OR_RUNNER_ERROR, SIZE_CAPABILITY |
| schema-beta-confirmation | pro | S46.chat.non_thinking.nonstream | FAIL | SCHEMA_CAPABILITY |
| schema-beta-confirmation | pro | S50.chat.non_thinking.nonstream | FAIL | SCHEMA_CAPABILITY |
| size-timeout-confirmation | flash | L11.chat.non_thinking.nonstream | INCONCLUSIVE | OUTPUT_BOUNDARY |
| size-timeout-confirmation | pro | L11.chat.non_thinking.nonstream | INCONCLUSIVE | OUTPUT_BOUNDARY |

## Verification and provenance

- Live start: `2026-09-22T23:00:05.129308+00:00`; report generated: `2026-09-23T00:08:49.210812+00:00`.
- One request at a time; zero automatic retries; hard request ceiling 2,112. Targeted workflow, Beta schema, and timeout confirmations use unused capacity within that ceiling.
- Initial live runner: `3d93a6f0fbc54e66f33d5d58db9ee4fdfea1c347`. All four confirmation phases and corrected offline scoring used `1233f85c8cf980e66f3cec128d138464037e3adb`.
- All 2,068 saved observations reassembled identically from verified captures. Original evidence files were hash-checked and left unchanged.
- Corrected offline scoring changed 10 trial statuses; this rescoring sent zero additional API requests. Historical failures and errors remain in the totals even when a later confirmation succeeds.
- The run used the repository CLI execution path. The installed wheel was checked separately using eight local synthetic requests.
- Credential remained in memory; raw evidence is retained only in the ignored local run directory.
- HTTP status counts include received headers from interrupted reads; HTTP 200 alone does not mean an exchange completed successfully.

## Scope and limits

- Core repeatability: 20 prompts, five repetitions, both models/APIs; non-thinking, non-streaming. Expanded variants add thinking and streaming, with one observation per variant.
- Workflows: 4/8/16 tool rounds, 4/16 parallel calls, branching histories, and accumulated state; both modes and streaming settings.
- Schemas: 16 fixture features, tool/output targets, strict omitted/true. The main matrix used the standard URL; four follow-ups used the documented Beta Chat strict-tools route. Acceptance of one valid example does not prove constrained-decoding enforcement.
- Size fixtures: 16/64/256/1,024 KiB authored inputs and 1,024/4,096/16,384-token output caps. These do not establish the advertised maximum context or output capacity.
- Five repetitions provide weak failure-rate estimates. Confidence intervals are per prompt/variant and assume independent trials; different prompts are not pooled into a confidence interval.
- Returned model labels are mutable aliases; these requests do not authenticate model weights or a release identity.
- This run covers the new depth suites. It does not add live load/recovery, parameter-effect, vision/files, or manual GitHub-workflow coverage.

Official references: [Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/), [Responses](https://api-docs.deepseek.com/api/create-response/). Findings above are from the captured live run.
