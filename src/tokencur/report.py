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
from pathlib import Path

from tokencur.ingest import claude_code
from tokencur.ingest.claude_code import UsageRecord
from tokencur.pricing import AS_OF, record_cost_usd
from tokencur.sources import load_records


def summarize(records: list[UsageRecord]) -> str:
    by_model: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    by_day: dict[str, float] = defaultdict(float)
    by_source: dict[str, float] = defaultdict(float)
    unpriced: dict[str, int] = defaultdict(int)
    total_cost = 0.0

    for r in records:
        cost = record_cost_usd(r)
        if cost is None:
            unpriced[r.model] += 1
            continue
        by_source[r.source] += cost
        agg = by_model[r.model]
        agg["messages"] += 1
        agg["input"] += r.input_tokens
        agg["output"] += r.output_tokens
        agg["cache_read"] += r.cache_read_tokens
        agg["cache_write"] += r.cache_write_5m_tokens + r.cache_write_1h_tokens
        agg["cost"] += cost
        by_day[r.date] += cost
        total_cost += cost

    lines = [
        f"tokencur report — {len(records)} assistant messages, "
        f"rates as of {AS_OF} (API-equivalent list cost)",
        "",
        f"{'model':<22}{'msgs':>6}{'input':>12}{'output':>12}"
        f"{'cache_read':>13}{'cache_write':>13}{'cost USD':>11}",
    ]
    for model, agg in sorted(by_model.items(), key=lambda kv: -kv[1]["cost"]):
        lines.append(
            f"{model:<22}{agg['messages']:>6.0f}{agg['input']:>12,.0f}"
            f"{agg['output']:>12,.0f}{agg['cache_read']:>13,.0f}"
            f"{agg['cache_write']:>13,.0f}{agg['cost']:>11,.2f}"
        )
    if len(by_source) > 1:
        lines += ["", "by source:"]
        for source, cost in sorted(by_source.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {source:<14}${cost:,.2f}")
    lines += ["", "daily cost (top 10 days):"]
    for day, cost in sorted(by_day.items(), key=lambda kv: -kv[1])[:10]:
        lines.append(f"  {day}  ${cost:,.2f}")
    lines += ["", f"API-EQUIVALENT TOTAL (showback): ${total_cost:,.2f}"]
    if unpriced:
        pairs = ", ".join(f"{m} x{n}" for m, n in sorted(unpriced.items()))
        lines.append(f"unpriced usage (model not in rate card): {pairs}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        root = Path(argv[1])
        if not root.exists():
            print(f"error: {root} does not exist", file=sys.stderr)
            return 1
        records = list(claude_code.iter_usage_records(root))
    else:
        records = load_records()
        if not records:
            print("error: no usage in known log locations or the ledger",
                  file=sys.stderr)
            return 1
    print(summarize(records))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
