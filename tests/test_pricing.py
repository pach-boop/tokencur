import pytest

from tokencur.pricing import rates_for, record_cost_usd
from tokencur.records import UsageRecord


def _record(model: str) -> UsageRecord:
    return UsageRecord(
        timestamp="2026-07-01T10:00:00.000Z",
        workspace="w",
        session_id="s",
        model=model,
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_read_tokens=1_000_000,
        cache_write_5m_tokens=1_000_000,
        cache_write_1h_tokens=1_000_000,
    )


def test_opus_cost_covers_all_token_types():
    # Opus 4.8: $5 in, $25 out; cache read 0.5, write 5m 6.25, write 1h 10.
    cost = record_cost_usd(_record("claude-opus-4-8"))
    assert cost == pytest.approx(5 + 25 + 0.5 + 6.25 + 10)


def test_dated_model_id_resolves_to_alias():
    assert rates_for("claude-haiku-4-5-20251001") == rates_for("claude-haiku-4-5")


def test_unknown_model_is_unpriced_not_zero():
    assert rates_for("some-future-model") is None
    assert record_cost_usd(_record("some-future-model")) is None


def test_curated_card_wins_over_snapshot():
    from tokencur.pricing import RATE_CARD

    # opus-4-8 exists in both layers; the curated card must resolve.
    assert rates_for("claude-opus-4-8") is RATE_CARD["claude-opus-4-8"]


def test_snapshot_extends_coverage_beyond_curated_card():
    from tokencur.pricing import RATE_CARD, _snapshot_rates

    extra = [m for m in _snapshot_rates() if m not in RATE_CARD]
    assert len(extra) > 100  # community data adds OpenAI/Gemini coverage
    assert all(rates_for(m) is not None for m in extra[:10])


def test_vendor_prefix_is_stripped():
    from tokencur.pricing import RATE_CARD

    assert rates_for("anthropic/claude-opus-4-8") is RATE_CARD["claude-opus-4-8"]
    # Kimi Code logs models as "moonshot-ai/<model>".
    assert rates_for("moonshot-ai/kimi-k2-0711-preview") is not None


def test_kimi_coding_alias_has_documented_proxy_rate():
    rates = rates_for("moonshot-ai/kimi-k2.7-code-highspeed")
    assert rates is not None  # proxy at kimi-k2.6 list rates
    assert (rates.input, rates.output) == (0.95, 4.00)
    assert rates.cache_write_5m == 0.0  # no write premium published


def test_snapshot_uses_explicit_provider_cache_rates():
    # OpenAI cache reads are 0.5x input — not Anthropic's 0.1x. The
    # snapshot must carry explicit fields, not assume multipliers.
    rates = rates_for("gpt-4o")
    assert rates is not None
    assert rates.cache_read == pytest.approx(rates.input * 0.5)


def test_cache_reads_follow_each_models_published_multiplier():
    """Most Claude models read cache at 0.1x input, but not all: the card
    must carry the exceptions the pricing page states."""
    read = {
        m: rates_for(m).cache_read
        for m in (
            "claude-opus-5-5",
            "claude-fable-5-1",
            "claude-mythos-5-1",
            "claude-fable-5",
            "claude-opus-5",
        )
    }

    assert read == pytest.approx(
        {
            "claude-opus-5-5": 0.20,  # 0.05x of $4
            "claude-fable-5-1": 0.25,  # 0.025x of $10
            "claude-mythos-5-1": 0.25,
            "claude-fable-5": 1.00,  # the standard 0.1x
            "claude-opus-5": 0.50,
        }
    )


def test_sonnet_5_is_valued_at_its_standard_price():
    """$2/$10 began as an introductory price and became the standard one;
    the card used to value Sonnet 5 at the cancelled $3/$15."""
    rates = rates_for("claude-sonnet-5")
    assert (rates.input, rates.output) == (2.00, 10.00)


def test_retired_models_resolve_from_their_dated_ids():
    from tokencur.pricing import RATE_CARD

    assert rates_for("claude-opus-4-20250514") is RATE_CARD["claude-opus-4"]
    assert rates_for("claude-3-5-haiku-20241022") is RATE_CARD["claude-3-5-haiku"]


def test_curated_card_agrees_with_the_community_snapshot():
    """Two independent sources for one price must say the same thing.

    The curated card always wins, so a disagreement never changes a
    number by itself; it means one source moved and a human must look.
    Sonnet 5 sat at $3/$15 here while the snapshot already had $2/$10.
    The daily price-watch run executes this test, so a provider price
    move on a curated model stops the refresh until the card is updated.
    """
    from dataclasses import astuple

    from tokencur.pricing import _DATE_SUFFIX, RATE_CARD, _snapshot_rates

    disagreements = []
    for name, snapshot in _snapshot_rates().items():
        curated = RATE_CARD.get(_DATE_SUFFIX.sub("", name))
        if curated and astuple(curated) != pytest.approx(astuple(snapshot)):
            disagreements.append(
                f"{name}: card {astuple(curated)} vs snapshot {astuple(snapshot)}"
            )

    assert not disagreements, "\n".join(disagreements)


def test_a_call_is_valued_at_the_rate_in_force_on_its_day():
    """Point-in-time pricing, on a move the snapshot really recorded: the
    price-watch bot saw this model's input rate fall from $2 to $1 per
    MTok on 2026-09-23."""
    from datetime import date

    model = "gemini-robotics-er-2-streaming-preview"

    assert rates_for(model, on=date(2026, 9, 22)).input == pytest.approx(2.0)
    assert rates_for(model, on=date(2026, 9, 23)).input == pytest.approx(1.0)
    assert rates_for(model).input == pytest.approx(1.0)  # no date: current


def test_history_is_revalued_through_record_cost():
    from dataclasses import replace

    record = replace(
        _record("gemini-robotics-er-2-streaming-preview"),
        output_tokens=0,
        cache_read_tokens=0,
        cache_write_5m_tokens=0,
        cache_write_1h_tokens=0,
    )
    before = replace(record, timestamp="2026-09-22T23:59:59.000Z")
    after = replace(record, timestamp="2026-09-23T00:00:00.000Z")

    assert record_cost_usd(before) == pytest.approx(2.0)  # 1M input at $2
    assert record_cost_usd(after) == pytest.approx(1.0)


def test_models_without_history_cost_the_same_on_any_day():
    from datetime import date

    for day in (date(2024, 1, 1), date(2026, 7, 6), date(2030, 12, 31)):
        assert rates_for("claude-opus-5-5", on=day) is rates_for("claude-opus-5-5")


@pytest.mark.parametrize(
    ("modifiers", "factor"),
    [("", 1.0), ("fast", 2.0), ("us", 1.1), ("batch", 0.5), ("fast+us", 2.2)],
)
def test_request_options_scale_every_rate(modifiers, factor):
    """Anthropic's published multipliers: fast mode 2x (Opus 5.5 $8/$40),
    US-only inference 1.1x on every category, Batch API 0.5x; cache
    multipliers stack on top of them."""
    from dataclasses import replace

    record = replace(_record("claude-opus-5-5"), price_modifiers=modifiers)
    standard = 4 + 20 + 0.20 + 5 + 8  # 1M tokens of each kind at list

    assert record_cost_usd(record) == pytest.approx(standard * factor)
