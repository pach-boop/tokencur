"""Plan limits from Claude Code's own quota metadata (ADR 0014)."""

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime

import pytest

from tokencur import ledger
from tokencur.ingest import claude_code
from tokencur.records import LimitHit


def _t(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def _hit(when: str, kind: str, resets: str, request: str = "req") -> LimitHit:
    return LimitHit(
        source="claude-code",
        record_id=f"{request}:{when}",
        timestamp=f"{when}Z",
        session_id="s",
        kind=kind,
        resets_at=f"{resets}Z",
    )


def _stub(when: str, kind: str, resets: datetime, status="rejected", request="req_1"):
    """The line Claude Code logs when a plan limit turns a request away."""
    return json.dumps(
        {
            "type": "assistant",
            "timestamp": f"{when}Z",
            "requestId": request,
            "sessionId": "sess",
            "error": "rate_limit",
            "quotaLimits": {
                "status": status,
                "rateLimitType": kind,
                "resetsAt": int(resets.timestamp()),
            },
            "message": {
                "id": f"msg_{request}",
                "model": "<synthetic>",
                "content": [{"type": "text", "text": "[redacted]"}],
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
        }
    )


# Ingest


def test_a_refusal_is_read_from_metadata_once_per_request(tmp_path):
    resets = _t("2026-10-14T11:00:00")
    log = tmp_path / "project" / "sess.jsonl"
    log.parent.mkdir()
    log.write_text(
        "\n".join(
            [
                _stub("2026-10-14T03:46:59.434", "seven_day", resets),
                _stub("2026-10-14T03:46:59.434", "seven_day", resets),  # copied
                _stub("2026-10-14T03:49:39.617", "seven_day", resets, request="req_2"),
                _stub("2026-10-14T04:00:00", "seven_day", resets, status="allowed"),
                '{"quotaLimits": "not an object"}',
                "not json, mentions quotaLimits",
            ]
        ),
        encoding="utf-8",
    )

    hits = list(claude_code.iter_limit_hits(tmp_path))

    assert [(h.timestamp, h.kind, h.resets_at) for h in hits] == [
        ("2026-10-14T03:46:59.434Z", "seven_day", "2026-10-14T11:00:00Z"),
        ("2026-10-14T03:49:39.617Z", "seven_day", "2026-10-14T11:00:00Z"),
    ]
    assert {h.session_id for h in hits} == {"sess"}


@pytest.mark.parametrize("resets", [None, -1, "soon", True, 1e20])
def test_a_refusal_without_a_usable_reset_is_skipped(tmp_path, resets):
    line = json.loads(_stub("2026-10-14T03:46:59", "five_hour", _t("2026-10-14T05:00")))
    line["quotaLimits"]["resetsAt"] = resets
    (tmp_path / "s.jsonl").write_text(json.dumps(line), encoding="utf-8")

    assert list(claude_code.iter_limit_hits(tmp_path)) == []


def test_the_usage_scan_still_skips_the_stub(tmp_path):
    (tmp_path / "s.jsonl").write_text(
        _stub("2026-10-14T03:46:59", "seven_day", _t("2026-10-14T11:00")),
        encoding="utf-8",
    )

    assert list(claude_code.iter_usage_records(tmp_path)) == []


# Ledger


def test_limit_hits_round_trip_and_a_rescan_adds_nothing(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    hits = [
        _hit("2026-10-07T00:43:45", "seven_day", "2026-10-07T11:00:00"),
        _hit("2026-10-07T19:17:40", "five_hour", "2026-10-07T21:00:00"),
    ]

    assert ledger.record_limit_hits(hits, path) == 2
    assert ledger.record_limit_hits(hits, path) == 0
    assert ledger.read_limit_hits(path) == hits
    assert ledger.record_limit_hits([], tmp_path / "none.sqlite3") == 0
    assert not (tmp_path / "none.sqlite3").exists()


def test_schema_6_gains_the_limit_hits_table(tmp_path, capsys):
    path = tmp_path / "ledger.sqlite3"
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute(ledger._V2_USAGE)
        conn.execute(ledger._V2_SUPERSEDED)
        conn.execute("PRAGMA user_version = 2")
    with closing(sqlite3.connect(path)) as conn:
        for step in (2, 3, 4, 5):
            ledger._MIGRATIONS[step](conn)

    assert ledger.read_limit_hits(path) == []
    assert "schema 7: added the limit_hits table" in capsys.readouterr().err
    assert (tmp_path / "ledger.sqlite3.schema-6.bak").exists()
