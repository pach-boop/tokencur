"""Route ``python -m tokencur <command>``: report | export | recommend."""

from __future__ import annotations

import sys


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else "report"
    rest = [argv[0], *argv[2:]]
    if command == "report":
        from tokencur.report import main as run
    elif command == "export":
        from tokencur.export import main as run
    elif command == "recommend":
        from tokencur.recommend_cli import main as run
    else:
        print(__doc__, file=sys.stderr)
        return 2
    return run(rest)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
