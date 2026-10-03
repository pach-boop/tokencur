# 0011. Curated rates change only through dated history or a declared correction

- Status: Accepted
- Date: 2026-10-02

## Context

ADR 0008 values each call at the list rate in force on its day. For the
LiteLLM snapshot, the price-watch bot keeps that history itself: when a
rate moves, `build_snapshot` keeps the old one with the day it stopped
applying. The curated Anthropic card is maintained by hand, and its
history, `RATE_CARD_HISTORY`, is empty.

It is empty for a good reason. Replaying every version of the card in git
shows no curated price has moved since the card began on 2026-07-06. The
one rate that changed, Sonnet 5 from $3/$15 to $2/$10 in 0.3.0, corrected
a price that never applied: the card had used a scheduled rise that
Anthropic cancelled.

An external review still found the weak spot: nothing stopped a future
edit from overwriting a curated rate. Every call before the edit would
then be revalued at the new price, and a September figure would move
because of an October price.

## Decision

- Every rate the curated card holds is recorded in
  `tests/fixtures/golden/curated_card.json`.
- A test fails when a recorded rate leaves the card any way but two: into
  `RATE_CARD_HISTORY` with the first day of its successor, which is a
  price move; or through `RATE_CARD_CORRECTIONS`, with the day and the
  wrong rate, which is a correction of a rate that never applied.
  Removing a model or rewriting its history fails too.
- The record is rewritten only on purpose (`TOKENCUR_UPDATE_GOLDEN=1`), so
  every change to the card shows up in review.
- Sonnet 5's correction is declared in `RATE_CARD_CORRECTIONS`.
- Two tests prove the behaviour on both pricing layers. A curated move on
  2026-09-01 values August 31 at the old rate and September 1 at the new
  one. A September figure stays the same when October's prices move,
  whether the move is in the curated card or in the snapshot.

## Consequences

- A curated price move takes two lines of code, and the failing test says
  which two.
- The existing test that compares the card with the snapshot already
  stops the daily refresh when a curated model's price moves upstream. A
  move cannot go unnoticed, and now it cannot be recorded wrongly.
- A correction restates history on purpose. It must also be disclosed in
  the changelog, and on the observatory when published figures change.
