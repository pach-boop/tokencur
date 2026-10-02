"""The fixtures derived from real logs must hold nothing personal.

tests/fixtures/logs-real was produced by scripts/redact_log.py from the
maintainer's own agent logs. Every string in those files, keys included,
must be one of the shapes the redaction emits: a pseudonym, a shifted
timestamp, an agent version, a model id or a known enum value. Anything
else fails the test, so a future regeneration cannot leak a path, a
name or a line of conversation.
"""

import json
import re
from pathlib import Path

REAL = Path(__file__).parent / "fixtures" / "logs-real"

ALLOWED = [
    re.compile(r"(session|req|uuid|msg|dir)_[0-9a-f]{12}"),  # pseudonyms
    re.compile(r"/home/dev/dir_[0-9a-f]{12}"),  # pseudonymous cwd
    re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z"),  # shifted times
    re.compile(r"\d+\.\d+\.\d+"),  # agent versions
    re.compile(r"(claude|gpt|o\d|codex|gemini)[a-z0-9.\-]*"),  # model ids
    re.compile(r"moonshot-ai/kimi[a-z0-9.\-]*|kimi[a-z0-9.\-]*|<synthetic>"),
]
ENUMS = {
    "[redacted]",
    "assistant",
    "message",
    "text",
    "standard",
    "fast",
    "us",
    "batch",
    "not_available",
    "global",
    "turn",
    "usage.record",
    "event_msg",
    "token_count",
    "session_meta",
    "turn_context",
    "codex_cli_rs",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
}
KEY = re.compile(r"[A-Za-z0-9_]+")


def _strings(value, keys):
    if isinstance(value, dict):
        for k, v in value.items():
            keys.add(k)
            yield from _strings(v, keys)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v, keys)
    elif isinstance(value, str):
        yield value


def test_real_fixtures_exist():
    assert {p.parts[len(REAL.parts)] for p in REAL.rglob("*.jsonl")} == {
        "claude-code",
        "codex",
        "kimi-code",
    }


def test_every_string_in_a_real_fixture_has_an_allowed_shape():
    bad, keys = set(), set()
    for path in REAL.rglob("*.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            for text in _strings(json.loads(line), keys):
                if text not in ENUMS and not any(p.fullmatch(text) for p in ALLOWED):
                    bad.add(text)
    assert not bad, f"strings a redaction must not publish: {sorted(bad)[:10]}"
    assert all(KEY.fullmatch(k) for k in keys), sorted(
        k for k in keys if not KEY.fullmatch(k)
    )


def test_no_path_or_name_survives_in_the_raw_bytes():
    for path in REAL.rglob("*.jsonl"):
        raw = path.read_text(encoding="utf-8").lower()
        assert "pach" not in raw
        assert re.sub(r"/home/dev/", "", raw).find("/home/") == -1
