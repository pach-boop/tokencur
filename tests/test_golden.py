"""Golden tests: real-shaped agent logs in, exact records and FOCUS CSV out.

``tests/fixtures/logs`` holds synthetic logs with the key structure of
each agent's real logs (every value invented, every content field
"[redacted]") and the cases that broke or nearly broke tokencur:
streamed messages, resumed sessions, synthetic stubs, old cache formats,
Codex re-sends and zero-billable reports, Kimi cumulative records,
unpriced models and malformed lines.

Any change to what the ingesters or the normalizer produce shows up as a
diff in ``tests/fixtures/golden``. When the change is intended, rewrite
the golden files and review that diff in the pull request:

    TOKENCUR_UPDATE_GOLDEN=1 pytest tests/test_golden.py
"""

import json
import os
from dataclasses import asdict
from pathlib import Path

from tokencur.export import export_csv
from tokencur.ingest import claude_code, codex, kimi_code

FIXTURES = Path(__file__).parent / "fixtures"
UPDATE = os.environ.get("TOKENCUR_UPDATE_GOLDEN") == "1"


def fixture_records():
    logs = FIXTURES / "logs"
    return [
        *claude_code.iter_usage_records(logs / "claude-code"),
        *codex.iter_usage_records(logs / "codex"),
        *kimi_code.iter_usage_records(logs / "kimi-code"),
    ]


def _matches_golden(name: str, actual: str) -> None:
    golden = FIXTURES / "golden" / name
    if UPDATE:
        golden.write_text(actual, encoding="utf-8")
    assert golden.read_text(encoding="utf-8") == actual, (
        f"{name} changed; if intended, rerun with TOKENCUR_UPDATE_GOLDEN=1 "
        "and review the diff"
    )


def test_ingesters_produce_the_golden_records():
    records = [asdict(r) for r in fixture_records()]
    _matches_golden("records.json", json.dumps(records, indent=1) + "\n")


def test_focus_export_produces_the_golden_csv(tmp_path):
    out = tmp_path / "focus.csv"
    export_csv(fixture_records(), out)
    _matches_golden("focus.csv", out.read_text(encoding="utf-8"))
