"""Historical pricing that cannot drift (ADR 0008, ADR 0011).

A call is valued at the list rate in force on its day. For the LiteLLM
snapshot the price-watch bot keeps each model's history; for the
hand-maintained Anthropic card a person does, and a person can overwrite
a rate. ``tests/fixtures/golden/curated_card.json`` records every rate
the curated card has held. A curated rate may leave the card only by
moving into ``RATE_CARD_HISTORY`` with the first day of its successor, or
through a disclosed correction (``RATE_CARD_CORRECTIONS``) of a rate that
never applied. Anything else fails here, before a September figure can
move because October's price did.

After an intended change, rewrite the record and review its diff:

    TOKENCUR_UPDATE_GOLDEN=1 pytest tests/test_rate_history.py
"""

import importlib.util
import json
import os
from dataclasses import fields
from datetime import date
from itertools import pairwise
from pathlib import Path

import pytest

from tokencur import pricing
from tokencur.pricing import ModelRates, record_cost_usd
from tokencur.records import UsageRecord

RECORD = Path(__file__).parent / "fixtures" / "golden" / "curated_card.json"
UPDATE = os.environ.get("TOKENCUR_UPDATE_GOLDEN") == "1"

_SCRIPT = Path(__file__).parent.parent / "scripts/update_pricing_snapshot.py"
_spec = importlib.util.spec_from_file_location("update_pricing_snapshot", _SCRIPT)
update = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(update)


def _rates(rates: ModelRates) -> dict[str, float]:
    return {f.name: round(getattr(rates, f.name), 6) for f in fields(ModelRates)}


def _card_state() -> dict:
    """The curated card as data: each model's current rate and history."""
    return {
        model: {
            "current": _rates(current),
            "history": [
                {"until": until, **_rates(old)}
                for until, old in pricing.RATE_CARD_HISTORY.get(model, ())
            ],
        }
        for model, current in sorted(pricing.RATE_CARD.items())
    }


def _violations(before: dict, after: dict, corrections: dict) -> list[str]:
    """How ``after`` lost a rate ``before`` held, if it did."""
    problems = []
    for model, was in before.items():
        now = after.get(model)
        if now is None:
            problems.append(
                f"{model}: removed from the card; keep retired models so old "
                "usage stays priced"
            )
            continue
        if now["history"][: len(was["history"])] != was["history"]:
            problems.append(f"{model}: its rate history changed; history only grows")
        if now["current"] == was["current"]:
            continue
        added = now["history"][len(was["history"]) :]
        moved = any(
            {k: v for k, v in entry.items() if k != "until"} == was["current"]
            for entry in added
        )
        wrong = corrections.get(model, (None, None, None))[1]
        corrected = wrong == (was["current"]["input"], was["current"]["output"])
        if not (moved or corrected):
            problems.append(
                f"{model}: its rate changed and the old one was not kept. Move "
                "it into RATE_CARD_HISTORY with the first day of the new rate, "
                "or, if it never applied, declare it in RATE_CARD_CORRECTIONS."
            )
    return problems


def test_curated_rates_leave_only_through_history_or_a_correction():
    recorded = json.loads(RECORD.read_text(encoding="utf-8"))

    problems = _violations(recorded, _card_state(), pricing.RATE_CARD_CORRECTIONS)

    assert not problems, "\n".join(problems)


def test_the_curated_card_matches_its_record():
    actual = json.dumps(_card_state(), indent=1, sort_keys=True) + "\n"
    if UPDATE:
        RECORD.write_text(actual, encoding="utf-8")
    assert RECORD.read_text(encoding="utf-8") == actual, (
        "the curated card changed; if intended, rerun with "
        "TOKENCUR_UPDATE_GOLDEN=1 and review the diff"
    )


def test_curated_history_is_dated_in_order_and_records_real_moves():
    for model, history in pricing.RATE_CARD_HISTORY.items():
        days = [date.fromisoformat(until) for until, _ in history]
        assert days == sorted(set(days)), f"{model}: until days must increase"
        rates = [r for _, r in history] + [pricing.RATE_CARD[model]]
        assert all(a != b for a, b in pairwise(rates)), (
            f"{model}: a history entry must differ from the rate after it"
        )


