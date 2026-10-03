"""Rate cards and cost computation.

Prices are USD per million tokens (MTok) at public API list rates.
Subscription usage (e.g. Claude Code plans) is not billed per token, so
costs computed here are *API-equivalent list costs* — the standard
showback approach: what this usage would cost at published rates.

Two layers, curated first:

1. ``RATE_CARD`` — the hand-maintained Anthropic card (dated, sourced):
   every model on Anthropic's pricing page as of ``AS_OF``. Writes cost
   1.25x the input rate (5-minute TTL) or 2x (1-hour TTL); reads 0.1x,
   except where the page says otherwise (Opus 5.5, Fable 5.1).
2. A vendored snapshot of the community-maintained LiteLLM price
   database (see ``scripts/update_pricing_snapshot.py``) as fallback,
   which extends coverage to OpenAI/Gemini and future models. Cache
   rates come from its explicit per-model fields because multipliers
   differ across providers (e.g. OpenAI cache reads are 0.5x).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, fields
from datetime import date
from functools import lru_cache
from importlib import resources
from pathlib import Path

from tokencur import __version__
from tokencur.records import UsageRecord, parse_timestamp

AS_OF = "2026-10-02"
SOURCE = "https://platform.claude.com/docs/en/about-claude/pricing"


@dataclass(frozen=True)
class ModelRates:
    """USD per MTok for each billable token type of one model."""

    input: float
    output: float
    cache_read: float
    cache_write_5m: float
    cache_write_1h: float


def _anthropic_rates(input: float, output: float, read: float = 0.10) -> ModelRates:
    """Anthropic's published cache multipliers over the input rate."""
    return ModelRates(
        input=input,
        output=output,
        cache_read=input * read,
        cache_write_5m=input * 1.25,
        cache_write_1h=input * 2.00,
    )


RATE_CARD: dict[str, ModelRates] = {
    # Anthropic's pricing page on 2026-10-02 (SOURCE), top to bottom.
    # Cache hits cost 0.025x input on Fable 5.1 / Mythos 5.1 ($0.25)
    # and 0.05x on Opus 5.5 ($0.20); every other model uses 0.1x.
    "claude-fable-5-1": _anthropic_rates(10.00, 50.00, read=0.025),
    "claude-mythos-5-1": _anthropic_rates(10.00, 50.00, read=0.025),
    "claude-fable-5": _anthropic_rates(10.00, 50.00),
    "claude-mythos-5": _anthropic_rates(10.00, 50.00),
    "claude-opus-5-5": _anthropic_rates(4.00, 20.00, read=0.05),
    "claude-opus-5": _anthropic_rates(5.00, 25.00),
    "claude-opus-4-8": _anthropic_rates(5.00, 25.00),
    "claude-opus-4-7": _anthropic_rates(5.00, 25.00),
    "claude-opus-4-6": _anthropic_rates(5.00, 25.00),
    "claude-opus-4-5": _anthropic_rates(5.00, 25.00),
    "claude-opus-4-1": _anthropic_rates(15.00, 75.00),
    "claude-sonnet-5-5": _anthropic_rates(2.00, 10.00),
    # Launched at $2/$10 as an introductory price through 2026-08-31;
    # Anthropic made it the standard price and cancelled the scheduled
    # rise to $3/$15. (This card had valued Sonnet 5 at $3/$15: see
    # RATE_CARD_CORRECTIONS.)
    "claude-sonnet-5": _anthropic_rates(2.00, 10.00),
    "claude-sonnet-4-6": _anthropic_rates(3.00, 15.00),
    "claude-sonnet-4-5": _anthropic_rates(3.00, 15.00),
    "claude-haiku-4-5": _anthropic_rates(1.00, 5.00),
    # Retired models, keyed the way rates_for() resolves their dated ids
    # (claude-opus-4-20250514 -> claude-opus-4), so old logs stay priced.
    "claude-opus-4": _anthropic_rates(15.00, 75.00),
    "claude-sonnet-4": _anthropic_rates(3.00, 15.00),
    "claude-3-5-haiku": _anthropic_rates(0.80, 4.00),
    # Proxy rate (documented estimation, 2026-07-08): Kimi Code's
    # coding-plan alias has no published per-token price. Valued at
    # kimi-k2.6 list rates — the nearest published generation — so real
    # usage surfaces in showback instead of reading as $0. Moonshot's
    # automatic caching bills reads only (no write premium). Replace
    # with the official rate if Moonshot publishes one.
    "kimi-k2.7-code-highspeed": ModelRates(
        input=0.95,
        output=4.00,
        cache_read=0.16,
        cache_write_5m=0.0,
        cache_write_1h=0.0,
    ),
}

