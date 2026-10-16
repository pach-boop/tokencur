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

import json
from collections.abc import Iterable, Iterator
from datetime import datetime, timedelta
from functools import cache

from tokencur.pricing import ModelRates, rates_for, rates_for_record
from tokencur.records import BilledCharge, UsageRecord, parse_timestamp

FOCUS_VERSION = "1.3"

# Column order for CSV export: every column of the FOCUS 1.3 Cost and
# Usage dataset. ProviderName and PublisherName are deprecated in 1.3 in
# favour of ServiceProviderName and HostProviderName, but still required.
FOCUS_COLUMNS = [
    "AllocatedMethodDetails",
    "AllocatedMethodId",
    "AllocatedResourceId",
    "AllocatedResourceName",
    "AllocatedTags",
    "AvailabilityZone",
    "BilledCost",
    "BillingAccountId",
    "BillingAccountName",
    "BillingAccountType",
    "BillingCurrency",
    "BillingPeriodStart",
    "BillingPeriodEnd",
    "CapacityReservationId",
    "CapacityReservationStatus",
    "ChargeCategory",
    "ChargeClass",
    "ChargeDescription",
    "ChargeFrequency",
    "ChargePeriodStart",
    "ChargePeriodEnd",
    "CommitmentDiscountCategory",
    "CommitmentDiscountId",
    "CommitmentDiscountName",
    "CommitmentDiscountQuantity",
    "CommitmentDiscountStatus",
    "CommitmentDiscountType",
    "CommitmentDiscountUnit",
    "ConsumedQuantity",
    "ConsumedUnit",
    "ContractApplied",
    "ContractedCost",
    "ContractedUnitPrice",
    "EffectiveCost",
    "HostProviderName",
    "InvoiceId",
    "InvoiceIssuerName",
    "ListCost",
    "ListUnitPrice",
    "PricingCategory",
    "PricingCurrency",
    "PricingCurrencyContractedUnitPrice",
    "PricingCurrencyEffectiveCost",
    "PricingCurrencyListUnitPrice",
    "PricingQuantity",
    "PricingUnit",
    "ProviderName",
    "PublisherName",
    "RegionId",
    "RegionName",
    "ResourceId",
    "ResourceName",
    "ResourceType",
    "ServiceCategory",
    "ServiceName",
    "ServiceProviderName",
    "ServiceSubcategory",
    "SkuId",
    "SkuMeter",
    "SkuPriceDetails",
    "SkuPriceId",
    "SubAccountId",
    "SubAccountName",
    "SubAccountType",
    "Tags",
]

# Columns for features no tokencur source has: cost allocation, zones and
# regions, capacity reservations, commitment discounts, contract
# commitments and tags. They are exported as explicit nulls rather than
# left out: the Foundation's validator checks a column's type even where
# its feature does not apply, and a fixed set of columns loads more easily.
_NOT_APPLICABLE = dict.fromkeys(
    (
        "AllocatedMethodDetails",
        "AllocatedMethodId",
        "AllocatedResourceId",
        "AllocatedResourceName",
        "AllocatedTags",
        "AvailabilityZone",
        "CapacityReservationId",
        "CapacityReservationStatus",
        "CommitmentDiscountCategory",
        "CommitmentDiscountId",
        "CommitmentDiscountName",
        "CommitmentDiscountQuantity",
        "CommitmentDiscountStatus",
        "CommitmentDiscountType",
        "CommitmentDiscountUnit",
        "ContractApplied",
        "RegionId",
        "RegionName",
        "Tags",
    )
)

# What a billed charge's unit meters, for SkuMeter.
_METER_BY_UNIT = {"Hours": "time", "GB": "disk"}

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

