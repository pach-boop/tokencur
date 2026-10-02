from tokencur.records import UsageRecord
from tokencur.report import summarize


def _record(
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    timestamp: str = "2026-07-01T10:00:00.000Z",
) -> UsageRecord:
    return UsageRecord(
        timestamp=timestamp,
        workspace="w",
        session_id="s",
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=0,
        cache_write_5m_tokens=0,
        cache_write_1h_tokens=0,
    )


def test_summarize_totals_and_surfaces_unpriced():
    records = [
        _record("claude-opus-4-8", input_tokens=1_000_000),  # $5.00 at list
        _record("mystery-model", input_tokens=999),
    ]

    out = summarize(records)

    assert "2 model calls" in out
    assert "API-EQUIVALENT TOTAL (showback): $5.00" in out
    assert "2026-07-01  $5.00" in out
    # Unpriced usage is reported, never silently valued at $0.
    assert "unpriced usage" in out and "mystery-model x1" in out


def test_summarize_counts_undated_usage_under_its_own_label():
    """Undated usage is real usage: it stays in the total, listed as
    undated rather than under an empty or invented day."""
    records = [
        _record("claude-opus-4-8", input_tokens=1_000_000),
        _record("claude-opus-4-8", input_tokens=1_000_000, timestamp=""),
    ]

    out = summarize(records)

    assert "API-EQUIVALENT TOTAL (showback): $10.00" in out
    assert "undated     $5.00" in out


def test_table_columns_never_run_together():
    """Billion-token totals and long model ids used to overflow fixed
    widths, gluing adjacent numbers into one unreadable string."""
    records = [
        UsageRecord(
            timestamp="2026-10-01T10:00:00.000Z",
            workspace="w",
            session_id="s",
            model="moonshot-ai/kimi-k2.7-code-highspeed",
            input_tokens=649_198,
            output_tokens=4_443_636,
            cache_read_tokens=1_037_918_794,
            cache_write_5m_tokens=0,
            cache_write_1h_tokens=0,
            source="kimi-code",
        )
    ]

    header, row = summarize(records).splitlines()[2:4]

    assert row.split() == [
        "moonshot-ai/kimi-k2.7-code-highspeed",
        "1",
        "649,198",
        "4,443,636",
        "1,037,918,794",
        "0",
        row.split()[-1],
    ]
    assert len(header) == len(row)
