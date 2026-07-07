"""Export local AI usage as a FOCUS-conformant CSV.

Usage:
    python -m tokencur.export OUTPUT.csv [ROOT]

With no ROOT, every known local source is scanned (same as the report).
Unpriced usage is skipped and reported on stderr — never exported as $0.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

from tokencur.focus import FOCUS_COLUMNS, to_focus_rows, unpriced_models
from tokencur.ingest import claude_code
from tokencur.ingest.claude_code import UsageRecord
from tokencur.report import DEFAULT_SOURCES


def export_csv(records: list[UsageRecord], output: Path) -> int:
    """Write FOCUS rows to ``output``; return the number of rows."""
    rows = 0
    with output.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FOCUS_COLUMNS)
        writer.writeheader()
        for row in to_focus_rows(records):
            writer.writerow(row)
            rows += 1
    return rows


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    output = Path(argv[1])

    records: list[UsageRecord] = []
    if len(argv) > 2:
        records = list(claude_code.iter_usage_records(Path(argv[2])))
    else:
        for root, iter_records in DEFAULT_SOURCES:
            if root.exists():
                records.extend(iter_records(root))

    rows = export_csv(records, output)
    print(f"wrote {rows} FOCUS charge rows to {output}", file=sys.stderr)
    skipped = unpriced_models(records)
    if skipped:
        pairs = ", ".join(f"{m} x{n}" for m, n in sorted(skipped.items()))
        print(f"skipped unpriced usage: {pairs}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
