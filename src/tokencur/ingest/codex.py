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
- Codex re-sends an unchanged report under a new timestamp (alongside
  rate-limit updates). Each event also carries the session's running
  ``total_token_usage``; only an event that moves it is a new model
  call. Counting every event roughly doubled Codex usage; with this
  rule, the counted calls sum to Codex's own running total. Reports
  with no billable tokens (only ``total_tokens`` moved) are no call.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from tokencur.ingest.fields import Malformed, count, entry, obj, text
from tokencur.ingest.identity import fingerprint
from tokencur.records import UsageRecord

_INTERESTING = ('"token_count"', '"session_meta"', '"turn_context"')


def iter_usage_records(root: Path) -> Iterator[UsageRecord]:
    """Yield one UsageRecord per model call reported by ``token_count``."""
    for path in sorted(root.rglob("*.jsonl")):
        yield from _parse_file(path)


def _parse_file(path: Path) -> Iterator[UsageRecord]:
    workspace = ""
    session_id = ""
    model = "unknown"
    previous: object = None  # last running total (or report) seen
    # errors="replace": a corrupted byte spoils one line, not the scan.
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not any(marker in line for marker in _INTERESTING):
                continue
            line_entry = entry(line)
            payload = line_entry.get("payload")
            if not isinstance(payload, dict):
                continue

            if line_entry.get("type") == "session_meta":
                session_id = text(payload.get("id"))
                cwd = text(payload.get("cwd"))
                workspace = Path(cwd).name if cwd else path.parent.name
                model = text(payload.get("model")) or model
            elif line_entry.get("type") == "turn_context":
                model = text(payload.get("model")) or model
            elif payload.get("type") == "token_count":
                info = obj(payload.get("info"))
                usage = info.get("last_token_usage")
                if not usage:
                    continue  # rate-limit-only updates carry no usage
                if not isinstance(usage, dict):
                    continue  # malformed: not a usage object
                # A re-sent report leaves the running total where it was.
                # Logs without a total fall back to skipping a consecutive
                # report that is identical, timestamp included.
                total = info.get("total_token_usage")
                marker = (
                    total if total is not None else (line_entry.get("timestamp"), usage)
                )
                if marker == previous:
                    continue
                previous = marker
                try:
                    input_total = count(usage.get("input_tokens"))
                    cached = count(usage.get("cached_input_tokens"))
                    output = count(usage.get("output_tokens"))
                except Malformed:
                    continue
                if not (input_total or cached or output):
                    continue  # moved only total_tokens: no billable call
                timestamp = text(line_entry.get("timestamp"))
                record = UsageRecord(
                    timestamp=timestamp,
                    workspace=workspace,
                    session_id=session_id,
                    model=model,
                    input_tokens=max(input_total - cached, 0),
                    output_tokens=output,
                    cache_read_tokens=cached,
                    cache_write_5m_tokens=0,
                    cache_write_1h_tokens=0,
                    source="codex",
                    # token_count events carry no request id: the session
                    # plus the event timestamp identify the call.
                    record_id=f"{session_id}@{timestamp}#{fingerprint(usage)}",
                )
                yield record
