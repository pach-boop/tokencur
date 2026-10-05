"""CI captures: the fetch script, the ingester and ``tokencur import github-ci``."""

import importlib.util
import io
import json
import os
import sys
from pathlib import Path

import pytest

from tokencur import cli, ledger
from tokencur.ingest import github_ci
from tokencur.records import UsageRecord

CAPTURE = Path(__file__).parent / "fixtures" / "ci" / "github-ci-capture.json"

_SCRIPT = Path(__file__).parent.parent / "scripts/fetch_github_ci.py"
_spec = importlib.util.spec_from_file_location("fetch_github_ci", _SCRIPT)
fetch = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fetch)


def test_a_capture_becomes_ci_runs():
    runs = list(github_ci.iter_runs(CAPTURE))

    # Runs 106 to 110 are malformed (an id that is text, no tree, a short
    # commit hash, attempt 0, a conclusion that is a number): skipped.
    assert [(r.run_id, r.attempt) for r in runs] == [
        (101, 1),
        (102, 1),
        (103, 2),
        (104, 1),
        (105, 1),
    ]
    first = runs[0]
    assert (first.repo, first.workflow, first.captured_at) == (
        "you/app",
        "ci.yml",
        "2026-09-28T15:00:00Z",
    )
    assert (first.event, first.status, first.conclusion) == (
        "push",
        "completed",
        "success",
    )
    assert (first.head_sha, first.tree) == ("a" * 40, "b" * 40)
    assert (first.created_at, first.updated_at) == (
        "2026-09-28T10:00:00Z",
        "2026-09-28T10:05:00Z",
    )
    assert runs[3].conclusion == ""  # still running: no conclusion yet


@pytest.mark.parametrize(
    "change",
    [
        {"repo": "not a repository"},
        {"repo": None},
        {"workflow": ""},
        {"captured_at": "yesterday"},
        {"runs": {"id": 1}},
    ],
)
def test_a_capture_that_does_not_say_what_it_is_reads_as_nothing(tmp_path, change):
    data = {**json.loads(CAPTURE.read_text(encoding="utf-8")), **change}
    path = tmp_path / "capture.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    assert list(github_ci.iter_runs(path)) == []


def test_a_file_that_is_not_json_reads_as_nothing(tmp_path):
    path = tmp_path / "capture.json"
    path.write_text("not json", encoding="utf-8")

    assert list(github_ci.iter_runs(path)) == []


def test_the_import_command_keeps_runs_once(capsys):
    assert cli.main(["import", "github-ci", str(CAPTURE)]) == 0
    assert cli.main(["import", "github-ci", str(CAPTURE)]) == 0

    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith(
        "5 CI runs (5 new) from you/app ci.yml, captured 2026-09-28T15:00:00Z"
    )
    assert out[1].startswith("5 CI runs (0 new)")
    assert len(ledger.read_ci_runs()) == 5


def test_a_later_capture_refreshes_a_run_that_was_running(tmp_path):
    data = json.loads(CAPTURE.read_text(encoding="utf-8"))
    ledger.record_ci_runs(github_ci.iter_runs(CAPTURE))
    data["captured_at"] = "2026-09-28T16:00:00Z"
    for run in data["runs"]:
        if run["id"] == 104:
            run.update(status="completed", conclusion="success")
    later = tmp_path / "later.json"
    later.write_text(json.dumps(data), encoding="utf-8")

    assert ledger.record_ci_runs(github_ci.iter_runs(later)) == 0

    (run,) = [r for r in ledger.read_ci_runs() if r.run_id == 104]
    assert (run.status, run.conclusion, run.captured_at) == (
        "completed",
        "success",
        "2026-09-28T16:00:00Z",
    )


def test_import_says_when_a_file_holds_no_runs(tmp_path, capsys):
    empty = tmp_path / "empty.json"
    empty.write_text("{}", encoding="utf-8")

    assert cli.main(["import", "github-ci", str(empty)]) == 1
    assert "no CI runs in those files" in capsys.readouterr().err
    assert cli.main(["import", "github-ci", str(tmp_path / "missing.json")]) == 1


def test_ci_runs_never_reach_the_export_or_the_observatory(tmp_path):
    """They hold a repository's name and hashes: local ledger only."""
    ledger.record(
        [
            UsageRecord(
                timestamp="2026-09-28T10:00:00Z",
                workspace="w",
                session_id="s",
                model="claude-opus-4-8",
                input_tokens=1000,
                output_tokens=0,
                cache_read_tokens=0,
                cache_write_5m_tokens=0,
                cache_write_1h_tokens=0,
                record_id="req_1",
            )
        ]
    )
    data = json.loads(CAPTURE.read_text(encoding="utf-8"))
    data["repo"] = "SECRET-owner/SECRET-repo"
    capture = tmp_path / "capture.json"
    capture.write_text(json.dumps(data), encoding="utf-8")
    assert cli.main(["import", "github-ci", str(capture)]) == 0

    assert cli.main(["export", str(tmp_path / "focus.csv")]) == 0
    assert cli.main(["observatory", str(tmp_path / "site")]) == 0

    published = [tmp_path / "focus.csv", *(tmp_path / "site").iterdir()]
    for path in published:
        text = path.read_text(encoding="utf-8")
        for leak in ("SECRET-owner", "SECRET-repo", "a" * 40, "b" * 40):
            assert leak not in text, (path.name, leak)


