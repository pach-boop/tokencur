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

from tokencur.pricing import ModelRates, rates_for, rates_for_record
from tokencur.records import BilledCharge, UsageRecord, parse_timestamp

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


def provider_for(source: str) -> str:
    """The FOCUS ProviderName of a usage source."""
    return _PROVIDER_BY_SOURCE.get(source, "Unknown")


def to_focus_rows(
    records: Iterable[UsageRecord], discounts: dict[str, float] | None = None
) -> Iterator[dict]:
    """Yield FOCUS charge rows for every priced record.

    ``discounts`` (provider name -> fraction off list, see
    ``pricing.load_discounts``) are a negotiated contract: ListCost keeps
    the public price; ContractedCost, EffectiveCost and BilledCost carry
    the price after the discount.
    """
    for record in records:
        charge_start = parse_timestamp(record.timestamp)
        # List price in force on the charge's day, for its request options.
        rates = rates_for_record(record)
        if rates is None or charge_start is None:
            continue  # surfaced by callers: never $0, never a made-up date
        off = (discounts or {}).get(provider_for(record.source), 0.0)
        yield from _record_rows(record, rates, charge_start, 1.0 - off)


def charge_rows(charges: Iterable[BilledCharge]) -> Iterator[dict]:
    """FOCUS rows for billed charges: real money, not showback.

    BilledCost is what the provider took. No list price is published per
    charge, so ListCost, ContractedCost and EffectiveCost equal it. A
    charge with no billed time (storage only) has no unit price: there is
    no quantity to divide by, and FOCUS wants an explicit null then.
    """
    for c in charges:
        start = parse_timestamp(c.period_start)
        if start is None:
            continue
        period_start = start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        unit = c.unit.lower()
        detail = f", {c.detail}" if c.detail else ""
        yield {
            "BilledCost": c.amount_usd,
            "EffectiveCost": c.amount_usd,
            "ContractedCost": c.amount_usd,
            "ListCost": c.amount_usd,
            "BillingAccountId": f"tokencur-{c.source}",
            "BillingAccountName": f"{c.provider} account",
            "BillingCurrency": "USD",
            "BillingPeriodStart": _fmt(period_start),
            "BillingPeriodEnd": _fmt(_next_month(period_start)),
            "ChargeCategory": "Usage",
            "ChargeClass": None,
            "ChargeDescription": f"{c.service} {c.resource_id}: {c.quantity:.2f} {unit}{detail}",
            "ChargeFrequency": "Usage-Based",
            "ChargePeriodStart": c.period_start,
            "ChargePeriodEnd": c.period_end,
            "ConsumedQuantity": float(c.quantity),
            "ConsumedUnit": c.unit,
            # Prepaid credits: the provider issues no invoice per charge.
            "InvoiceId": None,
            "InvoiceIssuerName": c.provider,
            "ListUnitPrice": c.amount_usd / c.quantity if c.quantity else None,
            "PricingCategory": "Standard",
            "PricingQuantity": float(c.quantity),
            "PricingUnit": c.unit,
            "ProviderName": c.provider,
            "PublisherName": c.provider,
            "ResourceId": c.resource_id,
            "ResourceName": c.resource_id,
            "ResourceType": c.resource_type,
            "ServiceCategory": "Compute",
            "ServiceName": c.service,
            "ServiceSubcategory": "Containers",
            "SkuId": f"{c.source}/{unit}",
            "SkuPriceId": f"{c.source}/{unit}",
            "SubAccountId": None,
            "SubAccountName": None,
        }


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
    record: UsageRecord, rates: ModelRates, charge_start: datetime, contracted: float
) -> Iterator[dict]:
    period_start = charge_start.replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    provider = provider_for(record.source)
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
        # A request option (fast mode, US-only, batch) is a price point of
        # the same SKU: SkuPriceId names it, ListUnitPrice carries it.
        option = f"/{record.price_modifiers}" if record.price_modifiers else ""
        note = f" ({record.price_modifiers})" if record.price_modifiers else ""
        yield {
            **shared,
            # Showback: the cost columns carry list cost (module doc); a
            # negotiated discount lowers all but ListCost.
            "BilledCost": cost * contracted,
            "EffectiveCost": cost * contracted,
            "ContractedCost": cost * contracted,
            "ListCost": cost,
            "ChargeDescription": f"{record.model} {label} via {service}{note}",
            # Quantities as decimals: FOCUS metric columns must not
            # schema-infer as integers.
            "ConsumedQuantity": float(quantity),
            "ListUnitPrice": unit_price,
            "PricingQuantity": float(quantity),
            "SkuId": sku,
            "SkuPriceId": sku + option,
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
