"""Print savings recommendations over the full local usage history.

Usage:
    python -m tokencur recommend
"""

from __future__ import annotations

import sys

from tokencur.recommend import recommendations, render
from tokencur.sources import load_records


def main(argv: list[str]) -> int:
    records = load_records()
    if not records:
        print("error: no usage in known log locations or the ledger", file=sys.stderr)
        return 1
    print(render(recommendations(records)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
