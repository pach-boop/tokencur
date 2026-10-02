# Test fixtures

## focus_sample_official_slice.csv

First 30 rows of `FOCUS-1.0/focus_sample.csv` from the FinOps Foundation's
[FOCUS-Sample-Data](https://github.com/FinOps-Open-Cost-and-Usage-Spec/FOCUS-Sample-Data)
repository, © FinOps Foundation, licensed under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Unmodified except for
truncation.

Used by `tests/test_focus_cross.py` to cross-check tokencur's export conventions
against an official FOCUS dataset (note: the sample targets FOCUS 1.0; tokencur
exports 1.2, which adds columns such as `InvoiceId` and `ServiceSubcategory`).

## logs/ and golden/

`logs/` holds synthetic agent logs with the key structure each agent's
real logs had in 2026-10 (Claude Code transcripts, Codex rollouts, Kimi
Code wire logs). Every value is invented and every content field reads
`[redacted]`: tokencur never needs content, and fixtures never carry it.
The files collect the cases that broke, or nearly broke, an ingester:

| Source | Cases |
|---|---|
| Claude Code | a message streamed over two lines with a partial first count; a resumed session re-copying a message; a `<synthetic>` stub; the old cache format without a TTL breakdown; a fast-mode call with US-only inference; an unpriced model; a malformed line |
| Codex CLI | a rate-limits-only event; a report re-sent with the running total unchanged; a model switch mid-session; a report with no billable tokens; a malformed line |
| Kimi Code | per-turn records; a cumulative `session` record that must not count; a non-usage line; a malformed line; a `state.json` naming the session's directory |

`golden/` holds what tokencur must produce from them: `records.json`
(every parsed record) and `focus.csv` (the FOCUS export).
`tests/test_golden.py` compares both byte for byte, and
`scripts/validate_focus.py` runs the FinOps Foundation's validator on the
same export. After an intended change, regenerate and review the diff:

```bash
TOKENCUR_UPDATE_GOLDEN=1 pytest tests/test_golden.py
```

## logs-real/

Real logs of the maintainer's agents, made safe to publish by
`scripts/redact_log.py`: from the lines that carry usage it keeps only
an allowlist (usage numbers, model, request options, line types, agent
version, working directory), turns every id into a stable pseudonym, every
path into `/home/dev/<pseudonym>`, shifts timestamps (intervals kept) and
drops all content. The script refuses to write unless the ingester reads
exactly the same usage from the redacted file as from the original, and
`tests/test_fixture_privacy.py` fails on any string that is not an
allowed shape.

| Source | Agent version | Calls |
|---|---|---|
| Claude Code | 2.1.282 (streamed messages, 1h cache writes, thinking tokens) | 24 |
| Codex CLI | 0.98.0 and 0.104.0 (re-sent reports) | 8 + 4 |
| Kimi Code | current wire format | 17 |

Their golden outputs are `golden/records-real.json` and
`golden/focus-real.csv`. To refresh one from a newer agent version:

```bash
python scripts/redact_log.py codex ~/.codex/sessions/.../rollout-X.jsonl \
  tests/fixtures/logs-real/codex/2026/09/17/rollout-real.jsonl --start 2026-09-17T10:00:00Z
TOKENCUR_UPDATE_GOLDEN=1 pytest tests/test_golden.py
```
