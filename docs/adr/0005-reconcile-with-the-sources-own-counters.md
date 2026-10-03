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

## Update, 2026-10-02: Claude Code does keep counters

The decision above says Claude Code keeps no running total. It does,
though not per call. When a session ends, Claude Code writes a
`cost-state` line with the tokens and cost it counted for each model; a
session continued in another hands its counters on, through a
`continued-in` line. `tokencur doctor` now compares those counters with
tokencur's value of the same chain of sessions.

On the maintainer's 12 closed chains, tokencur sees 93.6% of the cost
Claude Code counted: $331.50 against $354.32. The rest is in calls the
transcripts never record. $3.12 went to internal calls to a smaller
model, for web search, web fetch and session titles, and $19.70 to
main-model calls such as compaction. This is not a parser bug to fix,
like the Codex re-sends were: the calls are simply not in the
transcripts. tokencur's Claude Code value is a slight underestimate,
and the README says so. `doctor` flags coverage below 85%, a sign that
calls are going missing, and above 105%, a sign of double counting.

