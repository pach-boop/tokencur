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
| Claude Code | a message streamed over two lines with a partial first count; a resumed session re-copying a message; a `<synthetic>` stub; the old cache format without a TTL breakdown; an unpriced model; a malformed line |
| Codex CLI | a rate-limits-only event; a report re-sent with the running total unchanged; a model switch mid-session; a report with no billable tokens; a malformed line |
| Kimi Code | per-turn records; a cumulative `session` record that must not count; a non-usage line; a malformed line |

`golden/` holds what tokencur must produce from them: `records.json`
(every parsed record) and `focus.csv` (the FOCUS export).
`tests/test_golden.py` compares both byte for byte, and
`scripts/validate_focus.py` runs the FinOps Foundation's validator on the
same export. After an intended change, regenerate and review the diff:

```bash
TOKENCUR_UPDATE_GOLDEN=1 pytest tests/test_golden.py
```
