"""The five-hour limit at each window's model mix (ADR 0022)."""

from datetime import UTC, datetime

import pytest

from tokencur import limits
from tokencur.records import LimitHit, MeterReading, UsageRecord

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
_RATES = {"claude-opus-4-8": 5.0, "claude-fable-5-2": 8.0}  # input, $/MTok


def _call(when: str, dollars: float, model: str = "claude-opus-4-8") -> UsageRecord:
    return UsageRecord(
        timestamp=f"{when}Z",
        workspace="w",
        session_id="s",
        model=model,
        input_tokens=round(dollars / _RATES[model] * 1_000_000),
        output_tokens=0,
        cache_read_tokens=0,
        cache_write_5m_tokens=0,
        cache_write_1h_tokens=0,
        record_id=f"r:{model}:{when}",
    )


def _fable(when: str, dollars: float) -> UsageRecord:
    return _call(when, dollars, "claude-fable-5-2")


def _reading(kind: str, percent: float, at: str, resets: str) -> MeterReading:
    return MeterReading("claude-code", kind, f"{at}Z", percent, f"{resets}Z")


# A limit worth 100 units, where a dollar of Fable is two of Opus: $50 of
# Opus read 50%, $25 of Fable read 50%, and $40 of Opus with $30 of Fable
# hit the limit. In dollars alone that hit, $70, is the lowest ceiling.
PAST = [
    _call("2026-11-18T01:00:00", 50.0),
    _fable("2026-11-18T10:00:00", 25.0),
    _call("2026-11-19T01:00:00", 40.0),
    _fable("2026-11-19T01:30:00", 30.0),
]
HIT = LimitHit(
    source="claude-code",
    record_id="req:2026-11-19T02:00:00",
    timestamp="2026-11-19T02:00:00Z",
    session_id="s",
    kind="five_hour",
    resets_at="2026-11-19T06:00:00Z",
)
READINGS = [
    _reading("five_hour", 50.0, "2026-11-18T02:00:00", "2026-11-18T06:00:00"),
    _reading("five_hour", 50.0, "2026-11-18T11:00:00", "2026-11-18T15:00:00"),
    # an old week names Fable as a model with a limit of its own
    _reading("seven_day:Fable", 30.0, "2026-11-10T02:00:00", "2026-11-11T11:00:00"),
]


def _limits(now_calls, readings=READINGS, hits=(HIT,)):
    return limits.limits([*PAST, *now_calls], list(hits), None, NOW, readings=readings)


def test_the_factor_is_fit_on_the_hits_and_readings():
    result = _limits([_call("2026-11-20T11:00:00", 35.0)])

    assert result.mix is not None
    assert result.mix.models == ("Fable",)
    assert result.mix.factor == pytest.approx(2.0)
    assert result.mix.points == 3


def test_an_opus_window_is_set_against_the_ceiling_in_opus_dollars():
    result = _limits([_call("2026-11-20T11:00:00", 35.0)])

    five = limits.as_dict(result)["windows"]["five_hour"]

    assert (five["percent"], five["percent_of"]) == (35.0, "lowest_ceiling_by_mix")
    assert five["lowest_ceiling"] == 70.0  # still in dollars alone
    assert five["mix"] == {
        "models": ["Fable"],
        "factor": 2.0,
        "points": 3,
        "share": 0.0,
        "lowest_ceiling": 100.0,
    }


def test_a_mixed_window_is_set_against_the_ceiling_at_its_own_mix():
    calls = [_call("2026-11-20T11:00:00", 10.0), _fable("2026-11-20T11:10:00", 10.0)]

    five = limits.as_dict(_limits(calls))["windows"]["five_hour"]

    assert five["percent"] == 30.0  # 10 + 2 x 10 units of 100
    assert five["mix"]["share"] == 0.5
    assert five["mix"]["lowest_ceiling"] == pytest.approx(66.67)


