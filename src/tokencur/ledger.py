"""Persistent usage ledger: history that survives log cleanup.

Coding agents treat their logs as disposable — Claude Code deletes
session transcripts after ``cleanupPeriodDays`` (30 by default) — so a
report computed only from the logs on disk forgets old usage and its
totals *shrink* over time. The ledger is a local SQLite file that keeps
every usage record tokencur has ever seen:

- Deduplicated on ``(source, record_id)`` (see
  ``tokencur.ingest.identity``): re-scanning the same logs adds nothing.
- Upserted: an event still on disk is refreshed from the latest parse;
  an event whose log is gone keeps its last known values.
- Metadata only, like the logs it mirrors: token counts, models,
  timestamps, workspace and session ids — never message content. The
  file is created readable by its owner only.

Location: ``$TOKENCUR_LEDGER`` if set, else
``$XDG_DATA_HOME/tokencur/ledger.sqlite3`` (``~/.local/share/...``).
Deleting the file resets history to whatever the logs still hold.

The schema is versioned (``PRAGMA user_version``) and migrated in place,
one step at a time, after a backup copy (see ``_migrate``). Rows a later
version finds were not usage are moved to the ``superseded`` table with
when and why, never deleted.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from collections.abc import Iterable
from contextlib import closing
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from tokencur.ingest.identity import COPYABLE_SOURCES, content_key, fingerprint
from tokencur.records import UsageRecord

SCHEMA_VERSION = 2

# One column per UsageRecord field; the round-trip test fails if the
# dataclass gains a field the ledger does not store.
_FIELDS = (
    "source",
    "record_id",
    "timestamp",
    "workspace",
    "session_id",
    "model",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_5m_tokens",
    "cache_write_1h_tokens",
)

# Columns shared by the live table and the audit table below.
_COLUMNS = """
    source                TEXT    NOT NULL,
    record_id             TEXT    NOT NULL,
    timestamp             TEXT    NOT NULL,
    workspace             TEXT    NOT NULL,
    session_id            TEXT    NOT NULL,
    model                 TEXT    NOT NULL,
    input_tokens          INTEGER NOT NULL,
    output_tokens         INTEGER NOT NULL,
    cache_read_tokens     INTEGER NOT NULL,
    cache_write_5m_tokens INTEGER NOT NULL,
    cache_write_1h_tokens INTEGER NOT NULL,
    first_seen            TEXT    NOT NULL,  -- when the ledger first stored it
