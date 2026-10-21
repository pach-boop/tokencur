"""Subagent calls logged with a short output count, filled in from Claude
Code's own counters (``claude_code._top_up``)."""

import json

from tokencur import doctor
from tokencur.ingest import claude_code
from tokencur.ingest.stats import ScanStats

OPUS = "claude-opus-4-8"
START = "2026-10-05T12:00:00.000Z"
START_MS = 1_791_201_600_000  # START in milliseconds since the epoch


def _call(session: str, n: int, output: int, *, stop: str | None = None) -> dict:
    """Call ``n`` of ``session``, a minute apart; ``stop`` None is a call
    logged while it streamed, as a subagent's transcript logs every call."""
    return {
        "type": "assistant",
        "sessionId": session,
        "requestId": f"req_{session}_{n}",
        "timestamp": f"2026-10-05T12:{n:02d}:30.000Z",
        "message": {
            "id": f"msg_{session}_{n}",
            "model": OPUS,
            "stop_reason": stop,
            "usage": {"input_tokens": 10, "output_tokens": output},
        },
    }


def _counters(session: str, output: int, start_ms: int = START_MS) -> dict:
    return {
        "type": "cost-state",
        "sessionId": session,
        "startTime": start_ms,
        "modelUsage": {OPUS: {"outputTokens": output, "costUSD": 1.0}},
    }


def _write(path, lines: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")


def _session(root, session: str, main: list[dict], subagent: list[dict]) -> None:
    """``main`` in the session's transcript, ``subagent`` in one agent's."""
    _write(root / "project" / f"{session}.jsonl", main)
    _write(root / "project" / session / "subagents" / "agent-a1.jsonl", subagent)


def _outputs(root, stats: ScanStats | None = None) -> dict[str, int]:
    return {
        r.record_id: r.output_tokens
        for r in claude_code.iter_usage_records(root, stats=stats)
    }


def test_the_counted_output_is_spread_over_the_short_calls(tmp_path):
    _session(
        tmp_path,
        "S",
        [_call("S", 1, 100, stop="end_turn"), _counters("S", 1_117)],
        [_call("S", 2, 8), _call("S", 3, 8)],
    )
    stats = ScanStats()

    out = _outputs(tmp_path, stats)

    # 1,117 counted - 116 logged = 1,001: 501 to the earlier call, 500 to the later.
    assert out == {
        "req_S_1:msg_S_1": 100,
        "req_S_2:msg_S_2": 509,
        "req_S_3:msg_S_3": 508,
    }
    assert (stats.short_output_calls, stats.topped_up_calls) == (2, 2)
    assert stats.topped_up_tokens == 1_001


def test_a_streamed_line_followed_by_the_final_one_is_not_short(tmp_path):
    early, final = _call("S", 1, 8), _call("S", 1, 300, stop="tool_use")
    _session(tmp_path, "S", [early, final, _counters("S", 400)], [])

    assert _outputs(tmp_path) == {"req_S_1:msg_S_1": 300}


def test_a_session_still_open_keeps_its_short_counts(tmp_path):
    _session(tmp_path, "S", [_call("S", 1, 100, stop="end_turn")], [_call("S", 2, 8)])
    stats = ScanStats()

    assert _outputs(tmp_path, stats)["req_S_2:msg_S_2"] == 8
    assert (stats.short_output_calls, stats.topped_up_calls) == (1, 0)


def test_counters_older_than_the_transcripts_on_disk_are_not_spread(tmp_path):
    # The counters began two hours before the first line: they also counted a
    # session whose transcript is gone, and its output is not this one's.
    earlier = START_MS - 2 * 3_600_000
    _session(tmp_path, "S", [_counters("S", 5_000, earlier)], [_call("S", 1, 8)])

    assert _outputs(tmp_path)["req_S_1:msg_S_1"] == 8


def test_counters_without_a_start_are_not_spread(tmp_path):
    counters = _counters("S", 5_000)
    del counters["startTime"]
    _session(tmp_path, "S", [counters], [_call("S", 1, 8)])

    assert _outputs(tmp_path)["req_S_1:msg_S_1"] == 8


def test_a_chain_is_filled_in_from_the_counters_at_its_end(tmp_path):
    # A continued in B, whose counters carry A's on (and keep A's start).
    _session(
        tmp_path,
        "A",
        [
            _counters("A", 108),
            {"type": "continued-in", "sessionId": "A", "continuedInSessionId": "B"},
        ],
        [_call("A", 1, 8)],
    )
    _session(tmp_path, "B", [_counters("B", 516)], [_call("B", 2, 8)])

    out = _outputs(tmp_path)

    assert out == {"req_A_1:msg_A_1": 258, "req_B_2:msg_B_2": 258}


def test_nothing_is_added_when_the_transcripts_already_hold_the_count(tmp_path):
    _session(
        tmp_path,
        "S",
        [_call("S", 1, 100, stop="end_turn"), _counters("S", 100)],
        [_call("S", 2, 8)],
    )

    assert _outputs(tmp_path)["req_S_2:msg_S_2"] == 8


def test_the_doctor_says_how_many_short_calls_it_filled_in(tmp_path):
    _session(tmp_path, "S", [_counters("S", 1_008)], [_call("S", 1, 8)])
    _session(tmp_path, "T", [], [_call("T", 1, 8)])
    sources = ((tmp_path, claude_code.iter_usage_records),)

    text = doctor.render(doctor.diagnose(sources, tmp_path / "ledger.sqlite3"))

    assert (
        "2 calls logged no final output count (subagents never do); 1 filled in "
        "from Claude Code's counters (+1,000 output tokens), 1 still read low"
    ) in text
