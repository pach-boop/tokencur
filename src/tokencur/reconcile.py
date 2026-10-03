"""Reconcile tokencur's Claude Code figures with Claude Code's own counters.

ADR 0005: where a source keeps its own counters, tokencur's per-event
counting is checked against them. Claude Code writes, when a session
ends, the cost it counted for each model (``cost-state`` lines; see
``tokencur.ingest.claude_code.session_counters``). tokencur values the
calls the transcripts record. Comparing the two measures what the
transcripts never show: internal calls to smaller models (web search,
web fetch, session titles) and some main-model calls, compaction among
them.

A session continued in another one hands its counters on, so a chain of
sessions is compared as one: Claude Code's counters at the end of the
chain against tokencur's value of every session in it. A chain whose
first transcripts were deleted reads low.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

from tokencur.ingest.claude_code import SessionCounters
from tokencur.pricing import record_cost_usd
from tokencur.records import UsageRecord


@dataclass(frozen=True)
class Reconciliation:
    """tokencur against Claude Code, over the sessions with counters."""

    sessions: int  # chains of sessions closed with Claude Code's counters
    tokencur_usd: float  # tokencur's value of the calls their transcripts hold
    counted_usd: float  # Claude Code's cost for the models tokencur also saw
    unseen_usd: dict[str, float] = field(default_factory=dict)  # models only
    # Claude Code saw: every call to them is missing from the transcripts

    @property
    def agent_usd(self) -> float:
        """Everything Claude Code counted for these sessions."""
        return self.counted_usd + sum(self.unseen_usd.values())

    @property
    def coverage(self) -> float | None:
        """Share of Claude Code's cost that tokencur's value accounts for."""
        return self.tokencur_usd / self.agent_usd if self.agent_usd else None


def reconcile(
    records: Iterable[UsageRecord], counters: SessionCounters
) -> Reconciliation:
    """Compare tokencur's value of ``records`` with ``counters``, chain by
    chain. Sessions without counters (still open, or cut off) are left out."""
    value: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for record in records:
        value[record.session_id][record.model] += record_cost_usd(record) or 0.0
    previous = {after: before for before, after in counters.continued.items()}

    sessions, tokencur, counted = 0, 0.0, 0.0
    unseen: dict[str, float] = defaultdict(float)
    for end, models in sorted(counters.costs.items()):
        if counters.continued.get(end) in counters.costs:
            continue  # its counters carry on at the end of the chain
        if not any(models.values()):
            continue
        chain = [end]
        while chain[0] in previous and previous[chain[0]] not in chain:
            chain.insert(0, previous[chain[0]])
        seen = {model for session in chain for model in value.get(session, {})}
        sessions += 1
        tokencur += sum(v for s in chain for v in value.get(s, {}).values())
        for model, cost in models.items():
            if model in seen:
                counted += cost
            else:
                unseen[model] += cost
    return Reconciliation(sessions, tokencur, counted, dict(sorted(unseen.items())))
