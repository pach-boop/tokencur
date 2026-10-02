# 0008. Value each call at the list rate in force on its day

- Status: Accepted (supersedes ADR 0007)
- Date: 2026-10-02

## Context

ADR 0007 valued every call, however old, at today's rates, so a price
move revalued history and a published total was reproducible only with
its rates date. The review called this out: a FinOps tool must tell
"current price" from "price applicable when the event happened", and
FOCUS's `ListUnitPrice` is meant to be the latter. The information
existed: the snapshot's git history recorded 40 rate moves across 38
models between 2026-07-06 and 2026-10-01.

## Decision

- Each snapshot entry keeps a `history` of the rates it had before each
  move, with `until`: the first day the next rate applied. The
  price-watch bot appends to it on the day it sees a move (at most a day
  late, since it runs daily); `update_pricing_snapshot.py
  --backfill-history` rebuilt the history from git once.
- The curated card has `RATE_CARD_HISTORY` for the same purpose. It is
  empty until Anthropic moves a curated price; the old rate then moves
  there instead of being overwritten.
- `rates_for(model, on=day)` returns the rate in force that UTC day.
  Cost, the FOCUS export (`ListUnitPrice` and every cost column) and the
  recommendations price call by call at the rate of the call's day.
  Whether a right-sizing pair is a step down at all is judged on today's
  rates.

## Consequences

- A total no longer moves when a price moves, so published figures stay
  reproducible.
- History starts with the snapshot, on 2026-07-06: earlier usage is
  valued at the first rate observed. Moves are dated by the day they
  were seen, not by the provider's announcement.
- The snapshot grows with each move (40 history items today), and the
  lookup is cached per model and day.
- On the maintainer's data nothing changed: none of the models in use
  had moved.
