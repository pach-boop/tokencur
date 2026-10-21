"""Ingest usage records from local Claude Code session logs.

Claude Code writes one JSONL transcript per session under
``~/.claude/projects/<workspace>/<session-id>.jsonl``. Each assistant
message line carries a ``message.usage`` object with token counts.

It also reads Claude Code's own per-session cost counters
(``session_counters``), which ``tokencur.reconcile`` checks tokencur against,
the plan limits that turned requests away (``iter_limit_hits``), and its
own usage meter (``meter_readings``).

Privacy: this module keeps usage metadata only (tokens, model,
timestamps, the working directory, cost counters); SECURITY.md lists
every field it reads. It never extracts message content.
"""

from __future__ import annotations

import json
import math
import os
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Re-exported: ``tokencur.ingest.claude_code.UsageRecord`` predates
# ``tokencur.records`` and stays importable from here.
from tokencur.ingest.fields import Malformed, count, entry, obj, text
from tokencur.ingest.stats import ScanStats
from tokencur.records import LimitHit, MeterReading, UsageRecord

#: Claude Code logs client-side placeholder messages (API-error stubs,
#: interrupted turns) under this sentinel model, with all-zero usage.
#: They are not API traffic and must not surface as unpriced rows.
SYNTHETIC_MODEL = "<synthetic>"


def iter_usage_records(
    root: Path, stats: ScanStats | None = None
) -> Iterator[UsageRecord]:
    """Yield one UsageRecord per assistant message under ``root``.

    Records are deduplicated on (request id, message id): streaming can
    log one message across several lines, and resuming a session can
    re-copy past messages into a new file under a new session id — the
    same API request must never be counted twice. The first line seen
    names the session, workspace and working directory (``cwd``, which
    follows the agent as it changes directory); the token counts are
    each field's largest value across the message's lines, because
    streamed counts only grow and an early line can hold a partial
    output count. A subagent's transcript never logs that final count;
    where Claude Code's counters hold it, it is filled in (see
    ``_top_up``).
    Lines that are not valid JSON or carry no usage data are skipped,
    as are synthetic placeholder messages (see ``SYNTHETIC_MODEL``) and
    malformed usage (see ``tokencur.ingest.fields``). ``stats``, when
    given, counts what the scan saw (see ``tokencur.ingest.stats``).
    """
    stats = stats if stats is not None else ScanStats()
    messages: dict[str, UsageRecord] = {}
    finished: set[str] = set()  # records a line logged with the call's stop reason
    counters = SessionCounters()
    first_line: dict[str, str] = {}  # each session's earliest logged timestamp
    for path in sorted(root.rglob("*.jsonl")):
        stats.files += 1
        workspace = _workspace(root, path)
        # errors="replace": a corrupted byte spoils one line, not the scan.
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line_entry = entry(line)
                _read_counters(line_entry, counters)
                session, logged = (
                    text(line_entry.get("sessionId")),
                    text(line_entry.get("timestamp")),
                )
                if session and logged:
                    first_line[session] = min(first_line.get(session, logged), logged)
                try:
                    record = _parse_entry(line_entry, workspace, stats)
                except Malformed:
                    stats.malformed += 1
                    continue
                if record is None:
                    continue
                if obj(line_entry.get("message")).get("stop_reason") is not None:
                    finished.add(record.record_id)
                first = messages.get(record.record_id)
                messages[record.record_id] = (
                    record if first is None else _final_counts(first, record)
                )
    _top_up(messages, finished, counters, first_line, stats)
    yield from messages.values()


def _workspace(root: Path, path: Path) -> str:
    """The project a transcript belongs to: the first directory under
    ``root``. Subagent transcripts sit deeper
    (``<project>/<session>/subagents/agent-*.jsonl``) and belong to it too."""
    parts = path.relative_to(root).parts
    return parts[0] if len(parts) > 1 else path.parent.name


_COUNTS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_5m_tokens",
    "cache_write_1h_tokens",
)


