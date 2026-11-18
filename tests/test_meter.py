"""Claude Code's own usage meter, read from its state file (ADR 0015)."""

import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

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


def test_the_session_logs_follow_claude_config_dir(tmp_path, monkeypatch):
    """The logs and the state file move together, so the meter is never
    read from one Claude Code and the usage from another."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    assert claude_code.projects_path() == tmp_path / "projects"

    monkeypatch.delenv("CLAUDE_CONFIG_DIR")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert claude_code.projects_path() == tmp_path / ".claude" / "projects"
    assert claude_code.config_path() == tmp_path / ".claude.json"


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


def test_the_json_takes_the_percent_from_the_meter():
    records = [_call("2026-10-14T12:00:00", 200.0)]
    week = _reading("seven_day", 20.0, resets="2026-10-21T11:00:00Z")

    figures = limits.as_dict(limits.limits(records, [], None, NOW, readings=[week]))

    seven = figures["windows"]["seven_day"]
    assert (seven["percent"], seven["percent_of"]) == (20.0, "meter")
    assert seven["meter"] == {
        "percent": 20.0,
        "read_at": "2026-10-15T01:29:49Z",
        "limit": 1000.0,
        "base_percent": 0.0,
        "readings": 1,
    }
    assert seven["projection"]["limit_from"] == "meter"


def test_the_json_gives_a_low_reading_as_read():
    result = limits.limits(
        RECORDS, [], None, NOW, readings=[_reading("five_hour", 4.0)]
    )

    five = limits.as_dict(result)["windows"]["five_hour"]
    assert (five["percent"], five["percent_of"]) == (4.0, "meter_reading")
    assert five["meter"]["limit"] is None


WEEK_RESETS = "2026-10-21T11:00:00Z"
#: a week whose meter reads 5% before any usage, then a point per $10
WEEK_CALLS = [
    _call("2026-10-14T12:00:00", 150.0),
    _call("2026-10-14T18:00:00", 300.0),
    _call("2026-10-15T00:00:00", 300.0),
]


def _week_readings(
    *percents, at=("2026-10-14T13:00", "2026-10-14T19:00", "2026-10-15T01:00")
):
    return [
        _reading("seven_day", percent, at=f"{moment}:00Z", resets=WEEK_RESETS)
        for percent, moment in zip(percents, at, strict=False)
    ]


def test_several_readings_place_the_meter_on_a_line():
    result = limits.limits(
        WEEK_CALLS, [], None, NOW, readings=_week_readings(20, 50, 80)
    )

    window = result.current["seven_day"]
    assert window.meter_base == pytest.approx(5.0)
    assert window.meter_limit == pytest.approx(950.0)  # (100 - 5) points at $10
    assert window.meter_readings == 3
    assert limits.meter_share(window) == pytest.approx(0.80)  # 5 + 750 / 10
    out = limits.render(result, tz=UTC)
    assert "= 80% of the limit by Claude Code's meter (a line through 3 readings" in out
    assert (
        "about $950.00 of usage value (it starts at 5.0% and adds a point per $10.00)"
        in out
    )
    meter = limits.as_dict(result)["windows"]["seven_day"]["meter"]
    assert (meter["base_percent"], meter["readings"]) == (5.0, 3)


def test_a_single_reading_would_put_that_week_lower():
    """The same week read once at 20%: its share puts the limit at $750."""
    result = limits.limits(WEEK_CALLS, [], None, NOW, readings=_week_readings(20))

    window = result.current["seven_day"]
    assert (window.meter_base, window.meter_readings) == (0.0, 1)
    assert window.meter_limit == pytest.approx(750.0)  # $150 was 20%


@pytest.mark.parametrize(
    "percents",
    [(20, 50), (70, 75, 80)],
    ids=["too few readings", "too close together"],
)
def test_without_a_line_the_latest_reading_sets_the_limit(percents):
    at = ("2026-10-14T13:00", "2026-10-14T19:00", "2026-10-15T01:00")[-len(percents) :]
    result = limits.limits(
        WEEK_CALLS, [], None, NOW, readings=_week_readings(*percents, at=at)
    )

    window = result.current["seven_day"]
    assert window.meter_readings == 1
    assert window.meter_limit == pytest.approx(750.0 / (percents[-1] / 100))


def test_the_line_leaves_out_readings_from_before_the_plan():
    plans = {
        "plans": {
            "Claude Code": [
                {"monthly_usd": 100.0, "from": "2026-10-01", "until": "2026-10-15"},
                {"monthly_usd": 200.0, "from": "2026-10-15"},
            ]
        }
    }

    result = limits.limits(
        WEEK_CALLS, [], plans, NOW, tz=UTC, readings=_week_readings(20, 50, 80)
    )

    window = result.current["seven_day"]
    assert window.meter_readings == 1  # only the 01:00 reading is on the new plan
    assert window.meter_limit == pytest.approx(750.0 / 0.80)


def test_a_line_that_falls_is_no_line():
    result = limits.limits(
        WEEK_CALLS, [], None, NOW, readings=_week_readings(80, 50, 20)
    )

    assert result.current["seven_day"].meter_readings == 1


def test_limits_reads_and_keeps_the_meter(tmp_path, monkeypatch, capsys):
    root = tmp_path / "projects"
    root.mkdir()
    monkeypatch.setattr(
        sources, "DEFAULT_SOURCES", ((root, claude_code.iter_usage_records),)
    )
    monkeypatch.setattr(cli, "load_records", lambda: RECORDS)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    # The command reads the real clock, and a reading counts only while its
    # window is open: the week in this state file closed on 2026-10-21.
    monkeypatch.setattr(limits, "limits", partial(limits.limits, now=NOW))
    _state(tmp_path / ".claude.json")

    assert cli.main(["limits"]) == 0

    assert len(ledger.read_meter_readings()) == 2
    captured = capsys.readouterr()
    assert "by Claude Code's meter" in captured.out
    assert "warning" not in captured.err


def test_limits_warns_in_one_line_when_the_meter_is_reshaped(
    tmp_path, monkeypatch, capsys
):
    root = tmp_path / "projects"
    root.mkdir()
    monkeypatch.setattr(
        sources, "DEFAULT_SOURCES", ((root, claude_code.iter_usage_records),)
    )
    monkeypatch.setattr(cli, "load_records", lambda: RECORDS)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    (tmp_path / ".claude.json").write_text(
        json.dumps({"cachedUsageUtilization": {"fetchedAtMs": FETCHED_MS}}),
        encoding="utf-8",
    )

    assert cli.main(["limits", "--json"]) == 0

    captured = capsys.readouterr()
    json.loads(captured.out)  # the JSON stays clean for scripts
    warnings = captured.err.splitlines()
    assert len(warnings) == 1
    assert warnings[0].startswith("warning: claude-code: the usage meter")
    assert warnings[0].endswith("(see `tokencur doctor`)")


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


# A model's own weekly limit (Fable, since 2026-11)

WEEK_RESET = "2026-10-21T10:59:59.835233+00:00"


def _model_limits(path, fable=60, **extra):
    """A state file whose meter lists a weekly limit of Fable's own, next
    to limits tokencur leaves out: the session, one scoped to a surface,
    and entries that are no limit."""
    _state(path)
    state = json.loads(path.read_text(encoding="utf-8"))
    state["cachedUsageUtilization"]["utilization"]["limits"] = [
        {"kind": "session", "percent": 46, "resets_at": WEEK_RESET, "scope": None},
        {"kind": "weekly_all", "percent": 20, "resets_at": WEEK_RESET, "scope": None},
        {
            "kind": "weekly_scoped",
            "percent": fable,
            "severity": "warning",
            "resets_at": WEEK_RESET,
            "scope": {"model": {"id": None, "display_name": "Fable"}, "surface": None},
            "is_active": True,
        },
        {
            "kind": "weekly_scoped",
            "percent": 10,
            "resets_at": WEEK_RESET,
            "scope": {"model": None, "surface": {"display_name": "Cowork"}},
        },
        {"kind": "weekly_scoped", "percent": 5, "resets_at": WEEK_RESET},
        "not a limit",
    ]
    state["cachedUsageUtilization"]["utilization"].update(extra)
    path.write_text(json.dumps(state), encoding="utf-8")
    return path


def _priced_call(when, dollars, model, input_rate):
    return replace(
        _call(when, dollars),
        model=model,
        input_tokens=round(dollars / input_rate * 1_000_000),
        record_id=f"r:{model}:{when}",
    )


FABLE_RECORDS = [
    *RECORDS,  # $50 of Opus 4.8
    _priced_call("2026-10-14T23:00:00", 30.0, "claude-fable-5-2", 8.0),
]
WEEK_READINGS = [
    _reading("seven_day", 20.0, resets="2026-10-21T11:00:00Z"),
    _reading("seven_day:Fable", 60.0, resets="2026-10-21T11:00:00Z"),
]


def test_a_models_own_weekly_limit_is_read_and_other_scopes_are_not(tmp_path):
    readings = claude_code.meter_readings(_model_limits(tmp_path / ".claude.json"))

    assert readings == [
        _reading("five_hour", 46.0),
        _reading("seven_day", 20.0, resets="2026-10-21T11:00:00Z"),
        _reading("seven_day:Fable", 60.0, resets="2026-10-21T11:00:00Z"),
    ]


@pytest.mark.parametrize("bad", [-1, 101, "60", None])
def test_a_model_limit_that_is_no_percent_is_skipped(tmp_path, bad):
    readings = claude_code.meter_readings(_model_limits(tmp_path / "s.json", bad))

    assert [r.kind for r in readings] == ["five_hour", "seven_day"]


def test_a_reshaped_list_of_limits_gives_the_two_windows_still(tmp_path):
    path = _model_limits(tmp_path / "s.json", limits={"kind": "weekly_scoped"})

    assert [r.kind for r in claude_code.meter_readings(path)] == [
        "five_hour",
        "seven_day",
    ]


@pytest.mark.parametrize(
    ("name", "model", "same"),
    [
        ("Fable", "claude-fable-5-2", True),
        ("Fable", "claude-fable-5", True),
        ("Fable 5.2", "claude-fable-5-2", True),
        ("Fable 5.2", "claude-fable-5-1", False),
        ("Fable", "claude-mythos-5-2", False),  # the meter names Fable only
        ("Fable", "claude-opus-5-5", False),
        ("Opus", "claude-opus-5-5", True),
        ("", "claude-fable-5-2", False),
    ],
)
def test_a_meter_name_picks_its_model_ids(name, model, same):
    assert limits.of_model(name, model) is same


def test_a_model_limit_is_valued_by_that_models_calls_only():
    result = limits.limits(FABLE_RECORDS, [], None, NOW, readings=WEEK_READINGS)

    fable = result.models["Fable"]
    assert (fable.start, fable.end) == (_t("2026-10-14T11:00"), _t("2026-10-21T11:00"))
    assert fable.value == pytest.approx(30.0)  # the $50 of Opus are not Fable's
    assert fable.meter_limit == pytest.approx(50.0)  # $30 was 60%
    assert result.current["seven_day"].value == pytest.approx(80.0)
    out = limits.render(result, tz=UTC)
    assert (
        "  Fable has a weekly limit of its own: $30.00 of Fable's usage value = "
        "60% of it by Claude Code's meter (60% at Thu 2026-10-15 01:29 UTC); the "
        "meter puts it at about $50.00, $20.00 left"
    ) in out


def test_the_json_gives_a_model_limit_under_the_week():
    result = limits.limits(FABLE_RECORDS, [], None, NOW, readings=WEEK_READINGS)

    week = limits.as_dict(result)["windows"]["seven_day"]

    fable = week["models"]["Fable"]
    assert (fable["usage_value"], fable["percent"], fable["percent_of"]) == (
        30.0,
        60.0,
        "meter",
    )
    assert fable["meter"]["limit"] == 50.0


def test_without_a_model_limit_the_week_lists_none():
    result = limits.limits(FABLE_RECORDS, [], None, NOW, readings=WEEK_READINGS[:1])

    assert limits.as_dict(result)["windows"]["seven_day"]["models"] == {}
    assert "of its own" not in limits.render(result, tz=UTC)


def test_a_model_limit_reached_says_only_that_model_waits():
    full = [WEEK_READINGS[0], replace(WEEK_READINGS[1], percent=100.0)]

    result = limits.limits(FABLE_RECORDS, [], None, NOW, readings=full)

    assert result.models["Fable"].blocked
    assert "Fable waits until Wed 2026-10-21 11:00 UTC, other models do not" in (
        limits.render(result, tz=UTC)
    )


def test_a_low_model_reading_implies_no_limit():
    low = [WEEK_READINGS[0], replace(WEEK_READINGS[1], percent=4.0)]

    result = limits.limits(FABLE_RECORDS, [], None, NOW, readings=low)

    assert result.models["Fable"].meter_limit is None
    assert "Claude Code's meter read 4% at" in limits.render(result, tz=UTC)


def test_a_model_reading_from_a_closed_week_is_not_current():
    old = replace(
        WEEK_READINGS[1],
        fetched_at="2026-10-07T00:00:00Z",
        resets_at="2026-10-07T11:00:00Z",
    )

    result = limits.limits(FABLE_RECORDS, [], None, NOW, readings=[old])

    assert result.models == {}


def test_limits_keeps_a_model_limit_in_the_ledger(tmp_path, monkeypatch, capsys):
    root = tmp_path / "projects"
    root.mkdir()
    monkeypatch.setattr(
        sources, "DEFAULT_SOURCES", ((root, claude_code.iter_usage_records),)
    )
    monkeypatch.setattr(cli, "load_records", lambda: FABLE_RECORDS)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(limits, "limits", partial(limits.limits, now=NOW))
    _model_limits(tmp_path / ".claude.json")

    assert cli.main(["limits"]) == 0

    kinds = sorted(r.kind for r in ledger.read_meter_readings())
    assert kinds == ["five_hour", "seven_day", "seven_day:Fable"]
    assert "Fable has a weekly limit of its own" in capsys.readouterr().out
