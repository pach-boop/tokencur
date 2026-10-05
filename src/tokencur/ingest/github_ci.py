"""Ingest GitHub Actions CI captures: which code CI tested, and how it went.

``scripts/fetch_github_ci.py`` lists one workflow's runs through GitHub's
REST API and saves, for each run, only the fields below, wrapped as
``{"captured_at", "repo", "workflow", "request_urls", "runs"}``:

- ``id`` and ``run_attempt``: which run, and which attempt of it;
- ``event``, ``status`` and ``conclusion``: what started it, whether it
  finished, and how;
- ``head_sha`` and ``tree_id``: the commit it checked out, and that
  commit's tree, which names the code itself (see ADR 0013);
- ``created_at`` and ``updated_at``.

The capture holds no commit message, name, email or branch, and tokencur
reads nothing else from it. A malformed run is skipped, never guessed at.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path

from tokencur.ingest.fields import text
from tokencur.records import CIRun, parse_timestamp

_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_HASH = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")  # SHA-1, or SHA-256 repositories


def iter_runs(path: Path) -> Iterator[CIRun]:
    """Yield one CIRun per well-formed run in a capture."""
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except ValueError:
        return
    if not isinstance(data, dict):
        return
    repo, workflow = text(data.get("repo")), text(data.get("workflow"))
    captured = _moment(data.get("captured_at"))
    runs = data.get("runs")
    if not _REPO.fullmatch(repo) or not workflow or not captured:
        return
    if not isinstance(runs, list):
        return
    for row in runs:
        run = _run(row, repo, workflow, captured)
        if run is not None:
            yield run


def _run(row: object, repo: str, workflow: str, captured: str) -> CIRun | None:
    if not isinstance(row, dict):
        return None
    run_id, attempt = row.get("id"), row.get("run_attempt")
    event, status = text(row.get("event")), text(row.get("status"))
    conclusion = row.get("conclusion")
    head, tree = text(row.get("head_sha")), text(row.get("tree_id"))
    created, updated = _moment(row.get("created_at")), _moment(row.get("updated_at"))
    if not (_positive(run_id) and _positive(attempt) and event and status):
        return None
    if conclusion is not None and not isinstance(conclusion, str):
        return None
    if not (_HASH.fullmatch(head) and _HASH.fullmatch(tree) and created and updated):
        return None
    return CIRun(
        repo=repo,
        workflow=workflow,
        run_id=run_id,
        attempt=attempt,
        event=event,
        status=status,
        conclusion=conclusion or "",
        head_sha=head,
        tree=tree,
        created_at=created,
        updated_at=updated,
        captured_at=captured,
    )


def _positive(value: object) -> bool:
    return type(value) is int and value > 0


def _moment(value: object) -> str:
    """A timestamp as ``YYYY-MM-DDTHH:MM:SSZ`` (UTC), or "" when it is not one."""
    moment = parse_timestamp(value) if isinstance(value, str) else None
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ") if moment else ""
