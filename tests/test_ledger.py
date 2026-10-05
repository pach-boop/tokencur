import os
import sqlite3
from contextlib import closing
from dataclasses import fields

import pytest

from tokencur import ledger
from tokencur.records import CIRun, UsageRecord


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
        conn.execute(ledger._V1_USAGE)
        conn.execute("PRAGMA user_version = 1")
        conn.executemany(
            f"INSERT INTO usage VALUES ({', '.join('?' * 12)})",
            [(*ledger._key_fields(r)[:11], "2026-07-01T00:00:00Z") for r in rows],
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
        assert (
            conn.execute("PRAGMA user_version").fetchone()[0] == ledger.SCHEMA_VERSION
        )
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
    err = capsys.readouterr().err
    assert "retired 2 re-sent Codex reports" in err  # step 1 -> 2
    assert "added price_modifiers" in err  # step 2 -> 3, same run, same backup


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


def test_schema_2_gains_price_modifiers_and_keeps_its_rows(tmp_path, capsys):
    path = tmp_path / "ledger.sqlite3"
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute(ledger._V2_USAGE)
        conn.execute(ledger._V2_SUPERSEDED)
        conn.execute("PRAGMA user_version = 2")
        conn.execute(
            f"INSERT INTO usage VALUES ({', '.join('?' * 12)})",
            (*ledger._key_fields(_record("req_1:msg_1"))[:11], "2026-07-01T00:00:00Z"),
        )

    (kept,) = ledger.read(path)

    assert kept.record_id == "req_1:msg_1" and kept.price_modifiers == ""
    with closing(sqlite3.connect(path)) as conn:
        assert (
            conn.execute("PRAGMA user_version").fetchone()[0] == ledger.SCHEMA_VERSION
        )
    assert "schema 3" in capsys.readouterr().err
    assert (tmp_path / "ledger.sqlite3.schema-2.bak").exists()


def test_price_modifiers_round_trip(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    fast = _record("req_9:msg_9", price_modifiers="fast+us")

    ledger.record([fast], path)

    assert ledger.read(path) == [fast]


def test_a_new_ledger_is_created_at_the_current_schema_silently(tmp_path, capsys):
    path = tmp_path / "ledger.sqlite3"
    ledger.record([_record("req_1:msg_1")], path)

    assert capsys.readouterr().err == ""
    assert not list(tmp_path.glob("*.bak"))


def test_schema_4_gains_cwd_and_a_rescan_fills_it(tmp_path, capsys):
    path = tmp_path / "ledger.sqlite3"
    old = _record("req_1:msg_1")
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute(ledger._V2_USAGE)
        conn.execute(ledger._V2_SUPERSEDED)
        conn.execute("PRAGMA user_version = 2")
    with closing(sqlite3.connect(path)) as conn:
        for step in (2, 3):
            ledger._MIGRATIONS[step](conn)
        with conn:
            conn.execute(
                f"INSERT INTO usage VALUES ({', '.join('?' * 13)})",
                (*ledger._key_fields(old)[:11], "2026-07-01T00:00:00Z", ""),
            )

    assert ledger.read(path) == [old]  # migrated: stored rows have no cwd
    assert "schema 5" in capsys.readouterr().err
    assert (tmp_path / "ledger.sqlite3.schema-4.bak").exists()

    ledger.record([_record("req_1:msg_1", cwd="/home/dev/app")], path)
    assert ledger.read(path)[0].cwd == "/home/dev/app"


def test_a_known_working_directory_is_never_forgotten(tmp_path):
    """A later parse that cannot see the directory keeps the stored one."""
    path = tmp_path / "ledger.sqlite3"
    ledger.record([_record("req_1:msg_1", cwd="/home/dev/app")], path)

    ledger.record([_record("req_1:msg_1", output_tokens=60)], path)

    (kept,) = ledger.read(path)
    assert (kept.cwd, kept.output_tokens) == ("/home/dev/app", 60)


def test_the_working_directory_is_not_part_of_a_records_identity(tmp_path):
    """A record without a source id is identified by its content; where it
    ran is not content, so learning it later adds no second row."""
    path = tmp_path / "ledger.sqlite3"
    ledger.record([_record("")], path)

    added = ledger.record([_record("", cwd="/home/dev/app")], path)

    assert added == 0
    assert [r.cwd for r in ledger.read(path)] == ["/home/dev/app"]


def _ci_run(run_id=1, attempt=1, **overrides):
    fields = dict(
        repo="you/app",
        workflow="ci.yml",
        run_id=run_id,
        attempt=attempt,
        event="push",
        status="completed",
        conclusion="success",
        head_sha="a" * 40,
        tree="b" * 40,
        created_at="2026-09-28T10:00:00Z",
        updated_at="2026-09-28T10:05:00Z",
        captured_at="2026-09-28T15:00:00Z",
    )
    fields.update(overrides)
    return CIRun(**fields)


def test_ci_runs_round_trip_and_each_attempt_is_its_own_row(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    runs = [_ci_run(attempt=1, conclusion="failure"), _ci_run(attempt=2)]

    assert ledger.record_ci_runs(runs, path) == 2
    assert ledger.record_ci_runs(runs, path) == 0

    assert ledger.read_ci_runs(path) == runs


def test_an_older_capture_never_overwrites_a_newer_one(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    done = _ci_run(conclusion="failure", captured_at="2026-09-28T16:00:00Z")
    running = _ci_run(
        status="in_progress", conclusion="", captured_at="2026-09-28T15:00:00Z"
    )

    ledger.record_ci_runs([done], path)
    ledger.record_ci_runs([running], path)

    assert ledger.read_ci_runs(path) == [done]


def test_schema_5_gains_the_ci_runs_table_and_keeps_its_rows(tmp_path, capsys):
    path = tmp_path / "ledger.sqlite3"
    old = _record("req_1:msg_1", cwd="/home/dev/app")
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute(ledger._V2_USAGE)
        conn.execute(ledger._V2_SUPERSEDED)
        conn.execute("PRAGMA user_version = 2")
    with closing(sqlite3.connect(path)) as conn:
        for step in (2, 3, 4):
            ledger._MIGRATIONS[step](conn)
        with conn:
            conn.execute(
                f"INSERT INTO usage VALUES ({', '.join('?' * 14)})",
                (*ledger._key_fields(old)[:11], "2026-07-01T00:00:00Z", "", old.cwd),
            )

    assert ledger.read(path) == [old]
    assert "schema 6: added the ci_runs table" in capsys.readouterr().err
    assert (tmp_path / "ledger.sqlite3.schema-5.bak").exists()
    assert ledger.read_ci_runs(path) == []
    ledger.record_ci_runs([_ci_run()], path)
    assert ledger.read_ci_runs(path) == [_ci_run()]
