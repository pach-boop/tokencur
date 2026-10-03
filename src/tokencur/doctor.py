"""``tokencur doctor``: is everything tokencur reads and keeps still sound?

Agents change their log formats without notice, and a ledger file can be
damaged. doctor checks, read-only (it never migrates or writes the
ledger):

- each log source: files, usage lines, records, malformed lines and the
  agent versions the logs name, flagging what looks like a format change;
- the ledger: schema, records per source, retired rows, SQLite integrity,
  how many records name the directory they ran in (cost per commit);
- pricing: the curated card's date, the snapshot's fetch date and its
  SHA-256 (compare it with the attested release asset), unpriced models;
- reconciliation: tokencur's value of Claude Code sessions against the
  cost Claude Code itself counted for them (see ``tokencur.reconcile``).

The result is a list of problems; the CLI exits 1 when there is any.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

from tokencur import ledger
from tokencur.ingest import claude_code
from tokencur.ingest.stats import ScanStats
from tokencur.pricing import provenance, rates_for
from tokencur.reconcile import Reconciliation, reconcile
from tokencur.records import UsageRecord
from tokencur.sources import DEFAULT_SOURCES, Source
from tokencur.terminal import home_relative

#: Share of unreadable usage lines above which a source is flagged.
MALFORMED_ALERT = 0.01

#: Bounds on the share of Claude Code's own counted cost that tokencur's
#: value accounts for. Below the floor, calls are going missing; above
#: the ceiling, calls are being counted twice (as Codex's once were).
RECONCILE_FLOOR, RECONCILE_CEILING = 0.85, 1.05


@dataclass
class SourceCheck:
    name: str
    root: Path
    present: bool
    stats: ScanStats
    records: int = 0


@dataclass
class LedgerCheck:
    path: Path
    present: bool
    schema: int = 0
    records: dict[str, int] = field(default_factory=dict)
    retired: int = 0
    with_cwd: int = 0  # records that name their working directory
    charges: int = 0
    billed_usd: float = 0.0
    integrity: str = ""
    unpriced: dict[str, int] = field(default_factory=dict)
    error: str = ""


@dataclass
class Diagnosis:
    sources: list[SourceCheck]
    ledger: LedgerCheck
    card_as_of: str
    snapshot_fetched: str
    snapshot_models: int
    snapshot_sha256: str
    problems: list[str]
    reconciliation: Reconciliation | None = None


def diagnose(
    sources: tuple[Source, ...] | None = None, ledger_path: Path | None = None
) -> Diagnosis:
    checks, reconciliation = [], None
    for root, ingest in sources or DEFAULT_SOURCES:
        check, records = _check_source(root, ingest)
        checks.append(check)
        if ingest is claude_code.iter_usage_records and check.present:
            reconciliation = reconcile(records, claude_code.session_counters(root))
    kept = _check_ledger(ledger_path or ledger.default_path())
    pricing = provenance()
    problems = [p for check in checks for p in _source_problems(check)]
    problems += _ledger_problems(kept)
    problems += _reconciliation_problems(reconciliation)
    return Diagnosis(
        sources=checks,
        ledger=kept,
        card_as_of=pricing.curated_card,
        snapshot_fetched=pricing.snapshot_fetched,
        snapshot_models=pricing.snapshot_models,
        snapshot_sha256=pricing.snapshot_sha256,
        problems=problems,
        reconciliation=reconciliation,
    )


def _check_source(root: Path, ingest) -> tuple[SourceCheck, list[UsageRecord]]:
    name = ingest.__module__.rsplit(".", 1)[-1].replace("_", "-")
    stats = ScanStats()
    if not root.exists():
        return SourceCheck(name, root, present=False, stats=stats), []
    records = list(ingest(root, stats=stats))
    check = SourceCheck(name, root, present=True, stats=stats, records=len(records))
    return check, records


def _reconciliation_problems(r: Reconciliation | None) -> list[str]:
    if r is None or r.coverage is None:
        return []
    if r.coverage < RECONCILE_FLOOR:
        return [
            f"claude-code: tokencur accounts for {r.coverage:.0%} of the cost "
            f"Claude Code counted in {r.sessions} sessions; calls are going "
            "missing, and the log format may have changed"
        ]
    if r.coverage > RECONCILE_CEILING:
        return [
            f"claude-code: tokencur values {r.coverage:.0%} of the cost Claude "
            f"Code counted in {r.sessions} sessions; calls may be counted twice"
        ]
    return []


def _source_problems(check: SourceCheck) -> list[str]:
    s = check.stats
    change = "the log format may have changed"
    if not check.present or not s.files:
        return []
    if not s.usage_lines:
        return [f"{check.name}: {s.files:,} log files hold no usage lines; {change}"]
    if not check.records:
        return [f"{check.name}: {s.usage_lines:,} usage lines, no records; {change}"]
    if s.malformed / s.usage_lines > MALFORMED_ALERT:
        return [
            f"{check.name}: {s.malformed:,} of {s.usage_lines:,} usage lines "
            f"could not be read ({s.malformed / s.usage_lines:.1%}); {change}"
        ]
    return []


def _check_ledger(path: Path) -> LedgerCheck:
    check = LedgerCheck(path=path, present=path.exists())
    if not check.present:
        return check
    try:
        uri = path.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as conn:
            check.schema = conn.execute("PRAGMA user_version").fetchone()[0]
            check.integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            tables = {n for (n,) in conn.execute("SELECT name FROM sqlite_master")}
            if "usage" in tables:
                check.records = dict(
                    conn.execute("SELECT source, COUNT(*) FROM usage GROUP BY source")
                )
                columns = {row[1] for row in conn.execute("PRAGMA table_info(usage)")}
                if "cwd" in columns:
                    check.with_cwd = conn.execute(
                        "SELECT COUNT(*) FROM usage WHERE cwd != ''"
                    ).fetchone()[0]
                for model, n in conn.execute(
                    "SELECT model, COUNT(*) FROM usage GROUP BY model"
                ):
                    if rates_for(model) is None:
                        check.unpriced[model] = n
            if "charges" in tables:
                check.charges, billed = conn.execute(
                    "SELECT COUNT(*), COALESCE(SUM(amount_usd), 0) FROM charges"
                ).fetchone()
                check.billed_usd = float(billed)
            if "superseded" in tables:
                check.retired = conn.execute(
                    "SELECT COUNT(*) FROM superseded"
                ).fetchone()[0]
    except sqlite3.DatabaseError as exc:
        check.error = str(exc)
    return check


def _ledger_problems(check: LedgerCheck) -> list[str]:
    if check.error:
        return [f"ledger: not a readable SQLite ledger ({check.error})"]
    if check.present and check.integrity != "ok":
        return [f"ledger: SQLite integrity check failed ({check.integrity})"]
    if check.schema > ledger.SCHEMA_VERSION:
        return [
            f"ledger: schema {check.schema} was written by a newer tokencur; "
            f"this one reads up to {ledger.SCHEMA_VERSION}"
        ]
    return []


def _version_key(version: str) -> tuple:
    return tuple(int(p) if p.isdigit() else -1 for p in version.split("."))


def _render_reconciliation(r: Reconciliation | None) -> list[str]:
    if r is None:
        return []
    lines = ["", "reconciliation with Claude Code's own counters"]
    if not r.sessions:
        return [*lines, "  no session has closed with Claude Code's counters yet"]
    lines.append(
        f"  {r.sessions:,} sessions closed with counters: tokencur values their "
        f"calls at ${r.tokencur_usd:,.2f}, Claude Code counted ${r.agent_usd:,.2f}"
    )
    lines.append(
        f"  tokencur sees {r.coverage:.1%}; the rest is in calls the "
        "transcripts never record:"
    )
    lines.append(
        f"    main models: ${r.counted_usd - r.tokencur_usd:,.2f} beyond the "
        "calls their transcripts hold"
    )
    for model, cost in r.unseen_usd.items():
        lines.append(f"    {model}: ${cost:,.2f}, no call in any transcript")
    return lines


def render(d: Diagnosis) -> str:
    lines = ["tokencur doctor", "", "log sources"]
    width = max(len(c.name) for c in d.sources) if d.sources else 0
    for c in d.sources:
        s = c.stats
        head = f"  {c.name:<{width}}  {home_relative(c.root)}"
        if not c.present:
            lines.append(f"{head}  (not found)")
            continue
        versions = ""
        if s.versions:
            latest = max(s.versions, key=_version_key)
            others = len(s.versions) - 1
            versions = f" · agent {latest}" + (f" (+{others} older)" if others else "")
        lines.append(
            f"{head}  {s.files:,} files · {s.usage_lines:,} usage lines · "
            f"{c.records:,} records · {s.malformed:,} malformed{versions}"
        )
    lines += _render_reconciliation(d.reconciliation)
    k = d.ledger
    lines += ["", "ledger", f"  {home_relative(k.path)}"]
    if not k.present:
        lines.append("  not created yet: the first report or export creates it")
    elif not k.error:
        upgrade = (
            f" (this tokencur upgrades it to {ledger.SCHEMA_VERSION} on the next run)"
            if k.schema < ledger.SCHEMA_VERSION
            else ""
        )
        per_source = ", ".join(f"{src} {n:,}" for src, n in sorted(k.records.items()))
        lines.append(
            f"  schema {k.schema}{upgrade} · {sum(k.records.values()):,} records"
            f" ({per_source or 'none'}) · {k.retired:,} retired · integrity {k.integrity}"
        )
        total = sum(k.records.values())
        if total:
            lines.append(
                f"  {k.with_cwd:,} of {total:,} records name their working "
                "directory (`tokencur outcomes` attributes those)"
            )
        if k.charges:
            lines.append(f"  {k.charges:,} billed charges, ${k.billed_usd:,.2f}")
        unpriced = ", ".join(f"{m} x{n}" for m, n in sorted(k.unpriced.items()))
        lines.append(f"  unpriced models: {unpriced or 'none'}")
    lines += [
        "",
        "pricing",
        f"  curated card as of {d.card_as_of} · snapshot fetched "
        f"{d.snapshot_fetched}, {d.snapshot_models:,} models",
        f"  snapshot sha256 {d.snapshot_sha256}",
        "",
    ]
    if d.problems:
        lines.append(f"{len(d.problems)} problem{'s' * (len(d.problems) != 1)}:")
        lines += [f"  ! {p}" for p in d.problems]
    else:
        lines.append("all checks passed")
    return "\n".join(lines)
