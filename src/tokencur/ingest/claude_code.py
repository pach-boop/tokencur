"""Ingest usage records from local Claude Code session logs.

Claude Code writes one JSONL transcript per session under
``~/.claude/projects/<workspace>/<session-id>.jsonl``. Each assistant
message line carries a ``message.usage`` object with token counts.

Privacy: this module reads usage metadata only (tokens, model,
timestamps). It never extracts message content.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

# Re-exported: ``tokencur.ingest.claude_code.UsageRecord`` predates
# ``tokencur.records`` and stays importable from here.
from tokencur.records import UsageRecord

#: Claude Code logs client-side placeholder messages (API-error stubs,
#: interrupted turns) under this sentinel model, with all-zero usage.
#: They are not API traffic and must not surface as unpriced rows.
SYNTHETIC_MODEL = "<synthetic>"


def iter_usage_records(root: Path) -> Iterator[UsageRecord]:
    """Yield one UsageRecord per assistant message under ``root``.

    Records are deduplicated on (request id, message id): streaming can
    log one message across several lines, and resuming a session can
    re-copy past messages into a new file under a new session id — the
    same API request must never be counted twice. The first line seen
    names the session and workspace; the token counts are each field's
    largest value across the message's lines, because streamed counts
    only grow and an early line can hold a partial output count.
    Lines that are not valid JSON or carry no usage data are skipped,
    as are synthetic placeholder messages (see ``SYNTHETIC_MODEL``).
    """
    messages: dict[str, UsageRecord] = {}
    for path in sorted(root.rglob("*.jsonl")):
        workspace = path.parent.name
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                record = _parse_line(line, workspace)
                if record is None:
                    continue
                first = messages.get(record.record_id)
                messages[record.record_id] = (
                    record if first is None else _final_counts(first, record)
                )
    yield from messages.values()


_COUNTS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_5m_tokens",
    "cache_write_1h_tokens",
)


def _final_counts(first: UsageRecord, later: UsageRecord) -> UsageRecord:
    """``first``, with each token count raised to ``later``'s if larger."""
    return replace(
        first, **{f: max(getattr(first, f), getattr(later, f)) for f in _COUNTS}
    )


def _parse_line(line: str, workspace: str) -> UsageRecord | None:
    try:
        entry = json.loads(line)
    except json.JSONDecodeError:
        return None
    if entry.get("type") != "assistant":
        return None
    message = entry.get("message") or {}
    if message.get("model") == SYNTHETIC_MODEL:
        return None
    usage = message.get("usage")
    if not usage:
        return None

    key = (
        entry.get("requestId", ""),
        message.get("id") or entry.get("uuid", ""),
    )
    write_5m, write_1h = _cache_writes(usage)
    return UsageRecord(
        timestamp=entry.get("timestamp", ""),
        workspace=workspace,
        session_id=entry.get("sessionId", ""),
        model=message.get("model", "unknown"),
        input_tokens=usage.get("input_tokens", 0) or 0,
        output_tokens=usage.get("output_tokens", 0) or 0,
        cache_read_tokens=usage.get("cache_read_input_tokens", 0) or 0,
        cache_write_5m_tokens=write_5m,
        cache_write_1h_tokens=write_1h,
        source="claude-code",
        # The dedup key above is already the API request's identity.
        record_id=f"{key[0]}:{key[1]}",
    )


def _cache_writes(usage: dict) -> tuple[int, int]:
    """Split cache writes by TTL; they are priced differently (1.25x vs 2x).

    Older log formats only report the total ``cache_creation_input_tokens``.
    Those are attributed to the 5-minute tier — Claude Code's default TTL —
    which slightly underestimates cost when 1h writes were present.
    """
    breakdown = usage.get("cache_creation")
    if breakdown:
        return (
            breakdown.get("ephemeral_5m_input_tokens", 0) or 0,
            breakdown.get("ephemeral_1h_input_tokens", 0) or 0,
        )
    return usage.get("cache_creation_input_tokens", 0) or 0, 0
