"""Rate cards and cost computation.

Prices are USD per million tokens (MTok) at public API list rates.
Subscription usage (e.g. Claude Code plans) is not billed per token, so
costs computed here are *API-equivalent list costs* — the standard
showback approach: what this usage would cost at published rates.

Two layers, curated first:

1. ``RATE_CARD`` — the hand-maintained Anthropic card (dated, sourced).
   Cache multipliers per Anthropic pricing docs: reads at 0.1x the
   input rate; writes at 1.25x (5-minute TTL) or 2x (1-hour TTL).
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

from tokencur.ingest.claude_code import UsageRecord

AS_OF = "2026-07-06"
SOURCE = "https://platform.claude.com/docs/en/pricing"


@dataclass(frozen=True)
class ModelRates:
    """USD per MTok for each billable token type of one model."""

    input: float
    output: float
    cache_read: float
    cache_write_5m: float
    cache_write_1h: float


def _anthropic_rates(input: float, output: float) -> ModelRates:
    """Anthropic's published cache multipliers over the input rate."""
    return ModelRates(
        input=input,
        output=output,
        cache_read=input * 0.10,
        cache_write_5m=input * 1.25,
        cache_write_1h=input * 2.00,
    )


RATE_CARD: dict[str, ModelRates] = {
    "claude-fable-5": _anthropic_rates(10.00, 50.00),
    "claude-opus-4-8": _anthropic_rates(5.00, 25.00),
    "claude-opus-4-7": _anthropic_rates(5.00, 25.00),
    "claude-opus-4-6": _anthropic_rates(5.00, 25.00),
    "claude-opus-4-5": _anthropic_rates(5.00, 25.00),
    "claude-opus-4-1": _anthropic_rates(15.00, 75.00),
    # Sonnet 5 standard list price. An intro price ($2/$10) applies
    # through 2026-08-31; the card values usage at list for consistency.
    "claude-sonnet-5": _anthropic_rates(3.00, 15.00),
    "claude-sonnet-4-6": _anthropic_rates(3.00, 15.00),
    "claude-sonnet-4-5": _anthropic_rates(3.00, 15.00),
    "claude-haiku-4-5": _anthropic_rates(1.00, 5.00),
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

    Dated ids (``claude-haiku-4-5-20251001``) resolve to their alias,
    falling back to the exact id — snapshot keys themselves can be
    dated. The curated card wins over the community snapshot. Unknown
    models return None so callers can surface unpriced usage instead of
    silently valuing it at zero.
    """
    base = _DATE_SUFFIX.sub("", model)
    snapshot = _snapshot_rates()
    return RATE_CARD.get(base) or snapshot.get(base) or snapshot.get(model)


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
