import pytest

from tokencur.ingest.claude_code import UsageRecord
from tokencur.recommend import caching_roi, model_rightsizing, recommendations, render


def _record(model: str, **overrides) -> UsageRecord:
    defaults = dict(
        timestamp="2026-07-01T10:00:00.000Z", workspace="w", session_id="s",
        model=model, input_tokens=0, output_tokens=0, cache_read_tokens=0,
        cache_write_5m_tokens=0, cache_write_1h_tokens=0, source="claude-code",
    )
    defaults.update(overrides)
    return UsageRecord(**defaults)


def test_caching_roi_measures_savings_vs_fresh_input():
    # Opus 4.8: input $5/MTok, cache read $0.5, write 5m $6.25, write 1h $10.
    records = [_record(
        "claude-opus-4-8",
        cache_read_tokens=10_000_000,   # paid $5, would have been $50
        cache_write_5m_tokens=1_000_000,  # paid $6.25, would have been $5
        cache_write_1h_tokens=1_000_000,  # paid $10, would have been $5
    )]

    (rec,) = caching_roi(records)

    assert rec.kind == "achieved"
    assert rec.baseline_usd == pytest.approx(60.0)  # 12 MTok at $5
    assert rec.savings_usd == pytest.approx(60.0 - (5 + 6.25 + 10))
    assert "cache-stable" in rec.detail


def test_caching_roi_flags_unamortized_writes():
    records = [_record("claude-opus-4-8", cache_write_1h_tokens=1_000_000)]

    (rec,) = caching_roi(records)

    assert rec.savings_usd < 0
    assert "not being amortized" in rec.detail


def test_rightsizing_prices_same_mix_one_tier_down():
    # Fable ($10/$50) -> Opus ($5/$25): same mix costs exactly half.
    records = [_record("claude-fable-5", input_tokens=1_000_000,
                       output_tokens=1_000_000)]

    (rec,) = model_rightsizing(records)

    assert rec.kind == "potential"
    assert rec.baseline_usd == pytest.approx(60.0)
    assert rec.savings_usd == pytest.approx(30.0)
    assert rec.savings_pct == pytest.approx(50.0)


def test_rightsizing_skips_tiny_and_unpaired_models():
    records = [
        _record("claude-fable-5", input_tokens=1_000),  # < $1 saving
        _record("mystery-model", input_tokens=5_000_000),  # unpriced
        _record("claude-haiku-4-5", input_tokens=5_000_000),  # no sibling
    ]

    assert model_rightsizing(records) == []


def test_render_separates_achieved_from_potential():
    records = [_record(
        "claude-fable-5",
        input_tokens=1_000_000, output_tokens=1_000_000,
        cache_read_tokens=10_000_000,
    )]

    out = render(recommendations(records))

    assert "AVOIDED" in out and "HEADROOM" in out
    assert "Total what-if headroom:" in out
    assert "Money concepts" in out  # the flat-subscription caveat ships with the output
