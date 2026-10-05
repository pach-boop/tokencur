# 0012. Usage value per successful change, session by session

- Status: Accepted
- Date: 2026-10-02

## Context

ADR 0010 set usage value against commits, repository by repository. A
commit measures output, not success: one that never reaches the main
branch, or is reverted, produced nothing that stayed. An external review
named the next step as cost per successful task, on the way to quality,
latency, reliability and human effort. Issue #15 laid out a first step,
and the maintainer agreed its four decisions on 2026-10-02.

Three signals are available as metadata:

- Claude Code's end-of-session counters (ADR 0005, updated) carry the
  API time of a session, with and without retries; a continued session
  hands them on to the next.
- Claude Code marks where each message came from: typed by a person
  (`origin.kind` is `human`) or injected, such as a task notification.
- Local git knows which commits reached the default branch and which
  were later reverted.

## Decision

- A session is the agent's own. A chain of continued Claude Code
  sessions counts once.
- A change goes to the last session that worked in its repository before
  it was committed, if that was within the preceding 24 hours. Otherwise
  it was made without an agent session.
- A change is a commit and any copy of it with the same patch id, which
  a rebase or a cherry-pick keeps. It succeeded when a copy reached the
  default branch and no commit there reverts it. The default branch is
  origin's, with its local twin; without origin, the local main or
  master. A revert commit is a correction, not a change.
- Per session: usage value, prompts a person typed, API time and the
  share lost to retries (Claude Code only), and changes landed, reverted
  and pending. The headline is usage value per successful change: every
  session's usage value over the changes that landed and stayed.
- Two new reads, never stored or published: the origin of each message,
  never its text; and commit messages, only to find
  `This reverts commit <sha>`.
- Codex and Kimi Code sessions bring usage value and changes only, since
  they record no timings.

## Consequences

- On tokencur's own repository on 2026-10-02: four sessions, $106.06 of
  usage value, 56 changes that landed and stayed, so $1.89 per successful
  change.
- Exploration shows up instead of hiding: sessions with no landed change
  are counted, with their value.
- Prompts and API time cover whole sessions, even when a period or a
  repository filter keeps only part of a session's calls.
- This is still not quality. A change that landed can be wrong. Test
  results would need message content (ADR 0006), and CI status the
  forge's API; both wait on a decision.

## Update, 2026-10-03: timings, and two more reads

The decision above says Codex and Kimi Code record no timings. Kimi
Code does, step by step: each model step in its log carries the time to
the first token and the time spent streaming (`llmFirstTokenLatencyMs`,
`llmStreamDurationMs`, on all 168 steps in the maintainer's logs).
Codex 0.125 records each turn's duration and, on some turns, the time
to the first token; the earlier versions in those logs, 0.98 to 0.104,
record none. Unlike Claude Code's counters, neither keeps a session
total with and without retries, so their sessions still show no API
time or retry share. Reading their timings would be a new read, left
for a later decision.

The decision names two new reads; the feature makes two more, never
stored or published either. Each commit's diff is read as input to
`git patch-id`, to recognise a rebased copy. And telling a typed prompt
from other Claude Code messages also reads each line's sidechain, meta
and compaction flags and, on lines from before `origin` existed,
whether the content is plain text and the types of its blocks; never
the text. SECURITY.md lists both.
