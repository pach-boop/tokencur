"""Real git repositories for tests, built in temporary directories."""

import os
import subprocess

from tokencur.records import UsageRecord

YOU = "you@example.com"
TRAILER = "\n\nCo-Authored-By: Claude <noreply@anthropic.com>"


def git(repo, *args, **env):
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        env={**os.environ, **env},
    )


def make_repo(path, email=YOU):
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    if email:
        git(path, "config", "user.email", email)
    git(path, "config", "user.name", "You")
    git(path, "config", "commit.gpgsign", "false")
    return path


def at(when):
    """Environment that dates a git commit at ``when`` (UTC)."""
    stamp = f"{when} +0000"
    return {"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}


def commit(repo, when, lines=1, email=YOU, message="change", name="file.txt"):
    """A commit by ``email``, authored at ``when`` (UTC), adding ``lines``
    lines to ``name``."""
    target = repo / name
    old = target.read_text(encoding="utf-8") if target.exists() else ""
    target.write_text(old + "line\n" * lines, encoding="utf-8")
    git(repo, "add", name)
    who = {"GIT_AUTHOR_EMAIL": email, "GIT_COMMITTER_EMAIL": email}
    git(repo, "commit", "-q", "-m", message, **who, **at(when))


def call(
    cwd,
    timestamp="2026-09-10T12:00:00Z",
    model="claude-opus-4-8",
    session="s",
    source="claude-code",
):
    """A $5.00 call (1M input tokens on claude-opus-4-8) that ran in ``cwd``."""
    return UsageRecord(
        timestamp=timestamp,
        workspace="w",
        session_id=session,
        model=model,
        input_tokens=1_000_000,
        output_tokens=0,
        cache_read_tokens=0,
        cache_write_5m_tokens=0,
        cache_write_1h_tokens=0,
        source=source,
        record_id=f"{session}@{cwd}@{timestamp}",
        cwd=str(cwd),
    )
