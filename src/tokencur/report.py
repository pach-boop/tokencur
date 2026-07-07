"""Quick usage & cost summary over Claude Code logs.

Usage:
    python -m tokencur.report [ROOT]

ROOT defaults to ``~/.claude/projects``. This is the v0 "does the
pipeline see my real spend?" check; the FOCUS normalizer and the
DuckDB/Streamlit analysis layers build on the same records.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

from tokencur.ingest.claude_code import UsageRecord, iter_usage_records
from tokencur.pricing import AS_OF, record_cost_usd


def summarize(records: list[UsageRecord]) -> str:
    by_model: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    by_day: dict[str, float] = defaultdict(float)
    unpriced: dict[str, int] = defaultdict(int)
    total_cost = 0.0

    for r in records:
        cost = record_cost_usd(r)
        if cost is None:
            unpriced[r.model] += 1
            continue
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
    lines += ["", "daily cost (top 10 days):"]
    for day, cost in sorted(by_day.items(), key=lambda kv: -kv[1])[:10]:
        lines.append(f"  {day}  ${cost:,.2f}")
    lines += ["", f"TOTAL: ${total_cost:,.2f}"]
    if unpriced:
        pairs = ", ".join(f"{m} x{n}" for m, n in sorted(unpriced.items()))
        lines.append(f"unpriced usage (model not in rate card): {pairs}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path.home() / ".claude" / "projects"
    if not root.exists():
        print(f"error: {root} does not exist", file=sys.stderr)
        return 1
    print(summarize(list(iter_usage_records(root))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
