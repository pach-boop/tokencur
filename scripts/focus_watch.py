"""Weekly FOCUS watch: say when the Foundation's rules let tokencur move.

The export is gated on the released FOCUS 1.3 rules with one rule
tolerated, and the ``focus-1.4`` branch waits for released 1.4 rules the
validator can run (ADR 0016). This watch runs the newest
``focus-validator`` three ways:

1. main's export on the 1.3 rule file the validator downloads by default,
   tolerating nothing. Today it fails only the tolerated rule; once it
   passes, the tolerance can go.
2. the ``focus-1.4`` branch's export on the 1.4 rule file the validator
   downloads by default. Today the validator crashes on that file; once
   the export passes it, the branch can be merged.
3. the branch's own gate, on the prerelease 1.4 rules it pins. It should
   pass, on the newest such file.

"The file the validator downloads by default" is picked the way
``focus-validator`` picks it: from the assets of releases that are
neither drafts nor prereleases, newest release first, the highest
``model-<version>.json`` under the requested version.

When an answer differs from the expected one (above, as of 2026-10-15),
the watch opens an issue labelled ``focus-watch``, or comments on the
open one. Each distinct answer is posted once, so a week like the last
one only writes the run summary.

Usage:
    python scripts/focus_watch.py [--dry-run]
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from datetime import date
from pathlib import Path
from typing import NamedTuple

REPO = Path(__file__).parent.parent
SPEC_RELEASES = (
    "https://api.github.com/repos/FinOps-Open-Cost-and-Usage-Spec/FOCUS_Spec/releases"
)
BRANCH = "focus-1.4"
LABEL = "focus-watch"
ISSUE_TITLE = "FOCUS watch: the Foundation's rules moved"
PREFIX, SUFFIX = "model-", ".json"

_spec = importlib.util.spec_from_file_location(
    "validate_focus", REPO / "scripts" / "validate_focus.py"
)
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)


class Check(NamedTuple):
    name: str
    rules: str  # the rule file, or "—" when there was none to run
    outcome: str  # what happened
    alert: str | None = None  # what to do, when this is not the expected answer


def _version_key(version: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError:
        return (0,)  # sorts last, as in focus-validator


def model_assets(releases: list[dict], prereleases: bool = False) -> dict[str, dict]:
    """Rule files by model version; the newest release holding one wins."""
    found: dict[str, dict] = {}
    for release in releases:
        if release.get("draft") or (release.get("prerelease") and not prereleases):
            continue
        for asset in release.get("assets") or []:
            name = asset.get("name", "")
            if name.startswith(PREFIX) and name.endswith(SUFFIX):
                version = name[len(PREFIX) : -len(SUFFIX)]
                if version:
                    found.setdefault(version, asset)
    return found


def best_asset(assets: dict[str, dict], requested: str) -> dict | None:
    """The highest version under ``requested`` ("1.3" takes 1.3.0.4, not 1.30)."""
    matching = [v for v in assets if v == requested or v.startswith(requested + ".")]
    if not matching:
        return None
    return assets[max(matching, key=_version_key)]


def _short(text: str, limit: int = 160) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _ran(report: dict) -> str:
    if report["passed"]:
        return f"passes ({report['summary']})"
    failed = ", ".join(report["unexpected"]) or "with an error"
    return f"fails {failed} ({report['summary']})"


def check_released_13(asset: dict | None, report: dict | None) -> Check:
    """Main's export, strict, on the 1.3 file the validator downloads by default."""
    name = "1.3 released, nothing tolerated"
    if asset is None:
        return Check(
            name, "—", "no released 1.3 rule file", "No released 1.3 rule file found."
        )
    rules = asset["name"]
    if report is None or report.get("summary") is None:
        error = _short(report["last_line"]) if report else "the gate did not run"
        return Check(
            name,
            rules,
            f"the validator did not run: {error}",
            f"The validator does not run main's export on released 1.3 rules `{rules}`: {error}",
        )
    pinned = gate.RULES_URL.rsplit("/", 1)[1]
    if report["passed"]:
        return Check(
            name,
            rules,
            _ran(report),
            f"Released 1.3 rules `{rules}` pass with nothing tolerated: pin the gate "
            "to them and drop the tolerated rule (ADR 0016).",
        )
    if set(report["failures"]) != gate.TOLERATED:
        return Check(
            name,
            rules,
            _ran(report),
            f"Released 1.3 rules `{rules}` fail {', '.join(report['unexpected'])}: "
            "the export needs a look.",
        )
    if asset.get("digest") != f"sha256:{gate.RULES_SHA256}":
        return Check(
            name,
            rules,
            "fails only the tolerated rule",
            f"The validator now downloads `{rules}` for 1.3, but the gate pins "
            f"`{pinned}`: pin the gate to the new file.",
        )
    return Check(name, rules, "fails only the tolerated rule")


