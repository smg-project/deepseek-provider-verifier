# Contract sources and calibrated scope

The `deepseek-api-2026-09-21` profile records proposed assertions from the official
DeepSeek API documentation. It remains entirely diagnostic and non-gating. The
pages were retrieved on 2026-09-22 UTC (2026-09-21 America/Los_Angeles), with
SHA-256 provenance in each applicable rule and the [source capture index in the
sanitized calibration record](calibration/official-flash-2026-09-21.json).
Captured documentation is a research input; it is not live provider evidence.

| Source | Official URL | Scope |
| --- | --- | --- |
| Chat Completions reference | <https://api-docs.deepseek.com/api/create-chat-completion/> | Chat request, response, streaming, usage, and tool-choice fields |
| Responses reference | <https://api-docs.deepseek.com/api/create-response/> | Responses request, response items, and typed streaming events |
| Responses guide | <https://api-docs.deepseek.com/guides/responses_api/> | Stateless operation, ignored options, and tool-call behavior |
| Thinking mode guide | <https://api-docs.deepseek.com/guides/thinking_mode/> | Reasoning mode and conversation replay requirements |
| Tool calls guide | <https://api-docs.deepseek.com/guides/tool_calls/> | Function tool request and response structure |

The bounded [Chat reference capture](sources/deepseek-chat-2026-09-21.md) retains
the source URL, retrieval time, full-page hash, selected excerpt, and excerpt hash
without committing the third-party page. Full third-party captures and raw live
journals remain local. Original fixture policies are separately labeled
`project-policy`; official source claims remain `documented` even when their
specific observed variants become calibrated.

## Official Flash smoke calibration, revision 1

`deepseek-flash-smoke-2026-09-21-v1` promotes 19 rules only for the exact 24 smoke
variants recorded in its `conditions.calibrated_variants`, and only when the
endpoint's `contract_model` (or served model if unset) is `deepseek-flash`.
Unmatched variants and models downgrade to diagnostic, preserving measurements
and keeping required trials INCONCLUSIVE even if another generic gate matches.
Only a trailing repetition suffix `.r<integer>` is removed when matching variants.
A present scope must be a nonempty list of unique nonempty strings. Profiles
without this optional scope retain their existing semantics.

The official run used reviewed runner
`bc3a9cce705aa53161bfbf1037be397b5d05a7e0` between **2026-09-21 20:09:48 and
20:10:17 America/Los_Angeles**. Its 30 requests all returned HTTP 200; all 197
measured assertions across 24 trials passed. Those original results remain
INCONCLUSIVE under their original diagnostic profile; promotion does not rewrite
history. The sanitized record includes the exact rule-to-variant mapping,
settings, available response model/time metadata, source hashes, original
manifest/profile/dataset hashes and six original artifact hashes. The original
credential scan was clean, including decoded persisted bodies.

The new profile's assertions are bounded by the observed workload:

- Chat usage placement was observed with `stream_options.include_usage` **omitted**.
  Explicit `true`/`false` branches and C24 remain uncalibrated.
- Responses stateless calibration supports the tested full-history tool replay
  with `store=false`. It does not demonstrate that `store` or
  `previous_response_id` are unsupported; those probes remain diagnostic.
- No reasoning-effort-strength behavioral claim was calibrated.
- Only C01/C03/C05/C07/C11/C13/C14 smoke variants were requested, with
  C02/C17/C20 attached assertions. The complete C01–C24 matrix, V4 Pro, and
  candidate/direct-engine/SMG deployments were not exercised.

The immutable model checkpoint remains `unknown`. API observations support this
captured alias and scope without authenticating weights or enabling
quality-equivalence gates. Candidate alias mappings are operator declarations,
not proof of model identity. Final installed-CLI live confirmation is separate from this original calibration.
The [dated CLI addendum](calibration/official-flash-cli-confirmation-2026-09-21.json)
records `8b12f73` at 22:35:53–22:35:59 PDT on September 21: four HTTP 200
responses, four PASS trials, 23 passing assertions, exit 0 and verified integrity.
This makes 34 session requests without changing the original calibration.

