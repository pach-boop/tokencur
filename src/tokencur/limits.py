"""Plan limits: how close a subscription's usage limits are (ADR 0014).

A subscription such as Claude Max caps usage in windows, five hours and
seven days, but never states the cap, nor how much of it is left, in
tokens or dollars. What the logs do show is each time a limit turned a
request away and when that limit resets (``LimitHit``). tokencur
measures every window in the one unit it has for all usage,
API-equivalent usage value:

- The value used from a window's start to a hit is a ceiling observed
  on the plan in force that day. It is a reference for the current plan
  only when its whole window ran on that plan and these logs hold its
  usage.
- The value used in the current window, set against those ceilings, says
  how close the next hit is; the last seven days' pace says when it
  would come.

These are estimates by nature. Limits are not priced like the API, and
usage the logs do not hold (claude.ai chat, Claude Code's own background
calls) draws on the same limits. Only Claude Code usage is measured.
"""

from __future__ import annotations

import os
from bisect import bisect_left
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, tzinfo
from pathlib import Path

from tokencur.observatory import plans_of
from tokencur.pricing import provenance, record_cost_usd
from tokencur.records import LimitHit, UsageRecord, parse_timestamp

#: The windows tokencur knows, by the ``rateLimitType`` Claude Code logs.
WINDOWS = {"five_hour": timedelta(hours=5), "seven_day": timedelta(days=7)}
_LABELS = {"five_hour": "5 hours", "seven_day": "week"}
_PACE_DAYS = 7
#: How far back five-hour windows are rebuilt from call times: long enough
#: for a gap of five hours or more to have set the boundaries right.
_LOOKBACK = timedelta(days=2)
#: A five-hour window opens at its first call rounded down to ten minutes:
#: all five resets Claude Code logged up to 2026-10-14 (02:50, 21:00,
#: 02:00, 01:40, 21:00 UTC) match that rule, and none match the exact
#: call time or the hour.
_GRAIN_SECONDS = 600
#: Below this, a ceiling's window holds almost no usage these logs see:
#: the subscription was used elsewhere, so it is no reference.
_MEASURED = 1.0
_SHOWN_HITS = 3
_SOURCE = "claude-code"


@dataclass(frozen=True)
class Ceiling:
    """The usage value a window had used when its limit turned a request away."""

    kind: str
    hit_at: datetime
    resets_at: datetime
    value: float
    plan: str  # the plan in force that day, "" when unknown
    spans_plans: bool = False  # the window began under another plan


@dataclass(frozen=True)
class Window:
    """The window ``now`` falls in, and the usage value it has used so far."""

    kind: str
    start: datetime
    end: datetime
    value: float
    blocked: bool  # a limit hit in this window: requests wait for ``end``
    estimated: bool  # bounds inferred from call times, not from a logged reset


@dataclass(frozen=True)
class Limits:
    now: datetime
    plan: str
    ceilings: list[Ceiling]
    current: dict[str, Window | None]
    pace_per_day: float
    other_kinds: list[str] = field(default_factory=list)
    plans_known: bool = False  # the plans file names Claude Code plans
    plans_from: str = ""  # where the plans were read, for the view
    implausible: int = 0  # hits left out: their reset does not fit the window
    unpriced: dict[str, int] = field(default_factory=dict)  # model -> calls


def plans_path(explicit: Path | None = None) -> Path | None:
    """The plans file to read: ``explicit`` when given; else
    ``subscriptions.json`` in the current directory (a tokencur checkout);
    else ``$XDG_CONFIG_HOME/tokencur/subscriptions.json``
    (``~/.config/tokencur/...``), so the command finds it from anywhere.
    None when there is none."""
    if explicit is not None:
        return explicit
    config = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    for candidate in (
        Path("subscriptions.json"),
        Path(config) / "tokencur" / "subscriptions.json",
    ):
        if candidate.is_file():
            return candidate
    return None


