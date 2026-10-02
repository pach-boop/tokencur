import pytest

from tokencur import doctor, sources


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path_factory, monkeypatch):
    """No test may read or write the real ledger in the user's home."""
    ledger = tmp_path_factory.mktemp("ledger") / "ledger.sqlite3"
    monkeypatch.setenv("TOKENCUR_LEDGER", str(ledger))


@pytest.fixture(autouse=True)
def _no_host_logs(monkeypatch):
    """No test may read the agent logs of the machine it runs on: a test
    that passes only where real logs exist fails in CI. Tests that need
    logs point the sources at fixtures."""
    monkeypatch.setattr(sources, "DEFAULT_SOURCES", ())
    monkeypatch.setattr(doctor, "DEFAULT_SOURCES", ())
