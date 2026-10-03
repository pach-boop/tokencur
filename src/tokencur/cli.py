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
import sqlite3
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from tokencur import (
    __version__,
    doctor,
    ledger,
    observatory,
    outcomes,
    prices,
    sessions,
    sources,
)
from tokencur.export import export_csv
from tokencur.focus import undated_count, unpriced_models
from tokencur.ingest import claude_code, runpod
from tokencur.pricing import ConfigError, load_discounts, provenance
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
    contract = argparse.ArgumentParser(add_help=False)
    contract.add_argument(
        "--discounts",
        type=Path,
        metavar="FILE",
        help='negotiated discounts: {"discounts": {"Anthropic": 0.15}} (15%% off list)',
    )

    report = commands.add_parser(
        "report", parents=[period, contract], help="cost summary in the terminal"
    )
    report.add_argument("root", nargs="?", type=Path, help=root_help)
    report.add_argument(
        "--currency", type=_currency, metavar="CODE", help="also show totals in CODE"
    )
    report.add_argument(
        "--fx-rate",
        type=_positive,
        metavar="RATE",
        help="units of --currency per USD; you give the rate, nothing is fetched",
    )
    report.set_defaults(handler=_report)

    export = commands.add_parser(
        "export", parents=[period, contract], help="write a FOCUS 1.2 CSV"
    )
    export.add_argument("output", type=Path, help="CSV file to write")
    export.add_argument("root", nargs="?", type=Path, help=root_help)
    export.set_defaults(handler=_export)

    recommend = commands.add_parser(
        "recommend", parents=[period], help="avoided cost and what-if headroom"
    )
    recommend.set_defaults(handler=_recommend)

    unit = commands.add_parser(
        "outcomes",
        parents=[period],
        help="usage value per commit, repository by repository",
    )
    unit.add_argument(
        "repos",
        nargs="*",
        type=Path,
        metavar="REPO",
        help="git repositories to measure (default: every one the agents worked in)",
    )
    unit.add_argument(
        "--all-authors",
        action="store_true",
        help="count every author's commits, not only your git user.email's",
    )
    unit.add_argument(
        "--sessions",
        action="store_true",
        help="one row per agent session: usage value per change that landed and stayed",
    )
    unit.add_argument(
        "--top",
        type=int,
        default=20,
        metavar="N",
        help="with --sessions, show the N sessions of most usage value (0: all)",
    )
    unit.set_defaults(handler=_outcomes)

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

    keep = commands.add_parser(
        "import",
        help="keep a provider's billing export (billed cost) in the ledger",
    )
    keep.add_argument("provider", choices=["runpod"])
    keep.add_argument("files", nargs="+", type=Path, help="exports to import")
    keep.set_defaults(handler=_import)

    check = commands.add_parser(
        "doctor",
        help="check log formats, the ledger and pricing (read-only)",
    )
    check.set_defaults(handler=_doctor)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        args = parser.parse_args(["report"])
    since, until = getattr(args, "since", None), getattr(args, "until", None)
    if since and until and since >= until:
        parser.error("--since must be an earlier day than --until")
    if (getattr(args, "currency", None) is None) != (
        getattr(args, "fx_rate", None) is None
    ):
        parser.error("--currency and --fx-rate go together")
    try:
        return args.handler(args)
    except (ledger.LedgerError, ConfigError, outcomes.OutcomesError) as exc:
        _fail(str(exc))
    except sqlite3.DatabaseError as exc:
        _fail(f"the ledger looks damaged ({exc}); run `tokencur doctor`")
    return 1


def _currency(text: str) -> str:
    if len(text) != 3 or not text.isalpha() or not text.isupper():
        raise argparse.ArgumentTypeError(f"not a 3-letter currency code: {text!r}")
    return text


def _positive(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {text!r}") from None
    if not value > 0:
        raise argparse.ArgumentTypeError(f"must be above 0: {text!r}")
    return value


def _charges(args: argparse.Namespace) -> list:
    """Billed charges in the ledger for the command's period. An explicit
    log root is an ad hoc look at those logs alone, so it gets none."""
    if getattr(args, "root", None) is not None:
        return []
    return in_period(
        ledger.read_charges(), args.since, args.until, when=lambda c: c.period_start
    )


def _discounts(args: argparse.Namespace) -> dict[str, float] | None:
    return load_discounts(args.discounts) if args.discounts else None


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
    discounts = _discounts(args)  # a bad file fails before any scan
    records = _records(args)
    if records is None:
        return 1
    if not records:
        _fail(f"no usage {_period(args)}")
        return 1
    fx = (args.currency, args.fx_rate) if args.currency else None
    print(
        summarize(
            records,
            period=_period(args),
            discounts=discounts,
            fx=fx,
            charges=_charges(args),
        )
    )
    return 0


def _export(args: argparse.Namespace) -> int:
    discounts = _discounts(args)  # a bad file fails before any scan
    records = _records(args)
    if records is None:
        return 1
    rows = export_csv(records, args.output, discounts, _charges(args))
    print(
        f"wrote {rows} FOCUS charge rows to {args.output} ({provenance()})",
        file=sys.stderr,
    )
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


def _outcomes(args: argparse.Namespace) -> int:
    records = _records(args)
    if records is None:
        return 1
    if args.sessions:
        result = sessions.session_outcomes(
            records,
            _claude_code_counters(),
            repos=args.repos,
            since=args.since,
            until=args.until,
            all_authors=args.all_authors,
            period=_period(args),
        )
        print(sessions.render(result, top=args.top))
        return 0
    result = outcomes.outcomes(
        records,
        repos=args.repos,
        since=args.since,
        until=args.until,
        all_authors=args.all_authors,
        period=_period(args),
    )
    print(outcomes.render(result))
    return 0


def _claude_code_counters() -> claude_code.SessionCounters:
    """Claude Code's own counters, from its logs where tokencur reads them."""
    for root, ingest in sources.DEFAULT_SOURCES:
        if ingest is claude_code.iter_usage_records and root.exists():
            return claude_code.session_counters(root)
    return claude_code.SessionCounters()


def _observatory(args: argparse.Namespace) -> int:
    records = load_records()
    if not records:
        _fail(_NOTHING)
        return 1
    snap = observatory.snapshot(
        records, observatory.load_subscriptions(), ledger.read_charges()
    )
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


def _doctor(args: argparse.Namespace) -> int:
    diagnosis = doctor.diagnose()
    print(doctor.render(diagnosis))
    return 1 if diagnosis.problems else 0


def _import(args: argparse.Namespace) -> int:
    charges = []
    for path in args.files:
        if not path.exists():
            _fail(f"{path} does not exist")
            return 1
        charges.extend(runpod.iter_charges(path))
    if not charges:
        _fail("no billed charges in those files")
        return 1
    added = ledger.record_charges(charges)
    total = sum(c.amount_usd for c in charges)
    providers = ", ".join(sorted({c.provider for c in charges}))
    print(
        f"{len(charges)} billed charges ({added} new), ${total:,.2f} from "
        f"{providers} — {ledger.default_path()}"
    )
    return 0
