"""Normalize usage records into FOCUS-conformant charge rows.

Each :class:`~tokencur.ingest.claude_code.UsageRecord` explodes into up
to five charge rows — one per token bucket (input, output, cache read,
cache writes by TTL) — mirroring how provider billing exports emit one
line item per SKU.

Cost semantics (showback): local agent usage is not invoiced per token,
so ``BilledCost``, ``EffectiveCost``, ``ContractedCost`` and
``ListCost`` all carry the API-equivalent list cost (see
``pricing.py``). Unpriced records are skipped and counted by the
caller, never exported as $0.

Column semantics follow the FOCUS specification
(https://focus.finops.org); each mapping states its intent inline.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable, Iterator

from tokencur.ingest.claude_code import UsageRecord
from tokencur.pricing import ModelRates, rates_for

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
    ("cache_write_5m_tokens", "cache-write-5m", "cache write (5m TTL) tokens",
     "cache_write_5m"),
    ("cache_write_1h_tokens", "cache-write-1h", "cache write (1h TTL) tokens",
     "cache_write_1h"),
)


def to_focus_rows(records: Iterable[UsageRecord]) -> Iterator[dict]:
    """Yield FOCUS charge rows for every priced record."""
    for record in records:
        rates = rates_for(record.model)
        if rates is None:
            continue  # unpriced usage is surfaced by callers, not exported
        yield from _record_rows(record, rates)


def unpriced_models(records: Iterable[UsageRecord]) -> dict[str, int]:
    """Count records that to_focus_rows would skip, by model."""
    counts: dict[str, int] = {}
    for record in records:
        if rates_for(record.model) is None:
            counts[record.model] = counts.get(record.model, 0) + 1
    return counts


def _record_rows(record: UsageRecord, rates: ModelRates) -> Iterator[dict]:
    charge_start = _parse_ts(record.timestamp)
    charge_end = charge_start + timedelta(hours=1)
    period_start = charge_start.replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    period_end = _next_month(period_start)
    provider = _PROVIDER_BY_SOURCE.get(record.source, "Unknown")
    service = _SERVICE_BY_SOURCE.get(record.source, record.source)

    for attribute, sku_suffix, label, rate_attribute in _BUCKETS:
        quantity = getattr(record, attribute)
        if not quantity:
            continue
        rate_mtok = getattr(rates, rate_attribute)
        unit_price = rate_mtok / 1_000_000  # USD per token
        cost = quantity * unit_price
        sku = f"{record.model}/{sku_suffix}"
        yield {
            # Showback: all four cost columns carry list cost (module doc).
            "BilledCost": cost,
            "EffectiveCost": cost,
            "ContractedCost": cost,
            "ListCost": cost,
            "BillingAccountId": "tokencur-local",
            "BillingAccountName": "Local AI usage (showback)",
            "BillingCurrency": "USD",
            "BillingPeriodStart": _fmt(period_start),
            "BillingPeriodEnd": _fmt(period_end),
            "ChargeCategory": "Usage",
            "ChargeClass": None,
            "ChargeDescription": f"{record.model} {label} via {service}",
            "ChargeFrequency": "Usage-Based",
            "ChargePeriodStart": _fmt(_floor_hour(charge_start)),
            "ChargePeriodEnd": _fmt(_floor_hour(charge_end)),
            # Quantities as decimals: FOCUS metric columns must not
            # schema-infer as integers.
            "ConsumedQuantity": float(quantity),
            "ConsumedUnit": "tokens",
            # Showback charges have no invoice; the spec requires an
            # explicit null in that case.
            "InvoiceId": None,
            "InvoiceIssuerName": provider,
            "ListUnitPrice": unit_price,
            "PricingCategory": "Standard",
            "PricingQuantity": float(quantity),
            "PricingUnit": "tokens",
            "ProviderName": provider,
            "PublisherName": provider,
            "ResourceId": record.session_id or None,
            "ResourceName": record.session_id or None,
            "ResourceType": "AI agent session",
            "ServiceCategory": "AI and Machine Learning",
            "ServiceName": service,
            "ServiceSubcategory": "Generative AI",
            "SkuId": sku,
            "SkuPriceId": sku,
            "SubAccountId": record.workspace or None,
            "SubAccountName": record.workspace or None,
        }


def _parse_ts(timestamp: str) -> datetime:
    if not timestamp:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)
    value = timestamp.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _floor_hour(moment: datetime) -> datetime:
    return moment.replace(minute=0, second=0, microsecond=0)


def _next_month(period_start: datetime) -> datetime:
    if period_start.month == 12:
        return period_start.replace(year=period_start.year + 1, month=1)
    return period_start.replace(month=period_start.month + 1)


def _fmt(moment: datetime) -> str:
    """FOCUS datetimes: ISO 8601 UTC with Z suffix."""
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")
