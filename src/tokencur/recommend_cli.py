"""Print savings recommendations over local usage.

Usage:
    python -m tokencur recommend
"""

from __future__ import annotations

import sys

from tokencur.ingest.claude_code import UsageRecord
from tokencur.recommend import recommendations, render
from tokencur.report import DEFAULT_SOURCES


def main(argv: list[str]) -> int:
    records: list[UsageRecord] = []
    for root, iter_records in DEFAULT_SOURCES:
        if root.exists():
            records.extend(iter_records(root))
    if not records:
        print("error: no known usage-log locations found", file=sys.stderr)
        return 1
    print(render(recommendations(records)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