class _Timeline:
    """Claude Code usage value over time, for sums over any interval."""

    def __init__(self, records: list[UsageRecord]) -> None:
        points = []
        self.unpriced: list[tuple[datetime, str]] = []
        for r in records:
            moment = parse_timestamp(r.timestamp)
            if r.source != _SOURCE or moment is None:
                continue
            cost = record_cost_usd(r)
            if cost is None:
                self.unpriced.append((moment, r.model))
            else:
                points.append((moment, cost))
        points.sort(key=lambda p: p[0])
        self.times = [t for t, _ in points]
        self._sums = [0.0]
        for _, cost in points:
            self._sums.append(self._sums[-1] + cost)

    def value(self, start: datetime, end: datetime) -> float:
        """Usage value of the calls in ``[start, end)``."""
        if end <= start:
            return 0.0
        lo, hi = bisect_left(self.times, start), bisect_left(self.times, end)
        return self._sums[hi] - self._sums[lo]


def limits(
    records: list[UsageRecord],
    hits: list[LimitHit],
    subscriptions: dict | None,
    now: datetime | None = None,
    tz: tzinfo | None = None,
    plans_from: str = "",
) -> Limits:
    """Ceilings seen at each hit, and where the current windows stand.

    A plan's ``from`` and ``until`` days in subscriptions.json are the days
    a person lived, so they are read in ``tz`` (default: this machine's
    time zone): an evening hit in Mexico City is already the next UTC day,
    and would otherwise land on the next plan.
    """
    now = now or datetime.now(UTC)
    timeline = _Timeline(records)
    plans = plans_of(subscriptions).get("Claude Code", [])
    first: dict[tuple[str, datetime], datetime] = {}
    other_kinds = set()
    implausible = 0
    for hit in hits:
        moment, resets = parse_timestamp(hit.timestamp), parse_timestamp(hit.resets_at)
        if moment is None or resets is None:
            continue
        if hit.kind not in WINDOWS:
            other_kinds.add(hit.kind)
            continue
        if not timedelta(0) < resets - moment <= WINDOWS[hit.kind]:
            implausible += 1  # a reset outside the window it closes
            continue
        key = (hit.kind, resets)  # retries after a hit share its reset
        first[key] = min(first.get(key, moment), moment)
    ceilings = []
    for (kind, resets), hit_at in first.items():
        start = resets - WINDOWS[kind]
        plan = _plan_at(plans, hit_at.astimezone(tz).date())
        ceilings.append(
            Ceiling(
                kind=kind,
                hit_at=hit_at,
                resets_at=resets,
                value=timeline.value(start, hit_at),
                plan=plan,
                spans_plans=plan != _plan_at(plans, start.astimezone(tz).date()),
            )
        )
    ceilings.sort(key=lambda c: c.hit_at)
    current = {
        "seven_day": _week(ceilings, timeline, now),
        "five_hour": _five_hours(ceilings, timeline, now),
    }
    starts = [c.resets_at - WINDOWS[c.kind] for c in ceilings]
    starts += [w.start for w in current.values() if w is not None]
    shown_from = min(starts, default=now)
    unpriced: dict[str, int] = {}
    for moment, model in timeline.unpriced:
        if shown_from <= moment < now:
            unpriced[model] = unpriced.get(model, 0) + 1
    return Limits(
        now=now,
        plan=_plan_at(plans, now.astimezone(tz).date()),
        ceilings=ceilings,
        current=current,
        pace_per_day=timeline.value(now - timedelta(days=_PACE_DAYS), now) / _PACE_DAYS,
        other_kinds=sorted(other_kinds),
        plans_known=bool(plans),
        plans_from=plans_from,
        implausible=implausible,
        unpriced=unpriced,
    )


