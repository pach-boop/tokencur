"""Ingest usage records from local OpenAI Codex CLI session logs.

Codex writes one rollout JSONL per session under
``~/.codex/sessions/<yyyy>/<mm>/<dd>/rollout-*.jsonl``. Token usage
arrives as ``event_msg`` lines with a ``token_count`` payload whose
``info.last_token_usage`` reports the most recent model call; the
active model comes from ``session_meta`` / ``turn_context`` lines.
Only usage metadata is read — never message content.

Mapping notes:
- OpenAI's ``input_tokens`` includes cached tokens; the non-cached
  input is ``input_tokens - cached_input_tokens``.
- ``output_tokens`` already includes reasoning tokens
  (``reasoning_output_tokens`` is an informational subset).
- OpenAI bills no cache-write premium, so write tiers are zero.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

from tokencur.ingest.claude_code import UsageRecord

_INTERESTING = ('"token_count"', '"session_meta"', '"turn_context"')


def iter_usage_records(root: Path) -> Iterator[UsageRecord]:
    """Yield one UsageRecord per model call reported by ``token_count``."""
    for path in sorted(root.rglob("*.jsonl")):
        yield from _parse_file(path)


def _parse_file(path: Path) -> Iterator[UsageRecord]:
    workspace = ""
    session_id = ""
    model = "unknown"
    previous: tuple | None = None
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not any(marker in line for marker in _INTERESTING):
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload = entry.get("payload") or {}
            if not isinstance(payload, dict):
                continue

            if entry.get("type") == "session_meta":
                session_id = payload.get("id", "")
                cwd = payload.get("cwd") or ""
                workspace = Path(cwd).name if cwd else path.parent.name
                model = payload.get("model") or model
            elif entry.get("type") == "turn_context":
                model = payload.get("model") or model
            elif payload.get("type") == "token_count":
                usage = (payload.get("info") or {}).get("last_token_usage")
                if not usage:
                    continue  # rate-limit-only updates carry no usage
                cached = usage.get("cached_input_tokens", 0) or 0
                record = UsageRecord(
                    timestamp=entry.get("timestamp", ""),
                    workspace=workspace,
                    session_id=session_id,
                    model=model,
                    input_tokens=max((usage.get("input_tokens", 0) or 0) - cached, 0),
                    output_tokens=usage.get("output_tokens", 0) or 0,
                    cache_read_tokens=cached,
                    cache_write_5m_tokens=0,
                    cache_write_1h_tokens=0,
                    source="codex",
                )
                # Defensive: skip consecutive identical reports.
                key = (record.timestamp, record.input_tokens,
                       record.output_tokens, record.cache_read_tokens)
                if key == previous:
                    continue
                previous = key
                yield record
