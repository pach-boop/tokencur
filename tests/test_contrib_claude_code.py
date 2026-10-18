"""The Claude Code status line and hooks in contrib/claude-code."""

import importlib.util
import json
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

_PATH = Path(__file__).parent.parent / "contrib" / "claude-code" / "tokencur_hooks.py"
_SPEC = importlib.util.spec_from_file_location("tokencur_hooks", _PATH)
hooks = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(hooks)

FIGURES = {
    "windows": {
        "seven_day": {
            "percent": 68.1,
            "blocked": False,
            "resets_at": "2026-10-21T11:00:00Z",
            "projection": {"reaches_limit_at": "2026-10-21T02:21:56Z"},
        },
        "five_hour": {
            "percent": 13.1,
            "blocked": False,
            "resets_at": "2026-10-19T00:30:00Z",
        },
    }
}


def _local(moment):
    return datetime.fromisoformat(moment).astimezone().strftime("%a %H:%M")


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    for name in ("TOKENCUR_CONTEXT_CUT", "TOKENCUR_CONTEXT_WARN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TOKENCUR_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(hooks, "_refresh_in_background", lambda: None)
    return tmp_path / "state"


def _cache(state, figures=FIGURES):
    state.mkdir(parents=True, exist_ok=True)
    (state / "limits.json").write_text(json.dumps(figures), encoding="utf-8")


def _call(tokens, sidechain=False):
    return json.dumps(
        {
            "type": "assistant",
            "isSidechain": sidechain,
            "message": {
                "usage": {
                    "input_tokens": 10,
                    "cache_read_input_tokens": tokens,
                    "cache_creation_input_tokens": 990,
                    "output_tokens": 500,
                }
            },
        }
    )


# Context


def test_the_context_is_the_main_threads_last_call(tmp_path):
    transcript = tmp_path / "s.jsonl"
    lines = [_call(100_000), _call(150_000), _call(5_000, sidechain=True), "{oops"]
    transcript.write_text("\n".join(lines), encoding="utf-8")

    assert hooks.transcript_context(transcript) == 151_000
    assert hooks.transcript_context(tmp_path / "missing.jsonl") is None


def test_a_session_is_found_by_its_id_prefix(tmp_path):
    projects = tmp_path / "projects"
    (projects / "home").mkdir(parents=True)
    (projects / "home" / "abc123-1.jsonl").write_text(_call(310_000), "utf-8")
    (projects / "home" / "abd999-2.jsonl").write_text(_call(10_000), "utf-8")

    assert hooks.context_report("abc", projects) == (
        "context: 311k tokens · past the 300k cut: start a new session at the "
        "next break"
    )
    assert "289k left" in hooks.context_report("abd", projects)
    for prefix in ("ab", "*", ""):
        with pytest.raises(LookupError):
            hooks.find_transcript(prefix, projects)


# Status line


@pytest.mark.parametrize(
    ("tokens", "color", "words"),
    [
        (100_000, hooks.GREEN, "ctx 101k"),
        (260_000, hooks.YELLOW, "ctx 261k · new session after this task"),
        (300_000, hooks.RED, "ctx 301k · past the cut: new session"),
    ],
)
def test_the_status_line_colors_the_context_by_the_cut(tokens, color, words):
    usage = json.loads(_call(tokens))["message"]["usage"]
    data = {"context_window": {"current_usage": usage}}

    assert hooks.statusline(data).startswith(f"{color}{words}{hooks.RESET}")


def test_the_status_line_shows_the_cached_limits_and_their_age(_state, tmp_path):
    transcript = tmp_path / "s.jsonl"
    transcript.write_text(_call(50_000), encoding="utf-8")
    _cache(_state)
    written = (_state / "limits.json").stat().st_mtime

    line = hooks.statusline({"transcript_path": str(transcript)}, now=written + 7200)

    assert "ctx 51k" in line
    assert "week 68% · 5h 13% (2 h ago)" in line
    assert "at this pace" not in line  # too long for the status line
    assert hooks.statusline({}).startswith(f"{hooks.DIM}ctx —")


def test_the_cut_and_the_warning_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("TOKENCUR_CONTEXT_CUT", "200000")
    monkeypatch.setenv("TOKENCUR_CONTEXT_WARN", "not a number")
    usage = {"input_tokens": 210_000}

    assert hooks.RED in hooks.statusline({"context_window": {"current_usage": usage}})


# Plan limits


def test_the_summary_names_a_blocked_window_and_the_weeks_projection():
    blocked = json.loads(json.dumps(FIGURES))
    blocked["windows"]["five_hour"]["blocked"] = True

    line = hooks.summary(blocked)

    assert line == (
        f"week 68% · 5h at its limit until {_local('2026-10-19T00:30:00Z')} · at "
        f"this pace the week reaches its limit {_local('2026-10-21T02:21:56Z')}"
    )
    assert hooks.summary({"windows": {"seven_day": None}}) == "no limit measured yet"


def test_refresh_keeps_only_a_good_answer(_state, monkeypatch):
    answers = iter(
        [
            subprocess.CompletedProcess([], 0, json.dumps(FIGURES), ""),
            subprocess.CompletedProcess([], 0, "not json", ""),
            subprocess.CompletedProcess([], 1, "{}", "no usage"),
        ]
    )
    calls = []

    def run(command, **_):
        calls.append(command)
        return next(answers)

    monkeypatch.setattr(hooks.subprocess, "run", run)
    monkeypatch.setenv("TOKENCUR_BIN", "/opt/tokencur")
    monkeypatch.setenv("TOKENCUR_PLANS", "/plans.json")

    assert hooks.refresh() == 0
    assert hooks.refresh() == 1
    assert hooks.refresh() == 1
    assert calls[0] == ["/opt/tokencur", "limits", "--json", "--plans", "/plans.json"]
    assert hooks.read_cache()[0] == FIGURES  # the bad answers left it alone


# Hooks


def test_a_new_session_is_told_the_limits_and_how_to_measure_itself(_state):
    _cache(_state)
    data = {"session_id": "c541bcc1-7f08", "source": "startup", "cwd": "/w"}

    notice = hooks.session_start(data)["hookSpecificOutput"]

    assert notice["hookEventName"] == "SessionStart"
    text = notice["additionalContext"]
    assert text.startswith("[tokencur] Plan limits: week 68% · 5h 13% · at this pace")
    assert "(measured 0 min ago)" in text
    assert "Context cut: 300k tokens" in text
    assert text.endswith("tokencur_hooks.py context c541bcc1")
    assert "\tstart\tc541bcc1-7f08\t/w\tstartup" in (_state / "sessions.tsv").read_text(
        encoding="utf-8"
    )


def test_a_stale_or_missing_cache_is_refreshed_in_the_background(_state, monkeypatch):
    started = []
    monkeypatch.setattr(hooks, "_refresh_in_background", lambda: started.append(1))

    first = hooks.session_start({"source": "startup"})
    _cache(_state)
    written = (_state / "limits.json").stat().st_mtime
    hooks.session_start({"source": "startup"}, now=written + 60)
    hooks.session_start({"source": "startup"}, now=written + hooks.STALE_SECONDS + 1)

    assert "not measured yet" in first["hookSpecificOutput"]["additionalContext"]
    assert len(started) == 2


def test_a_resumed_session_is_logged_but_not_told_again(_state):
    assert hooks.session_start({"session_id": "s", "source": "resume"}) is None
    assert "\tresume" in (_state / "sessions.tsv").read_text(encoding="utf-8")


def test_the_watch_tells_each_step_once_and_rearms_after_a_reset(_state):
    notices = []
    shares = iter([70.0, 86.0, 90.0, 96.0, 10.0, 88.0])

    def measure():
        figures = json.loads(json.dumps(FIGURES))
        figures["windows"]["seven_day"]["percent"] = next(shares)
        figures["windows"]["five_hour"] = None
        _cache(_state, figures)

    def watch():
        return hooks.watch(lambda: True, lambda t, _: notices.append(t), measure)

    for _ in range(6):
        assert watch() == 0

    assert notices == [
        "tokencur: week at 86%",
        "tokencur: week at 96%",
        "tokencur: week at 88%",
    ]
    assert hooks.watch(lambda: False, notices.append, measure) == 0
    assert len(notices) == 3


def test_the_commands_answer_on_the_command_line(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(hooks.Path, "home", lambda: tmp_path)  # never the host's

    assert hooks.main(["context", "nosuchsession"]) == 1
    assert "0 transcripts start with" in capsys.readouterr().err
    assert hooks.main(["nonsense"]) == 2
    assert "statusline" in capsys.readouterr().err
