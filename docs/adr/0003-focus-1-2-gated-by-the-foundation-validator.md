# 0003. Target FOCUS 1.2, gated in CI by the Foundation's validator

- Status: Accepted
- Date: 2026-07-07; validator 2.2.1 on 2026-10-01; fixture logs 2026-10-02

## Context

FOCUS is the FinOps Foundation's open specification for cost and usage
data; FOCUS 1.2 already has columns for token-based billing. Claiming
conformance is easy; proving it is not. The newest spec is later than
1.2, but the Foundation's `focus-validator` (2.2.1, August 2026) only
ships rules for 1.2.

## Decision

tokencur exports FOCUS 1.2, the newest version that can be checked
mechanically, and CI fails unless the export passes the Foundation's own
validator. Each usage record becomes one charge row per token bucket
(input, output, cache read, cache writes by TTL), the way provider bills
emit one line per SKU.

The gate validates two datasets in one file: hand-built records for edge
dates, and the golden fixture logs (synthetic, shaped like real agent
logs) parsed by the real ingesters, so the validator sees what tokencur
produces from logs, not only what a test author typed. One rule failure
is tolerated by name: the not-null branch of the `InvoiceId` OR rule,
which showback data never takes (ADR 0001).

## Consequences

- "FOCUS-conformant" is a tested claim, in CI, on every change.
- Moving to a newer spec waits for the validator to support it.
- The validator needs Python 3.12+, so the gate skips the 3.11 job while
  the package keeps supporting and testing 3.11.
