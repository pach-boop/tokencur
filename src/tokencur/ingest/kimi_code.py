"""Ingest usage records from local Kimi Code session logs.

Kimi Code writes wire-protocol JSONL logs under
``~/.kimi-code/sessions/<workspace>/<session>/agents/<agent>/wire.jsonl``.
Token usage arrives as dedicated ``usage.record`` lines carrying the
model, an epoch-millisecond timestamp and per-turn token deltas
(``usageScope: "turn"``) — no message content is ever read.

The working directory comes from the ``cwd`` field of the session's
``state.json``, the only field tokencur takes from that file. Sessions
from before Kimi wrote it take the directory a sibling session recorded:
Kimi keeps one ``wd_<name>_<hash>`` directory per working directory.
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
    cwds = _session_cwds(root)
    for path in sorted(root.rglob("*.jsonl")):
        stats.files += 1
        workspace = next(
            (part for part in path.parts if part.startswith("wd_")),
            path.parent.name,
        )
        session_id = next(
            (part for part in path.parts if part.startswith("session_")), ""
        )
        cwd = cwds.get(_session_dir(path), "")
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
                        cwd=cwd,
                    )
                except Malformed:
                    stats.malformed += 1
                    continue
                yield record


def _session_dir(path: Path) -> Path | None:
    """The ``session_*`` directory a wire log sits in, if any."""
    for parent in path.parents:
        if parent.name.startswith("session_"):
            return parent
    return None


def _session_cwds(root: Path) -> dict[Path, str]:
    """Each session directory's working directory, where one is known.

    A session's own ``state.json`` wins. Otherwise, when the sessions
    that did record one under the same ``wd_`` directory all agree, that
    directory is the session's too.
    """
    own: dict[Path, str] = {}
    for state in root.rglob("state.json"):
        if state.parent.name.startswith("session_"):
            cwd = _state_cwd(state)
            if cwd:
                own[state.parent] = cwd
    shared: dict[Path, set[str]] = {}
    for session, cwd in own.items():
        shared.setdefault(session.parent, set()).add(cwd)
    cwds = dict(own)
    for session in root.rglob("session_*"):
        if session.is_dir() and session not in cwds:
            agreed = shared.get(session.parent, set())
            if len(agreed) == 1:
                cwds[session] = next(iter(agreed))
    return cwds


def _state_cwd(state: Path) -> str:
    """The ``cwd`` a session's state.json records, or "" if unreadable."""
    try:
        raw = state.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text(entry(raw).get("cwd"))


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
