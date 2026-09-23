# Official strict core confirmation — 2026-09-23

The frozen core returned **PASS** through the installed wheel against both official aliases. All selected original assertions and all 496 facets are mandatory under `strict-contract-v1`; none are report-only diagnostics.

| Official model | Raw trials | Required facets | Requests |
| --- | --- | --- | ---: |
| `deepseek-flash` | 84 PASS | 248 PASS | 138 |
| `deepseek-v4-pro` | 84 PASS | 248 PASS | 138 |

**276 actual requests** of a 284-request ceiling; planned output-token ceiling 604,160. Serial execution, zero retries, one fresh confirmation round. This is a separate run after freezing the core selection; it does not replace earlier campaigns.

## Selected coverage

Sixteen unchanged templates cover ordinary API responses, stream completion, thinking controls, tools, history, fixture lookup/arithmetic, exact structured output, a four-round tool workflow, and shallow schema including the retained strict-tool control. Chat and Responses, thinking/non-thinking, and streaming/nonstreaming variants are included where each selected fixture supports them. No trial is a predetermined unsupported skip.

Large-input/output stress, advanced schemas, larger parallel tools, exact-word quality prompts and extra reasoning measurements remain explicit extensions. Their earlier failures remain in the [expanded campaign report](official-acceptance-2026-09-23.md). A core PASS does not certify these excluded capabilities.

The inventory was selected retrospectively from earlier passing observations, documented in the [selection record](core-selection-2026-09-23.md). This new round provides one additional observation per selected variant; it is not a production reliability estimate or a guarantee of future success. The same selected requests and strict policy apply to self-hosted endpoints.

## Verification and provenance

- Frozen source revision: `5678bb36550d1fdd980e28943ac28b0295a2cc91`.
- The installed-wheel CLI ran outside the checkout against `https://api.deepseek.com`.
- Hash-verified offline assessment reproduced JSON, Markdown and JUnit acceptance reports byte-for-byte; source evidence was unchanged.
- Offline validation: 1,003 tests passed; lint, formatting, wheel/sdist and installed release checks passed.
- No self-hosted deployment or manual GitHub live workflow was executed. No live load, cancellation, retries or recovery test is claimed.
- Cumulative task traffic including the retained campaign: 5,336 requests within the 5,524 planned ceiling and 6,000-request operational cap.

| Frozen input | SHA256 |
| --- | --- |
| scorer_revision | `sha256:e6f11609db3796d7728fc82f11166bba19435997f0a1cca713ca3029bc3f9313` |
| policy_hash | `4f5e78ea097ac9cf501fca34ad205585155fcfcda2ccc392288d8012f7585248` |
| manifest_hash | `e3eb77623d798ad516d861af8b35a84246fd17dbe97650a65340441aa1473743` |
| dataset_hash | `f328d273e24116c5da1eb47ee42d1870021943489d517e3a66b9bd2b04651728` |
| profile_hash | `b4cff497fff328fdcd477257fd2933621d355e3a4946f16dacf7768d72803cc0` |
| wheel_sha256 | `7e90a460a63a7894a706c5b7876e5c2ac12f9931bea0dc80adca5f915926b8fc` |

The [sanitized JSON record](official-core-2026-09-23.json) includes source artifact hashes and per-model counts. Raw prompts, responses, reasoning and credentials are excluded.
