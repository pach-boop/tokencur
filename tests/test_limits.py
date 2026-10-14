"""Plan limits from Claude Code's own quota metadata (ADR 0014)."""

import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from tokencur import cli, ledger, limits, sources
from tokencur.ingest import claude_code
from tokencur.records import LimitHit, UsageRecord

MEXICO_CITY = timezone(timedelta(hours=-6))


def _t(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def _call(when: str, dollars: float = 5.0) -> UsageRecord:
    """A Claude Code call worth ``dollars`` (claude-opus-4-8 input at $5/MTok)."""
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
        record_id=f"r:{when}:{dollars}",
    )


def _hit(when: str, kind: str, resets: str, request: str = "req") -> LimitHit:
    return LimitHit(
        source="claude-code",
        record_id=f"{request}:{when}",
        timestamp=f"{when}Z",
        session_id="s",
        kind=kind,
        resets_at=f"{resets}Z",
    )


def _stub(when: str, kind: str, resets: datetime, status="rejected", request="req_1"):
    """The line Claude Code logs when a plan limit turns a request away."""
    return json.dumps(
        {
            "type": "assistant",
            "timestamp": f"{when}Z",
            "requestId": request,
            "sessionId": "sess",
            "error": "rate_limit",
            "quotaLimits": {
                "status": status,
                "rateLimitType": kind,
                "resetsAt": int(resets.timestamp()),
            },
            "message": {
                "id": f"msg_{request}",
                "model": "<synthetic>",
                "content": [{"type": "text", "text": "[redacted]"}],
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
        }
    )


# Ingest


def test_a_refusal_is_read_from_metadata_once_per_request(tmp_path):
    resets = _t("2026-10-14T11:00:00")
    log = tmp_path / "project" / "sess.jsonl"
    log.parent.mkdir()
    log.write_text(
        "\n".join(
            [
                _stub("2026-10-14T03:46:59.434", "seven_day", resets),
                _stub("2026-10-14T03:46:59.434", "seven_day", resets),  # copied
                _stub("2026-10-14T03:49:39.617", "seven_day", resets, request="req_2"),
                _stub("2026-10-14T04:00:00", "seven_day", resets, status="allowed"),
                '{"quotaLimits": "not an object"}',
                "not json, mentions quotaLimits",
            ]
        ),
        encoding="utf-8",
    )

    hits = list(claude_code.iter_limit_hits(tmp_path))

    assert [(h.timestamp, h.kind, h.resets_at) for h in hits] == [
        ("2026-10-14T03:46:59.434Z", "seven_day", "2026-10-14T11:00:00Z"),
        ("2026-10-14T03:49:39.617Z", "seven_day", "2026-10-14T11:00:00Z"),
    ]
    assert {h.session_id for h in hits} == {"sess"}


@pytest.mark.parametrize("resets", [None, -1, "soon", True, 1e20])
def test_a_refusal_without_a_usable_reset_is_skipped(tmp_path, resets):
    line = json.loads(_stub("2026-10-14T03:46:59", "five_hour", _t("2026-10-14T05:00")))
    line["quotaLimits"]["resetsAt"] = resets
    (tmp_path / "s.jsonl").write_text(json.dumps(line), encoding="utf-8")

    assert list(claude_code.iter_limit_hits(tmp_path)) == []


def test_the_usage_scan_still_skips_the_stub(tmp_path):
    (tmp_path / "s.jsonl").write_text(
        _stub("2026-10-14T03:46:59", "seven_day", _t("2026-10-14T11:00")),
        encoding="utf-8",
    )

    assert list(claude_code.iter_usage_records(tmp_path)) == []


# Ledger


def test_limit_hits_round_trip_and_a_rescan_adds_nothing(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    hits = [
        _hit("2026-10-07T00:43:45", "seven_day", "2026-10-07T11:00:00"),
        _hit("2026-10-07T19:17:40", "five_hour", "2026-10-07T21:00:00"),
    ]

    assert ledger.record_limit_hits(hits, path) == 2
    assert ledger.record_limit_hits(hits, path) == 0
    assert ledger.read_limit_hits(path) == hits
    assert ledger.record_limit_hits([], tmp_path / "none.sqlite3") == 0
    assert not (tmp_path / "none.sqlite3").exists()


def test_schema_6_gains_the_limit_hits_table(tmp_path, capsys):
    path = tmp_path / "ledger.sqlite3"
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute(ledger._V2_USAGE)
        conn.execute(ledger._V2_SUPERSEDED)
        conn.execute("PRAGMA user_version = 2")
    with closing(sqlite3.connect(path)) as conn:
        for step in (2, 3, 4, 5):
            ledger._MIGRATIONS[step](conn)

    assert ledger.read_limit_hits(path) == []
    assert "schema 7: added the limit_hits table" in capsys.readouterr().err
    assert (tmp_path / "ledger.sqlite3.schema-6.bak").exists()


# Ceilings and windows

WEEKLY_RESET = "2026-10-14T11:00:00"  # Wednesday 05:00 in Mexico City
PLANS = {
    "plans": {
        "Claude Code": [
            {"monthly_usd": 20.0, "from": "2026-09-24", "until": "2026-10-02"},
            {"monthly_usd": 100.0, "from": "2026-10-02"},
        ]
    }
}


def test_a_ceiling_is_the_value_from_the_window_start_to_the_first_refusal():
    records = [
        _call("2026-10-07T10:59:59", 1000.0),  # before the week opened
        _call("2026-10-07T11:00:00", 600.0),
        _call("2026-10-13T20:00:00", 400.0),
        _call("2026-10-14T04:00:00", 50.0),  # after the hit: never reached
    ]
    hits = [
        _hit("2026-10-14T03:46:59", "seven_day", WEEKLY_RESET, "req_1"),
        _hit("2026-10-14T03:49:39", "seven_day", WEEKLY_RESET, "req_2"),  # a retry
    ]

    result = limits.limits(records, hits, PLANS, _t("2026-10-14T05:00"), tz=UTC)

    (ceiling,) = result.ceilings
    assert ceiling.value == pytest.approx(1000.0)
    assert ceiling.hit_at == _t("2026-10-14T03:46:59")
    assert ceiling.plan == "$100/month since 2026-10-02"


def test_plan_days_are_read_in_local_time():
    """19:15 in Mexico City on 2026-10-01 is already 2026-10-02 in UTC,
    the first day of the next plan. The person lived it on Pro."""
    hits = [_hit("2026-10-02T01:15:00", "five_hour", "2026-10-02T02:00:00")]
    now = _t("2026-10-10T00:00")

    local = limits.limits([], hits, PLANS, now, tz=MEXICO_CITY)
    utc = limits.limits([], hits, PLANS, now, tz=UTC)

    assert local.ceilings[0].plan == "$20/month since 2026-09-24"
    assert utc.ceilings[0].plan == "$100/month since 2026-10-02"


def test_the_week_is_placed_by_the_latest_logged_reset():
    hits = [_hit("2026-10-14T03:46:59", "seven_day", WEEKLY_RESET)]
    records = [_call("2026-10-20T12:00:00", 30.0), _call("2026-10-21T12:00:00", 7.0)]

    blocked = limits.limits([], hits, None, _t("2026-10-14T05:00"))
    later = limits.limits(records, hits, None, _t("2026-10-21T13:00"))

    week = blocked.current["seven_day"]
    assert (week.start, week.end, week.blocked) == (
        _t("2026-10-07T11:00"),
        _t(WEEKLY_RESET),
        True,
    )
    week = later.current["seven_day"]
    assert (week.start, week.end, week.blocked) == (
        _t("2026-10-21T11:00"),
        _t("2026-10-28T11:00"),
        False,
    )
    assert week.value == pytest.approx(7.0)


def test_without_a_weekly_hit_the_week_cannot_be_placed():
    result = limits.limits(
        [_call("2026-10-14T12:00:00")], [], None, _t("2026-10-14T13:00")
    )

    assert result.current["seven_day"] is None
    assert "the week cannot be placed" in limits.render(result, tz=UTC)


def test_five_hour_windows_are_rebuilt_from_call_times():
    records = [
        _call("2026-10-14T08:00:00"),  # opens 08:00-13:00
        _call("2026-10-14T12:59:00"),
        _call("2026-10-14T13:30:00"),  # opens 13:30-18:30
        _call("2026-10-14T14:00:00"),
    ]

    open_ = limits.limits(records, [], None, _t("2026-10-14T15:00"))
    closed = limits.limits(records, [], None, _t("2026-10-14T19:00"))

    window = open_.current["five_hour"]
    assert (window.start, window.end, window.estimated) == (
        _t("2026-10-14T13:30"),
        _t("2026-10-14T18:30"),
        True,
    )
    assert window.value == pytest.approx(10.0)
    assert closed.current["five_hour"] is None


def test_a_logged_five_hour_reset_fixes_the_window():
    hits = [_hit("2026-10-07T19:17:40", "five_hour", "2026-10-07T21:00:00")]
    records = [_call("2026-10-07T21:30:00"), _call("2026-10-08T01:00:00")]

    blocked = limits.limits(records, hits, None, _t("2026-10-07T20:00"))
    after = limits.limits(records, hits, None, _t("2026-10-08T01:30"))

    window = blocked.current["five_hour"]
    assert (window.start, window.end, window.blocked) == (
        _t("2026-10-07T16:00"),
        _t("2026-10-07T21:00"),
        True,
    )
    window = after.current["five_hour"]
    assert (window.start, window.end) == (
        _t("2026-10-07T21:30"),
        _t("2026-10-08T02:30"),
    )


def test_unknown_limit_kinds_are_named_not_measured():
    hits = [_hit("2026-10-14T03:00:00", "seven_day_opus", "2026-10-14T11:00:00")]

    result = limits.limits([], hits, None, _t("2026-10-14T05:00"))

    assert result.ceilings == []
    assert "not measured: seven_day_opus" in limits.render(result, tz=UTC)


# The terminal view


def _week_of(value_per_day: float):
    """Six days of this week at a steady pace, seen on Tuesday 2026-10-20
    11:00 UTC, a day before the reset; last week hit its limit at $700."""
    records = [_call("2026-10-07T11:30:00", 700.0)]
    start = _t("2026-10-14T11:00")
    for hour in range(6 * 24):
        moment = start + timedelta(hours=hour)
        records.append(_call(moment.strftime("%Y-%m-%dT%H:%M:%S"), value_per_day / 24))
    hits = [_hit("2026-10-13T20:00:00", "seven_day", WEEKLY_RESET)]
    return limits.limits(records, hits, PLANS, _t("2026-10-20T11:00"), tz=UTC)


def test_the_view_projects_when_the_lowest_ceiling_would_come():
    """$660 used, $94.29/day over the last 7 days: $40 left lasts ~10 h."""
    out = limits.render(_week_of(110.0), tz=UTC)

    assert "plan in force: $100/month since 2026-10-02" in out
    assert "week: $660.00 = 94% of the lowest ceiling" in out
    assert "limit hit on this plan at $700.00 (Tue 2026-10-13 20:00 UTC)" in out
    assert (
        "reaches the lowest ceiling seen ($700.00) around Tue 2026-10-20 21:10" in out
    )
    assert "13h 49m before it resets" in out
    assert "reproducible with tokencur" in out


def test_the_view_says_when_the_week_stays_under():
    out = limits.render(_week_of(50.0), tz=UTC)

    assert "the week ends near $342.86, under the lowest ceiling seen ($700.00)" in out


def test_the_view_says_when_a_window_is_already_past_the_lowest_ceiling():
    out = limits.render(_week_of(200.0), tz=UTC)

    assert "already past the lowest ceiling seen ($700.00)" in out


def test_without_plans_every_ceiling_counts_and_the_view_says_why():
    hits = [_hit("2026-10-02T01:15:00", "five_hour", "2026-10-02T02:00:00")]
    records = [_call("2026-10-01T22:00:00", 40.0)]

    out = limits.render(
        limits.limits(records, hits, None, _t("2026-10-03T00:00")), tz=UTC
    )

    assert "plan in force: unknown (pass --plans" in out
    assert "limit hit at $40.00 (Fri 2026-10-02 01:15 UTC)" in out


def test_ceilings_only_on_other_plans_are_not_this_plans():
    hits = [_hit("2026-09-30T20:00:00", "five_hour", "2026-09-30T22:00:00")]

    out = limits.render(
        limits.limits([], hits, PLANS, _t("2026-10-14T00:00"), tz=UTC), tz=UTC
    )

    assert "limit hit 1 times on other plans, never on this one" in out


def test_five_hour_windows_open_on_ten_minute_marks():
    """The real case: a first call at 16:09:55 opened 16:00-21:00 (the
    logged reset), and the call right after it opened 21:00-02:00."""
    records = [_call("2026-10-07T16:09:55"), _call("2026-10-07T21:01:46")]

    first = limits.limits(records, [], None, _t("2026-10-07T17:00"))
    second = limits.limits(records, [], None, _t("2026-10-07T22:00"))

    assert (first.current["five_hour"].start, first.current["five_hour"].end) == (
        _t("2026-10-07T16:00"),
        _t("2026-10-07T21:00"),
    )
    assert (second.current["five_hour"].start, second.current["five_hour"].end) == (
        _t("2026-10-07T21:00"),
        _t("2026-10-08T02:00"),
    )


def test_a_ceiling_whose_window_began_on_another_plan_is_no_reference():
    """The week that hit on 2026-10-06 began on 2026-09-30, under Pro."""
    records = [
        _call("2026-10-01T12:00:00", 200.0),  # Pro days
        _call("2026-10-05T12:00:00", 1100.0),
        _call("2026-10-12T12:00:00", 1000.0),
        _call("2026-10-14T12:00:00", 100.0),
    ]
    hits = [
        _hit("2026-10-07T00:43:00", "seven_day", "2026-10-07T11:00:00"),
        _hit("2026-10-14T03:46:00", "seven_day", WEEKLY_RESET),
    ]

    result = limits.limits(records, hits, PLANS, _t("2026-10-15T00:00"), tz=UTC)
    out = limits.render(result, tz=UTC)

    assert [c.spans_plans for c in result.ceilings] == [True, False]
    assert "limit hit on this plan at $1,000.00 (Wed 2026-10-14 03:46 UTC)" in out
    assert "also hit at $1,300.00 (Wed 2026-10-07 00:43 UTC), in a window" in out
    assert "week: $100.00 = 10% of the lowest ceiling" in out


def test_a_ceiling_with_no_usage_here_is_no_reference():
    """The same subscription used on another machine: a hit this machine's
    logs hold almost no usage for."""
    hits = [_hit("2026-10-14T03:46:00", "seven_day", WEEKLY_RESET)]
    records = [_call("2026-10-14T12:00:00", 50.0)]

    out = limits.render(
        limits.limits(records, hits, PLANS, _t("2026-10-15T00:00"), tz=UTC), tz=UTC
    )

    assert "also hit at Wed 2026-10-14 03:46 UTC with $0.00 in these logs" in out
    assert "already past" not in out
    assert "week: $50.00 since" in out


@pytest.mark.parametrize(
    "resets",
    ["1970-01-01T00:00:00", "2026-10-14T03:00:00", "2026-10-21T04:00:00"],
)
def test_a_reset_outside_its_window_is_left_out(resets):
    hits = [_hit("2026-10-14T03:46:00", "seven_day", resets)]

    result = limits.limits([], hits, None, _t("2026-10-15T00:00"))

    assert result.ceilings == []
    assert result.implausible == 1
    assert "limit hits left out: 1" in limits.render(result, tz=UTC)


def test_unpriced_calls_in_the_windows_shown_are_named():
    unknown = replace(_call("2026-10-14T12:00:00"), model="x-1")
    hits = [_hit("2026-10-14T03:46:00", "seven_day", WEEKLY_RESET)]
    old = replace(_call("2026-01-01T00:00:00"), model="x-0")

    out = limits.render(
        limits.limits([unknown, old], hits, None, _t("2026-10-15T00:00")), tz=UTC
    )

    assert "left out, no price: x-1 x1 (these figures read low)" in out
    assert "x-0" not in out


def test_plans_read_like_the_observatory_reads_them():
    flat = {"monthly_usd": {"Claude Code": 100.0}}
    ended = {"plans": {"Claude Code": [{"monthly_usd": 20.0, "until": "2026-10-02"}]}}
    now = _t("2026-10-14T00:00")

    assert limits.limits([], [], flat, now).plan == "$100/month"
    assert limits.limits([], [], {"plans": None, **flat}, now).plan == "$100/month"
    out = limits.render(limits.limits([], [], ended, now, tz=UTC), tz=UTC)
    assert "plan in force: none of the plans given covers today" in out


# The command


def test_limits_runs_through_the_cli_and_keeps_the_hits(tmp_path, monkeypatch, capsys):
    root = tmp_path / "projects"
    log = root / "workspace" / "sess.jsonl"
    log.parent.mkdir(parents=True)
    usage = json.loads(_stub("2026-10-13T20:00:00", "seven_day", _t(WEEKLY_RESET)))
    usage.pop("quotaLimits")
    usage["message"] = {
        "id": "msg_usage",
        "model": "claude-opus-4-8",
        "usage": {"input_tokens": 1_000_000, "output_tokens": 0},
    }
    usage["requestId"] = "req_usage"
    log.write_text(
        json.dumps(usage)
        + "\n"
        + _stub("2026-10-14T03:46:59", "seven_day", _t(WEEKLY_RESET)),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sources, "DEFAULT_SOURCES", ((root, claude_code.iter_usage_records),)
    )
    plans = tmp_path / "subscriptions.json"
    plans.write_text(json.dumps(PLANS), encoding="utf-8")

    assert cli.main(["limits", "--plans", str(plans)]) == 0

    out = capsys.readouterr().out
    assert "limit hit on this plan at $5.00" in out
    assert len(ledger.read_limit_hits()) == 1


def test_limits_without_usage_says_so(capsys):
    assert cli.main(["limits"]) == 1
    assert "no usage" in capsys.readouterr().err
