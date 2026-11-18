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


# Plan limits: the meter and the limit stubs (ADR 0014, ADR 0015)


def _state_file(content):
    path = claude_code.config_path()
    path.write_text(json.dumps(content), encoding="utf-8")
    return path


METER = {
    "fetchedAtMs": 1792027789931,  # 2026-10-15T01:29:49Z
    "utilization": {
        "five_hour": {"utilization": 46, "resets_at": "2026-10-15T03:00:00+00:00"},
        "seven_day": {"utilization": 20, "resets_at": "2026-10-21T11:00:00+00:00"},
    },
}


def _stub_line(request, quota_key="quotaLimits"):
    stub = {
        "type": "assistant",
        "timestamp": "2026-10-14T03:46:59.434Z",
        "requestId": request,
        "sessionId": "sess",
        "error": "rate_limit",
        "isApiErrorMessage": True,
        "message": {"id": f"msg_{request}", "model": "<synthetic>"},
    }
    if quota_key:
        stub[quota_key] = {
            "status": "rejected",
            "rateLimitType": "seven_day",
            "resetsAt": 1792580400,
        }
    return json.dumps(stub)


def _claude_logs(tmp_path, *lines):
    root = tmp_path / "claude-logs"
    (root / "project").mkdir(parents=True)
    (root / "project" / "s.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return ((root, claude_code.iter_usage_records),)


def _limit_problems(d):
    return [p for p in d.problems if "meter" in p or "rate_limit" in p]


def test_a_readable_meter_is_shown_and_no_problem(tmp_path):
    _state_file({"cachedUsageUtilization": METER})

    d = doctor.diagnose(FIXTURE_SOURCES, tmp_path / "l")

    assert _limit_problems(d) == []
    out = doctor.render(d)
    assert "meter: fetched 2026-10-15T01:29:49Z · five_hour 46% · seven_day 20%" in out


@pytest.mark.parametrize(
    "content, shown",
    [
        (None, "meter: no state file at"),
        ({"oauthAccount": {}}, "API-key use has none"),
    ],
    ids=["no state file", "no meter key"],
)
def test_no_meter_is_information_not_a_problem(tmp_path, content, shown):
    if content is not None:
        _state_file(content)

    d = doctor.diagnose(FIXTURE_SOURCES, tmp_path / "l")

    assert _limit_problems(d) == []
    assert shown in doctor.render(d)


def test_a_reshaped_meter_is_a_problem(tmp_path):
    reshaped = {**METER, "utilization": {"session": {"percent": 46}}}
    _state_file({"cachedUsageUtilization": reshaped})

    d = doctor.diagnose(FIXTURE_SOURCES, tmp_path / "l")

    assert len(_limit_problems(d)) == 1
    assert "gives no reading; its shape changed" in _limit_problems(d)[0]


def test_every_rate_limit_stub_read_is_no_problem(tmp_path):
    logs = _claude_logs(tmp_path, _stub_line("req_1"), _stub_line("req_1"))

    d = doctor.diagnose(logs, tmp_path / "l")

    assert (d.limit_stubs.stubs, d.limit_stubs.unreadable) == (1, 0)  # copied once
    assert _limit_problems(d) == []
    assert "limit hits: 1 (seven_day 1)" in doctor.render(d)


@pytest.mark.parametrize("quota_key", ["quotaLimit", None], ids=["renamed", "absent"])
def test_a_rate_limit_stub_that_gives_no_hit_is_a_problem(tmp_path, quota_key):
    logs = _claude_logs(tmp_path, _stub_line("req_1"), _stub_line("req_2", quota_key))

    d = doctor.diagnose(logs, tmp_path / "l")

    assert (d.limit_stubs.stubs, d.limit_stubs.unreadable) == (2, 1)
    assert _limit_problems(d) == [
        "claude-code: 1 of 2 rate_limit stubs give no limit hit; quotaLimits "
        "may have changed, so those ceilings go unseen"
    ]
