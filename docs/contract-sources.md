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

A bounded [Chat reference capture](sources/deepseek-chat-2026-09-21.md)
retains the source URL, retrieval time, full-page hash, selected excerpt, and
excerpt hash without committing the third-party page.

The Chat documentation places aggregate token counts on its terminal content
event and narrows tool-choice behavior in thinking mode. The Responses guide
describes the API as stateless and lists accepted fields that are ignored.
These are recorded as documented distinctions in diagnostic rules. They are
not release gates until live calibration is authorized and completed.
