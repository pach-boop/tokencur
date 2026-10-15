"""Claude Code's own usage meter, read from its state file (ADR 0015)."""

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime

import pytest

from tokencur import cli, ledger, limits, sources
from tokencur.ingest import claude_code
from tokencur.records import MeterReading, UsageRecord

FETCHED_MS = 1792027789931  # 2026-10-15T01:29:49.931Z


def _state(path, five=46, week=20, **extra):
    """A Claude Code state file: the meter, next to account details and
    settings tokencur must never read."""
    state = {
        "oauthAccount": {"emailAddress": "someone@example.com"},
        "projects": {"/home/someone/secret": {"allowedTools": ["Bash"]}},
        "cachedUsageUtilization": {
            "fetchedAtMs": FETCHED_MS,
            "accountUuid": "00000000-0000-0000-0000-000000000000",
            "utilization": {
                "five_hour": {
                    "utilization": five,
                    "resets_at": "2026-10-15T02:59:59.835212+00:00",
                },
                "seven_day": {
                    "utilization": week,
                    "resets_at": "2026-10-21T10:59:59.835233+00:00",
                },
                "iguana_necktie": {"utilization": 0, "limit_dollars": 250},
            },
        },
        **extra,
    }
    path.write_text(json.dumps(state), encoding="utf-8")
    return path


def _t(text):
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def _call(when, dollars):
    return UsageRecord(
        timestamp=f"{when}Z",
        workspace="w",
        session_id="s",
        model="claude-opus-4-8",
        input_tokens=round(dollars / 5 * 1_000_000),
        output_tokens=0,
        cache_read_tokens=0,
        cache_write_5m_tokens=0,
        cache_write_1h_tokens=0,
        record_id=f"r:{when}",
    )


def _reading(kind, percent, at="2026-10-15T01:29:49Z", resets="2026-10-15T03:00:00Z"):
    return MeterReading("claude-code", kind, at, percent, resets)


# Reading the state file


def test_the_meter_is_read_and_nothing_else(tmp_path):
    readings = claude_code.meter_readings(_state(tmp_path / ".claude.json"))

    assert readings == [
        _reading("five_hour", 46.0),
        _reading("seven_day", 20.0, resets="2026-10-21T11:00:00Z"),
    ]
    text = repr(readings)
    assert "someone" not in text and "secret" not in text and "0000" not in text


def test_the_state_file_follows_claude_config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    _state(tmp_path / ".claude.json")

    assert claude_code.config_path() == tmp_path / ".claude.json"
    assert len(claude_code.meter_readings()) == 2


@pytest.mark.parametrize(
    "content",
    [
        None,  # no file
        "not json",
        "[]",
        json.dumps({"cachedUsageUtilization": {"fetchedAtMs": "soon"}}),
        json.dumps({"cachedUsageUtilization": {"fetchedAtMs": 1e30}}),
        json.dumps({"other": 1}),
    ],
)
def test_an_absent_or_odd_state_file_gives_no_readings(tmp_path, content):
    path = tmp_path / ".claude.json"
    if content is not None:
        path.write_text(content, encoding="utf-8")

    assert claude_code.meter_readings(path) == []


@pytest.mark.parametrize("bad", [-1, 101, "46", True, None])
def test_a_percent_that_is_no_percent_is_skipped(tmp_path, bad):
    readings = claude_code.meter_readings(_state(tmp_path / "s.json", five=bad))

    assert [r.kind for r in readings] == ["seven_day"]


# The ledger