# SKU price properties by SKU suffix, for SkuPriceDetails. FOCUS defines
# none for tokens, so they are custom properties: "x_" keys.
_PRICE_PROPERTIES = {
    "input": {"x_TokenType": "input"},
    "output": {"x_TokenType": "output"},
    "cache-read": {"x_TokenType": "cache read"},
    "cache-write-5m": {"x_TokenType": "cache write", "x_CacheTtl": "5m"},
    "cache-write-1h": {"x_TokenType": "cache write", "x_CacheTtl": "1h"},
}


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

    BilledCost and EffectiveCost are what the provider took. No list
    price is published per charge, so the unit price is the amount over
    the quantity, and ListCost and ContractedCost are that price times the
    quantity: FOCUS checks that product exactly, and dividing then
    multiplying back can be off the amount by a rounding unit. A charge
    with nothing billed to divide by has no unit price, an explicit null.
    """
    for c in charges:
        start = parse_timestamp(c.period_start)
        if start is None:
            continue
        period_start = start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        unit = c.unit.lower()
        detail = f", {c.detail}" if c.detail else ""
        unit_price = c.amount_usd / c.quantity if c.quantity else None
        list_cost = c.amount_usd if unit_price is None else unit_price * c.quantity
        yield {
            **_NOT_APPLICABLE,
            "BilledCost": c.amount_usd,
            "EffectiveCost": c.amount_usd,
            "ContractedCost": list_cost,
            "ListCost": list_cost,
            "BillingAccountId": f"tokencur-{c.source}",
            "BillingAccountName": f"{c.provider} account",
            "BillingAccountType": "Prepaid credits",
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
            "ContractedUnitPrice": unit_price,
            "HostProviderName": c.provider,
            # Prepaid credits: the provider issues no invoice per charge.
            "InvoiceId": None,
            "InvoiceIssuerName": c.provider,
            "ListUnitPrice": unit_price,
            "PricingCategory": "Standard",
            # Priced in the billing currency: the PricingCurrency columns
            # repeat the billing ones.
            "PricingCurrency": "USD",
            "PricingCurrencyContractedUnitPrice": unit_price,
            "PricingCurrencyEffectiveCost": c.amount_usd,
            "PricingCurrencyListUnitPrice": unit_price,
            "PricingQuantity": float(c.quantity),
            "PricingUnit": c.unit,
            "ProviderName": c.provider,
            "PublisherName": c.provider,
            "ResourceId": c.resource_id,
            "ResourceName": c.resource_id,
            "ResourceType": c.resource_type,
            "ServiceCategory": "Compute",
            "ServiceName": c.service,
            "ServiceProviderName": c.provider,
            "ServiceSubcategory": "Containers",
            "SkuId": f"{c.source}/{unit}",
            "SkuMeter": f"{c.resource_type} {_METER_BY_UNIT.get(c.unit, unit)}",
            "SkuPriceDetails": None,
            "SkuPriceId": f"{c.source}/{unit}",
            "SubAccountId": None,
            "SubAccountName": None,
            "SubAccountType": None,
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
        **_NOT_APPLICABLE,
        "BillingAccountId": "tokencur-local",
        "BillingAccountName": "Local AI usage (showback)",
        "BillingAccountType": "Showback",
        "BillingCurrency": "USD",
        "BillingPeriodStart": _fmt(period_start),
        "BillingPeriodEnd": _fmt(_next_month(period_start)),
        "ChargeCategory": "Usage",
        "ChargeClass": None,
        "ChargeFrequency": "Usage-Based",
        "ChargePeriodStart": _fmt(_floor_hour(charge_start)),
        "ChargePeriodEnd": _fmt(_floor_hour(charge_start + timedelta(hours=1))),
        "ConsumedUnit": "tokens",
        "HostProviderName": provider,
        # Showback charges have no invoice; the spec requires an
        # explicit null in that case.
        "InvoiceId": None,
        "InvoiceIssuerName": provider,
        "PricingCategory": "Standard",
        # Priced in the billing currency: the PricingCurrency columns
        # repeat the billing ones.
        "PricingCurrency": "USD",
        "PricingUnit": "tokens",
        "ProviderName": provider,
        "PublisherName": provider,
        "ResourceId": record.session_id or None,
        "ResourceName": record.session_id or None,
        "ResourceType": "AI agent session",
        "ServiceCategory": "AI and Machine Learning",
        "ServiceName": service,
        "ServiceProviderName": provider,
        "ServiceSubcategory": "Generative AI",
        "SubAccountId": record.workspace or None,
        "SubAccountName": record.workspace or None,
        "SubAccountType": "Workspace" if record.workspace else None,
    }

    for attribute, sku_suffix, label, rate_attribute in _BUCKETS:
        quantity = getattr(record, attribute)
        if not quantity:
            continue
        unit_price = getattr(rates, rate_attribute) / 1_000_000  # USD per token
        contracted_price = unit_price * contracted
        # Each cost is its unit price times the quantity, the product
        # FOCUS checks exactly.
        list_cost = quantity * unit_price
        cost = quantity * contracted_price
        sku = f"{record.model}/{sku_suffix}"
        # A request option (fast mode, US-only, batch) is a price point of
        # the same SKU: SkuPriceId names it, ListUnitPrice carries it.
        option = f"/{record.price_modifiers}" if record.price_modifiers else ""
        note = f" ({record.price_modifiers})" if record.price_modifiers else ""
        yield {
            **shared,
            # Showback: the cost columns carry list cost (module doc); a
            # negotiated discount lowers all but ListCost.
            "BilledCost": cost,
            "EffectiveCost": cost,
            "ContractedCost": cost,
            "ListCost": list_cost,
            "ChargeDescription": f"{record.model} {label} via {service}{note}",
            # Quantities as decimals: FOCUS metric columns must not
            # schema-infer as integers.
            "ConsumedQuantity": float(quantity),
            "ContractedUnitPrice": contracted_price,
            "ListUnitPrice": unit_price,
            "PricingCurrencyContractedUnitPrice": contracted_price,
            "PricingCurrencyEffectiveCost": cost,
            "PricingCurrencyListUnitPrice": unit_price,
            "PricingQuantity": float(quantity),
            "SkuId": sku,
            "SkuMeter": label,
            "SkuPriceDetails": _price_details(sku_suffix, record.price_modifiers),
            "SkuPriceId": sku + option,
        }


@cache
def _price_details(sku_suffix: str, price_modifiers: str) -> str:
    """SkuPriceDetails of a token SKU price, a JSON object: what kind of
    tokens, and the request options that set the price, if any."""
    properties = dict(_PRICE_PROPERTIES[sku_suffix])
    if price_modifiers:
        properties["x_RequestOptions"] = price_modifiers
    return json.dumps(properties, separators=(",", ":"))


def _floor_hour(moment: datetime) -> datetime:
    return moment.replace(minute=0, second=0, microsecond=0)


def _next_month(period_start: datetime) -> datetime:
    if period_start.month == 12:
        return period_start.replace(year=period_start.year + 1, month=1)
    return period_start.replace(month=period_start.month + 1)


def _fmt(moment: datetime) -> str:
    """FOCUS datetimes: ISO 8601 UTC with Z suffix."""
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")
