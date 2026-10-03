"""``tokencur outcomes --sessions``: usage value per successful change.

The second unit-economics layer (issue #15, ADR 0012). For each agent
session: what its usage was worth, what a person put into it, how its
API calls went, and which of its changes landed and stayed. Usage value
is showback, API-equivalent list value, as everywhere in tokencur.

A session is the agent's own. Claude Code sessions continued into one
another count once, as a chain, and the chain carries Claude Code's own
counters: API time, with and without retries. Codex and Kimi Code
sessions bring usage value and changes only, since they record no
timings.

A change goes to the last session that worked in its repository before
it was committed, if that was within the preceding day. Otherwise it was
made without an agent session. A change succeeded when it reached the
default branch and was never reverted (see ``tokencur.gitlog``).
"""

from __future__ import annotations

from bisect import bisect_right
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from tokencur import gitlog
from tokencur.ingest.claude_code import SessionCounters
from tokencur.pricing import record_cost_usd
from tokencur.records import UsageRecord, parse_timestamp
from tokencur.terminal import home_relative, table

#: How long after a session's last call in a repository a change made
#: there still counts as that session's.
ATTRIBUTION_WINDOW = timedelta(hours=24)


@dataclass
class SessionOutcome:
    session: str  # the agent's session id; a chain's last one
    source: str
    start: datetime | None = None
    calls: int = 0
    value_usd: float = 0.0
    unpriced: int = 0  # calls of models with no rate: not valued, never $0
    calls_by_repo: Counter = field(default_factory=Counter)
    prompts: int | None = None  # None: the agent records no prompts
    api_ms: tuple[float, float] | None = None  # with and without retries
    landed: int = 0  # changes that reached the default branch
    reverted: int = 0  # of those, later reverted
    pending: int = 0  # changes not on the default branch (yet)

    @property
    def successes(self) -> int:
        return self.landed - self.reverted

    @property
    def per_success(self) -> float | None:
        return self.value_usd / self.successes if self.successes else None

    @property
    def retry_share(self) -> float | None:
        if not self.api_ms or not self.api_ms[0]:
            return None
        total, net = self.api_ms
        return (total - net) / total


@dataclass
class SessionsResult:
    sessions: list[SessionOutcome]
    without_agent: int = 0  # changes no session preceded within the window
    errors: dict[Path, str] = field(default_factory=dict)
    all_authors: bool = False
    period: str | None = None

    @property
    def value_usd(self) -> float:
        return sum(s.value_usd for s in self.sessions)

    @property
    def successes(self) -> int:
        return sum(s.successes for s in self.sessions)


def session_outcomes(
    records: Iterable[UsageRecord],
    counters: SessionCounters,
    repos: Sequence[Path] = (),
    since: date | None = None,
    until: date | None = None,
    all_authors: bool = False,
    period: str | None = None,
) -> SessionsResult:
    """Usage value and changes per session. ``records`` are the period's
    calls; with ``repos``, only the calls and changes in those
    repositories count, and sessions that never worked there drop out."""
    finder = gitlog.Repositories()
    asked = {_root(finder, repo) for repo in repos}
    sessions: dict[str, SessionOutcome] = {}
    calls_in: dict[Path, list[tuple[datetime, str]]] = defaultdict(list)
    for record in records:
        root = finder.root(record.cwd)
        in_repo = isinstance(root, Path)
        if asked and not (in_repo and root in asked):
            continue
        key = (
            counters.chain_end(record.session_id)
            if record.source == "claude-code"
            else record.session_id
        )
        outcome = sessions.setdefault(key, SessionOutcome(key, record.source))
        cost = record_cost_usd(record)
        outcome.calls += 1
        outcome.value_usd += cost or 0.0
        outcome.unpriced += cost is None
        moment = parse_timestamp(record.timestamp)
        if moment and (outcome.start is None or moment < outcome.start):
            outcome.start = moment
        if in_repo:
            outcome.calls_by_repo[root] += 1
            if moment:
                calls_in[root].append((moment, key))

    _add_counters(sessions, counters)
    result = SessionsResult(list(sessions.values()), all_authors=all_authors)
    result.period = period
    start = datetime.combine(since, time(), UTC) if since else None
    end = datetime.combine(until, time(), UTC) if until else None
    for root, calls in sorted(calls_in.items()):
        calls.sort()
        _attribute(root, calls, sessions, result, start, end)
    result.sessions.sort(key=lambda s: -s.value_usd)
    return result


def _root(finder: gitlog.Repositories, repo: Path) -> Path:
    path = Path(repo).expanduser().absolute()
    if not path.is_dir():
        raise gitlog.GitError(f"{repo}: no such directory")
    root = finder.root(str(path))
    if not isinstance(root, Path):
        raise gitlog.GitError(f"{repo}: {root}")
    return root


