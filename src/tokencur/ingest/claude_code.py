"""Ingest usage records from local Claude Code session logs.

Claude Code writes one JSONL transcript per session under
``~/.claude/projects/<workspace>/<session-id>.jsonl``. Each assistant
message line carries a ``message.usage`` object with token counts.

Privacy: this module reads usage metadata only (tokens, model,
timestamps). It never extracts message content.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

# Re-exported: ``tokencur.ingest.claude_code.UsageRecord`` predates
# ``tokencur.records`` and stays importable from here.
from tokencur.ingest.fields import Malformed, count, entry, obj, text
from tokencur.ingest.stats import ScanStats
from tokencur.records import UsageRecord

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
    names the session and workspace; the token counts are each field's
    largest value across the message's lines, because streamed counts
    only grow and an early line can hold a partial output count.
    Lines that are not valid JSON or carry no usage data are skipped,
    as are synthetic placeholder messages (see ``SYNTHETIC_MODEL``) and
    malformed usage (see ``tokencur.ingest.fields``). ``stats``, when
    given, counts what the scan saw (see ``tokencur.ingest.stats``).
    """
    stats = stats if stats is not None else ScanStats()
    messages: dict[str, UsageRecord] = {}
    for path in sorted(root.rglob("*.jsonl")):
        stats.files += 1
        workspace = _workspace(root, path)
        # errors="replace": a corrupted byte spoils one line, not the scan.
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    record = _parse_line(line, workspace, stats)
                except Malformed:
                    stats.malformed += 1
                    continue
                if record is None:
                    continue
                first = messages.get(record.record_id)
                messages[record.record_id] = (
                    record if first is None else _final_counts(first, record)
                )
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
    """``first``, with each token count raised to ``later``'s if larger."""
    return replace(
        first, **{f: max(getattr(first, f), getattr(later, f)) for f in _COUNTS}
    )


def _parse_line(line: str, workspace: str, stats: ScanStats) -> UsageRecord | None:
    line_entry = entry(line)
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