def check_released_14(asset: dict | None, report: dict | None, branch: bool) -> Check:
    """The branch's export, strict, on the 1.4 file the validator downloads."""
    name = "1.4 released, on the focus-1.4 branch"
    if not branch:
        return Check(name, "—", "no branch", f"The `{BRANCH}` branch is missing.")
    if asset is None:
        return Check(
            name, "—", "no released 1.4 rule file", "No released 1.4 rule file found."
        )
    rules = asset["name"]
    if report is None:
        return Check(
            name,
            rules,
            "the gate did not run",
            f"The `{BRANCH}` branch's gate did not run on released 1.4 rules `{rules}`.",
        )
    if report.get("summary") is None:
        # The expected answer: focus-validator 2.2.1 crashes on model-1.4.json.
        return Check(
            name, rules, f"the validator does not run: {_short(report['last_line'])}"
        )
    if report["passed"]:
        return Check(
            name,
            rules,
            _ran(report),
            f"Released 1.4 rules `{rules}` run, and the `{BRANCH}` export passes them: "
            "the branch can be merged (rebase it, pin its gate to these rules, accept "
            "its ADR).",
        )
    return Check(
        name,
        rules,
        _ran(report),
        f"Released 1.4 rules `{rules}` now run, and the `{BRANCH}` export fails "
        f"{', '.join(report['unexpected']) or 'with an error'}: update the branch.",
    )


def check_branch_gate(newest: dict | None, report: dict | None, branch: bool) -> Check:
    """The branch's own gate, on the prerelease rules it pins."""
    name = "focus-1.4 branch's own gate"
    if not branch:
        return Check(name, "—", "no branch", f"The `{BRANCH}` branch is missing.")
    if report is None or not report["passed"]:
        outcome = _ran(report) if report and report.get("summary") else "did not run"
        rules = report["rules"] if report else "—"
        return Check(
            name, rules, outcome, f"The `{BRANCH}` branch's own gate {outcome}."
        )
    rules = report["rules"]
    if newest is not None and newest["name"] != rules:
        return Check(
            name,
            rules,
            _ran(report),
            f"Newer 1.4 rules are out as a prerelease, `{newest['name']}` (the branch "
            f"pins `{rules}`): validate the branch on them.",
        )
    return Check(name, rules, _ran(report))


def marker(checks: list[Check]) -> str:
    """Names this set of answers, so an issue never repeats one."""
    alerts = sorted({c.alert for c in checks if c.alert})
    digest = hashlib.sha256("\n".join(alerts).encode()).hexdigest()[:12]
    return f"<!-- focus-watch:{digest} -->"


def render(checks: list[Check], validator: str) -> str:
    alerts = sorted({c.alert for c in checks if c.alert})
    lines = [f"focus-validator {validator}, run on {date.today().isoformat()}.", ""]
    lines += ["| Check | Rule file | Outcome |", "|---|---|---|"]
    # A validator summary holds pipes, which would split the table cell.
    lines += [
        f"| {c.name} | `{c.rules}` | {c.outcome.replace('|', '/')} |" for c in checks
    ]
    lines.append("")
    if alerts:
        lines += ["**Needs attention:**", ""] + [f"- {a}" for a in alerts]
    else:
        lines.append("Every answer is the expected one: nothing to do.")
    return "\n".join(lines) + "\n"


def already_posted(issues: list[dict], mark: str) -> bool:
    """Whether any focus-watch issue, open or closed, carries this answer."""
    return any(
        mark in (issue.get("body") or "")
        or any(mark in (c.get("body") or "") for c in issue.get("comments") or [])
        for issue in issues
    )


