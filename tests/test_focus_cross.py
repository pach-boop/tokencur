"""Cross-checks against an official FOCUS sample dataset.

The fixture is a slice of the FinOps Foundation's published sample data
(see tests/fixtures/README.md). It targets FOCUS 1.0 while tokencur
exports 1.2, so these tests assert *convention compatibility* — shared
vocabulary, mutually parseable values, spec-conformant formats — not
column-set equality.
"""

import csv
import re
from datetime import datetime
from pathlib import Path

from tokencur.focus import FOCUS_COLUMNS, to_focus_rows
from tokencur.ingest.claude_code import UsageRecord

FIXTURE = Path(__file__).parent / "fixtures" / "focus_sample_official_slice.csv"

# Every column both datasets must share: the FOCUS core that predates 1.2.
CORE_SHARED = {
    "BilledCost", "BillingAccountId", "BillingCurrency",
    "BillingPeriodStart", "BillingPeriodEnd", "ChargeCategory",
    "ChargeDescription", "ChargePeriodStart", "ChargePeriodEnd",
    "ConsumedQuantity", "ConsumedUnit", "ContractedCost", "EffectiveCost",
    "InvoiceIssuerName", "ListCost", "ListUnitPrice", "PricingQuantity",
    "PricingUnit", "ProviderName", "PublisherName", "ServiceCategory",
    "ServiceName", "SkuId", "SkuPriceId", "SubAccountId",
}

CHARGE_CATEGORIES = {"Usage", "Purchase", "Tax", "Credit", "Adjustment"}
ISO_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _official_rows() -> list[dict]:
    with FIXTURE.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _our_rows() -> list[dict]:
    record = UsageRecord(
        timestamp="2026-07-01T10:23:45.500Z", workspace="ws",
        session_id="s1", model="claude-opus-4-8",
        input_tokens=1000, output_tokens=500, cache_read_tokens=2000,
        cache_write_5m_tokens=300, cache_write_1h_tokens=100,
        source="claude-code",
    )
    return list(to_focus_rows([record]))


def test_core_focus_vocabulary_is_shared():
    official = set(_official_rows()[0])
    ours = set(FOCUS_COLUMNS)

    assert CORE_SHARED <= official, "fixture no longer covers the FOCUS core"
    assert CORE_SHARED <= ours, "export dropped a core FOCUS column"
    # Broad overlap beyond the core: both speak the same schema family.
    assert len(official & ours) >= 25


def test_datetimes_parseable_everywhere_and_strict_iso_in_ours():
    # The official sample uses a looser datetime style ("YYYY-MM-DD HH:MM:SS");
    # both must parse, but our export sticks to strict ISO 8601 UTC (Z).
    for row in _official_rows():
        datetime.fromisoformat(row["ChargePeriodStart"])
    for row in _our_rows():
        assert ISO_Z.match(row["ChargePeriodStart"])
        assert ISO_Z.match(row["BillingPeriodEnd"])


def test_charge_category_stays_within_spec_vocabulary():
    official_values = {r["ChargeCategory"] for r in _official_rows()}
    our_values = {r["ChargeCategory"] for r in _our_rows()}

    assert official_values <= CHARGE_CATEGORIES
    assert our_values <= CHARGE_CATEGORIES


def test_costs_and_currency_share_conventions():
    for row in _official_rows():
        float(row["BilledCost"])  # plain decimal strings
        assert re.match(r"^[A-Z]{3}$", row["BillingCurrency"])
    for row in _our_rows():
        float(row["BilledCost"])
        assert row["BillingCurrency"] == "USD"
