import importlib.util
import json

import pytest

st = pytest.importorskip("streamlit", reason="dashboard extra not installed")

from streamlit.testing.v1 import AppTest  # noqa: E402

from tokencur import sources  # noqa: E402
from tokencur.ingest import claude_code  # noqa: E402

# Located through the import system, not a relative path: AppTest resolves
# relative paths against the calling file, and that rule changed between
# Streamlit releases.
DASHBOARD = importlib.util.find_spec("tokencur.dashboard").origin


def _call(path, request_id: str, model: str, usage: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "type": "assistant",
                "sessionId": path.stem,
                "requestId": request_id,
                "timestamp": "2026-09-15T12:00:00.000Z",
                "message": {"id": f"msg_{request_id}", "model": model, "usage": usage},
            }
        ),
        encoding="utf-8",
    )


def test_dashboard_renders_synthetic_usage(tmp_path, monkeypatch):
    """Runs the whole app script headlessly (DuckDB queries, charts,
    recommendations) over synthetic logs, so it never depends on whose
    machine runs it. Skipped when the dashboard extra isn't installed."""
    root = tmp_path / "projects" / "workspace"
    # $5.00 (1M input on Opus 4.8) + $10.00 (1M output on Sonnet 5).
    _call(root / "a.jsonl", "req_a", "claude-opus-4-8", {"input_tokens": 1_000_000})
    _call(root / "b.jsonl", "req_b", "claude-sonnet-5", {"output_tokens": 1_000_000})
    monkeypatch.setattr(
        sources, "DEFAULT_SOURCES", ((root.parent, claude_code.iter_usage_records),)
    )

    at = AppTest.from_file(DASHBOARD, default_timeout=180)
    at.run()

    assert not at.exception
    assert at.title[0].value.startswith("tokencur")
    assert len(at.metric) == 6  # 4 KPIs + 2 recommendation metrics
    assert at.metric[0].value == "$15.00"
