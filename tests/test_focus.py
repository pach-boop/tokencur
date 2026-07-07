import pytest

from tokencur.focus import FOCUS_COLUMNS, to_focus_rows, unpriced_models
from tokencur.ingest.claude_code import UsageRecord
from tokencur.pricing import record_cost_usd


def _record(**overrides) -> UsageRecord:
    defaults = dict(
        timestamp="2026-07-01T10:23:45.500Z",
        workspace="my-workspace",
        session_id="sess-1",
        model="claude-opus-4-8",
        input_tokens=1000,
        output_tokens=500,
        cache_read_tokens=2000,
        cache_write_5m_tokens=300,
        cache_write_1h_tokens=0,
        source="claude-code",
    )
    defaults.update(overrides)
    return UsageRecord(**defaults)


def test_explodes_into_one_row_per_nonzero_bucket():
    rows = list(to_focus_rows([_record()]))

    assert len(rows) == 4  # 1h cache writes are zero → no row
    assert {r["SkuId"] for r in rows} == {
        "claude-opus-4-8/input",
        "claude-opus-4-8/output",
        "claude-opus-4-8/cache-read",
        "claude-opus-4-8/cache-write-5m",
    }


def test_row_costs_sum_to_record_cost():
    record = _record()
    rows = list(to_focus_rows([record]))

    assert sum(r["BilledCost"] for r in rows) == pytest.approx(
        record_cost_usd(record)
    )
    # Showback: the four cost columns agree.
    for r in rows:
        assert r["BilledCost"] == r["EffectiveCost"] == r["ListCost"]
        assert r["BilledCost"] == pytest.approx(
            r["ConsumedQuantity"] * r["ListUnitPrice"]
        )


def test_focus_semantics_and_formats():
    (row, *_) = to_focus_rows([_record()])

    assert set(row) == set(FOCUS_COLUMNS)
    assert row["ChargePeriodStart"] == "2026-07-01T10:00:00Z"
    assert row["ChargePeriodEnd"] == "2026-07-01T11:00:00Z"
    assert row["BillingPeriodStart"] == "2026-07-01T00:00:00Z"
    assert row["BillingPeriodEnd"] == "2026-08-01T00:00:00Z"
    assert row["ChargeCategory"] == "Usage"
    assert row["ConsumedUnit"] == "tokens"
    assert isinstance(row["PricingQuantity"], float)  # decimal, not int
    assert row["InvoiceId"] is None  # showback: no invoice, explicit null
    assert row["ServiceSubcategory"] == "Generative AI"
    assert row["ProviderName"] == "Anthropic"
    assert row["ServiceName"] == "Claude Code"
    assert row["SubAccountId"] == "my-workspace"


def test_unpriced_records_are_skipped_and_counted():
    records = [_record(), _record(model="mystery-model")]

    rows = list(to_focus_rows(records))
    assert all(r["SkuId"].startswith("claude-opus-4-8/") for r in rows)
    assert unpriced_models(records) == {"mystery-model": 1}


def test_provider_mapping_per_source():
    rows = list(
        to_focus_rows(
            [
                _record(source="codex", model="gpt-5.2-codex"),
                _record(source="kimi-code", model="moonshot-ai/kimi-k2-0711-preview"),
            ]
        )
    )
    providers = {r["ProviderName"] for r in rows}
    assert providers == {"OpenAI", "Moonshot AI"}
