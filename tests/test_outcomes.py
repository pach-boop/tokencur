"""Usage value per commit: attribution by working directory, commits by git."""

import json
import re
import shutil
import subprocess
from datetime import date

import pytest
from gitrepos import TRAILER
from gitrepos import at as _at
from gitrepos import call as _call
from gitrepos import commit as _commit
from gitrepos import git as _git
from gitrepos import make_repo as _repo

from tokencur import cli, gitlog, sources
from tokencur.ingest import claude_code
from tokencur.outcomes import (
    AGENT,
    GONE,
    NO_DIRECTORY,
    NOT_A_REPOSITORY,
    NOT_ASKED,
    OutcomesError,
    outcomes,
    render,
)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def test_a_call_belongs_to_the_repository_it_ran_in(tmp_path):
    outer = _repo(tmp_path / "outer")
    (outer / "src" / "deep").mkdir(parents=True)
    inner = _repo(outer / "vendor" / "lib")  # a clone inside a clone
    records = [_call(outer), _call(outer / "src" / "deep"), _call(inner)]

    result = outcomes(records)

    by_root = {r.root: r.calls for r in result.repos}
    assert by_root == {outer.resolve(): 2, inner.resolve(): 1}
    assert result.attributed_usd == result.total_usd == 15.0


def test_value_per_commit_counts_your_commits_in_the_days_with_usage(tmp_path):
    repo = _repo(tmp_path / "app")
    _commit(repo, "2026-09-09 23:59:59", lines=7)  # the day before any usage
    _commit(repo, "2026-09-10 08:00:00", lines=2, message="feat: a" + TRAILER)
    _commit(repo, "2026-09-11 09:00:00", lines=3)
    _commit(repo, "2026-09-11 10:00:00", lines=5, email="other@example.com")
    _commit(repo, "2026-09-12 00:00:00", lines=4)  # the day after the last call
    records = [
        _call(repo, "2026-09-10T10:00:00Z"),
        _call(repo, "2026-09-11T23:00:00Z"),
    ]

    (mine,) = outcomes(records).repos
    (everyone,) = outcomes(records, all_authors=True).repos

    assert (mine.since, mine.until) == (date(2026, 9, 10), date(2026, 9, 12))
    assert (mine.commits.total, mine.commits.by_agent, mine.commits.lines) == (2, 1, 5)
    assert mine.value_usd == 10.0 and mine.per_commit == 5.0
    assert everyone.commits.total == 3 and everyone.per_commit == pytest.approx(10 / 3)


def test_an_explicit_period_bounds_the_commits(tmp_path):
    repo = _repo(tmp_path / "app")
    for day in ("2026-09-01", "2026-09-15", "2026-09-30", "2026-10-01"):
        _commit(repo, f"{day} 12:00:00")

    (r,) = outcomes(
        [_call(repo, "2026-09-15T12:00:00Z")],
        since=date(2026, 9, 1),
        until=date(2026, 10, 1),
    ).repos

    assert r.commits.total == 3  # September: the 1st through the 30th


def test_merge_commits_are_left_out(tmp_path):
    repo = _repo(tmp_path / "app")
    _commit(repo, "2026-09-10 08:00:00")
    _git(repo, "checkout", "-q", "-b", "side")
    (repo / "side.txt").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "side.txt")
    _git(repo, "commit", "-q", "-m", "side", **_at("2026-09-10 09:00:00"))
    _git(repo, "checkout", "-q", "-")
    _commit(repo, "2026-09-10 10:00:00")
    _git(
        repo, "merge", "-q", "--no-ff", "-m", "merge", "side", **_at("2026-09-10 11:00")
    )

    (r,) = outcomes([_call(repo, "2026-09-10T12:00:00Z")]).repos

    assert r.commits.total == 3


def test_usage_outside_any_repository_is_reported_not_spread(tmp_path):
    repo = _repo(tmp_path / "app")
    plain = tmp_path / "notes"
    plain.mkdir()
    gone = tmp_path / "deleted"
    other = _repo(tmp_path / "other")
    records = [_call(repo), _call(plain), _call(gone), _call(""), _call(other)]

    result = outcomes(records, repos=[repo])

    assert [r.root for r in result.repos] == [repo.resolve()]
    assert result.attributed_usd == 5.0 and result.total_usd == 25.0
    assert result.unattributed == {
        NOT_A_REPOSITORY: (1, 5.0),
        GONE: (1, 5.0),
        NO_DIRECTORY: (1, 5.0),
        NOT_ASKED: (1, 5.0),
    }
    text = render(result)
    assert "attributed: $5.00 of $25.00 usage value (20%)" in text
    assert "not attributed, directory no longer exists: $5.00 (1 call)" in text


def test_unpriced_calls_are_counted_never_valued(tmp_path):
    repo = _repo(tmp_path / "app")
    _commit(repo, "2026-09-10 08:00:00")

    result = outcomes([_call(repo), _call(repo, model="no-such-model")])

    (r,) = result.repos
    assert (r.calls, r.unpriced, r.value_usd) == (2, 1, 5.0)
    assert "unpriced models: 1 attributed call not valued" in render(result)


