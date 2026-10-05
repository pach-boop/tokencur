"""Usage value per successful change, session by session (issue #15)."""

import json
import re
import shutil
import subprocess
from datetime import UTC, datetime

import pytest
from gitrepos import YOU, at, call, commit, git, make_repo

from tokencur import cli, gitlog, sources
from tokencur.ingest import claude_code
from tokencur.ingest.claude_code import SessionCounters
from tokencur.sessions import render, session_outcomes

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def _by_session(result):
    return {s.session: s for s in result.sessions}


def test_a_change_goes_to_the_last_session_before_it(tmp_path):
    repo = make_repo(tmp_path / "app")
    records = [
        call(repo, "2026-09-10T10:00:00Z", session="early"),
        call(repo, "2026-09-10T10:30:00Z", session="early"),
        call(repo, "2026-09-12T12:00:00Z", session="late"),
    ]
    commit(repo, "2026-09-10 10:45:00")  # after early's last call
    commit(repo, "2026-09-12 12:30:00")  # after late's
    commit(repo, "2026-09-11 13:00:00")  # a day and more after early's
    commit(repo, "2026-09-10 09:00:00")  # before any call: out of the window

    result = session_outcomes(records, SessionCounters())

    sessions = _by_session(result)
    assert (sessions["early"].landed, sessions["late"].landed) == (1, 1)
    assert result.without_agent == 1  # made without an agent session
    assert sessions["early"].per_success == pytest.approx(10.0)  # $10 / 1


def test_success_means_landed_on_main_and_never_reverted(tmp_path):
    repo = make_repo(tmp_path / "app")
    records = [call(repo, "2026-09-10T09:00:00Z", session="s")]
    commit(repo, "2026-09-10 09:10:00", name="kept.txt")
    commit(repo, "2026-09-10 09:20:00", name="undone.txt", message="try something")
    git(repo, "revert", "--no-edit", "HEAD", **at("2026-09-10 09:30:00"))
    git(repo, "checkout", "-q", "-b", "feature")
    commit(repo, "2026-09-10 09:40:00", name="unmerged.txt")
    commit(repo, "2026-09-10 09:50:00", name="rebased.txt")
    git(repo, "checkout", "-q", "main")
    git(repo, "cherry-pick", "feature", **at("2026-09-10 10:00:00"))

    (s,) = session_outcomes(records, SessionCounters()).sessions

    # kept, undone and rebased landed; the rebased copy is one change with
    # its original; the revert commit itself is no change.
    assert (s.landed, s.reverted, s.pending, s.successes) == (3, 1, 1, 2)


def test_a_claude_code_chain_is_one_session_with_its_counters(tmp_path):
    repo = make_repo(tmp_path / "app")
    records = [
        call(repo, "2026-09-10T09:00:00Z", session="first"),
        call(repo, "2026-09-10T11:00:00Z", session="continued"),
    ]
    counters = SessionCounters(
        continued={"first": "continued"},
        prompts={"first": 2, "continued": 3},
        api_ms={"continued": (600_000.0, 540_000.0)},  # carries first's
    )

    (s,) = session_outcomes(records, counters).sessions

    assert (s.session, s.calls, s.prompts) == ("continued", 2, 5)
    assert s.api_ms == (600_000.0, 540_000.0)
    assert s.retry_share == pytest.approx(0.1)


def test_codex_sessions_bring_value_and_changes_only(tmp_path):
    repo = make_repo(tmp_path / "app")
    records = [call(repo, "2026-09-10T09:00:00Z", session="cx", source="codex")]
    commit(repo, "2026-09-10 09:30:00")

    (s,) = session_outcomes(records, SessionCounters(prompts={"cx": 4})).sessions

    assert (s.prompts, s.api_ms, s.landed) == (None, None, 1)


def test_only_the_named_repositories_count(tmp_path):
    app, lib = make_repo(tmp_path / "app"), make_repo(tmp_path / "lib")
    records = [
        call(app, "2026-09-10T09:00:00Z", session="s"),
        call(lib, "2026-09-10T09:05:00Z", session="s"),
    ]
    commit(app, "2026-09-10 09:30:00")
    commit(lib, "2026-09-10 09:40:00")

    elsewhere = call(app, "2026-09-10T09:10:00Z", session="other")

    (s,) = session_outcomes(
        [*records, elsewhere], SessionCounters(), repos=[lib]
    ).sessions

    assert (s.session, s.calls, s.value_usd) == ("s", 1, 5.0)  # lib's call only
    assert s.landed == 1 and list(s.calls_by_repo) == [lib.resolve()]
    with pytest.raises(gitlog.GitError, match="not in a git repository"):
        session_outcomes(records, SessionCounters(), repos=[tmp_path])


