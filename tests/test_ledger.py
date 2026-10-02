import os
import sqlite3
from contextlib import closing
from dataclasses import fields

import pytest

from tokencur import ledger
from tokencur.records import UsageRecord


def _record(record_id: str, output_tokens: int = 50, **overrides) -> UsageRecord:
    defaults = dict(
        timestamp="2026-07-01T10:00:00.000Z",
        workspace="workspace-a",
        session_id="sess_1",
        model="claude-opus-4-8",
        input_tokens=100,
        output_tokens=output_tokens,
        cache_read_tokens=1000,
        cache_write_5m_tokens=200,
        cache_write_1h_tokens=500,
        record_id=record_id,
    )
    defaults.update(overrides)
    return UsageRecord(**defaults)


def test_round_trip_preserves_every_field(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    records = [
        _record("req_1:msg_1"),
        _record("sess@ts#abc", source="codex", timestamp="2026-07-02T00:00:00Z"),
    ]

    ledger.record(records, path)

    assert ledger.read(path) == records
    # Guard: a new UsageRecord field must get a ledger column too.
    assert set(ledger._FIELDS) == {f.name for f in fields(UsageRecord)}


def test_rescanning_the_same_logs_adds_nothing(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    records = [_record("req_1:msg_1"), _record("req_2:msg_2")]

    assert ledger.record(records, path) == 2
    assert ledger.record(records, path) == 0
    assert len(ledger.read(path)) == 2


def test_history_survives_when_logs_disappear(tmp_path):
    """The reason the ledger exists: a later scan that no longer sees an
    event (its log was cleaned up) must not drop it."""
    path = tmp_path / "ledger.sqlite3"
    old, recent = _record("req_1:msg_1"), _record("req_2:msg_2")
    ledger.record([old, recent], path)

    ledger.record([recent], path)

    assert ledger.read(path) == [old, recent]


def test_present_events_are_refreshed_and_keep_first_seen(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    ledger.record([_record("req_1:msg_1", output_tokens=50)], path)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("UPDATE usage SET first_seen = '2026-07-01T00:00:00Z'")

    ledger.record([_record("req_1:msg_1", output_tokens=75)], path)

    [stored] = ledger.read(path)
    assert stored.output_tokens == 75
    with closing(sqlite3.connect(path)) as conn, conn:
        first_seen = conn.execute("SELECT first_seen FROM usage").fetchone()[0]
    assert first_seen == "2026-07-01T00:00:00Z"


def test_records_without_an_id_never_collapse(tmp_path):
    """An empty id must not become a shared key that swallows records."""
    path = tmp_path / "ledger.sqlite3"
    a, b = _record("", output_tokens=1), _record("", output_tokens=2)

    assert ledger.record([a, b], path) == 2
    assert ledger.record([a], path) == 0


def test_an_empty_scan_creates_nothing(tmp_path):
    path = tmp_path / "nowhere" / "ledger.sqlite3"

    assert ledger.record([], path) == 0
    assert ledger.read(path) == []
    assert not path.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
def test_ledger_file_is_private(tmp_path):
    path = tmp_path / "data" / "ledger.sqlite3"

    ledger.record([_record("req_1:msg_1")], path)

    assert os.stat(path).st_mode & 0o777 == 0o600


def test_newer_schema_is_refused(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute(f"PRAGMA user_version = {ledger.SCHEMA_VERSION + 1}")

    with pytest.raises(RuntimeError, match="Upgrade tokencur"):
        ledger.record([_record("req_1:msg_1")], path)


def test_default_path_honours_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENCUR_LEDGER", str(tmp_path / "custom.sqlite3"))
    assert ledger.default_path() == tmp_path / "custom.sqlite3"

    monkeypatch.delenv("TOKENCUR_LEDGER")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert ledger.default_path() == tmp_path / "xdg" / "tokencur" / "ledger.sqlite3"


def _v1_ledger(path, rows):
    """A ledger as tokencur 0.2 left it: schema 1, no superseded table."""
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute(ledger._CREATE_USAGE)
        conn.execute("PRAGMA user_version = 1")
        conn.executemany(
            f"INSERT INTO usage VALUES ({', '.join('?' * 12)})",
            [(*ledger._key_fields(r), "2026-07-01T00:00:00Z") for r in rows],
        )


def _codex(record_id: str, timestamp: str, tokens: int = 100, session: str = "s1"):
    return _record(
        record_id,
        source="codex",
        session_id=session,
        timestamp=timestamp,
        input_tokens=tokens,
        output_tokens=tokens,
        cache_read_tokens=tokens,
        cache_write_5m_tokens=0,
        cache_write_1h_tokens=0,
    )


def test_schema_1_ledger_retires_codex_resends_with_an_audit_trail(tmp_path, capsys):
    path = tmp_path / "ledger.sqlite3"
    call = _codex("s1@t1#aaaa", "2026-02-06T22:43:51.000Z")
    resend = _codex("s1@t2#aaaa", "2026-02-06T22:43:52.000Z")  # same usage
    empty = _codex("s1@t4#cccc", "2026-02-06T22:44:11.000Z", tokens=0)
    kept = [
        call,
        _codex("s1@t3#bbbb", "2026-02-06T22:44:10.000Z", tokens=300),
        # Same fingerprint as `call`, but another session: a different call.
        _codex("s2@t1#aaaa", "2026-02-06T22:43:51.000Z", session="s2"),
        # Claude rows are never touched, whatever their ids look like.
        _record("req_1:msg_1"),
    ]
    _v1_ledger(path, [*kept, resend, empty])

    history = ledger.read(path)

    assert sorted(r.record_id for r in history) == sorted(r.record_id for r in kept)
    with closing(sqlite3.connect(path)) as conn, conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        retired = conn.execute(
            "SELECT record_id, reason FROM superseded ORDER BY record_id"
        ).fetchall()
    assert [rid for rid, _ in retired] == ["s1@t2#aaaa", "s1@t4#cccc"]
    assert all("re-sent" in reason for _, reason in retired)
    backup = tmp_path / "ledger.sqlite3.schema-1.bak"
    with closing(sqlite3.connect(backup)) as conn, conn:
        assert conn.execute("SELECT COUNT(*) FROM usage").fetchone()[0] == 6
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    if os.name == "posix":
        assert os.stat(backup).st_mode & 0o777 == 0o600
    assert "retired 2 re-sent Codex reports" in capsys.readouterr().err


def test_migration_runs_once(tmp_path, capsys):
    path = tmp_path / "ledger.sqlite3"
    _v1_ledger(path, [_codex("s1@t1#aaaa", "2026-02-06T22:43:51.000Z")])
    ledger.read(path)
    capsys.readouterr()

    assert len(ledger.read(path)) == 1
    assert capsys.readouterr().err == ""


def test_new_ledger_starts_at_the_current_schema(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    ledger.record([_record("req_1:msg_1")], path)

    with closing(sqlite3.connect(path)) as conn, conn:
        assert (
            conn.execute("PRAGMA user_version").fetchone()[0] == ledger.SCHEMA_VERSION
        )
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
    assert {"usage", "superseded"} <= tables
    assert not (tmp_path / "ledger.sqlite3.schema-0.bak").exists()
