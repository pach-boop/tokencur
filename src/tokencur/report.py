"""Quick usage & cost summary over local AI coding-agent logs.

Usage:
    python -m tokencur.report          # all known sources, full history
    python -m tokencur.report ROOT     # ad hoc: ROOT as Claude Code logs

With no arguments, every known source that exists on this machine is
scanned — Claude Code (``~/.claude/projects``), Codex CLI
(``~/.codex/sessions``) and Kimi Code (``~/.kimi-code/sessions``) — and
kept in the ledger; the report covers the ledger's full history (see
``tokencur.sources``). An explicit ROOT is reported as-is and never
stored. This is the "does the pipeline see my real spend?" check; the
FOCUS normalizer and analysis layers build on the same records.
"""

from __future__ import annotations

import sys
from collections import defaultdict

from tokencur.focus import provider_for
from tokencur.pricing import AS_OF, record_cost_usd
from tokencur.records import UsageRecord, parse_timestamp


def summarize(
    records: list[UsageRecord],
    period: str | None = None,
    discounts: dict[str, float] | None = None,
    fx: tuple[str, float] | None = None,
) -> str:
    """The terminal report. ``discounts`` (provider -> fraction off list)
    add a contracted total; ``fx`` (currency, units per USD, given by the
    user) adds the totals converted at that rate."""
    by_model: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    by_day: dict[str, float] = defaultdict(float)
    by_source: dict[str, float] = defaultdict(float)
    unpriced: dict[str, int] = defaultdict(int)
    total_cost = 0.0
    contracted = 0.0

    for r in records:
        cost = record_cost_usd(r)
        if cost is None:
            unpriced[r.model] += 1
            continue
        by_source[r.source] += cost
        agg = by_model[r.model]
        agg["calls"] += 1
        agg["input"] += r.input_tokens
        agg["output"] += r.output_tokens
        agg["cache_read"] += r.cache_read_tokens
        agg["cache_write"] += r.cache_write_5m_tokens + r.cache_write_1h_tokens
        agg["cost"] += cost
        by_day[r.date if parse_timestamp(r.timestamp) else "undated"] += cost
        total_cost += cost
        contracted += cost * (1.0 - (discounts or {}).get(provider_for(r.source), 0.0))

    header = (
        "model",
        "calls",
        "input",
        "output",
        "cache_read",
        "cache_write",
        "cost USD",
    )
    rows = [
        (
            model,
            f"{agg['calls']:,.0f}",
            f"{agg['input']:,.0f}",
            f"{agg['output']:,.0f}",
            f"{agg['cache_read']:,.0f}",
            f"{agg['cache_write']:,.0f}",
            f"{agg['cost']:,.2f}",
        )
        for model, agg in sorted(by_model.items(), key=lambda kv: -kv[1]["cost"])
    ]
    lines = [
        f"tokencur report — {len(records)} model call{'s' * (len(records) != 1)}"
        f"{f' {period}' if period else ''}, "
        f"list rates in force on each call's day, curated card {AS_OF} "
        "(API-equivalent list cost)",
        "",
        *_table(header, rows),
    ]
    if len(by_source) > 1:
        lines += ["", "by source:"]
        for source, cost in sorted(by_source.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {source:<14}${cost:,.2f}")
    lines += ["", "daily cost (top 10 days):"]
    for day, cost in sorted(by_day.items(), key=lambda kv: -kv[1])[:10]:
        lines.append(f"  {day:<10}  ${cost:,.2f}")
    lines += ["", f"API-EQUIVALENT TOTAL (showback): ${total_cost:,.2f}"]
    if fx:
        lines.append(_converted(total_cost, fx))
    if discounts:
        lines.append(
            f"CONTRACTED TOTAL (after negotiated discounts): ${contracted:,.2f}"
        )
        if fx:
            lines.append(_converted(contracted, fx))
    if unpriced:
        pairs = ", ".join(f"{m} x{n}" for m, n in sorted(unpriced.items()))
        lines.append(f"unpriced usage (model not in rate card): {pairs}")
    return "\n".join(lines)


def _converted(usd: float, fx: tuple[str, float]) -> str:
    currency, rate = fx
    return (
        f"  = {currency} {usd * rate:,.2f} at {rate:g} {currency} per USD (rate given)"
    )


def _table(header: tuple[str, ...], rows: list[tuple[str, ...]]) -> list[str]:
    """Columns as wide as their widest cell: model left, numbers right.

    Fixed widths broke once real totals passed a billion tokens or a
    model id ran long, and adjacent columns ran together.
    """
    widths = [
        max(len(cell) for cell in column) for column in zip(header, *rows, strict=True)
    ]

    def line(cells: tuple[str, ...]) -> str:
        first = cells[0].ljust(widths[0])
        rest = (
            cell.rjust(width) for cell, width in zip(cells[1:], widths[1:], strict=True)
        )
        return "  ".join((first, *rest)).rstrip()

    return [line(header), *(line(row) for row in rows)]


def main(argv: list[str]) -> int:
    """``python -m tokencur.report [ROOT]`` — kept; see ``tokencur.cli``."""
    from tokencur.cli import main as cli

    return cli(["report", *argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