def _transcript(root, lines):
    path = root / "project" / "s.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")


def test_prompts_count_what_a_person_typed_and_nothing_else(tmp_path):
    def user(**fields):
        return {"type": "user", "sessionId": "s", **fields}

    typed = {"content": "[text a person wrote]"}
    tool = {"content": [{"type": "tool_result", "content": "[output]"}]}
    _transcript(
        tmp_path,
        [
            user(origin={"kind": "human"}, message=typed),
            user(origin={"kind": "human"}, message={"content": [{"type": "text"}]}),
            user(origin={"kind": "task-notification"}, message=typed),
            user(message=typed),  # an older line, before origin existed
            user(message=tool),
            user(isMeta=True, message=typed),
            user(isSidechain=True, message=typed),
            user(isCompactSummary=True, message=typed),
        ],
    )

    assert claude_code.session_counters(tmp_path).prompts == {"s": 3}


def test_api_time_comes_from_claude_codes_own_counters(tmp_path):
    def state(total, net):
        return {
            "type": "cost-state",
            "sessionId": "s",
            "modelUsage": {},
            "totalAPIDuration": total,
            "totalAPIDurationWithoutRetries": net,
        }

    _transcript(tmp_path, [state(1000, 900)])
    assert claude_code.session_counters(tmp_path).api_ms == {"s": (1000.0, 900.0)}

    _transcript(tmp_path, [state(900, 1000)])  # less with retries: nonsense
    assert claude_code.session_counters(tmp_path).api_ms == {}


def test_the_view_shows_each_session_and_the_totals(tmp_path):
    repo = make_repo(tmp_path / "app")
    records = [
        call(repo, f"2026-09-1{d}T09:00:00Z", session=f"session{d}") for d in range(3)
    ]
    commit(repo, "2026-09-10 09:30:00")
    counters = SessionCounters(
        prompts={"session0": 7}, api_ms={"session0": (3_900_000.0, 3_900_000.0)}
    )

    text = render(session_outcomes(records, counters), top=2)

    row = next(line for line in text.splitlines() if line.startswith("session0"))
    cells = re.split(r"\s{2,}", row.strip())
    assert cells[3:] == ["1", "$5.00", "7", "1h 05m", "0.0%", "1", "0", "$5.00"]
    assert "and 1 smaller sessions: $5.00" in text
    assert "usage value per successful change: $15.00" in text
    assert "sessions with no landed change: 2 of 3, $10.00 of usage value" in text


def test_the_view_names_what_reproduces_it():
    """As the report does: the version, the curated card and the pricing
    snapshot behind every value in the table."""
    from tokencur.pricing import provenance

    text = render(session_outcomes([], SessionCounters()))

    assert text.splitlines()[2] == f"reproducible with {provenance()}"


def test_the_sessions_command_end_to_end(tmp_path, monkeypatch, capsys):
    repo = make_repo(tmp_path / "app")
    commit(repo, "2026-09-10 10:30:00")
    log = tmp_path / "projects" / "app" / "s1.jsonl"
    log.parent.mkdir(parents=True)
    lines = [
        {
            "type": "user",
            "sessionId": "s1",
            "origin": {"kind": "human"},
            "message": {"content": "[redacted]"},
        },
        {
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
        },
    ]
    log.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")
    monkeypatch.setattr(
        sources,
        "DEFAULT_SOURCES",
        ((tmp_path / "projects", claude_code.iter_usage_records),),
    )

    assert cli.main(["outcomes", "--sessions"]) == 0
    out = capsys.readouterr().out
    assert "usage value per successful change: $5.00" in out
    row = next(line for line in out.splitlines() if line.startswith("s1"))
    assert re.split(r"\s{2,}", row.strip())[5] == "1"  # one typed prompt


