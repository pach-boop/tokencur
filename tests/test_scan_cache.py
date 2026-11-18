"""The scan cache: a rescan reads only the files that changed (ADR 0019)."""

import json
import os
import time
from dataclasses import asdict
from pathlib import Path

import pytest

from tokencur.ingest import cache, claude_code
from tokencur.ingest.stats import ScanStats

HOUR_AGO = time.time() - 3600
SECRET = "words a person typed"


def _line(**fields) -> str:
    return json.dumps(fields) + "\n"


def _transcript(session: str, request: str, output: int) -> str:
    return "".join(
        [
            _line(
                type="user",
                sessionId=session,
                timestamp="2026-11-17T10:00:00.000Z",
                message={"role": "user", "content": SECRET},
            ),
            _line(
                type="assistant",
                sessionId=session,
                requestId=request,
                timestamp="2026-11-17T10:00:01.000Z",
                cwd="/work",
                message={
                    "id": f"msg_{request}",
                    "model": "claude-opus-4-8",
                    "stop_reason": "end_turn",
                    "content": [{"type": "text", "text": SECRET}],
                    "usage": {"input_tokens": 100, "output_tokens": output},
                },
            ),
            _line(
                type="assistant",
                sessionId=session,
                requestId=f"{request}_limit",
                timestamp="2026-11-17T10:00:02.000Z",
                error="rate_limit",
                message={"id": f"msg_{request}_limit", "model": "<synthetic>"},
                quotaLimits={
                    "rateLimitType": "five_hour",
                    "status": "rejected",
                    "resetsAt": 1795000000,
                },
            ),
            _line(
                type="cost-state",
                sessionId=session,
                startTime=1794996000000,
                modelUsage={"claude-opus-4-8": {"costUSD": 0.5, "outputTokens": 900}},
            ),
        ]
    )


def _write(path, content: str, mtime: float = HOUR_AGO) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    os.utime(path, (mtime, mtime))


@pytest.fixture
def logs(tmp_path):
    root = tmp_path / "projects"
    _write(root / "ws" / "a.jsonl", _transcript("a", "req_1", 10))
    _write(root / "ws" / "b.jsonl", _transcript("b", "req_2", 20))
    return root


@pytest.fixture
def reads(monkeypatch):
    """The transcripts read in full, in order."""
    read, seen = claude_code._read_file, []

    def spy(path, workspace):
        seen.append(path.name)
        return read(path, workspace)

    monkeypatch.setattr(claude_code, "_read_file", spy)
    return seen


def _records(root):
    return list(claude_code.iter_usage_records(root))


def _scan(root):
    stats = ScanStats()
    records = list(claude_code.iter_usage_records(root, stats))
    stubs = claude_code.limit_stubs(root)
    return records, asdict(stats), claude_code.session_counters(root), stubs


def test_an_unchanged_file_is_not_read_again(logs, reads):
    first = _scan(logs)
    assert reads == ["a.jsonl", "b.jsonl"]
    assert _scan(logs) == first
    assert reads == ["a.jsonl", "b.jsonl"]  # every part came from the cache


def test_the_cache_gives_what_reading_the_files_gives(logs, monkeypatch):
    cached = _scan(logs)  # filled
    assert _scan(logs) == cached  # from the cache
    monkeypatch.setenv("TOKENCUR_CACHE", "off")
    records, stats, counters, stubs = _scan(logs)
    assert (records, stats, counters, stubs) == cached
    assert [r.output_tokens for r in records] == [10, 20]
    assert counters.prompts == {"a": 1, "b": 1}
    assert len(stubs.hits) == 2 and stubs.stubs == 2


def test_a_grown_file_is_read_again(logs, reads):
    _scan(logs)
    path = logs / "ws" / "b.jsonl"
    _write(path, path.read_text(encoding="utf-8") + _transcript("b", "req_3", 30))
    records, *_ = _scan(logs)
    assert reads == ["a.jsonl", "b.jsonl", "b.jsonl"]
    assert [r.record_id for r in records] == [
        "req_1:msg_req_1",
        "req_2:msg_req_2",
        "req_3:msg_req_3",
    ]


def test_a_file_written_just_now_is_not_kept(logs, reads):
    _write(logs / "ws" / "b.jsonl", _transcript("b", "req_2", 20), mtime=time.time())
    _records(logs)
    _records(logs)
    assert reads.count("a.jsonl") == 1
    assert reads.count("b.jsonl") == 2  # same size, maybe the same clock tick


def test_a_deleted_file_is_forgotten(logs, tmp_path):
    _scan(logs)
    (logs / "ws" / "a.jsonl").unlink()
    records, *_ = _scan(logs)
    assert [r.session_id for r in records] == ["b"]
    kept = cache.FileCache(logs, claude_code._code_key())
    assert kept.get(logs / "ws" / "a.jsonl", os.stat(logs / "ws" / "b.jsonl")) is None


def test_other_reading_code_discards_the_cache(logs, reads, monkeypatch):
    _scan(logs)
    monkeypatch.setattr(claude_code, "_code_key", lambda: "other code")
    _scan(logs)
    assert reads == ["a.jsonl", "b.jsonl"] * 2


def test_the_cache_keeps_no_message_content(logs):
    _scan(logs)
    kept = Path(os.environ["TOKENCUR_CACHE"]).read_bytes()
    assert b"msg_req_1" in kept  # it holds the parts...
    assert SECRET.encode() not in kept  # ...and no text


def test_off_turns_the_cache_off(logs, reads, monkeypatch, tmp_path):
    monkeypatch.setenv("TOKENCUR_CACHE", "off")
    _records(logs)
    _records(logs)
    assert reads == ["a.jsonl", "b.jsonl"] * 2
    assert not list(tmp_path.glob("**/*.sqlite3"))


def test_an_unreadable_cache_only_means_a_full_read(
    logs, reads, tmp_path, monkeypatch, capsys
):
    broken = tmp_path / "broken.sqlite3"
    broken.write_text("not a database", encoding="utf-8")
    monkeypatch.setenv("TOKENCUR_CACHE", str(broken))
    records, *_ = _scan(logs)
    assert [r.session_id for r in records] == ["a", "b"]
    assert "scan cache" in capsys.readouterr().err
