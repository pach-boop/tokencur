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

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources

from tokencur.records import UsageRecord

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
    # rise to $3/$15. (This card had valued Sonnet 5 at $3/$15.)
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

_DATE_SUFFIX = re.compile(r"-20\d{6}$")
_PER_TOKEN_TO_MTOK = 1_000_000


@lru_cache(maxsize=1)
def _snapshot_rates() -> dict[str, ModelRates]:
    """Rates from the vendored LiteLLM snapshot (per-token → per-MTok).

    A missing cache field means the source doesn't price that dimension;
    the 1h write rate falls back to the 5m rate when absent (slight,
    documented underestimate — mirrors the ingest-side assumption).
    """
    path = resources.files("tokencur").joinpath("pricing_data/litellm_snapshot.json")
    models = json.loads(path.read_text(encoding="utf-8"))["models"]
    rates: dict[str, ModelRates] = {}
    for name, entry in models.items():
        write_5m = entry.get("cache_creation_input_token_cost", 0.0)
        rates[name] = ModelRates(
            input=entry["input_cost_per_token"] * _PER_TOKEN_TO_MTOK,
            output=entry["output_cost_per_token"] * _PER_TOKEN_TO_MTOK,
            cache_read=entry.get("cache_read_input_token_cost", 0.0)
            * _PER_TOKEN_TO_MTOK,
            cache_write_5m=write_5m * _PER_TOKEN_TO_MTOK,
            cache_write_1h=entry.get(
                "cache_creation_input_token_cost_above_1hr", write_5m
            )
            * _PER_TOKEN_TO_MTOK,
        )
    return rates


def rates_for(model: str) -> ModelRates | None:
    """Resolve a model id to its rates; None if the model is unpriced.

    Vendor prefixes (``moonshot-ai/kimi-k2``) are stripped, matching
    how snapshot keys are stored. Dated ids
    (``claude-haiku-4-5-20251001``) resolve to their alias, falling
    back to the exact id — snapshot keys themselves can be dated. The
    curated card wins over the community snapshot. Unknown models
    return None so callers can surface unpriced usage instead of
    silently valuing it at zero.
    """
    bare = model.split("/")[-1]
    base = _DATE_SUFFIX.sub("", bare)
    snapshot = _snapshot_rates()
    return RATE_CARD.get(base) or snapshot.get(base) or snapshot.get(bare)


def record_cost_usd(record: UsageRecord) -> float | None:
    """API-equivalent list cost of one usage record; None if unpriced."""
    rates = rates_for(record.model)
    if rates is None:
        return None
    return (
        record.input_tokens * rates.input
        + record.output_tokens * rates.output
        + record.cache_read_tokens * rates.cache_read
        + record.cache_write_5m_tokens * rates.cache_write_5m
        + record.cache_write_1h_tokens * rates.cache_write_1h
    ) / 1_000_000
