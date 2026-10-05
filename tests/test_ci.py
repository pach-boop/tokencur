"""CI results per change (ADR 0013): which changes CI tested on their own
code, and how that went."""

import json
import re
import shutil
import subprocess
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from gitrepos import YOU, at, call, commit, git, make_repo

from tokencur import ci, cli, gitlog, ledger, sources
from tokencur.ingest import claude_code
from tokencur.ingest.claude_code import SessionCounters
from tokencur.records import CIRun
from tokencur.sessions import render, session_outcomes

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def _run(tree, conclusion="success", run_id=1, attempt=1, status="completed", **kw):
    fields = dict(
        repo="you/app",
        workflow="ci.yml",
        run_id=run_id,
        attempt=attempt,
        event="push",
        status=status,
        conclusion=conclusion,
        head_sha="0" * 40,
        tree=tree,
        created_at="2026-09-10T12:00:00Z",
        updated_at="2026-09-10T12:05:00Z",
        captured_at="2026-09-11T00:00:00Z",
    )
    fields.update(kw)
    return CIRun(**fields)


def _tree(repo, ref="HEAD"):
    out = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", f"{ref}^{{tree}}"],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def _app(tmp_path, url="https://github.com/you/app.git"):
    repo = make_repo(tmp_path / "app")
    if url:
        git(repo, "remote", "add", "origin", url)
    return repo


# How runs become a result per tree


def test_a_rerun_that_passed_replaces_the_attempt_that_failed():
    tested = ci.results([_run("t", "failure", attempt=1), _run("t", attempt=2)])

    assert tested == {"t": ci.PASSED}


def test_a_tree_fails_when_any_of_its_runs_failed():
    tested = ci.results(
        [_run("t", run_id=1), _run("t", "failure", run_id=2), _run("u", run_id=3)]
    )

    assert tested == {"t": ci.FAILED, "u": ci.PASSED}


def test_runs_that_tested_nothing_are_no_result():
    tested = ci.results(
        [
            _run("cancelled", "cancelled", run_id=1),
            _run("skipped", "skipped", run_id=2),
            _run("running", "", run_id=3, status="in_progress"),
            _run("broken-workflow", "startup_failure", run_id=4),
            _run("timed-out", "timed_out", run_id=5),
        ]
    )

    assert tested == {"timed-out": ci.FAILED}


@pytest.mark.parametrize(
    ("url", "found"),
    [
        ("https://github.com/you/app.git", ("github.com", "you/app")),
        ("https://user:token@github.com/you/app/", ("github.com", "you/app")),
        ("git@github.com:you/app.git", ("github.com", "you/app")),
        ("ssh://git@github.com:22/you/app.git", ("github.com", "you/app")),
        ("github-work:you/app.git", ("github-work", "you/app")),
        ("https://github.com/you/app.GIT", ("github.com", "you/app")),
        ("https://gitlab.com/group/sub/app.git", None),
        ("/srv/git/app.git", None),
        ("file:///srv/git/you/app.git", None),
        ("C:\\repos\\app", None),
        ("", None),
    ],
)
def test_a_remote_names_its_host_and_repository(url, found):
    assert gitlog.parse_remote(url) == found


# Changes judged in a real repository


@needs_git
def test_each_success_says_whether_ci_tested_its_own_code(tmp_path):
    repo = _app(tmp_path)
    records = [call(repo, "2026-09-10T09:00:00Z", session="s")]
    trees = {}
    for minute, name in ((10, "passed"), (20, "failed"), (30, "later"), (40, "next")):
        commit(repo, f"2026-09-10 09:{minute}:00", name=f"{name}.txt")
        trees[name] = _tree(repo)
    commit(repo, "2026-09-10 09:50:00", name="untested.txt")  # after the last run
    runs = [
        _run(trees["passed"], run_id=1),
        _run(trees["failed"], "failure", run_id=2),
        _run(trees["next"], run_id=3),  # tests "later" only with this commit
    ]

    result = session_outcomes(records, SessionCounters(), ci_runs=runs)

    (s,) = result.sessions
    assert s.landed == 5
    assert s.ci == {
        ci.PASSED: 2,
        ci.FAILED: 1,
        ci.LATER: 1,
        ci.UNTESTED: 1,
    }
    text = render(result)
    assert text.splitlines()[2].endswith(
        "; CI runs: you/app ci.yml captured 2026-09-11T00:00:00Z"
    )
    assert "usage value per successful change: $1.00 ($5.00 over 5 changes" in text
    assert "usage value per change that also passed its own CI: $2.50 " in text
    assert (
        "CI tested the code of 3 of 5 changes: 2 passed, 1 failed; "
        "1 tested only with later commits; 1 never tested"
    ) in text
    row = next(line for line in text.splitlines() if line.startswith("s "))
    assert re.split(r"\s{2,}", row.strip())[-1] == "2/1"
    assert "a failed run may have failed lint, not tests (ADR 0013)" in text