Final review tightened Chat streaming envelope/identity and initial assistant-role
checks, and applied the existing Responses envelope requirement to its terminal
snapshot. The captured Chat response schema documents required chunk ID, object,
created timestamp and model; IDs and timestamps stay constant. Continuation role
may be omitted or null (as in the documented final chunk). Additive fields are
retained. These scorer changes postdate both live revisions and are verified only
offline, including exact-request replay of both original captures. Neither live
run is a current-version quality baseline.


## Official two-model catalog calibration, September 22

`deepseek-official-full-2026-09-22-v1` adds a separate profile for
`deepseek-flash` and `deepseek-v4-pro`. It promotes 41 rules within enumerated
variants of the shipped C01–C24 matrix. The earlier diagnostic and Flash smoke
profiles and their source evidence remain unchanged. Model aliases remain
mutable and releases unknown; these observations do not authenticate weights.

The official run used merged revision `a97566f0aac6baec3c83e3187dd4c5b98e1b4e0c`
on September 22, approximately 10:01–10:19 America/Los_Angeles, with the user's
explicit authorization to run outside the usual window for that task. It sent
696 inference requests (365 Flash, 331 Pro): 582 HTTP 200, 105 HTTP 400, and nine
HTTP 422, with no transport or server errors. Three separate metadata requests
listed models and created/deleted only the tiny test fixture file. Original
live outcomes have not been rescored in place or presented as all passing.

Five corrections follow those observations:

- Responses C06 now sends typed properties with single-value enums. Both
  models accepted this exact schema in the follow-up capture.
- Chat C24 expects nonstream `stream_options` to be rejected with 4xx.
- Responses C08/C09 expect forced tool-choice rejection only in thinking mode;
  non-thinking cases still require valid calls. This is an observed project-policy
  expectation, not a new claim that the Responses documentation mandates it.
- C15 records reasoning-omission acceptance or rejection as diagnostic. Successful
  setup and an actual omission remain prerequisites. It cannot become PASS merely
  because a caller supplies a calibrated rule.
- C04/C23 explicitly request the lowercase color word without punctuation,
  matching their existing exact-answer oracle.

Offline verification used 198 saved responses, with every generated request
matched exactly to its original JSON payload, including multi-turn history.
C04 on both protocols, Responses C06, and Responses C23 use the corrected
`conversations` captures; other variants use `catalog` captures. The resulting
172 trials contain 154 PASS, 14 INCONCLUSIVE and four SKIP, with 786 passing and
20 inconclusive assertions, zero failed assertions and no execution errors.
This verification made **zero live requests**. Original source artifact hashes,
per-trial request/evidence hashes, and exact rule coverage are in the
[content-free calibration record](calibration/official-both-models-2026-09-22.json).

The profile intentionally keeps C10, C15, and Responses C24 diagnostic. Required
diagnostics preserve full-suite exit 2; no inference-quality gate is enabled.
Chat C06/C23 remain explicitly inapplicable. Additional probes exposed ambiguous
JSON formatting, three output-budget exhaustions, four user-field validation
differences, two remaining Pro instruction-following mismatches, and no reliable
image understanding. Clarified follow-ups resolved the ambiguous formatting and
tool-prohibition prompts; they do not erase the original observations. These
supplementary probes are outside this profile's calibrated scope.

An evidence holder can reproduce the correction without credentials or network:

```sh
uv run python scripts/replay_official_calibration.py \
  --evidence-root runs/official-full-20260922
```

The script validates the original journals and committed artifact hashes, uses
only an HTTPX mock transport, rejects any request/result mismatch, and checks
that source files are unchanged afterward. Script success means the stored
calibration was reproduced; its reported verifier exit code remains 2. Raw
captures stay private and are not included in the repository or release.