def _final_counts(first: UsageRecord, later: UsageRecord) -> UsageRecord:
    """``first``, with each token count raised to ``later``'s if larger,
    and ``later``'s working directory if ``first`` named none."""
    return replace(
        first,
        cwd=first.cwd or later.cwd,
        **{f: max(getattr(first, f), getattr(later, f)) for f in _COUNTS},
    )


#: How long after its counters begin a session may write its first line.
#: Claude Code writes it within seconds (207 of 210 sessions measured on
#: 2026-10-20, all within a minute). A later first line means the counters
#: began in a session whose transcript is no longer on disk, so the gap
#: between them and the transcripts is not this session's to fill.
_COUNTERS_LEAD = timedelta(minutes=10)


def _top_up(
    messages: dict[str, UsageRecord],
    finished: set[str],
    counters: SessionCounters,
    first_line: dict[str, str],
    stats: ScanStats,
) -> None:
    """Fill in the output counts subagent transcripts log short.

    A subagent (a Task agent or a workflow agent) logs each message of a
    call while it streams, with the output count of the call's first
    event (a few tokens), and never logs the final count; the main
    transcript does (measured 2026-10-20, Claude Code 2.1.282: 73-97% of
    subagent calls, under 1% of main-transcript calls). A call no line
    logged with a stop reason is one of these. In each chain of sessions
    that closed with counters, the output Claude Code counted for a model
    beyond what the transcripts logged is spread evenly over that model's
    short calls, so the chain's output matches Claude Code's own and each
    call keeps its time. Without counters (a session still open) or when
    the counters began before the transcripts on disk (``_COUNTERS_LEAD``),
    the short counts stay as logged.
    """
    short: dict[tuple[str, str], list[str]] = defaultdict(list)
    logged: dict[tuple[str, str], int] = defaultdict(int)
    sessions: dict[str, set[str]] = defaultdict(set)
    for record_id, record in messages.items():
        end = _counted_end(record.session_id, counters)
        sessions[end].add(record.session_id)
        logged[end, record.model] += record.output_tokens
        if record_id not in finished:
            short[end, record.model].append(record_id)
    stats.short_output_calls += sum(len(ids) for ids in short.values())
    for (end, model), ids in short.items():
        counted = counters.output.get(end, {}).get(model)
        if counted is None or not _counters_cover(
            end, sessions[end], counters, first_line
        ):
            continue
        missing = counted - logged[end, model]
        if missing <= 0:
            continue
        ids.sort(key=lambda r: (messages[r].timestamp, r))
        share, rest = divmod(missing, len(ids))
        for i, record_id in enumerate(ids):
            record = messages[record_id]
            added = share + (1 if i < rest else 0)
            messages[record_id] = replace(
                record, output_tokens=record.output_tokens + added
            )
        stats.topped_up_calls += len(ids)
        stats.topped_up_tokens += missing


def _counted_end(session: str, counters: SessionCounters) -> str:
    """The last session with counters that ``session``'s chain reaches."""
    end, seen = session, {session}
    while True:
        after = counters.continued.get(end)
        if after not in counters.output or after in seen:
            return end
        end = after
        seen.add(end)


def _counters_cover(
    end: str, sessions: set[str], counters: SessionCounters, first_line: dict[str, str]
) -> bool:
    """Whether ``end``'s counters began with the transcripts on disk."""
    started = counters.started.get(end)
    firsts = [first_line[s] for s in sessions if s in first_line]
    if started is None or not firsts:
        return False
    try:
        first = datetime.fromisoformat(min(firsts).replace("Z", "+00:00"))
    except ValueError:
        return False
    begun = datetime.fromtimestamp(started / 1000, UTC)
    return first.tzinfo is not None and first - begun <= _COUNTERS_LEAD


