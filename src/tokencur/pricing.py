"""Anthropic rate card and cost computation.

Prices are USD per million tokens (MTok) at public API list rates.
Subscription usage (e.g. Claude Code plans) is not billed per token, so
costs computed here are *API-equivalent list costs* — the standard
showback approach: what this usage would cost at published rates.

Cache multipliers per Anthropic pricing docs: reads cost 0.1x the input
rate; cache writes cost 1.25x (5-minute TTL) or 2x (1-hour TTL).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from tokencur.ingest.claude_code import UsageRecord

AS_OF = "2026-07-06"
SOURCE = "https://platform.claude.com/docs/en/pricing"

_CACHE_READ_MULT = 0.10
_CACHE_WRITE_5M_MULT = 1.25
_CACHE_WRITE_1H_MULT = 2.00


@dataclass(frozen=True)
class ModelRates:
    """USD per MTok for each billable token type of one model."""

    input: float
    output: float

    @property
    def cache_read(self) -> float:
        return self.input * _CACHE_READ_MULT

    @property
    def cache_write_5m(self) -> float:
        return self.input * _CACHE_WRITE_5M_MULT

    @property
    def cache_write_1h(self) -> float:
        return self.input * _CACHE_WRITE_1H_MULT


RATE_CARD: dict[str, ModelRates] = {
    "claude-fable-5": ModelRates(input=10.00, output=50.00),
    "claude-opus-4-8": ModelRates(input=5.00, output=25.00),
    "claude-opus-4-7": ModelRates(input=5.00, output=25.00),
    "claude-opus-4-6": ModelRates(input=5.00, output=25.00),
    "claude-opus-4-5": ModelRates(input=5.00, output=25.00),
    "claude-opus-4-1": ModelRates(input=15.00, output=75.00),
    # Sonnet 5 standard list price. An intro price ($2/$10) applies
    # through 2026-08-31; the card values usage at list for consistency.
    "claude-sonnet-5": ModelRates(input=3.00, output=15.00),
    "claude-sonnet-4-6": ModelRates(input=3.00, output=15.00),
    "claude-sonnet-4-5": ModelRates(input=3.00, output=15.00),
    "claude-haiku-4-5": ModelRates(input=1.00, output=5.00),
}

_DATE_SUFFIX = re.compile(r"-20\d{6}$")


def rates_for(model: str) -> ModelRates | None:
    """Resolve a model id to its rates; None if the model is unpriced.

    Dated ids (``claude-haiku-4-5-20251001``) resolve to their alias.
    Unknown models return None so callers can surface unpriced usage
    instead of silently valuing it at zero.
    """
    return RATE_CARD.get(_DATE_SUFFIX.sub("", model))


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
