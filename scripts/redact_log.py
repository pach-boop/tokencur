"""Turn a real agent log into a fixture that is safe to publish.

Usage:
    python scripts/redact_log.py SOURCE INPUT OUTPUT [--start ISO8601]

SOURCE is claude-code, codex or kimi-code. The output keeps, from the
lines that carry usage (and Codex's session and turn lines), only an
allowlist of fields: the usage numbers, the model, request options,
line types, the agent version and the working directory. Everything
else is dropped, message content included. Every id becomes a stable pseudonym (the same id
always maps to the same pseudonym, so streaming and dedup behave as in
the original), paths become /home/dev/<pseudonym>, and timestamps shift
so the first lands on --start, keeping every interval.

Before writing, the script checks that the ingester reads exactly the
same usage from the redacted lines as from the original file.
tests/test_fixture_privacy.py then fails on any string in a published
fixture that is not one of the allowed shapes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokencur.ingest import claude_code, codex, kimi_code
from tokencur.records import parse_timestamp

SALT = "tokencur-fixture-v1"
INGESTERS = {"claude-code": claude_code, "codex": codex, "kimi-code": kimi_code}
# Fields of a usage object that may carry text; numbers are always kept.
_USAGE_TEXT = {"service_tier", "speed", "inference_geo", "type"}
_ENUMS = {
    "originator": {"codex_cli_rs"},
    "effort": {"minimal", "low", "medium", "high", "xhigh"},
}


def pseudonym(kind: str, value: object) -> object:
    if not isinstance(value, str) or not value:
        return value
    return f"{kind}_{hashlib.sha256((SALT + value).encode()).hexdigest()[:12]}"


def path_pseudonym(value: object) -> object:
    """A working directory as /home/dev/<pseudonym>: the same directory
    always maps to the same path, so attribution behaves as in the original."""
    if not isinstance(value, str) or not value:
        return None
    return f"/home/dev/{pseudonym('dir', value)}"


def _numbers(value: object) -> object:
    """A usage object reduced to numbers, nested objects and allowed text."""
    if isinstance(value, dict):
        return {
            k: _numbers(v)
            for k, v in value.items()
            if isinstance(v, int | float | dict | list) or k in _USAGE_TEXT
        }
    if isinstance(value, list):
        return [_numbers(v) for v in value]
    return value


class Redactor:
    def __init__(self, source: str, start: datetime, first: datetime | None):
        self.source = source
        self.shift = (start - first) if first else timedelta(0)

    def iso(self, value: object) -> object:
        moment = parse_timestamp(value) if isinstance(value, str) else None
        if moment is None:
            return None
        shifted = moment + self.shift
        return (
            shifted.strftime("%Y-%m-%dT%H:%M:%S.")
            + f"{shifted.microsecond // 1000:03d}Z"
        )

    def epoch_ms(self, value: object) -> object:
        if type(value) is not int:
            return None
        return value + int(self.shift.total_seconds() * 1000)

    def line(self, raw: str) -> dict | None:
        try:
            entry = json.loads(raw)
        except ValueError:
            return None
        if not isinstance(entry, dict):
            return None
        return getattr(self, self.source.replace("-", "_"))(entry)

    def claude_code(self, e: dict) -> dict | None:
        message = e.get("message")
        if e.get("type") != "assistant" or not isinstance(message, dict):
            return None
        if not isinstance(message.get("usage"), dict):
            return None
        return {
            "type": "assistant",
            "sessionId": pseudonym("session", e.get("sessionId")),
            "requestId": pseudonym("req", e.get("requestId")),
            "uuid": pseudonym("uuid", e.get("uuid")),
            "timestamp": self.iso(e.get("timestamp")),
            "version": e.get("version") if isinstance(e.get("version"), str) else None,
            "cwd": path_pseudonym(e.get("cwd")),
            "message": {
                "id": pseudonym("msg", message.get("id")),
                "type": "message",
                "role": "assistant",
                "model": message.get("model"),
                "content": [{"type": "text", "text": "[redacted]"}],
                "usage": _numbers(message["usage"]),
            },
        }

    def codex(self, e: dict) -> dict | None:
        kind, payload = e.get("type"), e.get("payload")
        if not isinstance(payload, dict):
            return None
        base = {"timestamp": self.iso(e.get("timestamp")), "type": kind}
        if kind == "session_meta":
            return {
                **base,
                "payload": {
                    "id": pseudonym("session", payload.get("id")),
                    "timestamp": self.iso(payload.get("timestamp")),
                    "cwd": path_pseudonym(payload.get("cwd")),
                    "cli_version": payload.get("cli_version"),
                    "originator": _allowed("originator", payload.get("originator")),
                },
            }
        if kind == "turn_context":
            return {
                **base,
                "payload": {
                    "model": payload.get("model"),
                    "cwd": path_pseudonym(payload.get("cwd")),
                    "effort": _allowed("effort", payload.get("effort")),
                },
            }
        if kind == "event_msg" and payload.get("type") == "token_count":
            info = payload.get("info")
            return {
                **base,
                "payload": {
                    "type": "token_count",
                    "info": _numbers(info) if isinstance(info, dict) else None,
                },
            }
        return None

    def kimi_code(self, e: dict) -> dict | None:
        if e.get("type") != "usage.record":
            return None
        return {
            "type": "usage.record",
            "time": self.epoch_ms(e.get("time")),
            "model": e.get("model"),
            "usageScope": e.get("usageScope"),
            "usage": _numbers(e.get("usage")),
        }


def _allowed(field: str, value: object) -> object:
    return value if value in _ENUMS[field] else None


def _first_moment(source: str, lines: list[str]) -> datetime | None:
    for raw in lines:
        try:
            e = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(e, dict):
            continue
        if source == "kimi-code" and type(e.get("time")) is int:
            return datetime.fromtimestamp(e["time"] / 1000, tz=UTC)
        moment = (
            parse_timestamp(e.get("timestamp"))
            if isinstance(e.get("timestamp"), str)
            else None
        )
        if moment:
            return moment
    return None


def usage_of(source: str, path: Path, layout: str, redacted: bool) -> list[tuple]:
    """What the ingester reads from one file: models, counts, options and
    where each call ran (the original's directories as their pseudonyms)."""
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / layout
        target.parent.mkdir(parents=True)
        shutil.copy(path, target)
        records = INGESTERS[source].iter_usage_records(Path(tmp))
        return sorted(
            (
                r.model,
                r.input_tokens,
                r.output_tokens,
                r.cache_read_tokens,
                r.cache_write_5m_tokens,
                r.cache_write_1h_tokens,
                r.price_modifiers,
                r.cwd if redacted else path_pseudonym(r.cwd) or "",
            )
            for r in records
        )


_LAYOUT = {
    "claude-code": "project/session.jsonl",
    "codex": "2026/01/01/rollout.jsonl",
    "kimi-code": "wd_x/session_x/agents/main/wire.jsonl",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", choices=sorted(INGESTERS))
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--start", default="2026-09-15T10:00:00Z")
    args = parser.parse_args()

    lines = args.input.read_text(encoding="utf-8", errors="replace").splitlines()
    start = parse_timestamp(args.start)
    redactor = Redactor(args.source, start, _first_moment(args.source, lines))
    kept = [r for r in (redactor.line(raw) for raw in lines) if r is not None]
    text = "\n".join(json.dumps(r, separators=(",", ":")) for r in kept) + "\n"

    with tempfile.TemporaryDirectory() as tmp:
        candidate = Path(tmp) / "redacted.jsonl"
        candidate.write_text(text, encoding="utf-8")
        before = usage_of(args.source, args.input, _LAYOUT[args.source], False)
        after = usage_of(args.source, candidate, _LAYOUT[args.source], True)
    if before != after:
        print("refusing to write: the redacted log reads differently", file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
    print(
        f"{args.output}: {len(kept)} lines, {len(after)} records, same usage as the original"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
