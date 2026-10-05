"""Save a GitHub Actions workflow's runs as a capture ``tokencur import`` reads.

Usage:
    python scripts/fetch_github_ci.py --workflow ci.yml [--repo OWNER/NAME] [--out FILE]
    tokencur import github-ci FILE

Lists the workflow's runs through GitHub's REST API,
``GET /repos/{owner}/{name}/actions/workflows/{workflow}/runs``, 100 to a
page, and keeps of each run only what tokencur needs to tell which code
CI tested and how that went (``KEPT``, plus the tree hash of the commit
it checked out). The API also returns commit messages, names, emails,
branch names and run titles; none of it is written. Job logs, which hold
test output, are never fetched.

Name the workflow that runs your tests. A repository's runs also include
other workflows, such as Pages, CodeQL or a scheduled job, whose success
says nothing about the code (ADR 0013). The repository defaults to the
one ``origin`` points at in the current directory.

A public repository needs no token. For a private one, set
``$GITHUB_TOKEN`` or ``$GH_TOKEN`` to a fine-grained token that can read
the repository's Actions and metadata. It is sent only to api.github.com,
in a header urllib drops on any redirect, and never written. The capture
is created readable by its owner only (on Windows, the user profile's
permissions apply). tokencur itself makes no network calls; this script
is the one step that does, when you run it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from tokencur.gitlog import origin

API = "https://api.github.com"
USER_AGENT = "tokencur (+https://github.com/pach-boop/tokencur)"
RAW = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / (
    "tokencur/raw/github-ci"
)

#: The fields kept from each run; everything else in the response is dropped.
KEPT = (
    "id",
    "run_attempt",
    "event",
    "status",
    "conclusion",
    "head_sha",
    "created_at",
    "updated_at",
)

_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_WORKFLOW = re.compile(r"[\w.-]+\.ya?ml|\d+")  # a workflow file's name, or its id
_NEXT = re.compile(r'<([^>]+)>;\s*rel="next"')


def runs_url(repo: str, workflow: str) -> str:
    return f"{API}/repos/{repo}/actions/workflows/{quote(workflow)}/runs?per_page=100"


def keep(run: dict) -> dict:
    """Only the fields tokencur reads, with the tree hash taken out of
    ``head_commit``, whose message and author are left behind."""
    kept = {key: run.get(key) for key in KEPT}
    head = run.get("head_commit")
    kept["tree_id"] = head.get("tree_id") if isinstance(head, dict) else None
    return kept


def next_page(link: str | None) -> str | None:
    """The ``rel="next"`` URL of a ``Link`` header, if it stays on the API."""
    found = _NEXT.search(link or "")
    return found.group(1) if found and found.group(1).startswith(API + "/") else None


def fetch(url: str, token: str) -> tuple[list[dict], list[str]]:
    """Every run the listing pages through, and the URLs requested."""
    runs: list[dict] = []
    requested: list[str] = []
    while url:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": USER_AGENT,
            },
        )
        if token:
            request.add_unredirected_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                page = json.load(response)
                link = response.headers.get("Link")
        except urllib.error.HTTPError as exc:
            raise SystemExit(
                f"GitHub answered {exc.code} for {url}: {_hint(exc.code)}"
            ) from None
        requested.append(url)
        listed = page.get("workflow_runs") if isinstance(page, dict) else None
        if not isinstance(listed, list):
            raise SystemExit(f"{url}: not a list of workflow runs")
        runs += [keep(run) for run in listed if isinstance(run, dict)]
        url = next_page(link)
    return runs, requested


def _hint(status: int) -> str:
    if status == 404:
        return "no such repository or workflow, or it is private and needs a token"
    if status in (401, 403, 429):
        return "the token was refused, or the rate limit was hit (a token raises it)"
    return "see GitHub's status page"


def default_repo() -> str:
    found = origin(Path.cwd())
    if found is None or found[0] != "github.com":
        raise SystemExit("origin here is not on github.com: pass --repo OWNER/NAME")
    return found[1]


def write_private(path: Path, text: str) -> None:
    """Write ``text`` to ``path``, readable by its owner only before any
    byte is written, even when the file already existed."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    if hasattr(os, "fchmod"):  # POSIX; on Windows the mode has no say
        os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        out.write(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--workflow",
        required=True,
        help="the workflow that runs your tests: its file name, e.g. ci.yml, or id",
    )
    parser.add_argument("--repo", help="OWNER/NAME (default: where origin points)")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    if not _WORKFLOW.fullmatch(args.workflow):
        parser.error(f"not a workflow file name or id: {args.workflow!r}")
    repo = args.repo or default_repo()
    if not _REPO.fullmatch(repo):
        parser.error(f"not OWNER/NAME: {repo!r}")

    token = (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()
    runs, requested = fetch(runs_url(repo, args.workflow), token)

    now = datetime.now(UTC)
    capture = {
        "captured_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "repo": repo,
        "workflow": args.workflow,
        "request_urls": requested,
        "runs": runs,
    }
    name = f"{repo.replace('/', '_')}_{args.workflow}_{now:%Y%m%dT%H%M%SZ}.json"
    out = args.out or RAW / name
    write_private(out, json.dumps(capture, indent=1))
    print(f"{out}: {len(runs)} runs of {repo} {args.workflow}")
    print(f"next: tokencur import github-ci {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
