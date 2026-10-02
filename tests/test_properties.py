"""Property tests: invariants the money depends on, checked on generated input.

Example tests cover the cases someone thought of. Here Hypothesis
generates records, histories and log files and searches for a
counterexample to each promise tokencur makes about its numbers.
"""

import json
import tempfile
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tokencur import ledger
from tokencur.focus import to_focus_rows
from tokencur.ingest import claude_code, codex
from tokencur.ingest.identity import fingerprint
from tokencur.pricing import RATE_CARD, rates_for, record_cost_usd
from tokencur.records import UsageRecord, in_period, parse_timestamp

# Correctness, not speed: CI machines vary too much for a deadline.
PROPERTY = settings(deadline=None, max_examples=150)

COUNTS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_5m_tokens",
    "cache_write_1h_tokens",
)
PRICED = sorted(
    m
    for m in [*RATE_CARD, "gpt-5.4", "gpt-5.3-codex", "gpt-5.2-codex", "deepseek-chat"]
    if rates_for(m)
)

moments = st.datetimes(min_value=datetime(2024, 1, 1), max_value=datetime(2030, 12, 31))
timestamps = st.builds(
    lambda moment, zulu: (
        moment.strftime("%Y-%m-%dT%H:%M:%S.")
        + f"{moment.microsecond // 1000:03d}"
        + ("Z" if zulu else "+00:00")
    ),
    moments,
    st.booleans(),
)
token_counts = st.integers(min_value=0, max_value=3_000_000_000)
records = st.builds(
    UsageRecord,
    timestamp=timestamps,
    workspace=st.sampled_from(["acme-api", "infra", ""]),
    session_id=st.uuids().map(str),
    model=st.one_of(st.sampled_from(PRICED), st.just("not-a-real-model")),
    input_tokens=token_counts,
    output_tokens=token_counts,
    cache_read_tokens=token_counts,
    cache_write_5m_tokens=token_counts,
    cache_write_1h_tokens=token_counts,
    source=st.sampled_from(["claude-code", "codex", "kimi-code"]),
    record_id=st.uuids().map(str),
)


