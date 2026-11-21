# 0019. Cache what each log file gives the scan, keyed on the file and the code

- Status: Accepted
- Date: 2026-11-17

## Context

Every command starts by scanning the agents' logs (`sources.load_records`),
and `limits`, `sessions` and `doctor` scan Claude Code's again for its
counters and limit hits. On the maintainer's machine Claude Code's logs
had grown to 3.2 GB in 1,178 files, and `tokencur limits` took about 17 s:
13 s reading the transcripts for usage, 3-4 s reading them again for
limit hits, under 1 s computing. The status line and the session hooks run
`limits` often, so the time was paid many times a day, for files that had
not changed since the last run.

A transcript only grows while its session is open and is left alone
after. On 2026-11-17, six of the 1,178 files had changed in the last ten
minutes.

Skipping unchanged files and relying on the ledger does not work.
Joining the files is part of reading them. A resumed session copies
messages into a new file, and they are counted once, with each count's
largest value. A subagent's output is filled in from counters that the
main transcript writes (ADR 0017). The ledger stores the result of that
join, not the files' parts.

## Decision

Split the reading of a Claude Code transcript into a part per file and a
join in path order. A part holds what the file adds: its messages merged
within the file, the calls logged with a stop reason, each session's
first timestamp, its counter lines reduced to the fields
`_read_counters` reads, its typed prompts, its limit hits and stubs, and
its scan counts. The join is the same fold the scan always did, so
joining parts gives what reading every line in that order gives. Usage,
counters and limit stubs come from the same parts, so one read of a file
serves all three.

Keep each part in a SQLite cache, `$XDG_CACHE_HOME/tokencur/scan-cache.sqlite3`
(or `$TOKENCUR_CACHE`; `off` turns it off), with the file's size and
modification time. Reuse a part while both still match.

- A file modified within two seconds of being read is not kept. A rewrite
  in the same tick of the file system's clock, at the same size, would
  look unchanged (git calls this the racy-clean case).
- The cache is keyed on the hash of the modules that read the logs. Any
  change to them discards it whole, whether an upgrade or a local edit.
- Parts hold no message content. A rate-limit stub with no ids, counted
  once per distinct line, is keyed on the line's hash, not its text.
- Files that are gone are forgotten. The ledger still keeps their records.
- Any error reading or writing the cache means one full read, with a
  warning. It never fails a command.

## Consequences

- On the maintainer's logs, `limits` reads usage in 0.5-1.3 s instead of
  13 s, and counters and limit hits in 0.4-1.1 s instead of 9 s. Results
  match a full read record by record, in the same order. The only
  differences are in sessions still being written during the
  comparison.
- The first run after an upgrade reads everything and fills the cache,
  in about 14 s.
- A second file on disk, about 25 MB for 3.2 GB of logs, holds the same
  metadata the ledger holds. It is created readable by its owner only.
  Deleting it costs one full read.
- Codex and Kimi Code logs are still read in full (about 2 s together).
  Codex's copies of forked sessions are matched across files in a
  different way, and the same split can follow when it is worth it.

## Update 2026-11-20: Codex's rollouts too, one key per source

With Claude Code's scan cached, Codex's was the largest part left of a
`limits` run: 0.4 s of 1.4 on the maintainer's machine, for 193 MB in 102
rollouts, and growing with every session.

A rollout splits the same way. Its part is its calls in log order and its
scan counts. The join reads the parts in path order and counts a call the
first time its content key shows up, the rule that already keeps a forked
session's copies out (`identity.content_key`). So joining parts gives what
reading every rollout in that order gives.

The cache had one code key for the whole file. Two sources with different
reading code would each discard the other's parts on every run. Each root
now keeps its own key, and a key that no longer matches discards only the
files under that root. The loop that reuses or reads each file's part
moved to `cache.file_parts`, which both sources call.

On the maintainer's logs the Codex scan takes 0.01 s instead of 0.4, and
`limits` 1.0 s instead of 1.4. Records and scan counts match a full read.
Kimi Code's logs, 3 MB, are still read in full.

An older tokencur still finds its own key under the old name and keeps
using the Claude Code parts. Their format has not changed, so both
versions can share the file while an upgrade is under way.
