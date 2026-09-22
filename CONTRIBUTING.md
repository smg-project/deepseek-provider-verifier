# Contributing

This is a community-owned MIT-licensed project. Contributions should keep verification claims narrow, reproducible, and traceable to stored evidence.

Use Python 3.11 or newer and install the locked development environment with `uv sync`. Add runtime behavior test-first: run the focused test and observe the expected failure, implement the smallest change, then rerun it. Before committing, run:

```sh
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```

New contract rules must name a source URL, source section, retrieval date, evidence status, maturity, and gating decision. Keep provider documentation separate from project policy and synthetic fixture behavior. Do not promote a diagnostic rule to a required gate without captured calibration evidence for the exact model and workload scope.

Do not commit API keys, `.env` files, raw live-run evidence, or generated `runs/` directories. Never place credentials in command-line arguments. Tests that exercise HTTP behavior must use local fixture servers. Preserve request, retry, concurrency, conversation, deadline, and output-token bounds; a partial or budget-exhausted run must remain incomplete.

Reports derive from `summary.json`. Changes to report formatting must preserve every status count, enabled gates, endpoint/model/profile/dataset metadata, dates, and safe evidence links across JSON, Markdown, and JUnit. Escape provider-controlled Markdown and XML, and keep prompts and reasoning traces out of default summaries.