def _moment(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@PROPERTY
@given(records)
def test_focus_rows_add_up_to_the_record_cost(record):
    rows = list(to_focus_rows([record]))
    cost = record_cost_usd(record)

    if cost is None:
        assert rows == []  # unpriced: reported elsewhere, never exported as $0
        return
    assert sum(r["BilledCost"] for r in rows) == pytest.approx(
        cost, rel=1e-9, abs=1e-12
    )
    assert len(rows) == sum(1 for field in COUNTS if getattr(record, field))


@PROPERTY
@given(records)
def test_every_charge_row_is_internally_consistent(record):
    for row in to_focus_rows([record]):
        start, end = _moment(row["ChargePeriodStart"]), _moment(row["ChargePeriodEnd"])
        period_start = _moment(row["BillingPeriodStart"])
        period_end = _moment(row["BillingPeriodEnd"])

        assert start < end and start.minute == start.second == 0
        assert period_start.day == 1 and period_start <= start < period_end
        assert row["BilledCost"] >= 0 and row["ConsumedQuantity"] > 0
        assert (
            row["BilledCost"]
            == row["EffectiveCost"]
            == row["ContractedCost"]
            == row["ListCost"]
        )
        assert row["ListCost"] == pytest.approx(
            row["ListUnitPrice"] * row["PricingQuantity"], rel=1e-9, abs=1e-15
        )


@PROPERTY
@given(records, st.integers(min_value=2, max_value=7))
def test_cost_scales_linearly_with_tokens(record, factor):
    scaled = replace(record, **{f: getattr(record, f) * factor for f in COUNTS})
    cost = record_cost_usd(record)

    if cost is None:
        assert record_cost_usd(scaled) is None
    else:
        assert record_cost_usd(scaled) == pytest.approx(
            factor * cost, rel=1e-9, abs=1e-12
        )


@PROPERTY
@given(
    st.lists(records, max_size=40),
    st.dates(min_value=date(2024, 1, 1), max_value=date(2031, 1, 1)),
)
def test_a_split_day_divides_the_history_without_loss_or_overlap(history, split):
    before = in_period(history, None, split)
    after = in_period(history, split, None)

    dated = [r for r in history if parse_timestamp(r.timestamp)]
    assert len(before) + len(after) == len(dated)
    assert {id(r) for r in before}.isdisjoint(id(r) for r in after)


@PROPERTY
@given(st.lists(records, max_size=25, unique_by=lambda r: (r.source, r.record_id)))
def test_the_ledger_keeps_every_record_exactly_once(history):
    def key(r):
        return (r.source, r.record_id)

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "ledger.sqlite3"
        assert ledger.record(history, path) == len(history)
        assert ledger.record(history, path) == 0  # a rescan adds nothing
        assert sorted(ledger.read(path), key=key) == sorted(history, key=key)


@PROPERTY
@given(
    st.dictionaries(
        st.text(min_size=1, max_size=10), st.integers(0, 10**12), max_size=8
    )
)
def test_fingerprints_ignore_key_order(usage):
    assert fingerprint(usage) == fingerprint(dict(reversed(list(usage.items()))))


@PROPERTY
@given(
    st.lists(
        st.tuples(
            st.integers(1, 10**7),  # uncached input
            st.integers(0, 10**7),  # cached input
            st.integers(0, 10**6),  # output
            st.integers(0, 3),  # times Codex re-sends the same report
        ),
        min_size=1,
        max_size=20,
    )
)
def test_codex_counts_reconcile_with_the_running_total(calls):
    running = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0}
    lines = [json.dumps({"type": "session_meta", "payload": {"id": "sess"}})]
    second = 0
    for uncached, cached, output, resends in calls:
        last = {
            "input_tokens": uncached + cached,
            "cached_input_tokens": cached,
            "output_tokens": output,
            "reasoning_output_tokens": 0,
            "total_tokens": uncached + cached + output,
        }
        for field in running:
            running[field] += last[field]
        total = {**running, "reasoning_output_tokens": 0, "total_tokens": 0}
        for _ in range(1 + resends):
            second += 1
            lines.append(
                json.dumps(
                    {
                        "type": "event_msg",
                        "timestamp": f"2026-09-15T10:{second // 60:02d}:{second % 60:02d}.000Z",
                        "payload": {
                            "type": "token_count",
                            "info": {
                                "last_token_usage": last,
                                "total_token_usage": total,
                            },
                        },
                    }
                )
            )
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "rollout.jsonl").write_text("\n".join(lines), encoding="utf-8")
        parsed = list(codex.iter_usage_records(Path(tmp)))

    assert len(parsed) == len(calls)
    assert (
        sum(r.input_tokens + r.cache_read_tokens for r in parsed)
        == running["input_tokens"]
    )
    assert sum(r.cache_read_tokens for r in parsed) == running["cached_input_tokens"]
    assert sum(r.output_tokens for r in parsed) == running["output_tokens"]


@PROPERTY
@given(
    st.lists(
        st.fixed_dictionaries(
            {
                "input_tokens": st.integers(0, 10**6),
                "output_tokens": st.integers(0, 10**6),
                "cache_read_input_tokens": st.integers(0, 10**8),
            }
        ),
        min_size=1,
        max_size=6,
    )
)
def test_a_streamed_message_keeps_each_fields_largest_count(line_usages):
    lines = [
        json.dumps(
            {
                "type": "assistant",
                "sessionId": "sess",
                "requestId": "req",
                "timestamp": "2026-09-15T10:00:00.000Z",
                "message": {"id": "msg", "model": "claude-opus-5-5", "usage": usage},
            }
        )
        for usage in line_usages
    ]
    with tempfile.TemporaryDirectory() as tmp:
        log = Path(tmp) / "workspace" / "session.jsonl"
        log.parent.mkdir()
        log.write_text("\n".join(lines), encoding="utf-8")
        parsed = list(claude_code.iter_usage_records(Path(tmp)))

    (record,) = parsed
    assert record.input_tokens == max(u["input_tokens"] for u in line_usages)
    assert record.output_tokens == max(u["output_tokens"] for u in line_usages)
    assert record.cache_read_tokens == max(
        u["cache_read_input_tokens"] for u in line_usages
    )
