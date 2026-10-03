"""tokencur's Claude Code value against Claude Code's own counters."""

import json

import pytest

from tokencur import doctor
from tokencur.ingest import claude_code
from tokencur.reconcile import reconcile

OPUS, HAIKU = "claude-opus-4-8", "claude-haiku-4-5-20251001"


def _call(session: str, n: int) -> dict:
    """A $5.00 call: 1M input tokens on claude-opus-4-8."""
    return {
        "type": "assistant",
        "sessionId": session,
        "requestId": f"req_{session}_{n}",
        "timestamp": "2026-09-30T12:00:00.000Z",
        "message": {
            "id": f"msg_{session}_{n}",
            "model": OPUS,
            "usage": {"input_tokens": 1_000_000, "output_tokens": 0},
        },
    }


def _counters(session: str, **cost_by_model: float) -> dict:
    return {
        "type": "cost-state",
        "sessionId": session,
        "totalCostUSD": sum(cost_by_model.values()),
        "modelUsage": {m: {"costUSD": c} for m, c in cost_by_model.items()},
    }


def _session(root, session: str, lines: list[dict]) -> None:
    path = root / "project" / f"{session}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")


def _logs(root):
    """Session A ($10) continued in session B ($5 more): B's counters carry
    A's forward and add an internal Haiku call no transcript records.
    Session C is still open, so it has no counters; session D cost nothing."""
    _session(
        root,
        "A",
        [
            _call("A", 1),
            _call("A", 2),
            _counters("A", **{OPUS: 10.0}),
            {"type": "continued-in", "sessionId": "A", "continuedInSessionId": "B"},
        ],
    )
    _session(root, "B", [_call("B", 1), _counters("B", **{OPUS: 15.0, HAIKU: 1.0})])
    _session(root, "C", [_call("C", 1)])
    _session(root, "D", [_counters("D", **{OPUS: 0.0})])


def test_a_chain_of_sessions_is_compared_as_one(tmp_path):
    _logs(tmp_path)

    r = reconcile(
        claude_code.iter_usage_records(tmp_path),
        claude_code.session_counters(tmp_path),
    )

    assert r.sessions == 1  # A + B; C has no counters, D cost nothing
    assert (r.tokencur_usd, r.counted_usd) == pytest.approx((15.0, 15.0))
    assert r.unseen_usd == {HAIKU: 1.0}
    assert r.coverage == pytest.approx(15 / 16)


def test_a_session_whose_continuation_is_still_open_ends_its_own_chain(tmp_path):
    _session(
        tmp_path,
        "A",
        [
            _call("A", 1),
            _counters("A", **{OPUS: 5.0}),
            {"type": "continued-in", "sessionId": "A", "continuedInSessionId": "B"},
        ],
    )
    _session(tmp_path, "B", [_call("B", 1)])  # open: no counters yet

    r = reconcile(
        claude_code.iter_usage_records(tmp_path),
        claude_code.session_counters(tmp_path),
    )

    assert (r.sessions, r.tokencur_usd, r.counted_usd) == (1, 5.0, 5.0)


def test_damaged_counters_are_skipped_not_trusted(tmp_path):
    bad = _counters("A", **{OPUS: 5.0})
    bad["modelUsage"] = {
        OPUS: {"costUSD": float("nan")},
        HAIKU: {"costUSD": True},
        "other": "not an object",
        "negative": {"costUSD": -1},
    }
    _session(tmp_path, "A", [_call("A", 1), bad, {"type": "cost-state"}])
    with (tmp_path / "project" / "A.jsonl").open("a", encoding="utf-8") as fh:
        fh.write('\n{"type": "cost-state"')  # a truncated last line

    counters = claude_code.session_counters(tmp_path)

    assert counters.costs == {"A": {}}
    assert reconcile(claude_code.iter_usage_records(tmp_path), counters).sessions == 0


def test_the_doctor_reports_how_much_tokencur_sees(tmp_path):
    _logs(tmp_path / "logs")
    sources = ((tmp_path / "logs", claude_code.iter_usage_records),)

    d = doctor.diagnose(sources, tmp_path / "ledger.sqlite3")

    assert d.problems == []
    text = doctor.render(d)
    assert "tokencur sees 93.8%" in text
    assert f"{HAIKU}: $1.00, no call in any transcript" in text


@pytest.mark.parametrize(
    ("counted", "problem"),
    [(30.0, "calls are going missing"), (5.0, "calls may be counted twice")],
)
def test_the_doctor_flags_a_gap_either_way(tmp_path, counted, problem):
    _session(
        tmp_path, "A", [_call("A", 1), _call("A", 2), _counters("A", **{OPUS: counted})]
    )
    sources = ((tmp_path, claude_code.iter_usage_records),)

    d = doctor.diagnose(sources, tmp_path / "ledger.sqlite3")

    assert any(problem in p for p in d.problems)
