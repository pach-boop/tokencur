"""Read-only git plumbing for the outcomes commands.

Everything here runs ``git`` on local repositories and nothing else:
which repository a directory belongs to, who the repository's author
is, which changes landed on the default branch or were reverted, and
where ``origin`` points.
Commit messages are read only to find ``This reverts commit <sha>``;
nothing from a repository is stored or published.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

# Why a directory has no repository.
NO_DIRECTORY = "no working directory logged"
GONE = "directory no longer exists"
NOT_A_REPOSITORY = "not in a git repository"

_REVERTS = re.compile(r"This reverts commit ([0-9a-f]{7,64})")

# git's scp-like remote syntax, [user@]host:path. A one-letter host is a
# Windows drive (C:\repo), which is a local path.
_SCP = re.compile(r"(?:[^@/:]+@)?([^@/:\\]{2,}):(.+)")
_URL_SCHEMES = {"https", "http", "ssh", "git", "git+ssh", "ssh+git"}


def environment() -> dict[str, str]:
    """git's environment. A partial clone downloads the objects it left
    out when a diff needs them; with this set, git 2.45 and newer fail
    instead, so tokencur never goes online."""
    return {**os.environ, "GIT_NO_LAZY_FETCH": "1"}


class GitError(RuntimeError):
    """A repository cannot be read: not a repository, or git is missing."""


class Repositories:
    """Finds the repository a directory belongs to, remembering each answer."""

    def __init__(self) -> None:
        self._answers: dict[str, Path | str] = {}
        self._found: dict[Path, Path | None] = {}

    def root(self, directory: str) -> Path | str:
        """The repository root holding ``directory``, or why there is none."""
        if directory not in self._answers:
            self._answers[directory] = self._answer(directory)
        return self._answers[directory]

    def _answer(self, directory: str) -> Path | str:
        if not directory:
            return NO_DIRECTORY
        path = Path(directory)
        if not path.is_dir():
            return GONE
        root = self._lookup(path)
        return root if root is not None else NOT_A_REPOSITORY

    def _lookup(self, path: Path) -> Path | None:
        walked = []
        root = None
        for candidate in (path, *path.parents):
            if candidate in self._found:
                root = self._found[candidate]
                break
            walked.append(candidate)
            if (candidate / ".git").exists():
                root = candidate.resolve()
                break
        for candidate in walked:
            self._found[candidate] = root
        return root


def git(root: Path, *args: str, stdin: str | None = None) -> str:
    """git's output; GitError with git's own reason when it fails."""
    done = run_git(root, *args, stdin=stdin)
    if done.returncode != 0:
        reason = (done.stderr.strip().splitlines() or ["unknown error"])[-1]
        raise GitError(f"git {args[0]} failed: {reason}")
    return done.stdout


def run_git(
    root: Path, *args: str, stdin: str | None = None
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            env=environment(),
        )
    except FileNotFoundError:
        raise GitError("git is not installed or not on PATH") from None


def user_email(root: Path) -> str:
    """The repository's ``user.email``, or "" when none is set."""
    return run_git(root, "config", "user.email").stdout.strip()


def has_commits(root: Path) -> bool:
    """False for a repository with no commits yet. Exit 1 means no HEAD;
    any other failure is left for the next git command to report."""
    return run_git(root, "rev-parse", "--quiet", "--verify", "HEAD").returncode != 1


def origin(root: Path) -> tuple[str, str] | None:
    """Where the repository's ``origin`` remote points, as (host,
    "owner/name"), or None when it has none or it is not hosted."""
    done = run_git(root, "remote", "get-url", "origin")
    return parse_remote(done.stdout) if done.returncode == 0 else None


def parse_remote(url: str) -> tuple[str, str] | None:
    """A remote URL as (host, "owner/name"); None for a local path, a
    ``file://`` URL, or a path that is not owner/name. Credentials and
    ports are dropped; an ssh host alias comes back as written."""
    url = url.strip()
    if "://" in url:
        parts = urlsplit(url)
        if parts.scheme not in _URL_SCHEMES:
            return None
        host, path = parts.hostname or "", parts.path
    else:
        scp = _SCP.fullmatch(url)
        if scp is None:
            return None
        host, path = scp.groups()
    path = path.strip("/")
    if path.lower().endswith(".git"):
        path = path[:-4]
    owner, _, name = path.partition("/")
    if not (host and owner and name) or "/" in name:
        return None
    return host.lower(), f"{owner}/{name}"


@dataclass(frozen=True)
class Change:
    """A change a person committed: one commit, and any copy of it on
    another branch. A rebase or a cherry-pick keeps the patch id, so a
    feature branch's commit and its rebased copy on main are one change."""

    authored: datetime  # UTC
    landed: bool  # some copy is on the default branch
    reverted: bool  # a commit on the default branch reverts some copy


def changes(
    root: Path, since: datetime, until: datetime, author: str | None
) -> list[Change]:
    """Non-merge changes in ``root`` authored in ``[since, until)`` by
    ``author`` (an email; None for everyone), on any local or remote
    branch, with whether each landed on the default branch and stayed.
    A commit that reverts another is a correction, not a change."""
    if not has_commits(root):
        return []
    # git filters on the commit date, which comes after the author date
    # (a rebase moves it later still); a day of margin keeps it safe.
    floor = (since - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    after = f"--since={floor} +0000"
    everywhere = ("--branches", "--remotes", "HEAD")
    log = git(
        root,
        "log",
        "--no-merges",
        after,
        "--format=%H%x1f%ae%x1f%aI%x1f%B%x1e",
        *everywhere,
    )
    commits = {}
    for entry in log.split("\x1e"):
        if not entry.strip():
            continue
        sha, email, when, message = entry.strip("\n").split("\x1f", 3)
        authored = datetime.fromisoformat(when).astimezone(UTC)
        if not since <= authored < until or _REVERTS.search(message):
            continue
        if author is not None and email.lower() != author.lower():
            continue
        commits[sha] = authored
    if not commits:
        return []

    patches = git(
        root, "log", "--no-merges", after, "-p", "--format=commit %H", *everywhere
    )
    patch_of = {}
    for line in git(root, "patch-id", "--stable", stdin=patches).splitlines():
        patch, sha = line.split()
        patch_of[sha] = patch
    main = default_refs(root)
    landed = set(git(root, "rev-list", after, *main).split())
    reverted = set()
    for message in git(
        root, "log", "--no-merges", after, "--format=%B%x1e", *main
    ).split("\x1e"):
        reverted.update(_REVERTS.findall(message))

    groups: dict[str, list[str]] = {}
    for sha in commits:
        groups.setdefault(patch_of.get(sha, sha), []).append(sha)
    return [
        Change(
            authored=min(commits[sha] for sha in shas),
            landed=any(sha in landed for sha in shas),
            reverted=any(sha.startswith(r) for sha in shas for r in reverted),
        )
        for shas in groups.values()
    ]


def default_refs(root: Path) -> list[str]:
    """The default branch: origin's, with the local branch of the same
    name when there is one; without origin, the local main or master;
    else HEAD."""
    done = run_git(root, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD")
    remote = done.stdout.strip() if done.returncode == 0 else ""
    if remote and _exists(root, remote):
        local = "refs/heads/" + remote.removeprefix("refs/remotes/origin/")
        return [remote, local] if _exists(root, local) else [remote]
    for local in ("refs/heads/main", "refs/heads/master"):
        if _exists(root, local):
            return [local]
    return ["HEAD"]


def _exists(root: Path, ref: str) -> bool:
    return run_git(root, "rev-parse", "--quiet", "--verify", ref).returncode == 0
