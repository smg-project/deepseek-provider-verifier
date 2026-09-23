# Official compatibility verification — 2026-09-23 UTC

Frozen revision: `b0e7d5a9e70a6eddb7846076aeca05f38024ba96`. Both official aliases were called through the installed wheel; the same policy applies to self-hosted endpoints.

| Phase | Compatibility | Strict assessment | Raw PASS / FAIL / ERROR / INCONCLUSIVE / SKIP | Requests |
| --- | --- | --- | --- | ---: |
| round-1 | PASS | INCONCLUSIVE | 243 / 13 / 0 / 4 / 4 | 408 |
| round-2 | PASS | INCONCLUSIVE | 239 / 17 / 0 / 4 / 4 | 408 |
| round-3 | PASS | INCONCLUSIVE | 242 / 13 / 0 / 5 / 4 | 408 |
| full-stress | INCONCLUSIVE | INCONCLUSIVE | 822 / 37 / 1 / 12 / 64 | 1736 |
| beta-schema | PASS | FAIL | 4 / 2 / 0 / 0 / 0 | 6 |
| output-confirmation | PASS | PASS | 2 / 0 / 0 / 0 / 0 | 2 |
| full-stress-pro-confirmation | FAIL | INCONCLUSIVE | 406 / 24 / 0 / 6 / 32 | 868 |

Frozen-version verification and confirmations: **3,836 requests**. Including all retained calibration: **5,060 requests**, within the 5,240 planned ceiling and 6,000-request operational cap. Serial execution; zero retries. Combined planned output-token ceiling: 9,786,368.

## Required checks and diagnostics

Required checks cover valid HTTP/envelopes/stream completion, exact tools and results, complete conversation history, shallow structured output, exact retrieval facts, and measured total output-budget exercise. Mandatory errors or missing evidence cannot pass. Hostname/model labels do not grant exceptions.

Raw strict failures remain visible. Exact-word instruction quality, advanced schemas, plain-text retrieval formatting, and absent reasoning/visible-token measurements are separate diagnostics. Strict assessment requires these facets and can fail or remain inconclusive. Unknown is never reported as supported.

| Model / phase | Required PASS / FAIL / ERROR / INCONCLUSIVE / SKIP | Diagnostic PASS / FAIL / ERROR / INCONCLUSIVE / SKIP |
| --- | --- | --- |
| deepseek-flash / round-1 | 230 / 0 / 0 / 0 / 2 | 170 / 2 / 0 / 4 / 4 |
| deepseek-v4-pro / round-1 | 230 / 0 / 0 / 0 / 2 | 148 / 24 / 0 / 4 / 4 |
| deepseek-flash / round-2 | 230 / 0 / 0 / 0 / 2 | 164 / 8 / 0 / 4 / 4 |
| deepseek-v4-pro / round-2 | 230 / 0 / 0 / 0 / 2 | 146 / 26 / 0 / 4 / 4 |
| deepseek-flash / round-3 | 230 / 0 / 0 / 0 / 2 | 166 / 4 / 0 / 6 / 4 |
| deepseek-v4-pro / round-3 | 230 / 0 / 0 / 0 / 2 | 150 / 22 / 0 / 4 / 4 |
| deepseek-flash / full-stress | 709 / 0 / 0 / 0 / 32 | 632 / 31 / 0 / 12 / 64 |
| deepseek-v4-pro / full-stress | 706 / 2 / 1 / 0 / 32 | 608 / 53 / 1 / 13 / 64 |
| deepseek-flash / beta-schema | 4 / 0 / 0 / 0 / 0 | 3 / 2 / 0 / 0 / 0 |
| deepseek-v4-pro / beta-schema | 4 / 0 / 0 / 0 / 0 | 3 / 2 / 0 / 0 / 0 |
| deepseek-v4-pro / output-confirmation | 6 / 0 / 0 / 0 / 0 | 4 / 0 / 0 / 0 / 0 |
| deepseek-v4-pro / full-stress-pro-confirmation | 708 / 1 / 0 / 0 / 32 | 610 / 53 / 0 / 12 / 64 |

