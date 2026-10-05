"""CI results per change: did CI test a change's own code, and pass?

ADR 0013. A run tested a change's own code when the commit it checked
out holds the same tree as the change's copy on the default branch: the
same files, byte for byte, whatever the commit hash. A rebase gives a
change a new hash but, onto a base that has not moved, the same tree, so
the run on its pull request still counts.

For each change that landed and stayed:

- ``passed``: CI tested its own code, and every run that did passed;
- ``failed``: some run on its own code failed;
- ``later``: CI never tested its own code, only later commits on the
  default branch that contain it. Those results are not the change's
  own, so it gets none;
- ``untested``: CI tested neither its code nor any commit containing it.

A run counts by its latest attempt: a re-run that passed replaces the
attempt that failed. A run that was cancelled or skipped, or could not
start because its workflow file was broken, tested nothing. The result
covers the whole run, so a failure may be the linter's, not a test's.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path

from tokencur import gitlog
from tokencur.records import CIRun

PASSED, FAILED, LATER, UNTESTED = "passed", "failed", "later", "untested"

_PASS = {"success"}
_FAIL = {"failure", "timed_out"}


def results(runs: Iterable[CIRun]) -> dict[str, str]:
    """The result for each tree CI tested: failed when any run failed."""
    latest: dict[tuple[str, int], CIRun] = {}
    for run in runs:
        key = (run.repo, run.run_id)
        if key not in latest or run.attempt > latest[key].attempt:
            latest[key] = run
    tested: dict[str, str] = {}
    for run in latest.values():
        if run.status != "completed":
            continue
        if run.conclusion in _FAIL:
            tested[run.tree] = FAILED
        elif run.conclusion in _PASS:
            tested.setdefault(run.tree, PASSED)
    return tested


def verdicts(history: gitlog.History, runs: Iterable[CIRun]) -> list[str | None]:
    """One verdict per change in ``history``; None for a change that did
    not land or was reverted."""
    tested = results(runs)
    contained = _contained(history, tested)
    judged: list[str | None] = []
    for change in history.changes:
        if not change.landed or change.reverted:
            judged.append(None)
            continue
        trees = {history.trees[sha] for sha in change.copies}
        own = {tested[tree] for tree in trees if tree in tested}
        if own:
            judged.append(FAILED if FAILED in own else PASSED)
        elif change.copies & contained:
            judged.append(LATER)
        else:
            judged.append(UNTESTED)
    return judged


def _contained(history: gitlog.History, tested: dict[str, str]) -> set[str]:
    """Default-branch commits that some tested commit contains: the
    tested ones and their ancestors, followed through every parent."""
    seen: set[str] = set()
    stack = [sha for sha, tree in history.trees.items() if tree in tested]
    while stack:
        sha = stack.pop()
        if sha not in seen:
            seen.add(sha)
            stack += [up for up in history.parents[sha] if up in history.trees]
    return seen


def runs_for(root: Path, runs: Sequence[CIRun]) -> list[CIRun]:
    """The runs captured for the repository at ``root``: those of the
    owner/name its ``origin`` points at, whatever the host is called
    (an ssh alias names github.com its own way)."""
    found = gitlog.origin(root)
    if found is None:
        return []
    name = found[1].lower()
    return [run for run in runs if run.repo.lower() == name]


def captures(runs: Iterable[CIRun]) -> list[str]:
    """What the runs came from, one entry per repository and workflow,
    with its latest capture: what reproduces a CI figure."""
    latest: dict[tuple[str, str], str] = {}
    for run in runs:
        key = (run.repo, run.workflow)
        latest[key] = max(latest.get(key, ""), run.captured_at)
    return [f"{repo} {wf} captured {at}" for (repo, wf), at in sorted(latest.items())]
