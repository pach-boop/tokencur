import pytest

from tokencur.ingest.claude_code import UsageRecord
from tokencur.pricing import rates_for, record_cost_usd


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


def test_snapshot_uses_explicit_provider_cache_rates():
    # OpenAI cache reads are 0.5x input — not Anthropic's 0.1x. The
    # snapshot must carry explicit fields, not assume multipliers.
    rates = rates_for("gpt-4o")
    assert rates is not None
    assert rates.cache_read == pytest.approx(rates.input * 0.5)
