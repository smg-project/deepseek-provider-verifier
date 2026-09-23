# Bounded core selection — 2026-09-23

The default entry point retains controls that passed consistently in prior observations. The new `deepseek-core-2026-09-23-v1` profile selects 16 unchanged fixture templates from the completed official campaign. It uses `strict-contract-v1`: every selected facet and original raw assertion is mandatory.

This is a retrospective selection record, **not an independent fresh live result**. Consistent observations over this small sample do not guarantee future reliability. Historical broad stress and schema failures remain available in the [original report](official-acceptance-2026-09-23.md).

| Retained group | Templates |
| --- | --- |
| API, streaming, thinking controls, tools and history | C01, C03, C05, C07, C11, C13, C14 |
| Fixture lookup and arithmetic continuation | R26, R27, R31, R32 |
| Versioned exact structured output | R41, R42 |
| Four-round tool workflow | W01 |
| Shallow schema, including the selected strict-tool control | S01, S02 |

Both APIs, thinking modes and streaming modes are covered where the retained controls support them. A single endpoint plans **84 trials, at most 142 requests and 302,080 output tokens**, serially with zero retries. No selected trial is a predetermined unsupported skip.

Large input/output probes, deeper schema cases, exact-word quality prompts, larger parallel tools and additional reasoning probes are omitted from this default. They remain explicit extensions under `self-hosted-verify.example.toml` or the depth examples. No excluded capability is certified by a core PASS.

| Source phase | Selected raw PASS | Selected facets PASS |
| --- | ---: | ---: |
| round-1 | 168 | 496 |
| round-2 | 168 | 496 |
| round-3 | 168 | 496 |
| full-stress | 168 | 496 |
| full-stress-pro-confirmation | 84 | 248 |

Each selected observation and facet passed. The full original run can still be FAIL or INCONCLUSIVE because it also contains excluded cases. Source hashes were checked before extracting these counts; the source reports were not rewritten.

The new inventory is covered by tests that preserve each original case definition and reject wrong values, extra fields, malformed output, unfinished streams, authentication errors and invalid shallow strict-schema arguments. The same checks apply to any configured endpoint.

The README and manual workflow default to the core. The workflow continues to use canonical strict execution and exports only its existing content-free summary; its official/candidate credential routing remains separate.

[Machine-readable selection record](core-selection-2026-09-23.json).
