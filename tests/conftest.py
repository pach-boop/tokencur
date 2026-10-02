import pytest


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path_factory, monkeypatch):
    """No test may read or write the real ledger in the user's home."""
    ledger = tmp_path_factory.mktemp("ledger") / "ledger.sqlite3"
    monkeypatch.setenv("TOKENCUR_LEDGER", str(ledger))