#: Rates the curated card held before a price move, per model, oldest
#: first: ``(until, rates)`` where ``until`` is the first day (UTC,
#: ``YYYY-MM-DD``) the next rate applied. Empty because no curated price
#: has moved since the card began on 2026-07-06; the one change so far
#: was a correction (below). When Anthropic moves a curated price, the
#: old rate comes here, never away: tests/test_rate_history.py fails on
#: a curated rate that leaves the card any other way (ADR 0011).
RATE_CARD_HISTORY: dict[str, tuple[tuple[str, ModelRates], ...]] = {}

#: Curated rates found wrong after they were published, per model: the
#: day the card was corrected, the (input, output) rates it wrongly held,
#: and why. A correction is not a price move, because the wrong rate
#: never applied: it stays out of RATE_CARD_HISTORY and is disclosed
#: here and in the changelog instead.
RATE_CARD_CORRECTIONS: dict[str, tuple[str, tuple[float, float], str]] = {
    "claude-sonnet-5": (
        "2026-10-02",
        (3.00, 15.00),
        "the card used a scheduled rise Anthropic cancelled; the $2/$10 "
        "launch price became the standard price",
    ),
}

_DATE_SUFFIX = re.compile(r"-20\d{6}$")
_PER_TOKEN_TO_MTOK = 1_000_000

History = tuple[tuple[str, ModelRates], ...]


def _from_entry(entry: dict) -> ModelRates:
    """Snapshot fields (USD per token) as rates per MTok.

    A missing cache field means the source doesn't price that dimension;
    the 1h write rate falls back to the 5m rate when absent (slight,
    documented underestimate — mirrors the ingest-side assumption).
    """
    write_5m = entry.get("cache_creation_input_token_cost", 0.0)
    return ModelRates(
        input=entry["input_cost_per_token"] * _PER_TOKEN_TO_MTOK,
        output=entry["output_cost_per_token"] * _PER_TOKEN_TO_MTOK,
        cache_read=entry.get("cache_read_input_token_cost", 0.0) * _PER_TOKEN_TO_MTOK,
        cache_write_5m=write_5m * _PER_TOKEN_TO_MTOK,
        cache_write_1h=entry.get("cache_creation_input_token_cost_above_1hr", write_5m)
        * _PER_TOKEN_TO_MTOK,
    )


_SNAPSHOT_FILE = "pricing_data/litellm_snapshot.json"


@lru_cache(maxsize=1)
def _snapshot() -> tuple[dict[str, ModelRates], dict[str, History]]:
    """Current rates and rate history from the vendored LiteLLM snapshot."""
    path = resources.files("tokencur").joinpath(_SNAPSHOT_FILE)
    return parse_snapshot(json.loads(path.read_text(encoding="utf-8"))["models"])


@dataclass(frozen=True)
class Provenance:
    """What a figure was computed with, so anyone can reproduce it: the
    tokencur version, the curated card's date, and the pricing snapshot
    by fetch date and SHA-256. Each release publishes and attests that
    same snapshot file, so the hash names data anyone can download."""

    tokencur: str
    curated_card: str
    snapshot_fetched: str
    snapshot_models: int
    snapshot_sha256: str

    def __str__(self) -> str:
        return (
            f"tokencur {self.tokencur}, curated card {self.curated_card}, "
            f"pricing snapshot {self.snapshot_sha256[:12]} "
            f"(fetched {self.snapshot_fetched})"
        )


@lru_cache(maxsize=1)
def provenance() -> Provenance:
    """The provenance of every figure this tokencur computes."""
    raw = resources.files("tokencur").joinpath(_SNAPSHOT_FILE).read_bytes()
    data = json.loads(raw)
    return Provenance(
        tokencur=__version__,
        curated_card=AS_OF,
        snapshot_fetched=data.get("_meta", {}).get("fetched", "unknown"),
        snapshot_models=len(data.get("models", {})),
        snapshot_sha256=hashlib.sha256(raw).hexdigest(),
    )


def parse_snapshot(
    models: dict[str, dict],
) -> tuple[dict[str, ModelRates], dict[str, History]]:
    """A snapshot's ``models`` as current rates and rate history."""
    rates = {name: _from_entry(entry) for name, entry in models.items()}
    history = {
        name: tuple((item["until"], _from_entry(item)) for item in entry["history"])
        for name, entry in models.items()
        if entry.get("history")
    }
    return rates, history


