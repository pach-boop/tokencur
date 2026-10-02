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
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from tokencur.ingest.claude_code import UsageRecord
from tokencur.ingest.identity import fingerprint

SCHEMA_VERSION = 1

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

_SCHEMA = """
CREATE TABLE usage (
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
    PRIMARY KEY (source, record_id)
)
"""

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
    first_seen = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = [(*_key_fields(r), first_seen) for r in records]
    if not rows:
        return 0  # nothing to keep: don't create an empty ledger
    with closing(_connect(path or default_path())) as conn, conn:
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
        return [UsageRecord(**dict(zip(_FIELDS, row))) for row in cursor]


def _key_fields(r: UsageRecord) -> tuple:
    # A record without an id (a third-party ingester, a hand-built test
    # record) falls back to a hash of its content, so distinct records
    # never collapse into one row under an empty id.
    record_id = r.record_id or "content:" + fingerprint(asdict(r))
    return (
        r.source, record_id, r.timestamp, r.workspace, r.session_id, r.model,
        r.input_tokens, r.output_tokens, r.cache_read_tokens,
        r.cache_write_5m_tokens, r.cache_write_1h_tokens,
    )


def _connect(path: Path) -> sqlite3.Connection:
    created = not path.exists()
    if created:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    conn = sqlite3.connect(path)
    if created:
        os.chmod(path, 0o600)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version == 0:
        with conn:
            conn.execute(_SCHEMA)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    elif version > SCHEMA_VERSION:
        conn.close()
        raise RuntimeError(
            f"{path} uses ledger schema {version}; this tokencur reads up "
            f"to {SCHEMA_VERSION}. Upgrade tokencur to keep using it."
        )
    return conn
