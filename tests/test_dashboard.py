import pytest

st = pytest.importorskip("streamlit", reason="dashboard extra not installed")

from streamlit.testing.v1 import AppTest  # noqa: E402


def test_dashboard_runs_without_exceptions():
    """Executes the full app script (real local data, DuckDB queries,
    charts) headlessly. Skipped when the dashboard extra isn't installed."""
    at = AppTest.from_file("src/tokencur/dashboard.py", default_timeout=180)
    at.run()

    assert not at.exception
    assert at.title[0].value.startswith("tokencur")
    assert len(at.metric) == 6  # 4 KPIs + 2 recommendation metrics