def _snapshot_rates() -> dict[str, ModelRates]:
    """Current rates from the vendored LiteLLM snapshot (per MTok)."""
    return _snapshot()[0]


@lru_cache(maxsize=8192)
def rates_for(model: str, on: date | None = None) -> ModelRates | None:
    """Resolve a model id to its rates; None if the model is unpriced.

    Vendor prefixes (``moonshot-ai/kimi-k2``) are stripped, matching
    how snapshot keys are stored. Dated ids
    (``claude-haiku-4-5-20251001``) resolve to their alias, falling
    back to the exact id — snapshot keys themselves can be dated. The
    curated card wins over the community snapshot. Unknown models
    return None so callers can surface unpriced usage instead of
    silently valuing it at zero.

    ``on`` asks for the rate in force on that UTC day (point-in-time
    list price): a model whose price moved is valued at the rate it had
    then. Without ``on``, or for a model with no recorded move, the
    current rate.
    """
    bare = model.split("/")[-1]
    base = _DATE_SUFFIX.sub("", bare)
    if base in RATE_CARD:
        return _in_force(RATE_CARD[base], RATE_CARD_HISTORY.get(base, ()), on)
    rates, history = _snapshot()
    for key in (base, bare):
        if key in rates:
            return _in_force(rates[key], history.get(key, ()), on)
    return None


def _in_force(current: ModelRates, history: History, on: date | None) -> ModelRates:
    if on is not None:
        day = on.isoformat()
        for until, rates in history:  # oldest first
            if day < until:
                return rates
    return current


#: Price multipliers for the request options Anthropic publishes, applied
#: to every rate of the call, cache rates included (cache multipliers
#: stack on them). Fast mode doubles the price (Opus 5.5 $8/$40, Opus 5
#: and 4.8 $10/$50); US-only inference costs 1.1x on every category; the
#: Batch API halves input and output. Source: SOURCE, as of 2026-10-02.
MODIFIER_FACTORS = {"fast": 2.0, "us": 1.1, "batch": 0.5}


def with_modifiers(rates: ModelRates, modifiers: str) -> ModelRates:
    """``rates`` scaled for the request options in ``modifiers``."""
    factor = 1.0
    for name in filter(None, modifiers.split("+")):
        factor *= MODIFIER_FACTORS.get(name, 1.0)
    if factor == 1.0:
        return rates
    return ModelRates(*(getattr(rates, f.name) * factor for f in fields(ModelRates)))


def rates_for_record(record: UsageRecord) -> ModelRates | None:
    """The rates one call is valued at: its model's list rate in force on
    its day, scaled for its request options. None if unpriced."""
    rates = rates_for(record.model, record_day(record))
    if rates is None or not record.price_modifiers:
        return rates
    return with_modifiers(rates, record.price_modifiers)


def record_day(record: UsageRecord) -> date | None:
    """The UTC day a record's call happened, or None when undated."""
    moment = parse_timestamp(record.timestamp)
    return moment.date() if moment else None


def record_cost_usd(record: UsageRecord) -> float | None:
    """API-equivalent list cost of one usage record, at the rate in force
    on its day and for its request options; None if unpriced."""
    rates = rates_for_record(record)
    if rates is None:
        return None
    return (
        record.input_tokens * rates.input
        + record.output_tokens * rates.output
        + record.cache_read_tokens * rates.cache_read
        + record.cache_write_5m_tokens * rates.cache_write_5m
        + record.cache_write_1h_tokens * rates.cache_write_1h
    ) / 1_000_000


class ConfigError(ValueError):
    """A configuration file tokencur cannot use, with a readable reason."""


def load_discounts(path: Path) -> dict[str, float]:
    """Negotiated discounts off list price, by FOCUS provider name.

    The file is ``{"discounts": {"Anthropic": 0.15, "OpenAI": 0.1}}``:
    each value is the fraction taken off list, from 0 up to (not
    including) 1. Raises ConfigError with a readable message otherwise.
    """
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ConfigError(f"{path} is not valid JSON ({exc})") from None
    discounts = data.get("discounts") if isinstance(data, dict) else None
    if not isinstance(discounts, dict):
        raise ConfigError(f'{path} needs a "discounts" object of provider: fraction')
    for provider, fraction in discounts.items():
        if type(fraction) not in (int, float) or not 0 <= fraction < 1:
            raise ConfigError(
                f"{path}: the discount for {provider!r} must be a fraction "
                f"between 0 and 1 (0.15 = 15% off list), not {fraction!r}"
            )
    return {provider: float(fraction) for provider, fraction in discounts.items()}
