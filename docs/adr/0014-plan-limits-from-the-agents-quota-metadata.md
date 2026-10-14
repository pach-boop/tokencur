# 0014. Plan limits from the agent's own quota metadata, in usage value

- Status: Accepted
- Date: 2026-10-14

## Context

On a subscription, usage is not billed per token: it is capped. Claude
Max limits each five-hour window and each week, and states neither cap in
tokens or dollars, nor how much of it is left. Several agents sharing one
subscription drain the same windows: on 2026-10-13 two sessions working
on different projects stopped in the same minute when the week's limit
was reached, and work in flight had to start again after the reset.

Claude Code logs each refusal as a `<synthetic>` stub. Its text says
which limit and when it resets ("You've hit your weekly limit · resets
5am"), and next to it Claude Code writes the same as structured metadata:
`quotaLimits` with `status` (`rejected`), `rateLimitType` (`five_hour`,
`seven_day`) and `resetsAt` (Unix seconds). Nothing in the logs says how
much of a limit is used before it is reached.

## Decision

- Read limit hits from `quotaLimits` only, never from the stub's text, so
  ADR 0006 holds without an exception. Only refusals count, once per
  request, and retries after a hit share its reset and count as one hit.
  A hit whose reset does not fall within the window it closes (after the
  hit, at most one window later) is left out and counted, not trusted.
- Keep the hits in the ledger (`limit_hits`, schema 7), like usage, so
  they outlive the logs.
- Measure every window in API-equivalent usage value, the unit tokencur
  already has for all usage. The value a window used up to its first
  refusal is a ceiling seen on the plan in force that day (from
  `subscriptions.json`, whose days are read in the local time zone: they
  are the days a person lived, and an evening hit in Mexico City is
  already the next UTC day).
- A ceiling is a reference for the current plan only when its whole
  window ran on that plan, and when these logs hold its usage (at least
  $1). A week that began on Pro and hit on Max mixes two plans; a hit
  with almost no usage here means the subscription was used elsewhere,
  such as another machine. Both are shown, never used as a reference.
- Place the week by the latest weekly reset on record: weekly limits
  reset on a fixed schedule (two hits seven days apart reset at the same
  hour). Without one, say the week cannot be placed rather than guess.
- Five-hour windows open with the first call after the previous window
  closed, rounded down to ten minutes, and last five hours. The five resets
  Claude Code logged up to 2026-10-14 (02:50, 21:00, 02:00, 01:40 and
  21:00 UTC) all match that rule when windows are rebuilt from the calls
  before them; none match the exact call time, and two miss the hour. A
  logged reset fixes a window's end; past it, windows are rebuilt from
  call times and marked as inferred.
- Calls tokencur cannot price are named, never valued at $0 (ADR 0002).
- Show the current windows against the lowest ceiling seen on the current
  plan, and project the week at the last seven days' pace.
- Only `tokencur limits` scans for hits. A substring filter keeps it to
  about a second over a 1 GB history, but every other command would pay
  that second for nothing it shows.

## Consequences

- Several agents on one subscription can see, from one command, how close
  the next hit is, and start large work when it fits.
- It is a gauge, not a balance. Limits are not priced like the API (the
  five-hour ceilings measured on Max were about 3.7× those on Pro, not
  the 5× the plans suggest), and usage the logs do not hold, such as claude.ai chat and
  Claude Code's background calls, draws on the same limits. Ceilings vary
  for those reasons (the five-hour window hit at $148 and $135 on the same
  plan), so the lowest reference is the cautious one.
- A hit that was never logged on this machine is invisible, and a hit
  whose logs were deleted before `tokencur limits` ran is lost.
- If Claude Code renames `quotaLimits` or its fields, hits stop appearing
  and the view says no limit was hit; `doctor` does not check for that yet.
