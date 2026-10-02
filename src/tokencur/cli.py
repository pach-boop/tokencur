"""Command-line interface: ``tokencur <command>``.

One argparse parser for every command, installed as the ``tokencur``
console script and reachable as ``python -m tokencur``. The older
per-module entry points (``python -m tokencur.report`` and friends)
keep working: each delegates here.

Periods follow billing convention: ``--since`` is the first UTC day
included, ``--until`` the first day excluded, so ``--since 2026-09-01
--until 2026-10-01`` is exactly September.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from tokencur import __version__, observatory, prices
from tokencur.export import export_csv
from tokencur.focus import undated_count, unpriced_models
from tokencur.ingest import claude_code
from tokencur.recommend import recommendations, render
from tokencur.records import UsageRecord, in_period
from tokencur.report import summarize
from tokencur.sources import load_records

_NOTHING = "no usage in known log locations or the ledger"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tokencur",
        description="The CUR for your tokens: AI coding-agent usage as FOCUS cost data.",
        epilog="Figures are API-equivalent list prices (showback), not a bill: "
        "see the README's 'Money concepts'. With no command, runs `report`.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    commands = parser.add_subparsers(dest="command", metavar="<command>")

    period = argparse.ArgumentParser(add_help=False)
    period.add_argument(
        "--since", type=_day, metavar="YYYY-MM-DD", help="first UTC day included"
    )
    period.add_argument(
        "--until", type=_day, metavar="YYYY-MM-DD", help="first UTC day excluded"
    )
    root_help = "read these Claude Code logs instead; never stored in the ledger"

    report = commands.add_parser(
        "report", parents=[period], help="cost summary in the terminal"
    )
    report.add_argument("root", nargs="?", type=Path, help=root_help)
    report.set_defaults(handler=_report)

    export = commands.add_parser(
        "export", parents=[period], help="write a FOCUS 1.2 CSV"
    )
    export.add_argument("output", type=Path, help="CSV file to write")
    export.add_argument("root", nargs="?", type=Path, help=root_help)
    export.set_defaults(handler=_export)

    recommend = commands.add_parser(
        "recommend", parents=[period], help="avoided cost and what-if headroom"
    )
    recommend.set_defaults(handler=_recommend)

    obs = commands.add_parser(
        "observatory", help="render the static spend dashboard (aggregates only)"
    )
    obs.add_argument(
        "outdir",
        nargs="?",
        type=Path,
        default=observatory.DEFAULT_OUTPUT,
        help=f"default: {observatory.DEFAULT_OUTPUT}",
    )
    obs.set_defaults(handler=_observatory)

    card = commands.add_parser("prices", help="render the static price card")
    card.add_argument(
        "outdir",
        nargs="?",
        type=Path,
        default=prices.DEFAULT_OUTPUT,
        help=f"default: {prices.DEFAULT_OUTPUT}",
    )
    card.set_defaults(handler=_prices)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        args = parser.parse_args(["report"])
    since, until = getattr(args, "since", None), getattr(args, "until", None)
    if since and until and since >= until:
        parser.error("--since must be an earlier day than --until")
    return args.handler(args)


def _day(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a YYYY-MM-DD date: {text!r}") from None


def _records(args: argparse.Namespace) -> list[UsageRecord] | None:
    """The command's records, or None after printing why there are none."""
    root = getattr(args, "root", None)
    if root is not None:
        if not root.exists():
            return _fail(f"{root} does not exist")
        records = list(claude_code.iter_usage_records(root))
    else:
        records = load_records()
    if not records:
        return _fail(_NOTHING)
    return in_period(records, args.since, args.until)


def _period(args: argparse.Namespace) -> str | None:
    since, until = args.since, args.until
    if since and until:
        return f"{since} to {until} (UTC, end excluded)"
    if since:
        return f"since {since} (UTC)"
    if until:
        return f"before {until} (UTC)"
    return None


def _fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)


def _report(args: argparse.Namespace) -> int:
    records = _records(args)
    if records is None:
        return 1
    if not records:
        _fail(f"no usage {_period(args)}")
        return 1
    print(summarize(records, period=_period(args)))
    return 0


def _export(args: argparse.Namespace) -> int:
    records = _records(args)
    if records is None:
        return 1
    rows = export_csv(records, args.output)
    print(f"wrote {rows} FOCUS charge rows to {args.output}", file=sys.stderr)
    skipped = unpriced_models(records)
    if skipped:
        pairs = ", ".join(f"{m} x{n}" for m, n in sorted(skipped.items()))
        print(f"skipped unpriced usage: {pairs}", file=sys.stderr)
    undated = undated_count(records)
    if undated:
        print(
            f"skipped undated usage: {undated} records (no parseable timestamp)",
            file=sys.stderr,
        )
    return 0


def _recommend(args: argparse.Namespace) -> int:
    records = _records(args)
    if not records:
        if records is not None:
            _fail(f"no usage {_period(args)}")
        return 1
    print(render(recommendations(records)))
    return 0


def _observatory(args: argparse.Namespace) -> int:
    records = load_records()
    if not records:
        _fail(_NOTHING)
        return 1
    snap = observatory.snapshot(records, observatory.load_subscriptions())
    observatory.write_site(snap, args.outdir)
    print(f"observatory written to {args.outdir} ({len(records)} records aggregated)")
    return 0


def _prices(args: argparse.Namespace) -> int:
    changes = prices.price_changes()
    prices.write_site(args.outdir, changes)
    moved = sum(len(c.changed) for c in changes)
    gained = sum(len(c.added) for c in changes if not c.introduced)
    print(
        f"prices page written to {args.outdir} "
        f"({len(changes)} events: {moved} rate moves, {gained} models added)"
    )
    return 0
