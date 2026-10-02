"""Reproducible throughput benchmark on synthetic logs.

Usage:
    python scripts/benchmark.py [--messages N] [--files F]

Writes N synthetic Claude Code messages (usage metadata only, no
content) across F session files in a temporary directory, then times
each stage of the real pipeline on them: scan, first ledger write, an
idempotent rescan, ledger read, report and FOCUS export. Prints one row
per stage and the process's peak memory. Never touches the real logs or
the real ledger.
"""

from __future__ import annotations

import argparse
import json
import platform
import random
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokencur import ledger
from tokencur.export import export_csv
from tokencur.ingest import claude_code
from tokencur.report import summarize

MODELS = ("claude-opus-5-5", "claude-fable-5-1", "claude-sonnet-5", "claude-haiku-4-5")


def write_logs(root: Path, messages: int, files: int) -> int:
    """Synthetic sessions shaped like real transcripts; returns bytes written."""
    rng = random.Random(20261002)  # same logs on every run
    per_file = max(1, messages // files)
    written = 0
    for f in range(files):
        lines = []
        for m in range(
            per_file if f < files - 1 else messages - per_file * (files - 1)
        ):
            request = f"req_{f:05d}_{m:06d}"
            lines.append(
                json.dumps(
                    {
                        "type": "assistant",
                        "sessionId": f"session-{f:05d}",
                        "requestId": request,
                        "timestamp": f"2026-{1 + f % 9:02d}-{1 + m % 28:02d}T"
                        f"{m % 24:02d}:{m % 60:02d}:{m % 59:02d}.000Z",
                        "message": {
                            "id": f"msg_{request}",
                            "model": MODELS[m % len(MODELS)],
                            "content": [{"type": "text", "text": "[redacted]"}],
                            "usage": {
                                "input_tokens": rng.randint(1, 50),
                                "output_tokens": rng.randint(1, 4_000),
                                "cache_read_input_tokens": rng.randint(0, 900_000),
                                "cache_creation_input_tokens": rng.randint(0, 20_000),
                            },
                        },
                    }
                )
            )
        path = root / f"workspace-{f % 20:02d}" / f"session-{f:05d}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        text = "\n".join(lines) + "\n"
        path.write_text(text, encoding="utf-8")
        written += len(text.encode("utf-8"))
    return written


def peak_memory_mb() -> float | None:
    try:
        import resource
    except ImportError:  # Windows
        return None
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--messages", type=int, default=100_000)
    parser.add_argument("--files", type=int, default=500)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        root, db, csv_out = (
            Path(tmp) / "logs",
            Path(tmp) / "ledger.sqlite3",
            Path(tmp) / "focus.csv",
        )
        size = write_logs(root, args.messages, args.files)
        rows: list[tuple[str, float, str]] = []

        def stage(name, fn, detail=lambda result: ""):
            start = time.perf_counter()
            result = fn()
            rows.append((name, time.perf_counter() - start, detail(result)))
            return result

        records = stage(
            "scan (parse logs)",
            lambda: list(claude_code.iter_usage_records(root)),
            lambda r: f"{len(r):,} records from {size / 1e6:,.0f} MB",
        )
        stage(
            "ledger write (first run)",
            lambda: ledger.record(records, db),
            lambda n: f"{n:,} new",
        )
        stage(
            "ledger rescan (idempotent)",
            lambda: ledger.record(records, db),
            lambda n: f"{n:,} new",
        )
        history = stage(
            "ledger read",
            lambda: ledger.read(db),
            lambda r: f"{db.stat().st_size / 1e6:,.0f} MB file",
        )
        stage("report", lambda: summarize(history))
        stage(
            "FOCUS export",
            lambda: export_csv(history, csv_out),
            lambda n: f"{n:,} rows, {csv_out.stat().st_size / 1e6:,.0f} MB",
        )

    print(
        f"tokencur benchmark — {args.messages:,} messages, Python {platform.python_version()}, "
        f"{platform.machine()} {platform.system()}"
    )
    for name, seconds, detail in rows:
        print(f"  {name:<28}{seconds:>8.2f} s   {detail}")
    peak = peak_memory_mb()
    print(f"  peak memory {'n/a' if peak is None else f'{peak:,.0f} MB'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
