# 0006. Read metadata only, depend on nothing at runtime

- Status: Accepted
- Date: 2026-07-06

## Context

Agent logs hold whole conversations: source code, secrets pasted by
mistake, private thinking. A cost tool needs none of it. Every runtime
dependency is also supply-chain surface in a tool that reads a
developer's home directory.

## Decision

- Ingesters read usage metadata only: token counts, model, timestamps,
  workspace and session ids. Message content is never parsed into a
  record, stored or logged. Test fixtures carry `[redacted]` content,
  and a test asserts that no workspace, session or content string reaches
  the public observatory page.
- The core is the standard library only (`json`, `sqlite3`, `csv`,
  `pathlib`, `argparse`). Optional extras, such as the Streamlit
  dashboard, may depend on more; the core may not.

## Consequences

- `pip install tokencur` pulls nothing else, and installs on any
  Python 3.11+ (tested on Linux, macOS and Windows).
- Some conveniences are written by hand (the terminal table, the HTML
  pages), kept small and tested.
- Features that would need content, such as cost per task inferred from
  prompts, are out of scope by design.
