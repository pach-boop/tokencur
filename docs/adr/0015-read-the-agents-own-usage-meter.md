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
  the key or its fields, readings stop and the view falls back to ADR 0014
  without saying why; `doctor` does not check it yet.
