import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from tokencur import cli, doctor, ledger, sources
from tokencur.ingest import claude_code, codex, kimi_code

FIXTURE_LOGS = Path(__file__).parent / "fixtures" / "logs"
FIXTURE_SOURCES = (
    (FIXTURE_LOGS / "claude-code", claude_code.iter_usage_records),
    (FIXTURE_LOGS / "codex", codex.iter_usage_records),
    (FIXTURE_LOGS / "kimi-code", kimi_code.iter_usage_records),
)


def _check(diagnosis, name):
    return next(c for c in diagnosis.sources if c.name == name)


def test_healthy_logs_pass_with_what_each_scan_saw(tmp_path):
    d = doctor.diagnose(FIXTURE_SOURCES, tmp_path / "ledger.sqlite3")

    assert d.problems == []
    claude, cx, kimi = (_check(d, n) for n in ("claude-code", "codex", "kimi-code"))
    assert (claude.stats.files, claude.stats.usage_lines, claude.records) == (2, 7, 5)
    assert (cx.stats.files, cx.stats.usage_lines, cx.records) == (1, 4, 2)
    assert (kimi.stats.files, kimi.stats.usage_lines, kimi.records) == (1, 2, 2)
    assert dict(claude.stats.versions) == {"2.1.280": 7}
    assert dict(cx.stats.versions) == {"0.120.0": 1}
    assert "all checks passed" in doctor.render(d)


def test_a_renamed_usage_field_reads_as_a_format_change(tmp_path):
    """An agent update that moves usage elsewhere must not pass silently
    as "no usage this month"."""
    log = tmp_path / "logs" / "workspace" / "session.jsonl"
    log.parent.mkdir(parents=True)
    line = {
        "type": "assistant",
        "requestId": "r",
        "message": {
            "id": "m",
            "model": "claude-opus-5-5",
            "token_usage": {"input_tokens": 9},
        },
    }
    log.write_text(json.dumps(line), encoding="utf-8")

    d = doctor.diagnose(
        ((log.parents[1], claude_code.iter_usage_records),), tmp_path / "l"
    )

    assert d.problems == [
        "claude-code: 1 log files hold no usage lines; the log format may have changed"
    ]


def test_unreadable_usage_lines_are_flagged(tmp_path):
    log = tmp_path / "logs" / "rollout.jsonl"
    log.parent.mkdir(parents=True)
    report = {
        "type": "event_msg",
        "timestamp": "2026-09-15T10:00:00.000Z",
        "payload": {
            "type": "token_count",
            "info": {"last_token_usage": {"input_tokens": "1200", "output_tokens": 5}},
        },
    }
    log.write_text(json.dumps(report), encoding="utf-8")

    d = doctor.diagnose(((log.parent, codex.iter_usage_records),), tmp_path / "l")

    assert d.problems == [
        "codex: 1 usage lines, no records; the log format may have changed"
    ]
    assert _check(d, "codex").stats.malformed == 1


def test_a_damaged_ledger_is_reported_not_raised(tmp_path, monkeypatch, capsys):
    path = tmp_path / "ledger.sqlite3"
    path.write_bytes(b"this is not a database at all" * 100)
    monkeypatch.setenv("TOKENCUR_LEDGER", str(path))
    monkeypatch.setattr(sources, "DEFAULT_SOURCES", FIXTURE_SOURCES)

    d = doctor.diagnose(FIXTURE_SOURCES, path)
    assert d.problems and d.problems[0].startswith(
        "ledger: not a readable SQLite ledger"
    )

    assert cli.main(["report"]) == 1  # a clear error, no traceback
    err = capsys.readouterr().err
    assert "is not a readable tokencur ledger" in err and "Traceback" not in err


def test_a_ledger_from_a_newer_tokencur_is_flagged(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(f"PRAGMA user_version = {ledger.SCHEMA_VERSION + 1}")

    d = doctor.diagnose(FIXTURE_SOURCES, path)

    assert any("written by a newer tokencur" in p for p in d.problems)


def test_doctor_never_migrates_or_writes_the_ledger(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute(ledger._V1_USAGE)
        conn.execute("PRAGMA user_version = 1")
    before = path.read_bytes()

    d = doctor.diagnose(FIXTURE_SOURCES, path)

    assert d.ledger.schema == 1 and d.problems == []
    assert path.read_bytes() == before
    assert not list(tmp_path.glob("*.bak"))


@pytest.mark.parametrize(("logs_ok", "code"), [(True, 0), (False, 1)])
def test_the_doctor_command_exits_1_on_any_problem(
    tmp_path, monkeypatch, capsys, logs_ok, code
):
    if logs_ok:
        monkeypatch.setattr(sources, "DEFAULT_SOURCES", FIXTURE_SOURCES)
    else:
        log = tmp_path / "logs" / "w" / "s.jsonl"
        log.parent.mkdir(parents=True)
        log.write_text(
            '{"type": "assistant", "message": {"usage": "lots"}}', encoding="utf-8"
        )
        monkeypatch.setattr(
            sources,
            "DEFAULT_SOURCES",
            ((tmp_path / "logs", claude_code.iter_usage_records),),
        )
    monkeypatch.setattr(doctor, "DEFAULT_SOURCES", sources.DEFAULT_SOURCES)

    assert cli.main(["doctor"]) == code
    assert "tokencur doctor" in capsys.readouterr().out


def test_the_ledger_check_counts_records_that_name_their_directory(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    ledger.record(
        [
            r
            for root, ingest in FIXTURE_SOURCES
            for r in ingest(root)
            if r.source != "kimi-code"
        ],
        path,
    )
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("UPDATE usage SET cwd = '' WHERE source = 'codex'")

    d = doctor.diagnose(FIXTURE_SOURCES, path)

    assert (d.ledger.with_cwd, sum(d.ledger.records.values())) == (5, 7)
    assert "5 of 7 records name their working directory" in doctor.render(d)