def _add_counters(sessions: dict[str, SessionOutcome], counters: SessionCounters):
    """Claude Code's own counters, chain by chain: prompts summed over the
    chain's sessions, API time as the chain's last session counted it."""
    prompts: Counter = Counter()
    for session, n in counters.prompts.items():
        prompts[counters.chain_end(session)] += n
    for key, outcome in sessions.items():
        if outcome.source != "claude-code":
            continue
        if key in prompts:
            outcome.prompts = prompts[key]
        outcome.api_ms = counters.api_ms.get(key)


def _attribute(
    root: Path,
    calls: list[tuple[datetime, str]],
    sessions: dict[str, SessionOutcome],
    result: SessionsResult,
    start: datetime | None,
    end: datetime | None,
) -> None:
    """Give each change in ``root`` to the last session that worked there
    within ``ATTRIBUTION_WINDOW`` before it was committed."""
    first = start or calls[0][0]
    last = end or calls[-1][0] + ATTRIBUTION_WINDOW
    try:
        author = None if result.all_authors else gitlog.user_email(root) or None
        changes = gitlog.changes(root, first, last, author)
    except gitlog.GitError as exc:
        result.errors[root] = str(exc)
        return
    times = [moment for moment, _ in calls]
    for change in changes:
        i = bisect_right(times, change.authored) - 1
        if i < 0 or change.authored - times[i] > ATTRIBUTION_WINDOW:
            result.without_agent += 1
            continue
        outcome = sessions[calls[i][1]]
        if change.landed:
            outcome.landed += 1
            outcome.reverted += change.reverted
        else:
            outcome.pending += 1


def render(result: SessionsResult, top: int = 20) -> str:
    """The terminal view: the ``top`` sessions by usage value, then totals."""
    authors = (
        "every author's"
        if result.all_authors
        else "yours (each repository's git user.email)"
    )
    lines = [
        "tokencur outcomes --sessions — usage value per successful change "
        "(API-equivalent list value, not money paid)",
        "success: a change reached the default branch and was never reverted; "
        f"changes: {authors}" + (f"; window: {result.period}" if result.period else ""),
        "",
    ]
    header = (
        "session",
        "started (UTC)",
        "repository",
        "calls",
        "usage value",
        "prompts",
        "API time",
        "retries",
        "landed",
        "reverted",
        "per success",
    )
    shown = result.sessions if top <= 0 else result.sessions[:top]
    rows = [_row(s) for s in shown]
    lines += table(header, rows, left=3) if rows else ["no sessions"]
    hidden = result.sessions[len(shown) :]
    if hidden:
        value = sum(s.value_usd for s in hidden)
        lines.append(f"and {len(hidden):,} smaller sessions: ${value:,.2f}")
    lines += ["", *_totals(result)]
    for root, error in sorted(result.errors.items()):
        lines.append(f"{home_relative(root)}: {error}")
    lines.append(
        "prompts are messages a person typed; API time and retries come from "
        "Claude Code's own counters and cover whole sessions (ADR 0012)."
    )
    return "\n".join(lines)


def _row(s: SessionOutcome) -> tuple[str, ...]:
    repos = [root for root, _ in s.calls_by_repo.most_common()]
    where = home_relative(repos[0]) if repos else "-"
    if len(repos) > 1:
        where += f" +{len(repos) - 1}"
    retries = s.retry_share
    per = s.per_success
    return (
        s.session[:8],
        s.start.strftime("%Y-%m-%d %H:%M") if s.start else "undated",
        where,
        f"{s.calls:,}",
        f"${s.value_usd:,.2f}",
        f"{s.prompts:,}" if s.prompts is not None else "-",
        _duration(s.api_ms[0]) if s.api_ms else "-",
        f"{retries:.1%}" if retries is not None else "-",
        f"{s.landed:,}",
        f"{s.reverted:,}",
        f"${per:,.2f}" if per is not None else "n/a",
    )


def _duration(ms: float) -> str:
    minutes = round(ms / 60_000)
    return f"{minutes // 60}h {minutes % 60:02d}m" if minutes >= 60 else f"{minutes}m"


def _totals(result: SessionsResult) -> list[str]:
    lines = []
    if result.successes:
        lines.append(
            f"usage value per successful change: "
            f"${result.value_usd / result.successes:,.2f} "
            f"(${result.value_usd:,.2f} over {result.successes:,} changes "
            "that landed and stayed)"
        )
    else:
        lines.append("no change landed and stayed in the window")
    idle = [s for s in result.sessions if not s.landed]
    if idle:
        value = sum(s.value_usd for s in idle)
        lines.append(
            f"sessions with no landed change: {len(idle):,} of "
            f"{len(result.sessions):,}, ${value:,.2f} of usage value"
        )
    pending = sum(s.pending for s in result.sessions)
    if pending:
        lines.append(f"changes not on the default branch yet: {pending:,}")
    if result.without_agent:
        lines.append(
            f"changes with no agent session in the day before: {result.without_agent:,}"
        )
    unpriced = sum(s.unpriced for s in result.sessions)
    if unpriced:
        lines.append(f"unpriced models: {unpriced:,} calls not valued")
    return lines
