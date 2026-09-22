# Contract sources

The `deepseek-api-2026-09-21` profile records proposed assertions from the
official DeepSeek API documentation. The pages were retrieved on 2026-09-22
UTC (2026-09-21 America/Los_Angeles) and are identified by SHA-256 in the
profile. The captured pages are research inputs, not live provider evidence.

All initial rules remain `diagnostic` and non-gating until they are calibrated
against an explicitly identified model release. A successful HTTP response
cannot promote a diagnostic rule to a verified requirement.

| Source | Official URL | Scope |
| --- | --- | --- |
| Chat Completions reference | <https://api-docs.deepseek.com/api/create-chat-completion/> | Chat request, response, streaming, usage, and tool-choice fields |
| Responses reference | <https://api-docs.deepseek.com/api/create-response/> | Responses request, response items, and typed streaming events |
| Responses guide | <https://api-docs.deepseek.com/guides/responses_api/> | Stateless operation, ignored options, and tool-call behavior |
| Thinking mode guide | <https://api-docs.deepseek.com/guides/thinking_mode/> | Reasoning mode and conversation replay requirements |
| Tool calls guide | <https://api-docs.deepseek.com/guides/tool_calls/> | Function tool request and response structure |

The Chat documentation says usage is included in the final content chunk with
`finish_reason` rather than a separate usage-only chunk. It also describes
required and named tool choice as unsupported in thinking mode. The Responses
guide describes the API as stateless and lists accepted fields that are
ignored. These are recorded as rule-level diagnostic conflicts in the profile;
they are not release gates until live calibration is authorized and completed.