def test_a_repository_without_commits_or_email_still_reports(tmp_path):
    empty = _repo(tmp_path / "empty")
    anonymous = _repo(tmp_path / "anonymous", email=None)
    _commit(anonymous, "2026-09-10 08:00:00", email="someone@example.com")

    result = outcomes([_call(empty), _call(anonymous)])

    by_root = {r.root.name: r for r in result.repos}
    assert by_root["empty"].commits.total == 0 and by_root["empty"].per_commit is None
    assert by_root["anonymous"].everyone and by_root["anonymous"].commits.total == 1
    text = render(result)
    assert "no git user.email set, so every author's commits count" in text
    assert "n/a" in text


def test_a_named_repository_must_be_one(tmp_path):
    with pytest.raises(OutcomesError, match="not in a git repository"):
        outcomes([], repos=[tmp_path])
    with pytest.raises(OutcomesError, match="no such directory"):
        outcomes([], repos=[tmp_path / "missing"])


def test_a_named_repository_without_usage_shows_no_window(tmp_path):
    repo = _repo(tmp_path / "app")

    (r,) = outcomes([], repos=[repo]).repos

    assert r.since is None and r.calls == 0
    assert "no usage" in render(outcomes([], repos=[repo]))


@pytest.mark.parametrize(
    "who",
    [
        "Claude <noreply@anthropic.com>",
        "Claude Opus 5.5 <noreply@anthropic.com>",
        "copilot-swe-agent[bot] <198982749+Copilot@users.noreply.github.com>",
        "claude[bot] <209825114+claude[bot]@users.noreply.github.com>",
        "Cursor Agent <cursoragent@cursor.com>",
        "Jane Doe (aider) <jane@example.com>",
    ],
)
def test_agent_signatures_are_recognised(who):
    assert AGENT.search(who)


@pytest.mark.parametrize(
    "who",
    [
        "Devin Smith <devin@example.com>",
        "Claude Dupont <claude@example.fr>",
        "github-actions[bot] <41898282+github-actions[bot]@users.noreply.github.com>",
        "",
    ],
)
def test_people_and_ci_bots_are_not_agents(who):
    assert not AGENT.search(who)


def test_the_view_names_what_reproduces_it():
    """As the report does: the version, the curated card and the pricing
    snapshot behind every value in the table."""
    from tokencur.pricing import provenance

    text = render(outcomes([]))

    assert text.splitlines()[2] == f"reproducible with {provenance()}"


def test_the_outcomes_command_end_to_end(tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path / "app")
    _commit(repo, "2026-09-10 08:00:00", lines=10, message="feat" + TRAILER)
    _commit(repo, "2026-09-10 09:00:00", lines=10)
    log = tmp_path / "projects" / "app" / "session.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text(
        json.dumps(
            {
                "type": "assistant",
                "sessionId": "session",
                "requestId": "req_1",
                "timestamp": "2026-09-10T10:00:00.000Z",
                "cwd": str(repo),
                "message": {
                    "id": "msg_1",
                    "model": "claude-opus-4-8",
                    "usage": {"input_tokens": 1_000_000, "output_tokens": 0},
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sources,
        "DEFAULT_SOURCES",
        ((tmp_path / "projects", claude_code.iter_usage_records),),
    )

    assert cli.main(["outcomes", str(repo)]) == 0
    out = capsys.readouterr().out
    assert "AI usage value per commit" in out
    (row,) = [line for line in out.splitlines() if "2026-09-10" in line]
    cells = re.split(r"\s{2,}", row.strip())
    # days, calls, usage value, commits, agent-signed, lines, per commit
    assert cells[1:] == ["2026-09-10", "1", "$5.00", "2", "1", "20", "$2.50"]

    assert cli.main(["outcomes", str(tmp_path / "missing")]) == 1
    assert "no such directory" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("log", "message"),
    [
        (
            subprocess.CompletedProcess([], 128, "", "fatal: bad object HEAD\n"),
            "git log failed: fatal: bad object HEAD",
        ),
        (
            subprocess.CompletedProcess([], 0, "\x1enot what was asked for\n", ""),
            "git 2.24 or newer is needed",
        ),
    ],
)
def test_a_repository_git_cannot_read_is_reported_not_raised(
    tmp_path, monkeypatch, log, message
):
    repo = _repo(tmp_path / "app")
    _commit(repo, "2026-09-10 08:00:00")
    real = gitlog.run_git
    monkeypatch.setattr(
        gitlog,
        "run_git",
        lambda root, *args, stdin=None: log if args[0] == "log" else real(root, *args),
    )

    result = outcomes([_call(repo)])

    (r,) = result.repos
    assert r.commits is None and message in r.error
    text = render(result)
    assert message in text and "?" in text


def test_missing_git_is_a_clear_error(tmp_path, monkeypatch):
    repo = _repo(tmp_path / "app")

    def no_git(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(gitlog.subprocess, "run", no_git)

    (r,) = outcomes([_call(repo)]).repos

    assert r.error == "git is not installed or not on PATH"


def test_a_git_error_is_not_mistaken_for_an_empty_repository(tmp_path, monkeypatch):
    repo = _repo(tmp_path / "app")
    refused = subprocess.CompletedProcess(
        [], 128, "", "fatal: detected dubious ownership in repository\n"
    )
    monkeypatch.setattr(gitlog, "run_git", lambda root, *args, stdin=None: refused)

    (r,) = outcomes([_call(repo)]).repos

    assert r.commits is None and "dubious ownership" in r.error
