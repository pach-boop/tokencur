"""Ingest usage records from local Kimi Code session logs.

Kimi Code writes wire-protocol JSONL logs under
``~/.kimi-code/sessions/<workspace>/<session>/agents/<agent>/wire.jsonl``.
Token usage arrives as dedicated ``usage.record`` lines carrying the
model, an epoch-millisecond timestamp and per-turn token deltas
(``usageScope: "turn"``) — no message content is ever read.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from tokencur.ingest.fields import Malformed, count, entry, text
from tokencur.ingest.identity import fingerprint
from tokencur.ingest.stats import ScanStats
from tokencur.records import UsageRecord


def iter_usage_records(
    root: Path, stats: ScanStats | None = None
) -> Iterator[UsageRecord]:
    """Yield one UsageRecord per per-turn ``usage.record`` line.

    ``stats``, when given, counts what the scan saw (see
    ``tokencur.ingest.stats``).
    """
    stats = stats if stats is not None else ScanStats()
    for path in sorted(root.rglob("*.jsonl")):
        stats.files += 1
        workspace = next(
            (part for part in path.parts if part.startswith("wd_")),
            path.parent.name,
        )
        session_id = next(
            (part for part in path.parts if part.startswith("session_")), ""
        )
        agent = path.parent.name  # .../agents/<agent>/wire.jsonl
        # errors="replace": a corrupted byte spoils one line, not the scan.
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"usage.record"' not in line:
                    continue
                line_entry = entry(line)
                if line_entry.get("type") != "usage.record":
                    continue
                if line_entry.get("usageScope") != "turn":
                    continue  # only per-turn deltas; avoid double counting
                stats.usage_lines += 1
                usage = line_entry.get("usage") or {}
                if not isinstance(usage, dict):
                    stats.malformed += 1
                    continue
                try:
                    record = UsageRecord(
                        timestamp=_iso(line_entry.get("time")),
                        workspace=workspace,
                        session_id=session_id,
                        model=text(line_entry.get("model"), "unknown"),
                        input_tokens=count(usage.get("inputOther")),
                        output_tokens=count(usage.get("output")),
                        cache_read_tokens=count(usage.get("inputCacheRead")),
                        # Kimi reports one cache-creation figure; treated as the
                        # base (5m-tier) write rate.
                        cache_write_5m_tokens=count(usage.get("inputCacheCreation")),
                        cache_write_1h_tokens=0,
                        source="kimi-code",
                        record_id=(
                            f"{session_id}/{agent}@{line_entry.get('time')}"
                            f"#{fingerprint(usage)}"
                        ),
                    )
                except Malformed:
                    stats.malformed += 1
                    continue
                yield record


# Epoch milliseconds a log can plausibly carry: after 2000, before 2200.
_EPOCH_MS_RANGE = (946_684_800_000, 7_258_118_400_000)


def _iso(epoch_ms: object) -> str:
    """The record's UTC timestamp, or "" (undated) when ``time`` is unusable."""
    if type(epoch_ms) is not int:
        return ""
    low, high = _EPOCH_MS_RANGE
    if not low <= epoch_ms < high:
        return ""
    return datetime.fromtimestamp(epoch_ms / 1000, tz=UTC).isoformat()
