"""Savings recommendations computed from real usage.

Two kinds, kept deliberately separate:

- **achieved** — savings already realized, measured from the data
  (e.g. what prompt caching saved versus re-sending those tokens as
  fresh input at list rates).
- **potential** — what-if analysis at list rates (e.g. the same token
  mix priced on a cheaper sibling model). These are ceilings that
  assume the cheaper option is good enough; that judgment stays human.

Everything is derived from the rate card; nothing here invents
assumptions about hardware, electricity or batchability — analyses
that need user-supplied inputs belong to a later phase.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from tokencur.pricing import (
    ModelRates,
    rates_for,
    rates_for_record,
    record_day,
    with_modifiers,
)
from tokencur.records import UsageRecord

# Curated "one tier down" pairs. Only emitted when the sibling is
# actually cheaper on both input and output at list rates.
DOWNSIZE = {
    "claude-fable-5-1": "claude-opus-5-5",
    "claude-opus-5-5": "claude-sonnet-5-5",
    "claude-sonnet-5-5": "claude-haiku-4-5",
    "claude-opus-5": "claude-sonnet-5",
    "claude-fable-5": "claude-opus-4-8",
    "claude-opus-4-8": "claude-sonnet-5",
    "claude-sonnet-5": "claude-haiku-4-5",
    "gpt-5.4": "gpt-5.3-codex",
    "gpt-5.3-codex": "gpt-5.2-codex",
}


@dataclass(frozen=True)
class Recommendation:
    kind: str  # "achieved" | "potential"
    title: str
    detail: str
    savings_usd: float
    baseline_usd: float

    @property
    def savings_pct(self) -> float:
        return 100 * self.savings_usd / self.baseline_usd if self.baseline_usd else 0.0


def _cost(record: UsageRecord, rates: ModelRates) -> float:
    return (
        record.input_tokens * rates.input
        + record.output_tokens * rates.output
        + record.cache_read_tokens * rates.cache_read
        + record.cache_write_5m_tokens * rates.cache_write_5m
        + record.cache_write_1h_tokens * rates.cache_write_1h
    ) / 1_000_000


def caching_roi(records: Iterable[UsageRecord]) -> list[Recommendation]:
    """Measured savings from prompt caching, per model.

    Counterfactual: without caching, every cache-read and cache-write
    token would have been sent as fresh input at the input rate. Each
    record is priced at the rates in force on its own day.
    """
    sums: dict[str, list[float]] = {}  # model -> [without caching, actual]
    for r in records:
        cached = r.cache_read_tokens + r.cache_write_5m_tokens + r.cache_write_1h_tokens
        if not cached:
            continue
        rates = rates_for_record(r)
        if rates is None:
            continue
        acc = sums.setdefault(r.model, [0.0, 0.0])
        acc[0] += cached * rates.input / 1_000_000
        acc[1] += (
            r.cache_read_tokens * rates.cache_read
            + r.cache_write_5m_tokens * rates.cache_write_5m
            + r.cache_write_1h_tokens * rates.cache_write_1h
        ) / 1_000_000
    out = []
    for model, (no_cache, actual) in sums.items():
        saved = no_cache - actual
        if saved >= 0:
            detail = "Keep prompts cache-stable; this saving repeats every session."
        else:
            detail = "Cache writes are not being amortized by reads — investigate."
        out.append(
            Recommendation(
                kind="achieved",
                title=f"Prompt caching on {model}",
                detail=detail,
                savings_usd=saved,
                baseline_usd=no_cache,
            )
        )
    return out


def model_rightsizing(records: Iterable[UsageRecord]) -> list[Recommendation]:
    """What the same calls would cost one model tier down.

    Both sides are priced call by call at the rates in force on each
    call's day; whether the pair is a step down at all is judged on
    today's list rates.
    """
    sums: dict[str, list[float]] = {}  # model -> [current, downsized]
    for r in records:
        sibling = DOWNSIZE.get(r.model.split("/")[-1])
        if sibling is None:
            continue
        rates, sibling_rates = rates_for_record(r), rates_for(sibling, record_day(r))
        if rates is None or sibling_rates is None:
            continue
        # The cheaper model would run with the same region and batch
        # options, at standard speed: fast mode is an Opus option.
        sibling_rates = with_modifiers(sibling_rates, _without_fast(r.price_modifiers))
        acc = sums.setdefault(r.model, [0.0, 0.0])
        acc[0] += _cost(r, rates)
        acc[1] += _cost(r, sibling_rates)
    out = []
    for model, (current, downsized) in sums.items():
        sibling = DOWNSIZE[model.split("/")[-1]]
        now, now_sibling = rates_for(model), rates_for(sibling)
        if now_sibling.input >= now.input or now_sibling.output >= now.output:
            continue  # curated pair is not actually cheaper — skip
        if current - downsized < 1.0:
            continue  # not worth a recommendation
        out.append(
            Recommendation(
                kind="potential",
                title=f"Right-size {model} → {sibling}",
                detail=(
                    "Ceiling if the cheaper tier suffices for this workload; "
                    "quality trade-off is a human call."
                ),
                savings_usd=current - downsized,
                baseline_usd=current,
            )
        )
    return out


def _without_fast(modifiers: str) -> str:
    return "+".join(m for m in modifiers.split("+") if m and m != "fast")


def recommendations(records: Iterable[UsageRecord]) -> list[Recommendation]:
    """Every recommendation, largest first."""
    # Both analyses walk the records: materialize once, or a generator
    # would be spent by the first and right-sizing would come back empty.
    records = list(records)
    recs = caching_roi(records) + model_rightsizing(records)
    return sorted(recs, key=lambda r: -r.savings_usd)


def render(recs: list[Recommendation]) -> str:
    lines = []
    for kind, header in (
        ("achieved", "AVOIDED — measured, counterfactual at list prices"),
        ("potential", "HEADROOM — what-if at list prices"),
    ):
        subset = [r for r in recs if r.kind == kind]
        if not subset:
            continue
        lines += ["", header]
        width = max(48, *(len(r.title) for r in subset))
        for r in subset:
            lines.append(
                f"  {r.title:<{width}} ${r.savings_usd:>10,.2f}  ({r.savings_pct:.0f}% of ${r.baseline_usd:,.2f})"
            )
            lines.append(f"    {r.detail}")
    total = sum(r.savings_usd for r in recs if r.kind == "potential")
    lines += [
        "",
        f"Total what-if headroom: ${total:,.2f}",
        "Under a flat subscription these figures are value at list prices,",
        "not dollars saved or saveable — see README 'Money concepts'.",
    ]
    return "\n".join(lines).lstrip("\n")
