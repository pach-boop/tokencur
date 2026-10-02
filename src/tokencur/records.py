"""The record type every layer shares.

An ingester turns one source's log lines into ``UsageRecord`` values;
the ledger stores them; pricing, the FOCUS normalizer, reports and
recommendations read them. The type lives here, not inside any one
ingester, so no layer depends on how a particular agent logs.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime


# slots: no per-instance __dict__. A full history is held in memory as
# a list of these; most of its size is the strings themselves.
@dataclass(frozen=True, slots=True)
class UsageRecord:
    """Token usage for one billable model call, before pricing."""

    timestamp: str  # ISO 8601, as logged
    workspace: str  # project directory the session ran in
    session_id: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_5m_tokens: int
    cache_write_1h_tokens: int
    #: Which ingester produced the record. Defaults to Claude Code, the
    #: first source, so records built before multi-source support keep
    #: their meaning; every ingester sets it explicitly.
    source: str = "claude-code"
    #: Identity of the usage event across scans, built by each ingester
    #: from raw source fields (see ``tokencur.ingest.identity``). The
    #: ledger deduplicates on (source, record_id).
    record_id: str = ""
    #: Request options that change the call's price, "+"-joined in
    #: alphabetical order: "batch" (Batch API), "fast" (fast mode), "us"
    #: (US-only inference). Empty for a standard call. See
    #: ``tokencur.pricing.MODIFIER_FACTORS``.
    price_modifiers: str = ""

    @property
    def date(self) -> str:
        """UTC calendar day of the call, as logged (``YYYY-MM-DD``)."""
        return self.timestamp[:10]


@dataclass(frozen=True, slots=True)
class BilledCharge:
    """Money a provider billed for one resource over one period.

    A UsageRecord is valued by tokencur at list price (showback); a charge
    is what the provider actually took: real outlay, never mixed with
    showback (see ADR 0009).
    """

    source: str  # the ingester, e.g. "runpod"
    record_id: str  # identity within the source, stable across imports
    period_start: str  # ISO 8601 UTC, inclusive
    period_end: str  # ISO 8601 UTC, exclusive
    provider: str  # FOCUS ProviderName
    service: str  # FOCUS ServiceName
    resource_id: str  # what was billed, e.g. a pod id
    resource_type: str  # FOCUS ResourceType, e.g. "GPU pod"
    quantity: float  # usage billed, in `unit` (0 when only storage was billed)
    unit: str  # FOCUS ConsumedUnit, e.g. "Hours"
    amount_usd: float  # what was billed
    detail: str = ""  # a note for people, e.g. "50 GB disk"


def parse_timestamp(timestamp: str) -> datetime | None:
    """A logged timestamp as an aware UTC datetime; None if missing or malformed.

    None means *undated*: callers surface it and never substitute a date,
    the same way unpriced usage is never valued at $0.
    """
    if not timestamp:
        return None
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def in_period(
    records: Iterable, since: date | None, until: date | None, when=None
) -> list:
    """Records whose UTC day is in ``[since, until)``; either bound optional.

    Billing convention: ``until`` is the first day *not* included. With a
    bound set, undated records are left out, since no period can hold
    them; with no bounds, every record is kept, undated ones included.
    ``when`` picks the timestamp (default ``record.timestamp``; a billed
    charge uses its ``period_start``).
    """
    if since is None and until is None:
        return list(records)
    when = when or (lambda record: record.timestamp)
    kept = []
    for record in records:
        moment = parse_timestamp(when(record))
        if moment is None:
            continue
        day = moment.date()
        if (since is None or day >= since) and (until is None or day < until):
            kept.append(record)
    return kept