def _plan_at(plans: list[dict], day: date) -> str:
    """The Claude Code plan in force on ``day``, as subscriptions.json dates
    it (``from`` included, ``until`` excluded); "" when none is."""
    for plan in plans:
        start, end = plan.get("from"), plan.get("until")
        if (start is None or date.fromisoformat(start) <= day) and (
            end is None or day < date.fromisoformat(end)
        ):
            label = f"${plan.get('monthly_usd', 0):,.0f}/month"
            return f"{label} since {start}" if start else label
    return ""


def _week(ceilings: list[Ceiling], timeline: _Timeline, now: datetime) -> Window | None:
    """The seven-day window ``now`` is in. Weekly limits reset on a fixed
    schedule, so the latest logged reset places every later week; with
    none on record the week cannot be placed."""
    resets = [c.resets_at for c in ceilings if c.kind == "seven_day"]
    if not resets:
        return None
    week = WINDOWS["seven_day"]
    latest = max(resets)
    if now < latest:
        start, end, blocked = latest - week, latest, True
    else:
        start = latest + week * ((now - latest) // week)
        end, blocked = start + week, False
    return Window("seven_day", start, end, timeline.value(start, now), blocked, False)


def _five_hours(
    ceilings: list[Ceiling], timeline: _Timeline, now: datetime
) -> Window | None:
    """The five-hour window ``now`` is in, if one is open. A window opens
    with the first call after the previous one closed, rounded down to ten
    minutes, and lasts five hours. A logged reset fixes where a window
    ends; past it, and when no reset is recent, windows are rebuilt from
    call times."""
    span = WINDOWS["five_hour"]
    latest = max((c.resets_at for c in ceilings if c.kind == "five_hour"), default=None)
    if latest is not None and now < latest:
        start = latest - span
        return Window(
            "five_hour", start, latest, timeline.value(start, now), True, False
        )
    since, end = now - _LOOKBACK, None
    if latest is not None and latest >= since:
        since = end = latest
    start = None
    for moment in timeline.times[bisect_left(timeline.times, since) :]:
        if moment >= now:
            break
        if end is None or moment >= end:
            start = _floor(moment)
            start = max(start, end) if end is not None else start
            end = start + span
    if start is None or end is None or now >= end:
        return None
    return Window("five_hour", start, end, timeline.value(start, now), False, True)


def _floor(moment: datetime) -> datetime:
    """``moment`` rounded down to the window grain (ten minutes)."""
    seconds = moment.timestamp() // _GRAIN_SECONDS * _GRAIN_SECONDS
    return datetime.fromtimestamp(seconds, UTC)


def render(result: Limits, tz: tzinfo | None = None) -> str:
    """The terminal view. Times read in ``tz`` (default: this machine's)."""

    def at(moment: datetime) -> str:
        return moment.astimezone(tz).strftime("%a %Y-%m-%d %H:%M %Z")

    lines = [
        "tokencur limits — Claude Code plan limits in API-equivalent usage value "
        "(not money paid)",
        _plan_line(result),
        f"now: {at(result.now)}",
        "",
    ]
    for kind in ("seven_day", "five_hour"):
        lines.extend(_section(result, kind, at))
    if result.other_kinds:
        lines.append(
            f"other limits logged, not measured: {', '.join(result.other_kinds)}"
        )
    if result.implausible:
        lines.append(
            f"limit hits left out: {result.implausible}, their reset does not "
            "fit the window it closes"
        )
    if result.unpriced:
        pairs = ", ".join(f"{m} x{n}" for m, n in sorted(result.unpriced.items()))
        lines.append(f"left out, no price: {pairs} (these figures read low)")
    lines += [
        "A ceiling is the usage value these logs show when a limit turned a "
        "request away. Usage they do not hold (claude.ai chat, Claude Code's "
        "background calls) counted too, and limits are not priced like the "
        "API: read this as a gauge, not a balance (ADR 0014).",
        f"reproducible with {provenance()}",
    ]
    return "\n".join(lines)


def _plan_line(result: Limits) -> str:
    source = f" (from {result.plans_from})" if result.plans_from else ""
    if result.plan:
        return f"plan in force: {result.plan}{source}"
    if result.plans_known:
        return f"plan in force: none of the plans{source} covers today"
    return (
        "plan in force: unknown; put your plans in "
        "~/.config/tokencur/subscriptions.json (or pass --plans FILE) to tell "
        "ceilings of different plans apart"
    )


def _section(result: Limits, kind: str, at) -> list[str]:
    label = _LABELS[kind]
    window = result.current[kind]
    seen = [c for c in result.ceilings if c.kind == kind]
    mine = [c for c in seen if c.plan == result.plan] if result.plan else seen
    references = [c for c in mine if not c.spans_plans and c.value >= _MEASURED]
    lowest = min((c.value for c in references), default=None)
    lines = []
    if window is None and kind == "seven_day":
        lines.append(
            f"{label}: no weekly limit hit on record yet, so the week cannot be placed"
        )
    elif window is None:
        lines.append(f"{label}: no window open; the next opens with your next call")
    else:
        state = "limit reached, requests wait until" if window.blocked else "resets"
        how = " (window inferred from call times)" if window.estimated else ""
        share = (
            f" = {window.value / lowest:.0%} of the lowest ceiling" if lowest else ""
        )
        lines.append(
            f"{label}: ${window.value:,.2f}{share} since {at(window.start)}{how}; "
            f"{state} {at(window.end)} (in {_span(window.end - result.now)})"
        )
    on_plan = " on this plan" if result.plan else ""
    if references:
        recent = sorted(references, key=lambda c: c.hit_at, reverse=True)
        listed = ", ".join(
            f"${c.value:,.2f} ({at(c.hit_at)})" for c in recent[:_SHOWN_HITS]
        )
        more = len(recent) - _SHOWN_HITS
        tail = f", and {more} more" if more > 0 else ""
        lines.append(f"  limit hit{on_plan} at {listed}{tail}")
        if window is not None and not window.blocked and kind == "seven_day":
            lines.append("  " + _projection(result, window, lowest, at))
    for c in mine:
        if c.spans_plans:
            lines.append(
                f"  also hit at ${c.value:,.2f} ({at(c.hit_at)}), in a window that "
                "began on another plan: not a reference"
            )
        elif c.value < _MEASURED:
            lines.append(
                f"  also hit at {at(c.hit_at)} with ${c.value:,.2f} in these logs: "
                "the usage was elsewhere, not a reference"
            )
    if not mine:
        earlier = len(seen)
        lines.append(
            f"  limit hit {earlier} times on other plans, never on this one"
            if earlier
            else "  no limit hit on record"
        )
    lines.append("")
    return lines


def _projection(result: Limits, window: Window, lowest: float, at) -> str:
    if window.value >= lowest:
        return f"already past the lowest ceiling seen (${lowest:,.2f})"
    pace = result.pace_per_day
    if pace <= 0:
        return "no usage in the last 7 days to project from"
    eta = result.now + timedelta(days=(lowest - window.value) / pace)
    if eta >= window.end:
        left = (window.end - result.now) / timedelta(days=1)
        return (
            f"at the last 7 days' pace (${pace:,.2f}/day) the week ends near "
            f"${window.value + pace * left:,.2f}, under the lowest ceiling seen "
            f"(${lowest:,.2f})"
        )
    return (
        f"at the last 7 days' pace (${pace:,.2f}/day) the week reaches the lowest "
        f"ceiling seen (${lowest:,.2f}) around {at(eta)}, "
        f"{_span(window.end - eta)} before it resets"
    )


def _span(delta: timedelta) -> str:
    minutes = max(int(delta.total_seconds() // 60), 0)
    days, rest = divmod(minutes, 24 * 60)
    hours, minutes = divmod(rest, 60)
    if days:
        return f"{days}d {hours}h"
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"
