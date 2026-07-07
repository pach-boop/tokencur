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
