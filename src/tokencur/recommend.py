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

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from tokencur.ingest.claude_code import UsageRecord
from tokencur.pricing import ModelRates, rates_for

# Curated "one tier down" pairs. Only emitted when the sibling is
# actually cheaper on both input and output at list rates.
DOWNSIZE = {
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


def _totals_by_model(records: Iterable[UsageRecord]) -> dict[str, dict[str, int]]:
    totals: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in records:
        agg = totals[r.model]
        agg["input"] += r.input_tokens
        agg["output"] += r.output_tokens
        agg["cache_read"] += r.cache_read_tokens
        agg["cache_write_5m"] += r.cache_write_5m_tokens
        agg["cache_write_1h"] += r.cache_write_1h_tokens
    return totals


def _cost(tokens: dict[str, int], rates: ModelRates) -> float:
    return (
        tokens["input"] * rates.input
        + tokens["output"] * rates.output
        + tokens["cache_read"] * rates.cache_read
        + tokens["cache_write_5m"] * rates.cache_write_5m
        + tokens["cache_write_1h"] * rates.cache_write_1h
    ) / 1_000_000


def caching_roi(records: Iterable[UsageRecord]) -> list[Recommendation]:
    """Measured savings from prompt caching, per model.

    Counterfactual: without caching, every cache-read and cache-write
    token would have been sent as fresh input at the input rate.
    """
    out = []
    for model, t in _totals_by_model(records).items():
        rates = rates_for(model)
        if rates is None or not (t["cache_read"] or t["cache_write_5m"] or t["cache_write_1h"]):
            continue
        cached_tokens = t["cache_read"] + t["cache_write_5m"] + t["cache_write_1h"]
        no_cache = cached_tokens * rates.input / 1_000_000
        actual = (
            t["cache_read"] * rates.cache_read
            + t["cache_write_5m"] * rates.cache_write_5m
            + t["cache_write_1h"] * rates.cache_write_1h
        ) / 1_000_000
        saved = no_cache - actual
        if saved >= 0:
            detail = "Keep prompts cache-stable; this saving repeats every session."
        else:
            detail = "Cache writes are not being amortized by reads — investigate."
        out.append(Recommendation(
            kind="achieved",
            title=f"Prompt caching on {model}",
            detail=detail,
            savings_usd=saved,
            baseline_usd=no_cache,
        ))
    return out


def model_rightsizing(records: Iterable[UsageRecord]) -> list[Recommendation]:
    """What the same token mix would cost one model tier down."""
    out = []
    for model, t in _totals_by_model(records).items():
        sibling = DOWNSIZE.get(model.split("/")[-1])
        rates, sibling_rates = rates_for(model), rates_for(sibling) if sibling else None
        if rates is None or sibling_rates is None:
            continue
        if sibling_rates.input >= rates.input or sibling_rates.output >= rates.output:
            continue  # curated pair is not actually cheaper — skip
        current, downsized = _cost(t, rates), _cost(t, sibling_rates)
        if current - downsized < 1.0:
            continue  # not worth a recommendation
        out.append(Recommendation(
            kind="potential",
            title=f"Right-size {model} → {sibling}",
            detail=(
                "Ceiling if the cheaper tier suffices for this workload; "
                "quality trade-off is a human call."
            ),
            savings_usd=current - downsized,
            baseline_usd=current,
        ))
    return out


def recommendations(records: Iterable[UsageRecord]) -> list[Recommendation]:
    recs = caching_roi(records) + model_rightsizing(records)
    return sorted(recs, key=lambda r: -r.savings_usd)


def render(recs: list[Recommendation]) -> str:
    lines = []
    for kind, header in (("achieved", "AVOIDED — measured, counterfactual at list prices"),
                         ("potential", "HEADROOM — what-if at list prices")):
        subset = [r for r in recs if r.kind == kind]
        if not subset:
            continue
        lines += ["", header]
        for r in subset:
            lines.append(
                f"  {r.title:<48} ${r.savings_usd:>10,.2f}  ({r.savings_pct:.0f}% of ${r.baseline_usd:,.2f})"
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