def _get(url: str) -> bytes:
    request = urllib.request.Request(
        url, headers={"User-Agent": "tokencur-focus-watch"}
    )
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def _releases() -> list[dict]:
    releases, page = [], 1
    while batch := json.loads(_get(f"{SPEC_RELEASES}?per_page=100&page={page}")):
        releases += batch
        page += 1
    return releases


def _download(asset: dict, directory: Path) -> Path:
    """Save a rule file, checked against the digest GitHub publishes for it."""
    data = _get(asset["browser_download_url"])
    digest = f"sha256:{hashlib.sha256(data).hexdigest()}"
    if asset.get("digest") and asset["digest"] != digest:
        raise SystemExit(
            f"{asset['name']}: got {digest}, GitHub says {asset['digest']}"
        )
    path = directory / asset["name"]
    path.write_bytes(data)
    return path


def _fetch_branch(directory: Path) -> Path | None:
    """Unpack the branch's tree; None when the branch is not on origin."""
    fetch = subprocess.run(
        ["git", "fetch", "--quiet", "--depth=1", "origin", BRANCH],
        cwd=REPO,
        capture_output=True,
    )
    if fetch.returncode != 0:
        return None
    tree = subprocess.run(
        ["git", "archive", "--format=tar", "FETCH_HEAD"],
        cwd=REPO,
        capture_output=True,
        check=True,
    )
    with tarfile.open(fileobj=io.BytesIO(tree.stdout)) as archive:
        archive.extractall(directory, filter="data")
    return directory


def _gate(checkout: Path, *args: str) -> dict | None:
    """Run a checkout's gate; its JSON verdict, or None if it wrote none."""
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report.json"
        result = subprocess.run(
            [
                sys.executable,
                "scripts/validate_focus.py",
                *args,
                "--report",
                str(report),
            ],
            cwd=checkout,
            capture_output=True,
            text=True,
        )
        if not report.exists():
            print(result.stdout[-2000:] + result.stderr[-2000:], file=sys.stderr)
            return None
        return json.loads(report.read_text(encoding="utf-8"))


def _gh(*args: str) -> str:
    return subprocess.run(
        ["gh", *args], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout


def _post(body: str, mark: str) -> str:
    issues = json.loads(
        _gh(
            "issue",
            "list",
            "--label",
            LABEL,
            "--state",
            "all",
            "--limit",
            "50",
            "--json",
            "number,state,body,comments",
        )
    )
    if already_posted(issues, mark):
        return "already posted; nothing new to say"
    text = body + "\n" + mark + "\n"
    open_issue = next((i for i in issues if i["state"] == "OPEN"), None)
    if open_issue:
        _gh("issue", "comment", str(open_issue["number"]), "--body", text)
        return f"commented on issue #{open_issue['number']}"
    _gh(
        "label",
        "create",
        LABEL,
        "--force",
        "--color",
        "0e8a16",
        "--description",
        "The weekly FOCUS watch has news",
    )
    url = _gh(
        "issue", "create", "--title", ISSUE_TITLE, "--label", LABEL, "--body", text
    )
    return f"opened {url.strip()}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--dry-run", action="store_true", help="print the report; open no issue"
    )
    args = parser.parse_args(argv)

    releases = _releases()
    released = model_assets(releases)
    asset_13 = best_asset(released, "1.3")
    asset_14 = best_asset(released, "1.4")
    newest_14 = best_asset(model_assets(releases, prereleases=True), "1.4")

    with tempfile.TemporaryDirectory() as tmp:
        rules = Path(tmp) / "rules"
        rules.mkdir()
        report_13 = report_14 = report_branch = None
        if asset_13:
            report_13 = _gate(
                REPO, "--rules", str(_download(asset_13, rules)), "--strict"
            )
        branch = _fetch_branch(Path(tmp) / BRANCH)
        if branch:
            if asset_14:
                report_14 = _gate(
                    branch, "--rules", str(_download(asset_14, rules)), "--strict"
                )
            report_branch = _gate(branch)

    checks = [
        check_released_13(asset_13, report_13),
        check_released_14(asset_14, report_14, branch is not None),
        check_branch_gate(newest_14, report_branch, branch is not None),
    ]
    body = render(checks, importlib.metadata.version("focus-validator"))
    if any(c.alert for c in checks) and not args.dry_run:
        body += f"\nIssue: {_post(body, marker(checks))}\n"
    print(body)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as out:
            out.write(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
