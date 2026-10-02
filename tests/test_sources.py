import csv
import json

from tokencur import export, report, sources
from tokencur.ingest import claude_code

# 1M input tokens on claude-opus-4-8 ($5/MTok) = $5.00 per message.
FIVE_DOLLARS = {"input_tokens": 1_000_000, "output_tokens": 0}


def _write_session(path, request_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "type": "assistant",
                "sessionId": path.stem,
                "requestId": request_id,
                "timestamp": "2026-07-01T10:00:00.000Z",
                "message": {
                    "id": f"msg_{request_id}",
                    "model": "claude-opus-4-8",
                    "usage": FIVE_DOLLARS,
                },
            }
        ),
        encoding="utf-8",
    )


def _claude_logs(tmp_path):
    root = tmp_path / "projects"
    _write_session(root / "workspace-a" / "old.jsonl", "req_1")
    _write_session(root / "workspace-a" / "new.jsonl", "req_2")
    return root, ((root, claude_code.iter_usage_records),)


def test_history_survives_log_cleanup(tmp_path):
    root, logs = _claude_logs(tmp_path)
    ledger_path = tmp_path / "ledger.sqlite3"
    assert len(sources.load_records(logs, ledger_path)) == 2

    (root / "workspace-a" / "old.jsonl").unlink()  # agent cleanup

    assert len(sources.scan(logs)) == 1
    assert len(sources.load_records(logs, ledger_path)) == 2


def test_first_load_matches_a_direct_scan(tmp_path):
    """The ledger must never change a number — only stop numbers from
    disappearing."""
    _, logs = _claude_logs(tmp_path)

    loaded = sources.load_records(logs, tmp_path / "ledger.sqlite3")

    def key(r):
        return r.record_id

    assert sorted(loaded, key=key) == sorted(sources.scan(logs), key=key)


def test_report_total_does_not_shrink_after_cleanup(tmp_path, monkeypatch, capsys):
    root, logs = _claude_logs(tmp_path)
    monkeypatch.setattr(sources, "DEFAULT_SOURCES", logs)

    assert report.main(["report"]) == 0
    first = capsys.readouterr()
    (root / "workspace-a" / "old.jsonl").unlink()
    assert report.main(["report"]) == 0
    second = capsys.readouterr()

    assert "API-EQUIVALENT TOTAL (showback): $10.00" in first.out
    assert "API-EQUIVALENT TOTAL (showback): $10.00" in second.out
    assert "ledger: 2 records (2 new)" in first.err
    assert "ledger: 2 records (0 new)" in second.err


def test_report_errors_when_there_is_no_usage_anywhere(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sources, "DEFAULT_SOURCES", ((tmp_path / "missing", None),))

    assert report.main(["report"]) == 1
    assert "no usage" in capsys.readouterr().err


def test_export_writes_the_full_history(tmp_path, monkeypatch):
    root, logs = _claude_logs(tmp_path)
    monkeypatch.setattr(sources, "DEFAULT_SOURCES", logs)
    sources.load_records()  # first scan sees both sessions
    (root / "workspace-a" / "old.jsonl").unlink()
    out = tmp_path / "focus.csv"

    assert export.main(["export", str(out)]) == 0

    with out.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 2  # one input-token charge row per message
    assert sum(float(r["BilledCost"]) for r in rows) == 10.0
