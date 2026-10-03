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


@pytest.fixture(autouse=True)
def _no_host_git_config(monkeypatch, tmp_path_factory):
    """A test's git repositories carry their own identity, never the host's."""
    empty = tmp_path_factory.mktemp("gitconfig") / "config"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
