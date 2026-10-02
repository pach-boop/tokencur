# 0007. Value history at current list rates, not point-in-time rates

- Status: Accepted, to revisit
- Date: 2026-10-02

## Context

A list price applies from a date. FOCUS's `ListUnitPrice` is meant to be
the rate in force when the charge happened. tokencur prices every
record, old or new, with the rates it holds today: when a provider moves
a list price, past usage is revalued. The snapshot's git history knows
when rates moved (the price card shows it), but the package does not
carry that history at runtime.

## Decision

For now, value all usage at the current curated card and snapshot, and
say so: the README lists it under Limitations, and every report prints
the date of the rates it used. Showback at current list price is a
recognized method, and it keeps a month comparable with the next.

## Consequences

- Historical totals can move when list prices move, so a published
  figure is only reproducible together with its rates date.
- Point-in-time pricing, with effective-dated rates in the card and
  rate history carried in the snapshot, is the planned replacement. It
  touches pricing, the FOCUS export and recommendations, which today
  price aggregated tokens once. Tracked in
  [#5](https://github.com/pach-boop/tokencur/issues/5).
