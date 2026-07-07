"""Ingest usage records from local Kimi Code session logs.

Kimi Code writes wire-protocol JSONL logs under
``~/.kimi-code/sessions/<workspace>/<session>/agents/<agent>/wire.jsonl``.
Token usage arrives as dedicated ``usage.record`` lines carrying the
model, an epoch-millisecond timestamp and per-turn token deltas
(``usageScope: "turn"``) — no message content is ever read.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from tokencur.ingest.claude_code import UsageRecord


def iter_usage_records(root: Path) -> Iterator[UsageRecord]:
    """Yield one UsageRecord per per-turn ``usage.record`` line."""
    for path in sorted(root.rglob("*.jsonl")):
        workspace = next(
            (part for part in path.parts if part.startswith("wd_")),
            path.parent.name,
        )
        session_id = next(
            (part for part in path.parts if part.startswith("session_")), ""
        )
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                if '"usage.record"' not in line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("type") != "usage.record":
                    continue
                if entry.get("usageScope") != "turn":
                    continue  # only per-turn deltas; avoid double counting
                usage = entry.get("usage") or {}
                yield UsageRecord(
                    timestamp=_iso(entry.get("time")),
                    workspace=workspace,
                    session_id=session_id,
                    model=entry.get("model", "unknown"),
                    input_tokens=usage.get("inputOther", 0) or 0,
                    output_tokens=usage.get("output", 0) or 0,
                    cache_read_tokens=usage.get("inputCacheRead", 0) or 0,
                    # Kimi reports one cache-creation figure; treated as the
                    # base (5m-tier) write rate.
                    cache_write_5m_tokens=usage.get("inputCacheCreation", 0) or 0,
                    cache_write_1h_tokens=0,
                    source="kimi-code",
                )


def _iso(epoch_ms: int | None) -> str:
    if not epoch_ms:
        return ""
    return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).isoformat()
