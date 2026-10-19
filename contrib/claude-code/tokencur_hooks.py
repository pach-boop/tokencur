#!/usr/bin/env python3
"""Claude Code status line and hooks for tokencur. Standard library only.

Claude Code runs each of these as a command; none of them calls a model,
so they cost no tokens. Wire them in ~/.claude/settings.json as
settings.example.json shows.

  statusline      this session's context against a cut, and the plan
                  limits from the cache
  session-start   SessionStart hook: logs the start and tells the new
                  session how close the plan limits are and how to
                  measure its own context
  session-end     SessionEnd hook: logs the end
  context ID      a session's context, read from its transcript (an
                  agent can run it on itself); ID may be a prefix
  refresh         runs `tokencur limits --json` into the cache
  watch           desktop notice when a limit crosses a step (a systemd
                  timer runs it, see systemd/)

Settings, all optional, come from the environment:

  TOKENCUR_BIN            the tokencur command (default: tokencur)
  TOKENCUR_PLANS          a subscriptions.json for `tokencur limits --plans`
  TOKENCUR_STATE_DIR      cache, session log and watch marks
                          (default: $XDG_STATE_HOME/tokencur-hooks)
  TOKENCUR_CONTEXT_CUT    context, in tokens, past which to start a new
                          session (default: 300000)
  TOKENCUR_CONTEXT_WARN   context at which the status line turns yellow
                          (default: 250000)
  TOKENCUR_WATCH_WEEK     weekly steps, in percent (default: 85,95)
  TOKENCUR_WATCH_5H       five-hour steps, in percent (default: 65,85)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows: refreshes are not serialized there
    fcntl = None

#: The session-start hook refreshes a cache older than this. `tokencur
#: limits` takes a few seconds, too long to wait for when a session opens.
STALE_SECONDS = 20 * 60
#: The last call sits near the end of a transcript; reading only the tail
#: keeps the status line fast on long sessions.
TAIL_BYTES = 2_000_000
LABELS = {"seven_day": "week", "five_hour": "5h"}
GREEN, YELLOW, RED, DIM, RESET = (
    "\033[32m",
    "\033[33m",
    "\033[1;31m",
    "\033[2m",
    "\033[0m",
)


def _number(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _steps(name: str, default: str) -> list[int]:
    try:
        return sorted(int(s) for s in os.environ.get(name, default).split(","))
    except ValueError:
        return sorted(int(s) for s in default.split(","))


def context_cut() -> int:
    return _number("TOKENCUR_CONTEXT_CUT", 300_000)


def state_dir() -> Path:
    explicit = os.environ.get("TOKENCUR_STATE_DIR")
    if explicit:
        return Path(explicit)
    base = os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state"
    return Path(base) / "tokencur-hooks"


# Context


def context_of(usage: dict) -> int:
    """Tokens a call read: what fills the context window. Output tokens
    join the context only on the next call, as its input."""
    return (
        usage.get("input_tokens", 0)
        + usage.get("cache_read_input_tokens", 0)
        + usage.get("cache_creation_input_tokens", 0)
    )


def transcript_context(path: str | Path) -> int | None:
    """The context of the main thread's last call. Subagents run in their
    own context and are left out."""
    try:
        with open(path, "rb") as transcript:
            transcript.seek(0, os.SEEK_END)
            transcript.seek(max(0, transcript.tell() - TAIL_BYTES))
            lines = transcript.read().splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        if b'"usage"' not in line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("type") != "assistant" or entry.get("isSidechain"):
            continue
        return context_of(entry.get("message", {}).get("usage") or {})
    return None


def find_transcript(prefix: str, projects: Path | None = None) -> Path:
    """The one transcript whose session id starts with ``prefix``.

    A session id is a UUID, so a prefix may hold hyphens; anything else that is
    not a letter or digit (``*``, ``/``) is refused rather than globbed.
    """
    projects = projects or Path.home() / ".claude" / "projects"
    plain = prefix.replace("-", "").isalnum()
    found = sorted(projects.glob(f"*/{prefix}*.jsonl")) if plain else []
    if len(found) != 1:
        raise LookupError(
            f"{len(found)} transcripts start with {prefix!r}: give a longer prefix"
        )
    return found[0]


def context_report(prefix: str, projects: Path | None = None) -> str:
    context = transcript_context(find_transcript(prefix, projects))
    if context is None:
        return "no calls yet"
    cut = context_cut()
    if context >= cut:
        advice = f"past the {cut // 1000}k cut: start a new session at the next break"
    else:
        advice = f"{(cut - context) // 1000}k left before the {cut // 1000}k cut"
    return f"context: {context // 1000}k tokens · {advice}"


# Plan limits


def read_cache() -> tuple[dict, float] | None:
    """The last `tokencur limits --json` and when it was written."""
    path = state_dir() / "limits.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")), path.stat().st_mtime
    except (OSError, ValueError):
        return None


def refresh() -> int:
    """Runs `tokencur limits --json` into the cache. One at a time: when
    several sessions open together, the others leave it to the first."""
    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    with open(directory / ".refresh.lock", "w") as lock:
        if fcntl is not None:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return 0
        command = [os.environ.get("TOKENCUR_BIN", "tokencur"), "limits", "--json"]
        if os.environ.get("TOKENCUR_PLANS"):
            command += ["--plans", os.environ["TOKENCUR_PLANS"]]
        try:
            done = subprocess.run(
                command, capture_output=True, text=True, timeout=120, check=False
            )
            json.loads(done.stdout)
        except (OSError, subprocess.TimeoutExpired, ValueError):
            return 1
        if done.returncode != 0:
            return 1
        written = directory / ".limits.json.tmp"
        written.write_text(done.stdout, encoding="utf-8")
        written.replace(directory / "limits.json")
    return 0


def _refresh_in_background() -> None:
    subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "refresh"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _local(moment: str) -> str:
    return datetime.fromisoformat(moment).astimezone().strftime("%a %H:%M")


def summary(figures: dict, projection: bool = True) -> str:
    """One line: each window's share of its limit and, when the week's
    pace reaches its limit before the reset, when."""
    windows = figures.get("windows") or {}
    parts = []
    for kind, label in LABELS.items():
        window = windows.get(kind)
        if window is None:
            continue
        if window.get("blocked"):
            parts.append(f"{label} at its limit until {_local(window['resets_at'])}")
        elif window.get("percent") is not None:
            parts.append(f"{label} {window['percent']:.0f}%")
    line = " · ".join(parts) or "no limit measured yet"
    reaches = ((windows.get("seven_day") or {}).get("projection") or {}).get(
        "reaches_limit_at"
    )
    if projection and reaches:
        line += f" · at this pace the week reaches its limit {_local(reaches)}"
    return line


# Commands


def statusline(data: dict, now: float | None = None) -> str:
    usage = (data.get("context_window") or {}).get("current_usage")
    if isinstance(usage, dict):
        context = context_of(usage)
    elif data.get("transcript_path"):
        context = transcript_context(data["transcript_path"])
    else:
        context = None
    cut, warn = context_cut(), _number("TOKENCUR_CONTEXT_WARN", 250_000)
    if context is None:
        line = f"{DIM}ctx —{RESET}"
    elif context >= cut:
        line = f"{RED}ctx {context // 1000}k · past the cut: new session{RESET}"
    elif context >= warn:
        line = f"{YELLOW}ctx {context // 1000}k · new session after this task{RESET}"
    else:
        line = f"{GREEN}ctx {context // 1000}k{RESET}"
    cached = read_cache()
    if cached is not None:
        figures, written = cached
        hours = ((now or time.time()) - written) / 3600
        age = f" ({hours:.0f} h ago)" if hours >= 1 else ""
        line += f" {DIM}│ {summary(figures, projection=False)}{age}{RESET}"
    return line


def _log(event: str, data: dict, detail: str) -> None:
    """One line per session start or end in sessions.tsv: when sessions
    run, and from which folder."""
    try:
        directory = state_dir()
        directory.mkdir(parents=True, exist_ok=True)
        fields = [
            time.strftime("%Y-%m-%d %H:%M:%S"),
            event,
            data.get("session_id", ""),
            data.get("cwd", ""),
            detail,
        ]
        with open(directory / "sessions.tsv", "a", encoding="utf-8") as log:
            log.write("\t".join(fields) + "\n")
    except OSError:
        pass  # a hook must never stop a session


def session_start(data: dict, now: float | None = None) -> dict | None:
    _log("start", data, data.get("source", ""))
    if data.get("source") == "resume":
        return None  # the notice is in the resumed session's history already
    cached = read_cache()
    now = now or time.time()
    if cached is None or now - cached[1] > STALE_SECONDS:
        _refresh_in_background()
    if cached is None:
        limits = "not measured yet (the first measurement is running)"
    else:
        minutes = int((now - cached[1]) // 60)
        limits = f"{summary(cached[0])} (measured {minutes} min ago)"
    cut = context_cut() // 1000
    session = data.get("session_id", "")[:8]
    notice = (
        f"[tokencur] Plan limits: {limits}.\n"
        f"Context cut: {cut}k tokens. Each call costs more as the context grows: "
        f"past the cut, start a new session at the next natural break. Your own "
        f"estimate of the context runs low, since the system prompt and tool "
        f"definitions count too; measure it, at no token cost, with: "
        f"python3 {Path(__file__).resolve()} context {session}"
    )
    return {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": notice,
        }
    }


def _claude_running() -> bool:
    pgrep = shutil.which("pgrep")
    if pgrep is None:
        return True
    found = subprocess.run([pgrep, "-x", "claude"], capture_output=True, check=False)
    return found.returncode == 0


def _notify(title: str, body: str) -> None:
    sender = shutil.which("notify-send")
    if sender:
        command = [sender, "-u", "critical", "-a", "tokencur", title, body]
        subprocess.run(command, check=False)


def watch(running=_claude_running, notify=_notify, measure=refresh) -> int:
    """A notice once per step a window crosses. A mark per window keeps
    the step reached; when the window resets, the mark falls with it and
    the steps arm again. Nothing runs while no Claude Code is open."""
    if not running():
        return 0
    measure()
    cached = read_cache()
    if cached is None:
        return 0
    figures = cached[0]
    steps = {
        "seven_day": _steps("TOKENCUR_WATCH_WEEK", "85,95"),
        "five_hour": _steps("TOKENCUR_WATCH_5H", "65,85"),
    }
    for kind, label in LABELS.items():
        window = (figures.get("windows") or {}).get(kind)
        percent = window.get("percent") if window else None
        if percent is None:
            continue
        mark = state_dir() / f".watch_{kind}"
        reached = max((s for s in steps[kind] if percent >= s), default=0)
        try:
            before = int(mark.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            before = 0
        if reached > before:
            notify(f"tokencur: {label} at {percent:.0f}%", summary(figures))
        mark.write_text(str(reached), encoding="utf-8")
    return 0


def _stdin_json() -> dict:
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    command = args[0] if args else ""
    if command == "statusline":
        print(statusline(_stdin_json()))
    elif command == "session-start":
        notice = session_start(_stdin_json())
        if notice is not None:
            print(json.dumps(notice))
    elif command == "session-end":
        data = _stdin_json()
        _log("end", data, data.get("reason", ""))
    elif command == "context" and len(args) == 2:
        try:
            print(context_report(args[1]))
        except LookupError as error:
            print(error, file=sys.stderr)
            return 1
    elif command in ("refresh", "watch"):
        if hasattr(os, "nice"):
            os.nice(19)  # background work: never compete with the sessions
        return refresh() if command == "refresh" else watch()
    else:
        print(__doc__, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
