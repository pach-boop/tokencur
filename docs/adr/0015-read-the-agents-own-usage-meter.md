# 0015. Read the agent's own usage meter, from one key of its state file

- Status: Accepted
- Date: 2026-10-14

## Context

ADR 0014 measures plan limits in usage value. Its references are the
ceilings seen when a limit turned a request away, so before the first hit
of a window nothing says what the limit is worth, and the five-hour
ceilings vary: $148.30, $135.05 and $135.33 on the same plan.

Claude Code shows the provider's own figure in `/usage`: the percent of
the five-hour and weekly limits used, and when each resets. It caches the
last one it fetched in its state file (`~/.claude.json`, or
`$CLAUDE_CONFIG_DIR/.claude.json`), under `cachedUsageUtilization`:
`fetchedAtMs`, and per limit `utilization` (percent) and `resets_at`.
That file also holds account details, per-project settings and feature
flags.

On 2026-10-14 at 19:29 Mexico City time the meter read 46% (five hours)
and 20% (week). tokencur's ADR 0014 estimate read 56% and 20%.

## Decision

- Read `cachedUsageUtilization` only: `fetchedAtMs`, and for `five_hour`
  and `seven_day` the percent and the reset, rounded to the minute
  (Claude Code writes 02:59:59.835 for a 03:00 reset). The file is decoded
  as JSON to reach that key; nothing else in it is read into a record,
  stored or shown. An absent, unreadable or reshaped file gives no
  readings, never an error.
- Keep every reading in the ledger (`meter_readings`, schema 8), so the
  value a limit is worth can be followed over time. Only `tokencur limits`
  reads the file.
- Show a reading only in the window it was taken in (its reset is still
  ahead, and matches the window's end within two minutes), always with the
  time it was taken. A reading's reset also fixes the window's bounds,
  which ADR 0014 otherwise infers from call times.
- From a reading of at least 10%, derive the usage value of the whole
  limit: the value used from the window's start to the reading, divided by
  the percent. Below 10%, a whole-percent reading moves that figure by
  more than a tenth, so none is derived. The current share is then the
  value used now over that limit; the week is projected against it, and
  the lowest ceiling hit stays next to it for comparison.

## Consequences

- The gauge reads the provider's figure when one is fresh, and shows how
  far the ADR 0014 estimate is from it. On the first reading, the weekly
  limit the meter implied ($1,062.95) matched the weekly ceiling hit the
  day before ($1,063.07). The five-hour limit it implied ($166.32) was
  above the ceilings hit (about $135), so the estimate from hits errs on
  the safe side.
- The reading is only as fresh as Claude Code's last fetch (when `/usage`
  or its own checks run). Between fetches the view still moves with usage,
  because the share is computed from the value used against the implied
  limit.
- The meter counts usage these logs do not hold (claude.ai, other
  machines), which the implied limit then absorbs: on a machine that is
  not the only one using the subscription, the implied limit reads low.
- This is a read of a file another program owns. If Claude Code renames
  the key or its fields, readings stop and the view falls back to ADR 0014.
  Since 2026-10-20 `doctor` checks it (a key that gives no reading is a
  problem; an old reading, or no key at all as with an API key, is not),
  and `limits` prints one warning line, so the fallback is no longer
  silent. `doctor` also checks that every `rate_limit` stub still gives a
  limit hit (ADR 0014).

## Update 2026-10-20: the meter follows a line, not a share

A week of readings showed that the share of one reading is the wrong
model. The week that reset on 2026-10-21 was read nine times, from 20% to
93%. Divided one at a time, those readings put the whole limit anywhere
from $1,063 (at 20%) to $1,412 (at 93%), climbing with every reading, so a
projection made early in the week reached the limit days too soon. Set
against the usage value logged by each reading, the nine fall on one
line, percent = 5.71 + usage value / $15.03, none more than 0.38 points
off. The meter starts the week about six points ahead of the usage these
logs hold. Why it does is not known (usage elsewhere, or how the provider
counts); tokencur measures it and does not explain it.

- With at least three readings in the window, spread over at least 20
  points, the meter is placed on the least-squares line through them:
  its base (the percent before any usage logged here) and the usage value
  per point. The whole limit is the value at 100% on that line; the
  current share is the base plus the value used now along the line. Fewer
  or closer readings, a line that does not rise, or a base of 100% or more
  fall back to the share of the latest reading, as decided above.
- Only readings taken on the plan in force, from its first day in
  subscriptions.json, go into the line: another plan's limit is another
  line.
- The view says which it used ("a line through 9 readings") and gives the
  base and the value per point; the JSON adds `base_percent` and
  `readings` to the meter.

## Update 2026-11-17: a model's own weekly limit

Since November 2026 the meter also lists its limits under
`utilization.limits`. That list holds the five hours (`session`), the week
(`weekly_all`) and a new kind, the weekly limit of one model
(`weekly_scoped`, with `scope.model.display_name` "Fable"). On 2026-11-17
it read 75%, severity "warning", while the week read 39%. A second new
field, `weekly_scoped_shares`, said that Fable may use 50% of the week and
had used 38 points of it.

The meter also values Fable differently from the API price. On the week
to 2026-11-18, Fable's 38 points came with $233.88 of Fable usage value,
$6.15 a point. The plan's earlier weeks, almost all Opus 5.5, took $13.30
to $14.40 a point. One dollar of Fable usage value thus moves the week's
meter about twice as far as one dollar of Opus. Per token it is about four
times, since Fable's list price is twice Opus's. The week's line above
holds while the mix of models stays the same.

- Read the `weekly_scoped` entries of `utilization.limits` that name a
  model and no surface, as the limit `seven_day:<model name>`: percent and
  reset, rounded as above. Entries scoped to a surface, and malformed
  entries, are skipped. Nothing else in the list is read: the five-hour
  and weekly limits still come from their own keys, and
  `weekly_scoped_shares` is not read.
- A model's limit is a week with the bounds of the plan's week. It is set
  against the usage value of that model's calls only. A model id matches
  when the name's words are dash-separated parts of it: "Fable" is every
  `claude-fable-*`, and Mythos is not Fable. The share, the limit it
  implies and the line follow the rules above. A reading of 100% means
  that model waits until the reset; other models do not.
- `--json` adds the week's `models`, by name. The hooks show them and
  watch them at the week's steps.

Consequences:

- A Fable week shows its own ceiling before Fable is turned away, and how
  much of it is left.
- Not done: a line for the week per model. The shares could split the
  week's readings into Fable's points and the rest, so the line would no
  longer depend on the mix. One week of readings is too little to fit
  that, so tokencur shows the two limits and leaves the week's line as it
  is.