# The fetch script


def _api_run(**overrides):
    """A run as GitHub's API lists it, personal fields included."""
    run = {
        "id": 7,
        "name": "ci",
        "display_title": "fix: a commit message",
        "head_branch": "feature/secret-plan",
        "run_attempt": 1,
        "event": "push",
        "status": "completed",
        "conclusion": "success",
        "head_sha": "a" * 40,
        "created_at": "2026-09-28T10:00:00Z",
        "updated_at": "2026-09-28T10:05:00Z",
        "actor": {"login": "someone"},
        "head_commit": {
            "id": "a" * 40,
            "tree_id": "b" * 40,
            "message": "fix: a commit message",
            "author": {"name": "Some One", "email": "someone@example.com"},
        },
    }
    run.update(overrides)
    return run


class _Response(io.BytesIO):
    def __init__(self, body, link=None):
        super().__init__(json.dumps(body).encode())
        self.headers = {"Link": link} if link else {}


def test_the_capture_keeps_only_what_tokencur_reads():
    kept = fetch.keep(_api_run())

    assert kept == {
        "id": 7,
        "run_attempt": 1,
        "event": "push",
        "status": "completed",
        "conclusion": "success",
        "head_sha": "a" * 40,
        "created_at": "2026-09-28T10:00:00Z",
        "updated_at": "2026-09-28T10:05:00Z",
        "tree_id": "b" * 40,
    }


def test_pages_are_followed_on_the_api_only():
    page2 = "https://api.github.com/repositories/1/actions/workflows/ci.yml/runs?page=2"
    assert fetch.next_page(f'<{page2}>; rel="next", <{page2}>; rel="last"') == page2
    assert fetch.next_page('<https://elsewhere.example/runs>; rel="next"') is None
    assert fetch.next_page(None) is None


def test_the_token_goes_only_in_a_header_dropped_on_redirect(monkeypatch):
    first = fetch.runs_url("you/app", "ci.yml")
    second = "https://api.github.com/repos/you/app/actions/workflows/ci.yml/runs?page=2"
    pages = {
        first: _Response(
            {"workflow_runs": [_api_run(id=1)]}, link=f'<{second}>; rel="next"'
        ),
        second: _Response({"workflow_runs": [_api_run(id=2)]}),
    }
    seen = []

    def urlopen(request, timeout):
        seen.append(request)
        return pages[request.full_url]

    monkeypatch.setattr(fetch.urllib.request, "urlopen", urlopen)

    runs, requested = fetch.fetch(first, "secret-token")

    assert [run["id"] for run in runs] == [1, 2]
    assert requested == [first, second]
    for request in seen:
        assert request.unredirected_hdrs == {"Authorization": "Bearer secret-token"}
        assert "Authorization" not in request.headers


def test_a_capture_is_written_private_and_without_the_token(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("GITHUB_TOKEN", "secret-token")
    monkeypatch.setattr(
        fetch.urllib.request,
        "urlopen",
        lambda request, timeout: _Response({"workflow_runs": [_api_run()]}),
    )
    out = tmp_path / "raw" / "capture.json"

    assert (
        fetch.main(["--workflow", "ci.yml", "--repo", "you/app", "--out", str(out)])
        == 0
    )

    text = out.read_text(encoding="utf-8")
    assert "secret-token" not in text and "someone" not in text
    assert "commit message" not in text and "secret-plan" not in text
    (run,) = github_ci.iter_runs(out)
    assert (run.repo, run.workflow, run.tree) == ("you/app", "ci.yml", "b" * 40)
    if sys.platform != "win32":
        assert out.stat().st_mode & 0o777 == 0o600
    assert f"next: tokencur import github-ci {out}" in capsys.readouterr().out


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_an_existing_capture_file_turns_private_before_it_is_written(tmp_path):
    out = tmp_path / "capture.json"
    out.write_text("old", encoding="utf-8")
    os.chmod(out, 0o644)

    fetch.write_private(out, "new")

    assert out.read_text(encoding="utf-8") == "new"
    assert out.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "argv",
    [
        ["--workflow", "../../secrets", "--repo", "you/app"],
        ["--workflow", "ci.yml", "--repo", "you/app/extra"],
    ],
)
def test_the_script_refuses_what_is_not_a_workflow_or_a_repository(argv):
    with pytest.raises(SystemExit) as exc:
        fetch.main(argv)
    assert exc.value.code == 2


def test_an_http_error_is_explained(monkeypatch):
    def urlopen(request, timeout):
        raise fetch.urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr(fetch.urllib.request, "urlopen", urlopen)

    with pytest.raises(SystemExit, match=r"404 .*needs a token"):
        fetch.fetch(fetch.runs_url("you/private", "ci.yml"), "")
