"""``tokencur outcomes``: AI usage value per commit, repository by repository.

The first unit-economics layer: what the agents' usage was worth for each
unit of output it went into, with the commit as the unit. Usage value is
showback (API-equivalent list value, as everywhere in tokencur), not
money paid, and a commit measures output, not quality (ADR 0010).

Attribution. A call belongs to the git repository that holds the
directory it ran in (``UsageRecord.cwd``): the nearest directory, going
up, with a ``.git`` entry. A call is never spread across repositories.
One whose directory was not logged, no longer exists or is in no
repository stays unattributed, and the result says how much.

Commits. ``git log`` in each repository, read-only and local: non-merge
commits reachable from the checked-out branch whose author date falls in
the window. By default only yours, by the repository's ``user.email``;
``all_authors`` counts everyone's.
A commit whose author or ``Co-authored-by`` trailer carries a coding
agent's signature counts as agent co-authored (see ``AGENT``). Agents
that sign nothing, as Codex CLI and Kimi Code do by default, leave that
count at zero: it counts signatures, not involvement.

Window. The period asked for, else each repository's own days with agent
usage, from its first attributed call's day through its last: commits
outside those days had no recorded usage, so they carry no known cost.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from tokencur.pricing import record_cost_usd
from tokencur.records import UsageRecord, parse_timestamp
from tokencur.terminal import home_relative, table

#: Signatures coding agents leave on a commit, as its author or in a
#: Co-authored-by trailer. Only unambiguous ones: a bare name would also
#: match people (a "Devin", a "Claude").
AGENT = re.compile(
    r"noreply@anthropic\.com"  # Claude Code's Co-Authored-By trailer
    r"|\b(?:claude|codex|copilot|cursor|devin|gemini)[\w.-]*\[bot\]"  # app bots
    r"|cursoragent@cursor\.com"  # Cursor's agent
    r"|\(aider\)",  # aider marks the names it commits under
    re.IGNORECASE,
)

# Why a call is not attributed to a repository.
NO_DIRECTORY = "no working directory logged"
GONE = "directory no longer exists"
NOT_A_REPOSITORY = "not in a git repository"
NOT_ASKED = "in other repositories"

# One commit per record: hash, author name, author email, author date
# (strict ISO 8601) and Co-authored-by values; --shortstat follows it.
_FORMAT = (
    "%x1e%H%x1f%an%x1f%ae%x1f%aI%x1f"
    "%(trailers:key=Co-authored-by,valueonly,separator=%x1d)"
)
_LINES = re.compile(r"(\d+) (?:insertion|deletion)")


class OutcomesError(RuntimeError):
    """A repository cannot be read: not a repository, or git is missing."""


@dataclass(frozen=True)
class Commits:
    total: int = 0
    by_agent: int = 0  # signed by an agent, as author or co-author (``AGENT``)
    lines: int = 0  # lines added plus lines deleted


@dataclass
class RepoOutcome:
    root: Path
    since: date | None  # None: no usage and no period, so no window
    until: date | None  # first day not included
    calls: int
    value_usd: float
    unpriced: int  # calls of models with no rate: not valued, never $0
    commits: Commits | None  # None when git log failed (see ``error``)
    everyone: bool = False  # every author's commits (no user.email set)
    error: str = ""

    @property
    def per_commit(self) -> float | None:
        if self.commits is None or not self.commits.total:
            return None
        return self.value_usd / self.commits.total


@dataclass
class Outcomes:
    repos: list[RepoOutcome]
    total_usd: float  # value of every priced call in the period
    unattributed: dict[str, tuple[int, float]] = field(default_factory=dict)
    all_authors: bool = False
    period: str | None = None

    @property
    def attributed_usd(self) -> float:
        return sum(r.value_usd for r in self.repos)


class _Repositories:
    """Finds the repository a directory belongs to, remembering each answer."""

    def __init__(self) -> None:
        self._answers: dict[str, Path | str] = {}
        self._found: dict[Path, Path | None] = {}

    def root(self, directory: str) -> Path | str:
        """The repository root holding ``directory``, or why there is none."""
        if directory not in self._answers:
            self._answers[directory] = self._answer(directory)
        return self._answers[directory]

    def _answer(self, directory: str) -> Path | str:
        if not directory:
            return NO_DIRECTORY
        path = Path(directory)
        if not path.is_dir():
            return GONE
        root = self._lookup(path)
        return root if root is not None else NOT_A_REPOSITORY

    def _lookup(self, path: Path) -> Path | None:
        walked = []
        root = None
        for candidate in (path, *path.parents):
            if candidate in self._found:
                root = self._found[candidate]
                break
            walked.append(candidate)
            if (candidate / ".git").exists():
                root = candidate.resolve()
                break
        for candidate in walked:
            self._found[candidate] = root
        return root


def outcomes(
    records: Iterable[UsageRecord],
    repos: Sequence[Path] = (),
    since: date | None = None,
    until: date | None = None,
    all_authors: bool = False,
    period: str | None = None,
) -> Outcomes:
    """Usage value per commit for ``repos``, or for every repository the
    agents worked in when none are named. ``records`` are the period's
    calls; ``since`` and ``until`` bound the commits (UTC days, ``until``
    excluded), and default to each repository's days with usage."""
    finder = _Repositories()
    asked: set[Path] = set()
    for repo in repos:
        path = Path(repo).expanduser().absolute()
        if not path.is_dir():
            raise OutcomesError(f"{repo}: no such directory")
        root = finder.root(str(path))
        if not isinstance(root, Path):
            raise OutcomesError(f"{repo}: {root}")
        asked.add(root)

    calls: dict[Path, list[tuple[UsageRecord, float | None]]] = {}
    unattributed: dict[str, tuple[int, float]] = {}
    total = 0.0
    for record in records:
        cost = record_cost_usd(record)
        total += cost or 0.0
        root = finder.root(record.cwd)
        if isinstance(root, Path) and asked and root not in asked:
            root = NOT_ASKED
        if isinstance(root, Path):
            calls.setdefault(root, []).append((record, cost))
        else:
            n, value = unattributed.get(root, (0, 0.0))
            unattributed[root] = (n + 1, value + (cost or 0.0))

    results = [
        _repo_outcome(root, calls.get(root, []), since, until, all_authors)
        for root in (sorted(asked) if asked else sorted(calls))
    ]
    results.sort(key=lambda r: -r.value_usd)
    return Outcomes(results, total, unattributed, all_authors, period)