@pytest.mark.parametrize(
    ("change", "allowed"),
    [
        ("kept in history", True),
        ("overwritten", False),
        ("declared a correction", True),
        ("history rewritten", False),
        ("model removed", False),
    ],
)
def test_the_rule_tells_a_price_move_from_an_overwrite(change, allowed):
    old = {"input": 3.0, "output": 15.0, "cache_read": 0.3}
    new = {"input": 2.0, "output": 10.0, "cache_read": 0.2}
    before = {"m": {"current": old, "history": [{"until": "2026-01-01", **new}]}}
    after = {
        "kept in history": {
            "m": {
                "current": new,
                "history": [
                    {"until": "2026-01-01", **new},
                    {"until": "2026-09-01", **old},
                ],
            }
        },
        "overwritten": {"m": {"current": new, "history": before["m"]["history"]}},
        "declared a correction": {
            "m": {"current": new, "history": before["m"]["history"]}
        },
        "history rewritten": {"m": {"current": old, "history": []}},
        "model removed": {},
    }[change]
    corrections = (
        {"m": ("2026-10-02", (3.0, 15.0), "never applied")}
        if change == "declared a correction"
        else {}
    )

    assert (not _violations(before, after, corrections)) is allowed


@pytest.fixture
def fresh_rates():
    pricing.rates_for.cache_clear()
    yield
    pricing.rates_for.cache_clear()


def _call(model: str, day: str) -> UsageRecord:
    """One million input tokens, so the cost reads as the input rate."""
    return UsageRecord(
        timestamp=f"{day}T12:00:00.000Z",
        workspace="w",
        session_id="s",
        model=model,
        input_tokens=1_000_000,
        output_tokens=0,
        cache_read_tokens=0,
        cache_write_5m_tokens=0,
        cache_write_1h_tokens=0,
    )


def test_a_curated_price_move_values_each_day_at_its_rate(monkeypatch, fresh_rates):
    """$A before 2026-09-01, $B from that day: the review's own test."""
    model = "claude-opus-4-8"
    a = pricing.RATE_CARD[model]
    b = pricing._anthropic_rates(a.input * 2, a.output * 2)
    monkeypatch.setitem(pricing.RATE_CARD, model, b)
    monkeypatch.setitem(pricing.RATE_CARD_HISTORY, model, (("2026-09-01", a),))

    assert record_cost_usd(_call(model, "2026-08-31")) == pytest.approx(a.input)
    assert record_cost_usd(_call(model, "2026-09-01")) == pytest.approx(b.input)


def test_a_september_figure_never_moves_when_october_prices_do(
    monkeypatch, fresh_rates
):
    """Both layers. A curated price moves on 2026-10-10 the way the card
    records it; a snapshot price moves the same day through the
    price-watch bot's own build_snapshot. September's values stay put and
    October's take the new rates."""
    curated, community = "claude-opus-4-8", "gpt-x"

    def snapshot(rate: float, previous=None, today=None) -> dict:
        entry = {
            "input_cost_per_token": rate,
            "output_cost_per_token": rate * 4,
            "litellm_provider": "openai",
        }
        return update.build_snapshot({f"openai/{community}": entry}, previous, today)

    september = snapshot(1e-6, today="2026-09-01")
    october = snapshot(2e-6, previous=september, today="2026-10-10")

    monkeypatch.setattr(pricing, "_snapshot", lambda: pricing.parse_snapshot(september))
    before = {m: record_cost_usd(_call(m, "2026-09-15")) for m in (curated, community)}

    a = pricing.RATE_CARD[curated]
    b = pricing._anthropic_rates(a.input * 2, a.output * 2)
    monkeypatch.setitem(pricing.RATE_CARD, curated, b)
    monkeypatch.setitem(pricing.RATE_CARD_HISTORY, curated, (("2026-10-10", a),))
    monkeypatch.setattr(pricing, "_snapshot", lambda: pricing.parse_snapshot(october))
    pricing.rates_for.cache_clear()

    for model in (curated, community):
        assert record_cost_usd(_call(model, "2026-09-15")) == before[model]
        assert record_cost_usd(_call(model, "2026-10-10")) == pytest.approx(
            2 * before[model]
        )
