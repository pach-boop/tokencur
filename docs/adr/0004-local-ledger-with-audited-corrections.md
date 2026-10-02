# 0004. A local SQLite ledger keyed on stable ids, with audited corrections

- Status: Accepted
- Date: 2026-10-01; schema 2 on 2026-10-02

## Context

Coding agents treat their logs as disposable: Claude Code deletes
transcripts after 30 days by default. Computed from the logs alone,
totals shrank over time. The maintainer's July and August Claude usage
is gone for good: it was deleted before tokencur kept anything.

## Decision

Every command first keeps what it scans in a local SQLite ledger, then
reports the ledger's full history.

- **Identity**: `(source, record_id)`. Each ingester builds `record_id`
  from the source's own identity fields: a request and message id where
  the source logs one, otherwise session, timestamp and a fingerprint of
  the raw usage object as logged, so a parser fix never changes an id.
- **Upsert**: events still on disk are refreshed from the latest parse;
  events whose logs are gone keep their last known values.
- **Private and local**: stdlib `sqlite3`, metadata only, file mode 0600.
- **Versioned schema, audited corrections**: `PRAGMA user_version`;
  migrations run one step at a time after a backup copy through SQLite's
  backup API. Rows a later version finds were not usage move to a
  `superseded` table with when and why: never deleted. Schema 2 uses it
  to retire Codex re-sent reports (ADR 0005): 8,099 rows on the
  maintainer's ledger.
- A newer ledger is refused by an older tokencur, never misread.

## Consequences

- History survives the agents, but only from the first run on: the
  ledger cannot recover what was deleted before it existed.
- A correction to past data is visible and reversible, as a financial
  record's should be.
- The whole history is loaded in memory: fine at today's scale (about
  11k records, well under 20 MB), to revisit for organization-wide
  volumes.
