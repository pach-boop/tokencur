"""Normalize usage records into FOCUS-conformant charge rows.

Each :class:`~tokencur.ingest.claude_code.UsageRecord` explodes into up
to five charge rows — one per token bucket (input, output, cache read,
cache writes by TTL) — mirroring how provider billing exports emit one
line item per SKU.

Cost semantics (showback): local agent usage is not invoiced per token,
so ``BilledCost``, ``EffectiveCost``, ``ContractedCost`` and
``ListCost`` all carry the API-equivalent list cost (see
``pricing.py``). Unpriced records are skipped and counted by the
caller, never exported as $0; undated records likewise, never given
an invented charge period.

Column semantics follow the FOCUS specification
(https://focus.finops.org); each mapping states its intent inline.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import datetime, timedelta

from tokencur.pricing import ModelRates, rates_for
from tokencur.records import UsageRecord, parse_timestamp

FOCUS_VERSION = "1.2"

# Column order for CSV export.
FOCUS_COLUMNS = [
    "BilledCost",
    "BillingAccountId",
    "BillingAccountName",
    "BillingCurrency",
    "BillingPeriodStart",
    "BillingPeriodEnd",
    "ChargeCategory",
    "ChargeClass",
    "ChargeDescription",
    "ChargeFrequency",
    "ChargePeriodStart",
    "ChargePeriodEnd",
    "ConsumedQuantity",
    "ConsumedUnit",
    "ContractedCost",
    "EffectiveCost",
    "InvoiceId",
    "InvoiceIssuerName",
    "ListCost",
    "ListUnitPrice",
    "PricingCategory",
    "PricingQuantity",
    "PricingUnit",
    "ProviderName",
    "PublisherName",
    "ResourceId",
    "ResourceName",
    "ResourceType",
    "ServiceCategory",
    "ServiceName",
    "ServiceSubcategory",
    "SkuId",
    "SkuPriceId",
    "SubAccountId",
    "SubAccountName",
]

_PROVIDER_BY_SOURCE = {
    "claude-code": "Anthropic",
    "codex": "OpenAI",
    "kimi-code": "Moonshot AI",
}
_SERVICE_BY_SOURCE = {
    "claude-code": "Claude Code",
    "codex": "Codex CLI",
    "kimi-code": "Kimi Code",
}

# (record attribute, SKU suffix, human label, USD/MTok attribute)
_BUCKETS = (
    ("input_tokens", "input", "input tokens", "input"),
    ("output_tokens", "output", "output tokens", "output"),
    ("cache_read_tokens", "cache-read", "cache read tokens", "cache_read"),
    (
        "cache_write_5m_tokens",
        "cache-write-5m",
        "cache write (5m TTL) tokens",
        "cache_write_5m",
    ),
    (
        "cache_write_1h_tokens",
        "cache-write-1h",
        "cache write (1h TTL) tokens",
        "cache_write_1h",
    ),
)


def to_focus_rows(records: Iterable[UsageRecord]) -> Iterator[dict]:
    """Yield FOCUS charge rows for every priced record."""
    for record in records:
        rates = rates_for(record.model)
        charge_start = parse_timestamp(record.timestamp)
        if rates is None or charge_start is None:
            continue  # surfaced by callers: never $0, never a made-up date
        yield from _record_rows(record, rates, charge_start)


def unpriced_models(records: Iterable[UsageRecord]) -> dict[str, int]:
    """Count records that to_focus_rows would skip, by model."""
    counts: dict[str, int] = {}
    for record in records:
        if rates_for(record.model) is None:
            counts[record.model] = counts.get(record.model, 0) + 1
    return counts


def undated_count(records: Iterable[UsageRecord]) -> int:
    """Count records that to_focus_rows would skip for want of a date."""
    return sum(1 for r in records if parse_timestamp(r.timestamp) is None)


def _record_rows(
    record: UsageRecord, rates: ModelRates, charge_start: datetime
) -> Iterator[dict]:
    period_start = charge_start.replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    provider = _PROVIDER_BY_SOURCE.get(record.source, "Unknown")
    service = _SERVICE_BY_SOURCE.get(record.source, record.source)
    # Columns that are the same for every bucket of this record, built
    # once: an export writes up to five rows per record.
    shared = {
        "BillingAccountId": "tokencur-local",
        "BillingAccountName": "Local AI usage (showback)",
        "BillingCurrency": "USD",
        "BillingPeriodStart": _fmt(period_start),
        "BillingPeriodEnd": _fmt(_next_month(period_start)),
        "ChargeCategory": "Usage",
        "ChargeClass": None,
        "ChargeFrequency": "Usage-Based",
        "ChargePeriodStart": _fmt(_floor_hour(charge_start)),
        "ChargePeriodEnd": _fmt(_floor_hour(charge_start + timedelta(hours=1))),
        "ConsumedUnit": "tokens",
        # Showback charges have no invoice; the spec requires an
        # explicit null in that case.
        "InvoiceId": None,
        "InvoiceIssuerName": provider,
        "PricingCategory": "Standard",
        "PricingUnit": "tokens",
        "ProviderName": provider,
        "PublisherName": provider,
        "ResourceId": record.session_id or None,
        "ResourceName": record.session_id or None,
        "ResourceType": "AI agent session",
        "ServiceCategory": "AI and Machine Learning",
        "ServiceName": service,
        "ServiceSubcategory": "Generative AI",
        "SubAccountId": record.workspace or None,
        "SubAccountName": record.workspace or None,
    }

    for attribute, sku_suffix, label, rate_attribute in _BUCKETS:
        quantity = getattr(record, attribute)
        if not quantity:
            continue
        unit_price = getattr(rates, rate_attribute) / 1_000_000  # USD per token
        cost = quantity * unit_price
        sku = f"{record.model}/{sku_suffix}"
        yield {
            **shared,
            # Showback: all four cost columns carry list cost (module doc).
            "BilledCost": cost,
            "EffectiveCost": cost,
            "ContractedCost": cost,
            "ListCost": cost,
            "ChargeDescription": f"{record.model} {label} via {service}",
            # Quantities as decimals: FOCUS metric columns must not
            # schema-infer as integers.
            "ConsumedQuantity": float(quantity),
            "ListUnitPrice": unit_price,
            "PricingQuantity": float(quantity),
            "SkuId": sku,
            "SkuPriceId": sku,
        }


def _floor_hour(moment: datetime) -> datetime:
    return moment.replace(minute=0, second=0, microsecond=0)


def _next_month(period_start: datetime) -> datetime:
    if period_start.month == 12:
        return period_start.replace(year=period_start.year + 1, month=1)
    return period_start.replace(month=period_start.month + 1)


def _fmt(moment: datetime) -> str:
    """FOCUS datetimes: ISO 8601 UTC with Z suffix."""
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")
