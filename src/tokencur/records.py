"""The record type every layer shares.

An ingester turns one source's log lines into ``UsageRecord`` values;
the ledger stores them; pricing, the FOCUS normalizer, reports and
recommendations read them. The type lives here, not inside any one
ingester, so no layer depends on how a particular agent logs.
"""

from __future__ import annotations

from dataclasses import dataclass


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

    @property
    def date(self) -> str:
        """UTC calendar day of the call, as logged (``YYYY-MM-DD``)."""
        return self.timestamp[:10]