"""

_CREATE_USAGE = f"CREATE TABLE usage ({_COLUMNS}    PRIMARY KEY (source, record_id)\n)"

# Rows a later tokencur found were not usage after all. They leave the
# totals but are never deleted: each keeps when and why it was retired,
# so a restatement can be audited and undone.
_CREATE_SUPERSEDED = f"""CREATE TABLE IF NOT EXISTS superseded ({_COLUMNS}
    superseded_at         TEXT    NOT NULL,
    reason                TEXT    NOT NULL,
    PRIMARY KEY (source, record_id)
)"""

_UPSERT = (
    f"INSERT INTO usage ({', '.join(_FIELDS)}, first_seen) "
    f"VALUES ({', '.join('?' * (len(_FIELDS) + 1))}) "
    "ON CONFLICT (source, record_id) DO UPDATE SET "
    + ", ".join(f"{f} = excluded.{f}" for f in _FIELDS[2:])
)


def default_path() -> Path:
    """Where the ledger lives unless a caller passes an explicit path."""
    override = os.environ.get("TOKENCUR_LEDGER")
    if override:
        return Path(override).expanduser()
    data_home = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(data_home) / "tokencur" / "ledger.sqlite3"


def record(records: Iterable[UsageRecord], path: Path | None = None) -> int:
    """Upsert ``records`` into the ledger; return how many were new."""
    first_seen = _utc_now()
    rows = [(*_key_fields(r), first_seen) for r in records]
    if not rows:
        return 0  # nothing to keep: don't create an empty ledger
    with closing(_connect(path or default_path())) as conn, conn:
        rows = _without_copies(conn, rows)
        before = conn.execute("SELECT COUNT(*) FROM usage").fetchone()[0]
        conn.executemany(_UPSERT, rows)
        after = conn.execute("SELECT COUNT(*) FROM usage").fetchone()[0]
    return after - before


def read(path: Path | None = None) -> list[UsageRecord]:
    """Every record in the ledger, oldest first."""
    target = path or default_path()
    if not target.exists():
        return []
    with closing(_connect(target)) as conn:
        cursor = conn.execute(
            f"SELECT {', '.join(_FIELDS)} FROM usage "
            "ORDER BY timestamp, source, record_id"
        )
        return [UsageRecord(**dict(zip(_FIELDS, row, strict=True))) for row in cursor]


def _without_copies(conn: sqlite3.Connection, rows: list[tuple]) -> list[tuple]:
    """Drop rows that copy a call already on record under another session.

    A forked Codex session can re-copy earlier calls under its own session
    id, so the same call arrives with a new record id. The scan already
    skips copies whose original log is still on disk (see
    ``tokencur.ingest.codex``); this catches the case where the original
    log is gone and only the fork remains.
    """
    marks = ", ".join("?" * len(COPYABLE_SOURCES))
    owners = {
        (source, content_key(record_id)): record_id
        for source, record_id in conn.execute(
            f"SELECT source, record_id FROM usage WHERE source IN ({marks})",
            sorted(COPYABLE_SOURCES),
        )
    }
    kept = []
    for row in rows:
        source, record_id = row[0], row[1]
        if source in COPYABLE_SOURCES:
            key = (source, content_key(record_id))
            if owners.setdefault(key, record_id) != record_id:
                continue  # a copy of a call already on record
        kept.append(row)
    return kept


def _key_fields(r: UsageRecord) -> tuple:
    # A record without an id (a third-party ingester, a hand-built test
    # record) falls back to a hash of its content, so distinct records
    # never collapse into one row under an empty id.
    record_id = r.record_id or "content:" + fingerprint(asdict(r))
    return (
        r.source,
        record_id,
        r.timestamp,
        r.workspace,
        r.session_id,
        r.model,
        r.input_tokens,
        r.output_tokens,
        r.cache_read_tokens,
        r.cache_write_5m_tokens,
        r.cache_write_1h_tokens,
    )


def _connect(path: Path) -> sqlite3.Connection:
    created = not path.exists()
    if created:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    conn = sqlite3.connect(path)
    if created:
        os.chmod(path, 0o600)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        conn.close()
        raise RuntimeError(
            f"{path} uses ledger schema {version}; this tokencur reads up "
            f"to {SCHEMA_VERSION}. Upgrade tokencur to keep using it."
        )
    if version == 0:
        with conn:
            conn.execute(_CREATE_USAGE)
            conn.execute(_CREATE_SUPERSEDED)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    elif version < SCHEMA_VERSION:
        _migrate(conn, path, version)
    return conn


def _migrate(conn: sqlite3.Connection, path: Path, version: int) -> None:
    """Bring an older ledger up to ``SCHEMA_VERSION``, one step at a time.

    The file is first copied next to itself as ``<name>.schema-<N>.bak``
    through SQLite's backup API, so an upgrade can always be undone. Each
    step says on stderr what it changed.
    """
    backup = path.with_name(f"{path.name}.schema-{version}.bak")
    with closing(sqlite3.connect(backup)) as copy:
        conn.backup(copy)
    os.chmod(backup, 0o600)
    while version < SCHEMA_VERSION:
        change = _MIGRATIONS[version](conn)
        version += 1
        print(
            f"tokencur: ledger upgraded to schema {version}: {change} "
            f"(previous version kept at {backup})",
            file=sys.stderr,
        )


_CODEX_RESEND = "codex re-sent report: running total unchanged, call already counted"


def _v1_to_v2(conn: sqlite3.Connection) -> str:
    """Schema 2: the superseded table, and the Codex re-send correction.

    Up to tokencur 0.2, every Codex ``token_count`` event was counted, but
    Codex re-sends an unchanged report under a new timestamp, so about
    half of the stored Codex rows repeat a call already counted. The
    parser now skips them (see ``tokencur.ingest.codex``); this step
    retires the ones already stored. Running totals are not in the
    ledger, so a stored row counts as a re-send when its raw-usage
    fingerprint (the end of its record id) equals the previous row's in
    the same session, or when it carries no tokens at all. On the
    maintainer's ledger that selects exactly the rows the corrected
    parser no longer yields.
    """
    conn.execute(_CREATE_SUPERSEDED)
    rows = conn.execute(
        "SELECT record_id, session_id, input_tokens, output_tokens, "
        "cache_read_tokens, cache_write_5m_tokens, cache_write_1h_tokens "
        "FROM usage WHERE source = 'codex' ORDER BY session_id, timestamp, record_id"
    ).fetchall()
    resent: list[tuple[str]] = []
    previous: tuple[str, str] | None = None
    for record_id, session_id, *tokens in rows:
        key = (session_id, record_id.rpartition("#")[2])
        if not any(tokens) or key == previous:
            resent.append((record_id,))
        previous = key
    stamp = _utc_now()
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO superseded "
            "SELECT *, ?, ? FROM usage WHERE source = 'codex' AND record_id = ?",
            [(stamp, _CODEX_RESEND, record_id) for (record_id,) in resent],
        )
        conn.executemany(
            "DELETE FROM usage WHERE source = 'codex' AND record_id = ?", resent
        )
        conn.execute("PRAGMA user_version = 2")
    return f"retired {len(resent):,} re-sent Codex reports to the superseded table"


# Step from version N to N + 1, keyed by N.
_MIGRATIONS = {1: _v1_to_v2}


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
