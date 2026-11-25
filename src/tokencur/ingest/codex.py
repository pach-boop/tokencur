"""Ingest usage records from local OpenAI Codex CLI session logs.

Codex writes one rollout JSONL per session under
``~/.codex/sessions/<yyyy>/<mm>/<dd>/rollout-*.jsonl``. Token usage
arrives as ``event_msg`` lines with a ``token_count`` payload whose
``info.last_token_usage`` reports the most recent model call; the
active model and working directory come from ``session_meta`` /
``turn_context`` lines.
Only usage metadata is kept — never message content.

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

What each rollout gives is kept in the scan cache and reused while the
file is unchanged (``tokencur.ingest.cache``, ADR 0019).
"""

from __future__ import annotations

import functools
import sys
from collections.abc import Iterator
from dataclasses import astuple, dataclass, field
from pathlib import Path

from tokencur import records as records_module
from tokencur.ingest import cache
from tokencur.ingest import fields as fields_module
from tokencur.ingest import identity as identity_module
from tokencur.ingest import stats as stats_module
from tokencur.ingest.fields import Malformed, count, entry, obj, text
from tokencur.ingest.identity import content_key, fingerprint
from tokencur.ingest.stats import ScanStats
from tokencur.records import UsageRecord

_INTERESTING = ('"token_count"', '"session_meta"', '"turn_context"')


def iter_usage_records(
    root: Path, stats: ScanStats | None = None
) -> Iterator[UsageRecord]:
    """Yield one UsageRecord per model call reported by ``token_count``.

    A forked session can copy earlier reports into a new rollout. A call
    is recognised by its content key, the event timestamp plus the raw
    usage as logged; a key already seen in an earlier file (in path,
    hence date, order) is a copy and is not counted again. ``stats``,
    when given, counts what the scan saw (see ``tokencur.ingest.stats``).
    """
    stats = stats if stats is not None else ScanStats()
    seen: set[str] = set()
    for part in cache.file_parts(root, _code_key(), _read_file, _FilePart.from_json):
        stats.add_file(part.stats)
        for record in part.records:
            key = content_key(record.record_id)
            if key in seen:
                continue
            seen.add(key)
            yield record


@dataclass
class _FilePart:
    """What one rollout gives a scan: its calls in log order and what the
    reading saw. Joining the parts in path order, counting each call the
    first time its content key shows up, gives what reading every file in
    that order gives. So a part can be kept in the scan cache and reused
    while its file is unchanged (ADR 0019).
    """

    records: list[UsageRecord] = field(default_factory=list)
    stats: ScanStats = field(default_factory=ScanStats)

    def to_json(self) -> dict:
        return {
            "records": [astuple(r) for r in self.records],
            **self.stats.file_counts(),
        }

    @classmethod
    def from_json(cls, kept: dict) -> _FilePart:
        return cls(
            records=[UsageRecord(*values) for values in kept["records"]],
            stats=ScanStats.from_file_counts(kept),
        )


@functools.cache
def _code_key() -> str:
    """The scan cache's key: the code that decides what a rollout gives."""
    return cache.code_key(
        (
            sys.modules[__name__],
            cache,
            fields_module,
            identity_module,
            records_module,
            stats_module,
        )
    )


def _read_file(path: Path) -> _FilePart:
    """Read one rollout's lines into its part."""
    part = _FilePart()
    part.records.extend(_parse_file(path, part.stats))
    return part


def _parse_file(path: Path, stats: ScanStats) -> Iterator[UsageRecord]:
    workspace = ""
    cwd = ""
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
                stats.saw_version(text(payload.get("cli_version")))
            elif line_entry.get("type") == "turn_context":
                model = text(payload.get("model")) or model
                cwd = text(payload.get("cwd")) or cwd
            elif payload.get("type") == "token_count":
                info = obj(payload.get("info"))
                usage = info.get("last_token_usage")
                if not usage:
                    continue  # rate-limit-only updates carry no usage
                stats.usage_lines += 1
                if not isinstance(usage, dict):
                    stats.malformed += 1
                    continue
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
                    stats.malformed += 1
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
                    cwd=cwd,
                )
                yield record