def test_readings_round_trip_and_a_reread_adds_nothing(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    readings = [_reading("five_hour", 46.0), _reading("seven_day", 20.0)]

    assert ledger.record_meter_readings(readings, path) == 2
    assert ledger.record_meter_readings(readings, path) == 0
    assert ledger.read_meter_readings(path) == readings


def test_schema_7_gains_the_meter_readings_table(tmp_path, capsys):
    path = tmp_path / "ledger.sqlite3"
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute(ledger._V2_USAGE)
        conn.execute(ledger._V2_SUPERSEDED)
        conn.execute("PRAGMA user_version = 2")
    with closing(sqlite3.connect(path)) as conn:
        for step in (2, 3, 4, 5, 6):
            ledger._MIGRATIONS[step](conn)

    assert ledger.read_meter_readings(path) == []
    assert "schema 8: added the meter_readings table" in capsys.readouterr().err
    assert (tmp_path / "ledger.sqlite3.schema-7.bak").exists()


# The view

NOW = _t("2026-10-15T01:33:00")
RECORDS = [_call("2026-10-14T22:30:00", 46.0), _call("2026-10-15T01:31:00", 4.0)]


def test_the_meter_fixes_the_window_and_implies_the_limit():
    result = limits.limits(
        RECORDS, [], None, NOW, readings=[_reading("five_hour", 46.0)]
    )

    window = result.current["five_hour"]
    assert (window.start, window.end, window.estimated) == (
        _t("2026-10-14T22:00"),
        _t("2026-10-15T03:00"),
        False,
    )
    assert window.meter_percent == 46.0
    assert window.meter_limit == pytest.approx(100.0)  # $46 was 46%
    out = limits.render(result, tz=UTC)
    assert "5 hours: $50.00 = 50% of the limit by Claude Code's meter (46% at" in out
    assert "the meter puts the whole limit at about $100.00 of usage value" in out


def test_a_low_reading_is_shown_but_implies_no_limit():
    result = limits.limits(
        RECORDS, [], None, NOW, readings=[_reading("five_hour", 4.0)]
    )

    assert result.current["five_hour"].meter_limit is None
    assert "Claude Code's meter read 4% at" in limits.render(result, tz=UTC)


def test_a_reading_from_a_closed_window_is_not_current():
    old = _reading(
        "five_hour", 90.0, at="2026-10-14T20:00:00Z", resets="2026-10-14T22:00:00Z"
    )

    result = limits.limits(RECORDS, [], None, NOW, readings=[old])

    assert result.current["five_hour"].meter_percent is None


def test_the_latest_reading_in_the_window_wins():
    readings = [
        _reading("five_hour", 23.0, at="2026-10-14T23:00:00Z"),
        _reading("five_hour", 46.0),
    ]

    result = limits.limits(RECORDS, [], None, NOW, readings=readings)

    assert result.current["five_hour"].meter_percent == 46.0


def test_the_meter_places_the_week_and_the_projection_uses_its_limit():
    records = [_call("2026-10-14T12:00:00", 200.0)]
    week = _reading("seven_day", 20.0, resets="2026-10-21T11:00:00Z")

    result = limits.limits(records, [], None, NOW, readings=[week])
    out = limits.render(result, tz=UTC)

    window = result.current["seven_day"]
    assert (window.start, window.end) == (
        _t("2026-10-14T11:00"),
        _t("2026-10-21T11:00"),
    )
    assert window.meter_limit == pytest.approx(1000.0)
    assert "under the limit (about $1,000.00 by the meter)" in out


def test_limits_reads_and_keeps_the_meter(tmp_path, monkeypatch, capsys):
    root = tmp_path / "projects"
    root.mkdir()
    monkeypatch.setattr(
        sources, "DEFAULT_SOURCES", ((root, claude_code.iter_usage_records),)
    )
    monkeypatch.setattr(cli, "load_records", lambda: RECORDS)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    _state(tmp_path / ".claude.json")

    assert cli.main(["limits"]) == 0

    assert len(ledger.read_meter_readings()) == 2
    assert "by Claude Code's meter" in capsys.readouterr().out


# Found in review


def test_a_reading_with_almost_no_usage_here_implies_no_limit():
    """Usage on claude.ai or another machine: 20% read, $0.40 logged."""
    records = [_call("2026-10-14T12:00:00", 0.4), _call("2026-10-15T01:31:00", 50.0)]
    week = _reading("seven_day", 20.0, resets="2026-10-21T11:00:00Z")

    result = limits.limits(records, [], None, NOW, readings=[week])
    out = limits.render(result, tz=UTC)

    assert result.current["seven_day"].meter_limit is None
    assert "too little to tell what the whole limit is worth" in out
    assert "already past" not in out


def test_a_current_reading_places_the_week_over_an_old_schedule():
    old_hit = [
        limits.LimitHit(
            "claude-code",
            "r",
            "2026-09-21T09:00:00Z",
            "s",
            "seven_day",
            "2026-09-21T10:00:00Z",
        )
    ]
    week = _reading("seven_day", 20.0, resets="2026-10-21T11:00:00Z")

    result = limits.limits(RECORDS, old_hit, None, NOW, readings=[week])

    window = result.current["seven_day"]
    assert (window.start, window.end) == (
        _t("2026-10-14T11:00"),
        _t("2026-10-21T11:00"),
    )
    assert window.meter_percent == 20.0


def test_a_past_reading_places_later_weeks_like_a_hit():
    past = _reading(
        "seven_day", 50.0, at="2026-10-01T00:00:00Z", resets="2026-10-07T11:00:00Z"
    )

    result = limits.limits(RECORDS, [], None, NOW, readings=[past])

    window = result.current["seven_day"]
    assert (window.start, window.end) == (
        _t("2026-10-14T11:00"),
        _t("2026-10-21T11:00"),
    )
    assert window.meter_percent is None


@pytest.mark.parametrize(
    "resets", ["9999-12-31T23:59:59.9+00:00", "0001-01-01T00:00:00+05:00"]
)
def test_an_extreme_reset_is_skipped_not_fatal(tmp_path, resets):
    path = _state(tmp_path / "s.json")
    state = json.loads(path.read_text(encoding="utf-8"))
    state["cachedUsageUtilization"]["utilization"]["five_hour"]["resets_at"] = resets
    path.write_text(json.dumps(state), encoding="utf-8")

    assert [r.kind for r in claude_code.meter_readings(path)] == ["seven_day"]


def test_a_reset_without_a_zone_reads_as_utc(tmp_path, monkeypatch):
    monkeypatch.setenv("TZ", "America/Mexico_City")
    path = _state(tmp_path / "s.json")
    state = json.loads(path.read_text(encoding="utf-8"))
    state["cachedUsageUtilization"]["utilization"]["five_hour"]["resets_at"] = (
        "2026-10-15T02:59:59.835"
    )
    path.write_text(json.dumps(state), encoding="utf-8")

    (five, _) = claude_code.meter_readings(path)
    assert five.resets_at == "2026-10-15T03:00:00Z"
