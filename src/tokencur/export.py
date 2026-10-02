"""Export local AI usage as a FOCUS-conformant CSV.

Usage:
    python -m tokencur.export OUTPUT.csv [ROOT]

With no ROOT, the full history is exported: every known local source is
scanned into the ledger, same as the report. An explicit ROOT is read as
Claude Code logs and never stored. Unpriced and undated usage is
skipped and reported on stderr — never exported as $0 or under an
invented date.
"""

from __future__ import annotations

import csv
import sys
from operator import itemgetter
from pathlib import Path

from tokencur.focus import (
    FOCUS_COLUMNS,
    to_focus_rows,
)
from tokencur.records import UsageRecord


def export_csv(
    records: list[UsageRecord],
    output: Path,
    discounts: dict[str, float] | None = None,
) -> int:
    """Write FOCUS rows to ``output``; return the number of rows."""
    # itemgetter pulls a row's values in column order in C; a row missing
    # a column raises KeyError. (csv.DictWriter checked each row in Python
    # and was most of an export's time.)
    values = itemgetter(*FOCUS_COLUMNS)
    rows = 0
    with output.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(FOCUS_COLUMNS)
        for row in to_focus_rows(records, discounts):
            writer.writerow(values(row))
            rows += 1
    return rows


def main(argv: list[str]) -> int:
    """``python -m tokencur.export OUTPUT [ROOT]`` — kept; see ``tokencur.cli``."""
    from tokencur.cli import main as cli

    return cli(["export", *argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