def _sha(repo, ref="HEAD"):
    out = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", ref],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def _judged(history, runs):
    """Each change's verdict, keyed by the minute it was authored."""
    verdicts = ci.verdicts(history, runs)
    return {
        c.authored.minute: v for c, v in zip(history.changes, verdicts, strict=True)
    }


@needs_git
def test_later_means_a_tested_commit_contains_the_change(tmp_path):
    """Across a merge, a commit listed after another need not contain it:
    only a tested descendant makes a change "tested with later commits"."""
    repo = _app(tmp_path)
    commit(repo, "2026-09-10 09:00:00", name="base.txt")
    git(repo, "checkout", "-q", "-b", "side")
    commit(repo, "2026-09-10 09:10:00", name="side.txt")
    side = _tree(repo)
    git(repo, "checkout", "-q", "main")
    commit(repo, "2026-09-10 09:20:00", name="main.txt")
    git(
        repo,
        "merge",
        "-q",
        "--no-ff",
        "side",
        "-m",
        "merge",
        **at("2026-09-10 09:30:00"),
    )
    merged = _tree(repo)
    window = (
        datetime(2026, 9, 10, 9, 5, tzinfo=UTC),
        datetime(2026, 9, 11, tzinfo=UTC),
    )
    history = gitlog.history(repo, *window, None)

    assert _judged(history, [_run(side)]) == {10: ci.PASSED, 20: ci.UNTESTED}
    assert _judged(history, [_run(merged)]) == {10: ci.LATER, 20: ci.LATER}


@needs_git
def test_a_commit_only_on_the_other_twin_of_main_is_not_contained(tmp_path):
    """origin/main and the local main can part ways: a run on the local
    commit says nothing about the one only origin has."""
    upstream = make_repo(tmp_path / "upstream")
    commit(upstream, "2026-09-10 09:00:00", name="base.txt")
    git(tmp_path, "clone", "-q", str(upstream), "clone")
    clone = tmp_path / "clone"
    for key, value in (
        ("user.email", YOU),
        ("user.name", "You"),
        ("commit.gpgsign", "false"),
    ):
        git(clone, "config", key, value)  # a clone does not copy them
    commit(upstream, "2026-09-10 09:10:00", name="theirs.txt")
    git(clone, "fetch", "-q")
    commit(clone, "2026-09-10 09:20:00", name="mine.txt")  # never pushed
    window = (
        datetime(2026, 9, 10, 9, 5, tzinfo=UTC),
        datetime(2026, 9, 11, tzinfo=UTC),
    )
    history = gitlog.history(clone, *window, None)

    assert _judged(history, [_run(_tree(clone))]) == {10: ci.UNTESTED, 20: ci.PASSED}


@needs_git
def test_a_change_with_two_copies_on_main_is_later_when_either_is_contained(tmp_path):
    repo = _app(tmp_path)
    commit(repo, "2026-09-10 09:00:00", name="base.txt")
    commit(repo, "2026-09-10 09:10:00", name="twice.txt")
    first = _sha(repo)
    commit(repo, "2026-09-10 09:20:00", name="other.txt")
    tested = _tree(repo)
    git(repo, "revert", "--no-edit", first, **at("2026-09-10 09:30:00"))
    commit(repo, "2026-09-10 09:35:00", name="more.txt")
    git(repo, "cherry-pick", first, **at("2026-09-10 09:40:00"))  # the same patch again
    window = (
        datetime(2026, 9, 10, 9, 5, tzinfo=UTC),
        datetime(2026, 9, 11, tzinfo=UTC),
    )
    history = gitlog.history(repo, *window, None)

    # The revert names only the first copy; the change is reverted.
    assert [c.reverted for c in history.changes if c.authored.minute == 10] == [True]
    (twice,) = [c for c in history.changes if c.authored.minute == 10]
    assert len(twice.copies) == 2
    later = gitlog.History(
        [replace(twice, reverted=False)], history.trees, history.parents
    )
    assert ci.verdicts(later, [_run(tested)]) == [ci.LATER]


@needs_git
def test_a_rebased_copy_keeps_its_code_so_the_pull_request_run_counts(tmp_path):
    """Onto a base that has not moved, a rebase gives the change a new
    commit hash and the same tree: the run on the pull request tested
    exactly the code that landed."""
    repo = _app(tmp_path)
    commit(repo, "2026-09-10 09:00:00", name="base.txt")
    git(repo, "checkout", "-q", "-b", "feature")
    commit(repo, "2026-09-10 10:00:00", name="work.txt")
    pull_request = _tree(repo)
    git(repo, "checkout", "-q", "main")
    git(repo, "cherry-pick", "feature", **at("2026-09-10 11:00:00"))
    git(repo, "branch", "-q", "-D", "feature")
    records = [call(repo, "2026-09-10T09:30:00Z", session="s")]

    result = session_outcomes(
        records, SessionCounters(), ci_runs=[_run(pull_request, event="pull_request")]
    )

    assert result.sessions[0].ci == {ci.PASSED: 1}


