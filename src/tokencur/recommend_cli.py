"""Print savings recommendations over the full local usage history.

Usage:
    python -m tokencur recommend
"""

from __future__ import annotations

import sys


def main(argv: list[str]) -> int:
    """``python -m tokencur.recommend_cli`` — kept; see ``tokencur.cli``."""
    from tokencur.cli import main as cli

    return cli(["recommend", *argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