def _repo_outcome(
    root: Path,
    calls: list[tuple[UsageRecord, float | None]],
    since: date | None,
    until: date | None,
    all_authors: bool,
) -> RepoOutcome:
    """One repository's row; ``calls`` pairs each call with its value
    (None when unpriced)."""
    days = sorted(
        moment.date()
        for moment in (parse_timestamp(r.timestamp) for r, _ in calls)
        if moment is not None
    )
    first = since or (days[0] if days else None)
    end = until or (days[-1] + timedelta(days=1) if days else None)
    outcome = RepoOutcome(
        root=root,
        since=first,
        until=end,
        calls=len(calls),
        value_usd=sum(cost for _, cost in calls if cost is not None),
        unpriced=sum(1 for _, cost in calls if cost is None),
        commits=None,
    )
    if first is None or end is None:
        outcome.commits = Commits()  # no usage and no period: no window
        return outcome
    try:
        author = (
            None
            if all_authors
            else _run_git(root, "config", "user.email").stdout.strip()
        )
        outcome.everyone = not author
        outcome.commits = commits(root, first, end, author or None)
    except OutcomesError as exc:
        outcome.error = str(exc)
    return outcome


def commits(root: Path, since: date, until: date, author: str | None) -> Commits:
    """Non-merge commits in ``root`` authored on a UTC day in
    ``[since, until)``, by ``author`` (an email) or, with None, by anyone."""
    # Exit 1 means no HEAD yet; any other failure surfaces from git log.
    if _run_git(root, "rev-parse", "--quiet", "--verify", "HEAD").returncode == 1:
        return Commits()  # a repository with no commits yet
    # git filters on the commit date, which normally comes after the
    # author date; the day of margin keeps the prefilter on the safe side.
    margin = since - timedelta(days=1)
    out = _git(
        root,
        "log",
        "--no-merges",
        f"--since={margin.isoformat()} 00:00:00 +0000",
        f"--format={_FORMAT}",
        "--shortstat",
    )
    total = by_agent = lines = 0
    for chunk in out.split("\x1e")[1:]:
        head, _, stat = chunk.partition("\n")
        try:
            _sha, name, email, when, trailers = head.split("\x1f")
            day = datetime.fromisoformat(when).astimezone(UTC).date()
        except ValueError:
            raise OutcomesError(
                "git log printed an unexpected format; git 2.24 or newer is needed"
            ) from None
        if not since <= day < until:
            continue
        if author is not None and email.lower() != author.lower():
            continue
        total += 1
        signed = [f"{name} <{email}>", *trailers.split("\x1d")]
        by_agent += any(AGENT.search(who) for who in signed)
        lines += sum(int(n) for n in _LINES.findall(stat))
    return Commits(total, by_agent, lines)