@needs_git
def test_reverted_and_pending_changes_get_no_verdict(tmp_path):
    repo = _app(tmp_path)
    records = [call(repo, "2026-09-10T09:00:00Z", session="s")]
    commit(repo, "2026-09-10 09:10:00", name="undone.txt")
    undone = _tree(repo)
    git(repo, "revert", "--no-edit", "HEAD", **at("2026-09-10 09:20:00"))
    git(repo, "checkout", "-q", "-b", "feature")
    commit(repo, "2026-09-10 09:30:00", name="pending.txt")
    pending = _tree(repo)

    (s,) = session_outcomes(
        records,
        SessionCounters(),
        ci_runs=[_run(undone, run_id=1), _run(pending, run_id=2)],
    ).sessions

    assert (s.landed, s.reverted, s.pending) == (1, 1, 1)
    assert s.ci == {}


@needs_git
@pytest.mark.parametrize(
    "url",
    [
        None,  # no origin at all
        "https://github.com/someone-else/app.git",  # runs of another repository
        "/srv/git/app.git",  # a local remote
    ],
)
def test_without_runs_for_its_repository_the_view_is_unchanged(tmp_path, url):
    repo = _app(tmp_path, url)
    commit(repo, "2026-09-10 09:30:00")
    tree = _tree(repo)
    records = [call(repo, "2026-09-10T09:00:00Z", session="s")]

    with_runs = render(
        session_outcomes(records, SessionCounters(), ci_runs=[_run(tree)])
    )
    without = render(session_outcomes(records, SessionCounters()))

    assert with_runs == without
    assert "CI" not in without


@needs_git
def test_an_ssh_alias_still_finds_its_repository(tmp_path):
    repo = _app(tmp_path, "github-work:You/App.git")
    commit(repo, "2026-09-10 09:30:00")
    records = [call(repo, "2026-09-10T09:00:00Z", session="s")]

    result = session_outcomes(records, SessionCounters(), ci_runs=[_run(_tree(repo))])

    assert result.sessions[0].ci == {ci.PASSED: 1}


@needs_git
def test_the_ci_figure_counts_only_repositories_with_ci_captured(tmp_path):
    app = _app(tmp_path)
    lib = make_repo(tmp_path / "lib")
    records = [
        call(app, "2026-09-10T09:00:00Z", session="s"),
        call(lib, "2026-09-10T09:05:00Z", session="s"),
        call(lib, "2026-09-10T09:06:00Z", session="t"),
    ]
    commit(app, "2026-09-10 09:30:00")
    commit(lib, "2026-09-10 09:40:00")

    result = session_outcomes(records, SessionCounters(), ci_runs=[_run(_tree(app))])

    assert result.ci_repos == {app.resolve()}
    assert result.ci_value_usd == pytest.approx(5.0)  # app's one call
    text = render(result)
    assert "usage value per change that also passed its own CI: $5.00 " in text
    assert "CI tested the code of 1 of 1 changes: 1 passed, 0 failed" in text
    assert "changes in repositories with no CI captured: 1" in text


@needs_git
def test_no_pass_is_said_plainly(tmp_path):
    repo = _app(tmp_path)
    commit(repo, "2026-09-10 09:30:00")
    records = [call(repo, "2026-09-10T09:00:00Z", session="s")]

    text = render(
        session_outcomes(
            records, SessionCounters(), ci_runs=[_run(_tree(repo), "failure")]
        )
    )

    assert "no change that landed and stayed passed its own CI" in text
    assert "CI tested the code of 1 of 1 changes: 0 passed, 1 failed" in text


@needs_git
def test_the_sessions_command_judges_with_the_runs_in_the_ledger(
    tmp_path, monkeypatch, capsys
):
    repo = _app(tmp_path)
    commit(repo, "2026-09-10 10:30:00")
    log = tmp_path / "projects" / "app" / "s1.jsonl"
    log.parent.mkdir(parents=True)
    line = {
        "type": "assistant",
        "sessionId": "s1",
        "requestId": "req_1",
        "timestamp": "2026-09-10T10:00:00.000Z",
        "cwd": str(repo),
        "message": {
            "id": "msg_1",
            "model": "claude-opus-4-8",
            "usage": {"input_tokens": 1_000_000, "output_tokens": 0},
        },
    }
    log.write_text(json.dumps(line), encoding="utf-8")
    monkeypatch.setattr(
        sources,
        "DEFAULT_SOURCES",
        ((tmp_path / "projects", claude_code.iter_usage_records),),
    )
    ledger.record_ci_runs([_run(_tree(repo))])

    assert cli.main(["outcomes", "--sessions"]) == 0

    out = capsys.readouterr().out
    assert "usage value per change that also passed its own CI: $5.00" in out
    assert "CI runs: you/app ci.yml captured 2026-09-11T00:00:00Z" in out