def test_a_rebase_keeps_the_author_date_so_attribution_holds(tmp_path):
    """GitHub's rebase merge rewrites the commit but keeps when it was
    authored, which is what attribution reads."""
    repo = make_repo(tmp_path / "app")
    commit(repo, "2026-09-10 09:00:00", name="base.txt")
    git(repo, "checkout", "-q", "-b", "feature")
    commit(repo, "2026-09-10 10:30:00", name="work.txt")
    git(repo, "checkout", "-q", "main")
    commit(repo, "2026-09-10 11:00:00", name="other.txt", email="someone@example.com")
    git(repo, "rebase", "-q", "main", "feature", **at("2026-09-12 08:00:00"))
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "-q", "--ff-only", "feature")
    git(repo, "branch", "-q", "-D", "feature")

    changes = gitlog.history(
        repo,
        datetime(2026, 9, 10, 10, tzinfo=UTC),
        datetime(2026, 9, 11, tzinfo=UTC),
        "you@example.com",
    ).changes

    assert [(c.authored.hour, c.landed) for c in changes] == [(10, True)]


def test_the_default_branch_is_origins_and_its_local_twin(tmp_path):
    upstream = make_repo(tmp_path / "upstream")
    commit(upstream, "2026-09-10 09:00:00")
    git(tmp_path, "clone", "-q", str(upstream), "clone")
    clone = tmp_path / "clone"

    assert gitlog.default_refs(clone) == ["refs/remotes/origin/main", "refs/heads/main"]
    assert gitlog.default_refs(upstream) == ["refs/heads/main"]


def test_a_repository_git_cannot_read_is_named_not_fatal(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "app")

    def refuse(*args, **kwargs):
        raise gitlog.GitError("git log failed: fatal: bad object HEAD")

    monkeypatch.setattr(gitlog, "history", refuse)

    result = session_outcomes([call(repo, session="s")], SessionCounters())

    assert result.errors == {repo.resolve(): "git log failed: fatal: bad object HEAD"}
    assert "fatal: bad object HEAD" in render(result)


def _git_version():
    out = subprocess.run(["git", "--version"], capture_output=True, text=True).stdout
    return tuple(int(n) for n in re.findall(r"\d+", out)[:2])


@pytest.mark.skipif(
    shutil.which("git") is None or _git_version() < (2, 45),
    reason="git 2.45 added GIT_NO_LAZY_FETCH",
)
def test_a_partial_clone_is_never_fetched_from(tmp_path):
    """A blobless clone would download what a diff needs from its remote;
    tokencur reports the repository instead of going online."""
    origin = make_repo(tmp_path / "origin")
    commit(origin, "2026-09-10 09:00:00")
    commit(origin, "2026-09-10 09:10:00")  # the first version is left out
    git(origin, "config", "uploadpack.allowFilter", "true")
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", "--filter=blob:none", origin.as_uri(), str(clone))
    for key, value in (
        ("user.email", YOU),
        ("user.name", "You"),
        ("commit.gpgsign", "false"),
    ):
        git(clone, "config", key, value)  # a clone does not copy them
    commit(clone, "2026-09-10 10:00:00", lines=3, name="new.txt")  # a new blob

    def missing():
        objects = gitlog.git(clone, "rev-list", "--objects", "--missing=print", "--all")
        return sum(line.startswith("?") for line in objects.splitlines())

    before = missing()
    result = session_outcomes(
        [call(clone, "2026-09-10T09:30:00Z", session="s")], SessionCounters()
    )

    assert before and missing() == before
    assert list(result.errors) == [clone.resolve()]


def test_the_totals_say_what_did_not_land_or_had_no_agent(tmp_path):
    repo = make_repo(tmp_path / "app")
    records = [
        call(repo, "2026-09-10T09:00:00Z", session="s"),
        call(repo, "2026-09-12T09:00:00Z", session="s", model="no-such-model"),
    ]
    commit(repo, "2026-09-10 09:30:00", name="landed.txt")
    git(repo, "checkout", "-q", "-b", "feature")
    commit(repo, "2026-09-10 10:00:00", name="pending.txt")
    git(repo, "checkout", "-q", "main")
    commit(repo, "2026-09-11 12:00:00", name="by-hand.txt")

    text = render(session_outcomes(records, SessionCounters()))

    assert "changes not on the default branch yet: 1" in text
    assert "changes with no agent session in the day before: 1" in text
    assert "unpriced models: 1 calls not valued" in text


def test_no_success_is_said_plainly(tmp_path):
    repo = make_repo(tmp_path / "app")

    text = render(session_outcomes([call(repo, session="s")], SessionCounters()))

    assert "no change landed and stayed in the window" in text
    assert "sessions with no landed change: 1 of 1" in text
