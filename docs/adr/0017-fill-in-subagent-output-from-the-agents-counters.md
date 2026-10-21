# 0017. Fill in subagents' short output counts from Claude Code's counters

- Status: Accepted
- Date: 2026-10-20

## Context

`tokencur doctor` compares tokencur's value of each closed chain of Claude
Code sessions with the counters Claude Code writes when a session ends
(ADR 0005). On the maintainer's 189 chains, coverage had fallen to 85%,
the doctor's floor: $4,001.54 against $4,709.64. $657 of the gap was
Opus 5.5, almost all of it in a few long sessions of 5-13 October.

Both sides price the same tokens the same way; the gap is in the counts,
and almost all of it in output. In the largest session Claude Code
counted 14.6M output tokens and the transcripts held 3.4M, against 97%
of its cache reads and 98% of its cache writes. 92% of that session's
gap was output.

The cause is how subagents log. A Task agent's or a workflow agent's
transcript logs each message of a call while it streams, with the usage
of the call's first event: the input and cache counts are final, the
output count is a few tokens. The line that would carry the final count,
the one with the call's stop reason, is never written. The main
transcript writes it. Measured on 2026-10-20 (Claude Code 2.1.282), 73-97%
of subagent calls per day have no line with a stop reason and an average
logged output of about 8 tokens; on the main transcript, under 1% do (a
turn interrupted mid-stream). Sessions that ran workflows read low;
sessions without subagents reconcile at 99-100%.

## Decision

At ingest, a Claude Code call no line logged with a stop reason is a
short call. In each chain of sessions that closed with counters, the
output Claude Code counted for a model beyond what the transcripts
logged for it is spread evenly over that model's short calls, so the
chain's output matches Claude Code's own and each call keeps its time
(and so its day, its five-hour window and its commit).

- Even shares, not shares weighted by what each call wrote: most of the
  missing output is thinking, which transcripts do not hold, so a call's
  visible size says little about its count. Over a day or a window, even
  shares land where the calls were.
- Only where counters cover the transcripts. A session still open has no
  counters yet, so its short calls stay as logged until it closes (the
  ledger then updates them in place). A session continued from one whose
  transcript is gone carries counters that began before anything on disk
  (Claude Code keeps the first session's `startTime`); filling its gap
  would count the lost session's output again, on top of what the ledger
  kept. A chain is filled in only when its first line on disk is within
  ten minutes of its counters' start: Claude Code writes that line within
  seconds (207 of 210 sessions, all within a minute).
- Records keep no mark that their output was filled in: the scan counts
  short and filled-in calls, and `doctor` reports them.

## Consequences

- On the maintainer's logs, 15,688 of 15,690 short calls were filled in
  (+23.9M output tokens) and coverage rose from 85% to 95%: $4,483.00
  against $4,717.52. What remains is calls no transcript holds: $42.66
  of the smaller model behind web search and web fetch, and $191.86 of
  main-model calls such as compaction.
- A filled-in call's output count is an estimate; the chain's total is
  Claude Code's. Usage of a session still open reads low, as before, by
  its subagents' output; `doctor` says how many short calls wait for
  their session to close.
- Plan-limit views that read usage value (ADR 0014, ADR 0015) now see
  subagent output: the ceilings hit and the meter's line are measured
  against higher, truer values.
- If Claude Code starts logging the final count in subagent transcripts,
  there are no short calls and nothing is filled in. If it stops writing
  `startTime` or per-model `outputTokens`, nothing is filled in and
  coverage falls back, which `doctor` flags.