Beta nested strict-schema probes still recorded capability failures for **deepseek-flash, deepseek-v4-pro**. The affected requests returned HTTP 200, with tool arguments failing the declared nested schema. Default compatibility PASS does **not** certify those advanced schema features. The [official Tool Calls guide](https://api-docs.deepseek.com/guides/tool_calls/) describes strict tool schemas on the `/beta` route; these explicit-route observations and strict assessment preserve the discrepancy.

## Retained execution timeout and confirmations

The original two-model full stress run remains **INCONCLUSIVE**: Pro `L10.responses.non_thinking.nonstream` hit the 600-second case deadline with a requested 4,096-token output budget. No completed HTTP response evidence was retained, so this establishes a request completion failure within the configured limit, not its internal provider/network cause. The resulting protocol ERROR and dependent functional/budget failures belong to this one timed-out request.

All three default rounds passed. Flash completed all 709 applicable required stress checks. The original Pro run completed its other 706 required checks successfully; the timed-out case could not certify its three required facets.

The unchanged installed scorer and controls were then used for one explicitly bounded two-request Pro 4K confirmation, followed by exactly one complete Pro stress confirmation if the first succeeded. These were declared before calls and have separate manifests and evidence. They do not replace the failed run or establish an unbiased production failure rate.

Confirmation outcomes: `output-confirmation` **PASS**, `full-stress-pro-confirmation` **FAIL**.

## Outstanding full-stress failure

The complete Pro confirmation did **not** pass. In `L06.chat.non_thinking.stream`, the 64 KiB multi-turn retrieval case received HTTP 200 and a completed stream, but the final answer acknowledged the archive instead of returning the three explicitly requested facts. All earlier acknowledgments and conversation replay passed. This is a genuine missing functional answer under the unchanged control, not a parser or terminal-state error.

The required retrieval facet remains FAIL. The three default rounds still pass, but the goal of an all-green full stress run is **unmet**. No additional repetition or weaker threshold was used to erase this finding. Reviewers can assess the implementation with this external behavior explicitly unresolved.

| Supplemental blocking case | Required facet | Status |
| --- | --- | --- |
| `L06.chat.non_thinking.stream` | retrieval | FAIL |

## Retained calibration and fixes

| Revision | Requests | Acceptance | Finding and action |
| --- | ---: | --- | --- |
| `62e4a76` | 408 | FAIL | Ordinary stream completion incorrectly applied to deliberate length stops; Responses replay evaluated one step at a time. Scoped completion rules and restored whole-conversation protocol checks. |
| `8385d2f` | 408 | INCONCLUSIVE | Successful thinking workflow emitted fewer than two nonempty reasoning rounds. Separated reasoning measurability from exact functional/history validation. |
| `8308288` | 408 | FAIL | Flash emitted an extra field in a prose-only JSON-mode control. Preserved the failure and old prompt; added versioned literal-JSON example controls with the same exact schema/value requirements. |

The final three rounds all use the frozen version above. No earlier successful round was retained after a change. The [official JSON Output guide](https://api-docs.deepseek.com/guides/json_mode/) calls for an example of the desired JSON format. New `depth-v2` controls R41/R42 follow that guidance and still reject extra fields, incorrect values and malformed JSON. Old `depth-v1` Chat prose-only controls remain in full stress as quality diagnostics; Responses native strict-schema controls remain mandatory.

## Reproducibility and boundaries

- Each live run was reassessed offline through the same installed wheel. All three default acceptance artifacts are byte-identical; canonical source artifacts are unchanged.
- The companion JSON records per-phase manifest/dataset/scorer/policy hashes, source artifact hashes, raw counts, mandatory failures and strict findings without response bodies or credentials.
- The earlier 2,068-request campaign was replayed read-only. Its six original timeout errors remain unresolved in those historical records; new observations never rewrite them.
- Offline validation: 969 tests, lint/format checks, and wheel/sdist installed CLI verification. CI covers Python 3.11 and 3.14. Independent review findings received regression tests and fixes.
- Selected stress limits reach 16-round/16-parallel workflows, 1 MiB authored inputs and 16K output-token budgets; this does not establish model maximum capacity.
- Three default sessions are a small sample. This is no model identity or production reliability guarantee. No self-hosted endpoint, manual GitHub live workflow, or live recovery/load campaign was run.

## Frozen provenance

- Policy: `a5c2d0844e64188aefc626d8ea1ff5b3d799073469865fb83c10da5088a9fe5c`
- Scorer: `sha256:e6f11609db3796d7728fc82f11166bba19435997f0a1cca713ca3029bc3f9313`
- Live wheel SHA256: `947696e9e4ebce951ff00251f981f3451959602aa996bbe44677e73173180ac8`

Detailed evidence: [sanitized JSON](official-acceptance-2026-09-23.json).