def test_the_view_says_how_the_limit_weighs_the_mix():
    out = limits.render(_limits([_call("2026-11-20T11:00:00", 35.0)]), tz=UTC)

    assert "5 hours: $35.00 = 35% of the lowest ceiling at this window's model mix" in (
        out
    )
    assert (
        "  a dollar of Fable draws 2.00 times what one of other models does on this "
        "limit (fit on 3 hits and meter readings): at this window's mix (0% Fable) "
        "the lowest ceiling hit is worth $100.00, against $70.00 in dollars alone"
    ) in out


def test_without_a_model_limit_dollars_count_alike():
    readings = [r for r in READINGS if r.kind == "five_hour"]

    result = _limits([_call("2026-11-20T11:00:00", 35.0)], readings=readings)

    five = limits.as_dict(result)["windows"]["five_hour"]
    assert result.mix is None
    assert (five["percent"], five["percent_of"], five["mix"]) == (
        50.0,
        "lowest_ceiling",
        None,
    )
    assert "model mix" not in limits.render(result, tz=UTC)


def test_a_mix_too_even_to_tell_is_not_fit():
    calls = [
        _call("2026-11-18T01:00:00", 50.0),
        _call("2026-11-18T10:00:00", 25.0),
        _call("2026-11-19T01:00:00", 40.0),
        _call("2026-11-20T11:00:00", 35.0),
    ]
    readings = [
        READINGS[0],
        _reading("five_hour", 25.0, "2026-11-18T11:00:00", "2026-11-18T15:00:00"),
        _reading("five_hour", 40.0, "2026-11-19T02:00:00", "2026-11-19T06:00:00"),
        READINGS[2],
    ]

    result = limits.limits(calls, [], None, NOW, readings=readings)

    assert result.mix is None  # every point is Opus alone


def test_a_fit_that_puts_a_model_at_no_cost_is_no_fit():
    # $20 of Opus with $30 of Fable read 15%: less than $20 of Opus alone
    # would, so Fable would cost less than nothing
    calls = [_call("2026-11-17T01:00:00", 20.0), _fable("2026-11-17T01:30:00", 30.0)]
    readings = [
        READINGS[0],
        _reading("five_hour", 50.0, "2026-11-17T22:00:00", "2026-11-18T00:00:00"),
        _reading("five_hour", 15.0, "2026-11-17T02:00:00", "2026-11-17T06:00:00"),
        READINGS[2],
    ]

    result = limits.limits(
        [_call("2026-11-17T20:00:00", 50.0), *calls, PAST[0]],
        [],
        None,
        NOW,
        readings=readings,
    )

    assert result.mix is None


def test_a_current_reading_still_wins_over_the_mix():
    reading = _reading("five_hour", 35.0, "2026-11-20T11:30:00", "2026-11-20T16:00:00")
    result = _limits([_call("2026-11-20T11:00:00", 35.0)], [*READINGS, reading])

    five = limits.as_dict(result)["windows"]["five_hour"]

    assert five["percent_of"] == "meter"
    assert five["mix"]["lowest_ceiling"] == 100.0
    assert "by the lowest ceiling hit at this window's model mix ($100.00)" in (
        limits.render(result, tz=UTC)
    )


def test_without_a_mix_a_current_reading_compares_in_dollars_alone():
    readings = [r for r in READINGS if r.kind == "five_hour"]
    reading = _reading("five_hour", 35.0, "2026-11-20T11:30:00", "2026-11-20T16:00:00")

    out = limits.render(
        _limits([_call("2026-11-20T11:00:00", 35.0)], [*readings, reading]), tz=UTC
    )

    assert "; by the lowest ceiling hit ($70.00) it would read 50%" in out


def test_readings_from_before_the_plan_are_left_out_of_the_fit():
    plans = {"plans": {"Claude Code": [{"monthly_usd": 100.0, "from": "2026-11-18"}]}}
    old = _reading("five_hour", 90.0, "2026-11-17T02:00:00", "2026-11-17T06:00:00")
    calls = [_call("2026-11-17T01:00:00", 9.0), _call("2026-11-20T11:00:00", 35.0)]

    result = limits.limits(
        [*PAST, *calls], [HIT], plans, NOW, tz=UTC, readings=[*READINGS, old]
    )

    assert result.mix is not None
    assert (result.mix.factor, result.mix.points) == (pytest.approx(2.0), 3)