def _parse_entry(
    line_entry: dict, workspace: str, stats: ScanStats
) -> UsageRecord | None:
    if line_entry.get("type") != "assistant":
        return None
    message = obj(line_entry.get("message"))
    if message.get("model") == SYNTHETIC_MODEL:
        return None
    usage = message.get("usage")
    if not usage:
        return None
    stats.usage_lines += 1
    stats.saw_version(text(line_entry.get("version")))
    if not isinstance(usage, dict):
        raise Malformed("usage is not an object")

    key = (
        text(line_entry.get("requestId")),
        text(message.get("id")) or text(line_entry.get("uuid")),
    )
    write_5m, write_1h = _cache_writes(usage)
    return UsageRecord(
        timestamp=text(line_entry.get("timestamp")),
        workspace=workspace,
        session_id=text(line_entry.get("sessionId")),
        model=text(message.get("model"), "unknown"),
        input_tokens=count(usage.get("input_tokens")),
        output_tokens=count(usage.get("output_tokens")),
        cache_read_tokens=count(usage.get("cache_read_input_tokens")),
        cache_write_5m_tokens=write_5m,
        cache_write_1h_tokens=write_1h,
        source="claude-code",
        # The dedup key above is already the API request's identity.
        record_id=f"{key[0]}:{key[1]}",
        price_modifiers=_modifiers(usage),
        cwd=text(line_entry.get("cwd")),
    )


def _modifiers(usage: dict) -> str:
    """The logged request options that change the call's price."""
    found = []
    if usage.get("service_tier") == "batch":
        found.append("batch")
    if usage.get("speed") == "fast":
        found.append("fast")
    if usage.get("inference_geo") == "us":
        found.append("us")
    return "+".join(found)


def _cache_writes(usage: dict) -> tuple[int, int]:
    """Split cache writes by TTL; they are priced differently (1.25x vs 2x).

    Older log formats only report the total ``cache_creation_input_tokens``.
    Those are attributed to the 5-minute tier — Claude Code's default TTL —
    which slightly underestimates cost when 1h writes were present.
    """
    breakdown = obj(usage.get("cache_creation"))
    if breakdown:
        return (
            count(breakdown.get("ephemeral_5m_input_tokens")),
            count(breakdown.get("ephemeral_1h_input_tokens")),
        )
    return count(usage.get("cache_creation_input_tokens")), 0


def iter_limit_hits(root: Path) -> Iterator[LimitHit]:
    """Yield each logged refusal by a plan usage limit under ``root``.

    When a subscription limit turns a request away, Claude Code logs a
    ``<synthetic>`` stub carrying ``quotaLimits``: the limit's type
    (``rateLimitType``: ``five_hour``, ``seven_day``, ...), ``status``
    and ``resetsAt`` (Unix seconds). Only refusals (``status ==
    "rejected"``) are kept, once per request. The stub's text, which says
    the same in words, is never read (ADR 0006, ADR 0014).
    """
    yield from limit_stubs(root).hits


@dataclass(frozen=True)
class LimitStubs:
    """The refusals by a plan limit under a root, and how many of the
    stubs Claude Code logs for them could be read."""

    hits: list[LimitHit]
    stubs: int = 0  # requests whose stub has ``error: "rate_limit"``
    unreadable: int = 0  # of those, the ones that give no hit


def limit_stubs(root: Path) -> LimitStubs:
    """The limit hits under ``root`` (see ``iter_limit_hits``), checked
    against every ``rate_limit`` stub: each one up to 2026-10-16 carried
    ``quotaLimits``, so a stub that gives no hit means Claude Code changed
    how it logs a limit, and the hits it stands for go unseen. Requests
    are counted once, however many files repeat them."""
    hits: dict[str, LimitHit] = {}
    stubs: dict[str, bool] = {}  # request -> gave a hit
    for path in sorted(root.rglob("*.jsonl")):
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"quotaLimits"' not in line and '"rate_limit"' not in line:
                    continue  # a cheap filter: these lines are rare
                line_entry = entry(line)
                hit = _limit_hit(line_entry)
                if hit is not None:
                    hits.setdefault(hit.record_id, hit)
                if line_entry.get("error") == "rate_limit":
                    request = text(line_entry.get("requestId"))
                    message = text(obj(line_entry.get("message")).get("id"))
                    key = f"{request}:{message}" if request or message else line
                    stubs[key] = stubs.get(key, False) or hit is not None
    return LimitStubs(
        hits=list(hits.values()),
        stubs=len(stubs),
        unreadable=sum(not read for read in stubs.values()),
    )