def _git(root: Path, *args: str) -> str:
    """git's output; OutcomesError with git's own reason when it fails."""
    done = _run_git(root, *args)
    if done.returncode != 0:
        reason = (done.stderr.strip().splitlines() or ["unknown error"])[-1]
        raise OutcomesError(f"git {args[0]} failed: {reason}")
    return done.stdout


def _run_git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except FileNotFoundError:
        raise OutcomesError("git is not installed or not on PATH") from None


def render(result: Outcomes) -> str:
    """The terminal view: one row per repository, then what stayed out."""
    authors = (
        "every author's"
        if result.all_authors
        else "yours (each repository's git user.email)"
    )
    window = result.period or "each repository's days with agent usage (UTC)"
    lines = [
        "tokencur outcomes — AI usage value per commit "
        "(API-equivalent list value, not money paid)",
        f"commits: {authors}, merges left out; window: {window}",
        "",
    ]
    header = (
        "repository",
        "days",
        "calls",
        "usage value",
        "commits",
        "agent",
        "lines",
        "per commit",
    )
    rows = [_row(r) for r in result.repos]
    lines += table(header, rows) if rows else ["no repository had agent usage"]
    lines += ["", *_coverage(result)]
    for r in result.repos:
        if r.error:
            lines.append(f"{home_relative(r.root)}: {r.error}")
        elif r.everyone and not result.all_authors:
            lines.append(
                f"{home_relative(r.root)}: no git user.email set, "
                "so every author's commits count"
            )
    unpriced = sum(r.unpriced for r in result.repos)
    if unpriced:
        lines.append(
            f"unpriced models: {unpriced:,} attributed call{'s' * (unpriced != 1)}"
            " not valued"
        )
    lines += [
        "agent: commits a coding agent signed, as author or Co-authored-by trailer.",
        "A commit measures output, not quality: compare a repository with itself "
        "over time (ADR 0010).",
    ]
    return "\n".join(lines)


def _row(r: RepoOutcome) -> tuple[str, ...]:
    if r.since is None or r.until is None:
        days = "no usage"
    else:
        last = r.until - timedelta(days=1)
        days = f"{r.since}" if last == r.since else f"{r.since}..{last}"
    if r.commits is None:
        made = ("?", "?", "?")
    else:
        made = (
            f"{r.commits.total:,}",
            f"{r.commits.by_agent:,}",
            f"{r.commits.lines:,}",
        )
    per = r.per_commit
    return (
        home_relative(r.root),
        days,
        f"{r.calls:,}",
        f"${r.value_usd:,.2f}",
        *made,
        f"${per:,.2f}" if per is not None else "n/a",
    )


def _coverage(result: Outcomes) -> list[str]:
    if not result.total_usd:
        return ["no priced usage in the period"]
    share = result.attributed_usd / result.total_usd
    lines = [
        f"attributed: ${result.attributed_usd:,.2f} of ${result.total_usd:,.2f} "
        f"usage value ({share:.0%})"
    ]
    for reason, (n, value) in sorted(
        result.unattributed.items(), key=lambda kv: -kv[1][1]
    ):
        lines.append(
            f"  not attributed, {reason}: ${value:,.2f} ({n:,} call{'s' * (n != 1)})"
        )
    return lines
