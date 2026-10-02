# 0005. Reconcile with a source's own counters wherever it keeps them

- Status: Accepted
- Date: 2026-10-02

## Context

The Codex ingester counted every `token_count` event, guarding only
against consecutive identical reports. A review flagged that dedup as
weaker than Claude Code's. Codex writes, in every event, the session's
running `total_token_usage`; summing what tokencur counted and comparing
it with that total showed the size of the problem: in 85 of 94 rollouts
the count was about twice Codex's own. Codex re-sends each report under
a new timestamp, alongside rate-limit updates. Codex usage value had
been published at $685.66; the reconciled figure is $332.95.

## Decision

When a source keeps its own cumulative counter, tokencur's per-event
counting must reconcile with it, and a test pins that reconciliation.

- Codex: a call counts only when the running total moves; a report with
  no billable tokens is no call. All 94 rollouts now reconcile exactly
  on input, cached input and output, and a property test checks it under
  random re-sends.
- Claude Code keeps no running total. Its rule is the next best
  invariant: streamed counts only grow, so each field takes its largest
  value across a message's lines.
- Kimi Code's cumulative `session` records are never counted alongside
  per-turn ones.

## Consequences

- An ingester change that breaks reconciliation fails a test, not a
  published number.
- Published figures that included Codex overstated it, and the changelog
  says so; the ledger's stored re-sends were retired through ADR 0004.
- A source with neither ids nor counters can only be guarded by
  heuristics; such limits are stated in the README.
