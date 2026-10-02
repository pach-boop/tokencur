import pytest

from tokencur.focus import FOCUS_COLUMNS, to_focus_rows, undated_count, unpriced_models
from tokencur.pricing import record_cost_usd
from tokencur.records import UsageRecord


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

    assert sum(r["BilledCost"] for r in rows) == pytest.approx(record_cost_usd(record))
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


@pytest.mark.parametrize("timestamp", ["", "not-a-timestamp", "2026-13-45T99:00:00Z"])
def test_undated_records_are_skipped_and_counted(timestamp):
    """No date is ever invented. A missing timestamp used to land on
    1970-01-01 and a malformed one crashed the export; both are now
    skipped and counted, like unpriced usage."""
    records = [_record(timestamp=timestamp), _record()]

    rows = list(to_focus_rows(records))

    assert rows
    assert all(r["ChargePeriodStart"].startswith("2026-07-01") for r in rows)
    assert undated_count(records) == 1


def test_a_priced_option_is_its_own_sku_price():
    rows = list(
        to_focus_rows([_record(model="claude-opus-5-5", price_modifiers="fast")])
    )

    out = next(r for r in rows if r["SkuId"].endswith("/output"))
    assert out["SkuId"] == "claude-opus-5-5/output"
    assert out["SkuPriceId"] == "claude-opus-5-5/output/fast"
    assert out["ListUnitPrice"] == pytest.approx(40 / 1_000_000)  # $40/MTok
    assert out["ChargeDescription"].endswith("(fast)")


def test_a_negotiated_discount_lowers_contracted_cost_not_list_cost():
    """FOCUS keeps the public price in ListCost; a contract's discount shows
    in ContractedCost, EffectiveCost and BilledCost."""
    list_rows = list(to_focus_rows([_record()]))
    rows = list(to_focus_rows([_record()], discounts={"Anthropic": 0.15}))

    for plain, discounted in zip(list_rows, rows, strict=True):
        assert discounted["ListCost"] == plain["ListCost"]
        for column in ("ContractedCost", "EffectiveCost", "BilledCost"):
            assert discounted[column] == pytest.approx(plain["ListCost"] * 0.85)


def test_a_discount_applies_only_to_its_provider():
    rows = list(
        to_focus_rows(
            [_record(source="codex", model="gpt-5.4")], discounts={"Anthropic": 0.5}
        )
    )

    assert all(r["BilledCost"] == r["ListCost"] for r in rows)
