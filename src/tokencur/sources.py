"""Where usage records come from — and the one loader every command uses.

``DEFAULT_SOURCES`` lists the local logs tokencur knows how to read.
``load_records()`` scans them, keeps what it finds in the ledger and
returns the ledger's full history, so a command's totals never shrink
when an agent deletes old logs (see ``tokencur.ledger``).
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator
from pathlib import Path

from tokencur import ledger
from tokencur.ingest import claude_code, codex, kimi_code
from tokencur.records import UsageRecord

Source = tuple[Path, Callable[[Path], Iterator[UsageRecord]]]

DEFAULT_SOURCES: tuple[Source, ...] = (
    (Path.home() / ".claude" / "projects", claude_code.iter_usage_records),
    (Path.home() / ".codex" / "sessions", codex.iter_usage_records),
    (Path.home() / ".kimi-code" / "sessions", kimi_code.iter_usage_records),
)


def scan(sources: tuple[Source, ...] | None = None) -> list[UsageRecord]:
    """Every record the logs on disk still hold."""
    records: list[UsageRecord] = []
    for root, iter_records in DEFAULT_SOURCES if sources is None else sources:
        if root.exists():
            records.extend(iter_records(root))
    return records


def load_records(
    sources: tuple[Source, ...] | None = None, ledger_path: Path | None = None
) -> list[UsageRecord]:
    """Scan ``sources``, keep the result in the ledger, return full history.

    Prints one status line to stderr — how many records the ledger holds
    and how many this scan added — so a CLI run shows where its numbers
    come from.
    """
    path = ledger_path or ledger.default_path()
    added = ledger.record(scan(sources), path)
    history = ledger.read(path)
    print(f"ledger: {len(history)} records ({added} new) — {path}", file=sys.stderr)
    return history