def _limit_hit(line_entry: dict) -> LimitHit | None:
    quota = obj(line_entry.get("quotaLimits"))
    kind = text(quota.get("rateLimitType"))
    resets = quota.get("resetsAt")
    if quota.get("status") != "rejected" or not kind or not _is_amount(resets):
        return None
    try:
        resets_at = datetime.fromtimestamp(resets, UTC)
    except (OverflowError, OSError, ValueError):
        return None
    timestamp = text(line_entry.get("timestamp"))
    request = text(line_entry.get("requestId"))
    message = text(obj(line_entry.get("message")).get("id"))
    if not timestamp or not (request or message):
        return None
    return LimitHit(
        source="claude-code",
        record_id=f"{request}:{message}",
        timestamp=timestamp,
        session_id=text(line_entry.get("sessionId")),
        kind=kind,
        resets_at=resets_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


#: The limits whose meter tokencur reads, as Claude Code names them.
_METERED = ("five_hour", "seven_day")


def config_path() -> Path:
    """Claude Code's own state file: ``$CLAUDE_CONFIG_DIR/.claude.json``,
    else ``~/.claude.json``."""
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(base) / ".claude.json" if base else Path.home() / ".claude.json"


@dataclass(frozen=True)
class MeterStatus:
    """What Claude Code's state file holds of its ``/usage`` meter."""

    file: bool  # the state file could be read
    found: bool  # it holds the meter's key
    readings: list[MeterReading] = field(default_factory=list)

    @property
    def reshaped(self) -> bool:
        """The key is there but gives no reading: its shape changed."""
        return self.found and not self.readings


def meter_readings(path: Path | None = None) -> list[MeterReading]:
    """The plan usage Claude Code last fetched for its ``/usage`` meter
    (see ``meter_status``)."""
    return meter_status(path).readings


def meter_status(path: Path | None = None) -> MeterStatus:
    """The plan usage Claude Code last fetched for its ``/usage`` meter.

    Claude Code keeps it in its state file under ``cachedUsageUtilization``:
    when it was fetched (``fetchedAtMs``) and, per limit, the percent used
    and when the limit resets (rounded to the minute). That file also holds account details and
    project settings; it is decoded as JSON to reach this key, and nothing
    else in it is read (ADR 0015). An absent, unreadable or reshaped file
    gives no readings.
    """
    target = path or config_path()
    try:
        state = json.loads(target.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return MeterStatus(file=False, found=False)
    if "cachedUsageUtilization" not in obj(state):
        return MeterStatus(file=True, found=False)  # API-key use has no meter
    cached = obj(obj(state).get("cachedUsageUtilization"))
    fetched = cached.get("fetchedAtMs")
    if not _is_amount(fetched):
        return MeterStatus(file=True, found=True)
    try:
        fetched_at = datetime.fromtimestamp(fetched / 1000, UTC)
    except (OverflowError, OSError, ValueError):
        return MeterStatus(file=True, found=True)
    readings = []
    for kind in _METERED:
        limit = obj(obj(cached.get("utilization")).get(kind))
        percent, resets = limit.get("utilization"), text(limit.get("resets_at"))
        if not _is_amount(percent) or percent > 100 or not resets:
            continue
        try:
            resets_at = datetime.fromisoformat(resets)
            if resets_at.tzinfo is None:
                resets_at = resets_at.replace(tzinfo=UTC)  # as parse_timestamp reads it
            # Logged as 02:59:59.835 for a limit that resets at 03:00.
            resets_at = (resets_at.astimezone(UTC) + timedelta(seconds=30)).replace(
                second=0, microsecond=0
            )
        except (ValueError, OverflowError):
            continue
        readings.append(
            MeterReading(
                source="claude-code",
                kind=kind,
                fetched_at=fetched_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                percent=float(percent),
                resets_at=resets_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
        )
    return MeterStatus(file=True, found=True, readings=readings)


@dataclass(frozen=True)
class SessionCounters:
    """Claude Code's own accounting, as its transcripts record it.

    When a session ends, Claude Code writes a ``cost-state`` line with
    the cost it counted for each model, including calls it never writes
    to the transcript, and the time its API calls took. ``costs`` maps
    each session to the last such line's per-model cost in USD, and
    ``api_ms`` to its API time in milliseconds, with and without retries,
    ``output`` to its per-model output tokens and ``started`` to when its
    counters began (``startTime``, milliseconds since the epoch).
    A session continued in another one (``continued-in`` lines) hands
    those counters on to it: ``continued`` maps each session to the one
    it continued in, and the later session keeps the earlier one's start.
    ``prompts`` counts, per session, the messages a person typed (see
    ``_is_prompt``). Ids and numbers only.
    """

    costs: dict[str, dict[str, float]] = field(default_factory=dict)
    api_ms: dict[str, tuple[float, float]] = field(default_factory=dict)
    continued: dict[str, str] = field(default_factory=dict)
    prompts: dict[str, int] = field(default_factory=dict)
    output: dict[str, dict[str, int]] = field(default_factory=dict)
    started: dict[str, float] = field(default_factory=dict)

    def chain_end(self, session: str) -> str:
        """The last session of the chain ``session`` continued into."""
        seen = {session}
        while session in self.continued and self.continued[session] not in seen:
            session = self.continued[session]
            seen.add(session)
        return session


def session_counters(root: Path) -> SessionCounters:
    """Read Claude Code's own per-session counters under ``root``."""
    counters = SessionCounters()
    for path in sorted(root.rglob("*.jsonl")):
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"cost-state"' in line or '"continued-in"' in line:
                    _read_counters(entry(line), counters)
                elif '"user"' in line and '"tool_result"' not in line:
                    line_entry = entry(line)
                    session = text(line_entry.get("sessionId"))
                    if session and _is_prompt(line_entry):
                        counters.prompts[session] = counters.prompts.get(session, 0) + 1
    return counters


def _is_prompt(line_entry: dict) -> bool:
    """A message a person typed. Claude Code marks one with
    ``origin.kind == "human"`` (a task notification says otherwise); a
    line from before that field counts when it is neither tool output, nor
    injected, nor a compaction summary. Only the kind, the line's flags
    and the content's shape are read, never text."""
    if line_entry.get("type") != "user":
        return False
    if any(line_entry.get(f) for f in ("isSidechain", "isMeta", "isCompactSummary")):
        return False
    origin = line_entry.get("origin")
    if isinstance(origin, dict) and "kind" in origin:
        return origin.get("kind") == "human"
    content = obj(line_entry.get("message")).get("content")
    if isinstance(content, str):
        return True
    return isinstance(content, list) and not any(
        obj(block).get("type") == "tool_result" for block in content
    )


def _read_counters(line_entry: dict, counters: SessionCounters) -> None:
    """Add a ``cost-state`` or ``continued-in`` line to ``counters``."""
    kind = line_entry.get("type")
    if kind == "cost-state":
        _read_cost_state(line_entry, counters)
    elif kind == "continued-in":
        before = text(line_entry.get("sessionId"))
        after = text(line_entry.get("continuedInSessionId"))
        if before and after and before != after:
            counters.continued[before] = after


def _read_cost_state(line_entry: dict, counters: SessionCounters) -> None:
    session = text(line_entry.get("sessionId"))
    if not session:
        return
    costs, output = {}, {}
    for model, usage in obj(line_entry.get("modelUsage")).items():
        cost, tokens = obj(usage).get("costUSD"), obj(usage).get("outputTokens")
        if _is_amount(cost):
            costs[model] = float(cost)
        if _is_amount(tokens):
            output[model] = int(tokens)
    counters.costs[session] = costs  # the last line of a session wins
    counters.output[session] = output
    start = line_entry.get("startTime")
    if _is_amount(start):
        counters.started[session] = float(start)
    else:
        counters.started.pop(session, None)
    total = line_entry.get("totalAPIDuration")
    net = line_entry.get("totalAPIDurationWithoutRetries")
    if _is_amount(total) and _is_amount(net) and net <= total:
        counters.api_ms[session] = (float(total), float(net))


def _is_amount(value: object) -> bool:
    """A finite, non-negative number (booleans are not amounts)."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    return math.isfinite(value) and value >= 0
