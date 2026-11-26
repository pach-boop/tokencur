# 0022. The five-hour ceiling at each window's model mix

- Status: Accepted
- Date: 2026-11-26

## Context

ADR 0014 sets a five-hour window against the lowest ceiling hit on the
plan, in usage value. ADR 0015 replaces that with Claude Code's own meter,
but only while a reading taken in the window is fresh, and Claude Code
fetches it only when `/usage` or its own checks run. Most windows have no
reading, so the lowest ceiling is what people and scripts see.

Since November 2026 one model, Fable, has a weekly limit of its own, and it
also draws more of the five-hour limit per dollar of usage value. On the
plan in force, every five-hour hit and meter reading from 2026-10-02 to
2026-11-26 (58 of them) puts the limit at:

| Fable share of the window's usage value | Limit in usage value |
|---|---|
| 0% (Opus 5.5 alone) | $124 to $207, most within $170 to $200 |
| 9-15% | $151 to $175 |
| 62-95% | $87 to $105 |

The lowest ceiling was a Fable window: $87.14 on 2026-11-13, 94% Fable.
Set against it, an Opus window read about twice its real share. On
2026-11-25 at 21:31 Mexico City time the meter read 92% and the lowest
ceiling 184%; on 2026-11-26 at 12:47 it read 49% and the lowest ceiling
109%. Agents that pace their work by this figure stopped early, and the
watcher that warns at 85% warned at about 45%.

## Decision

- **The models with a weekly limit of their own are the ones the meter
  names** (`seven_day:<model>` in any reading on record). Nothing in the
  code names Fable. With no such reading, nothing changes.
- **One factor weighs their usage value against the others'.** A
  five-hour share is fit as share = others / L + factor x own / L, by least
  squares through zero, on every five-hour hit on the plan in force (a
  share of 1) and every five-hour meter reading of 10% or more taken on it.
  The factor is fit afresh on each run, so each new reading or hit moves
  it.
- **It is fit only when the data can tell.** Three points at least, and
  the share of those models must differ by 0.5 or more among them, or the
  factor could not be told from the limit itself. A fit that puts either
  side at no cost (a factor of zero or less) is no fit. Without a fit,
  dollars count alike, as before.
- **The lowest ceiling is taken in units, then priced at the window's
  mix.** Each reference ceiling counts its own-limit models' dollars
  `factor` times; the lowest of those is turned back into dollars at the
  share those models have in the open window. That is the figure the
  window is set against when no fresh reading is: still the lowest ceiling
  hit, on the safe side, but at this window's mix.
- **Nothing a script reads changes meaning.** `tokencur limits --json`
  keeps `lowest_ceiling` in dollars alone. The five-hour window gains
  `mix` (the models, the factor, the points it was fit on, their share in
  the window and the lowest ceiling at that share), and `percent_of` gains
  one value, `lowest_ceiling_by_mix`. A fresh reading still wins.

## Consequences

- Checked against the eight five-hour readings of the week that reset on
  2026-11-25, each estimated only from the hits and readings taken before
  its window opened (the factor came out at 2.01 to 2.04):

  | Reading (Mexico City) | Meter | Lowest ceiling | At the window's mix |
  |---|---|---|---|
  | 2026-11-25 10:41 | 88% | 100% | 93% |
  | 2026-11-25 11:40 | 100% | 115% | 106% |
  | 2026-11-25 12:59 | 65% | 67% | 68% |
  | 2026-11-25 16:23 | 95% | 104% | 97% |
  | 2026-11-25 20:17 | 61% | 114% | 68% |
  | 2026-11-25 21:31 | 92% | 184% | 104% |
  | 2026-11-25 22:14 | 9% | 20% | 10% |
  | 2026-11-26 12:47 | 49% | 109% | 57% |

  The mean distance to the meter falls from 32 points to 5.5, and the
  figure stays at or above the meter in all eight.
- One factor serves every model with a limit of its own. If the meter
  names a second one that weighs differently, a single factor will blur
  the two, and a factor per model will need its own spread of points.
- The week is left as it is: its meter is read far more often, and its
  projection already follows the meter's line (ADR 0015).
- The meter counts usage these logs do not hold, which the fit absorbs as
  it does in ADR 0015: on a machine that is not the only one using the
  subscription, both the limit and the factor read with that noise.
