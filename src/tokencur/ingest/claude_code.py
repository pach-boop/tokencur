"""Ingest usage records from local Claude Code session logs.

Claude Code writes one JSONL transcript per session under
``~/.claude/projects/<workspace>/<session-id>.jsonl``. Each assistant
message line carries a ``message.usage`` object with token counts.

Privacy: this module reads usage metadata only (tokens, model,
timestamps). It never extracts message content.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True)
class UsageRecord:
    """Token usage for one assistant message, before pricing."""

    timestamp: str  # ISO 8601, as logged
    workspace: str  # project directory the session ran in
    session_id: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_5m_tokens: int
    cache_write_1h_tokens: int

    @property
    def date(self) -> str:
        return self.timestamp[:10]


def iter_usage_records(root: Path) -> Iterator[UsageRecord]:
    """Yield one UsageRecord per assistant message under ``root``.

    Streaming can log the same assistant message across several lines,
    so records are deduplicated on (session id, request id, message id).
    Lines that are not valid JSON or carry no usage data are skipped.
    """
    seen: set[tuple[str, str, str]] = set()
    for path in sorted(root.rglob("*.jsonl")):
        workspace = path.parent.name
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                record = _parse_line(line, workspace, seen)
                if record is not None:
                    yield record


def _parse_line(
    line: str, workspace: str, seen: set[tuple[str, str, str]]
) -> UsageRecord | None:
    try:
        entry = json.loads(line)
    except json.JSONDecodeError:
        return None
    if entry.get("type") != "assistant":
        return None
    message = entry.get("message") or {}
    usage = message.get("usage")
    if not usage:
        return None

    key = (
        entry.get("sessionId", ""),
        entry.get("requestId", ""),
        message.get("id") or entry.get("uuid", ""),
    )
    if key in seen:
        return None
    seen.add(key)

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
