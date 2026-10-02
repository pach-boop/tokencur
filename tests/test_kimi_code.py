import json

from tokencur.ingest.kimi_code import iter_usage_records


def test_parses_turn_usage_records(tmp_path):
    log = tmp_path / "wd_abc123" / "session_s1" / "agents" / "main" / "wire.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text(
        "\n".join(
            [
                json.dumps({"type": "metadata"}),
                json.dumps(
                    {
                        "type": "usage.record",
                        "usageScope": "turn",
                        "time": 1782024520201,  # 2026-06-21 UTC
                        "model": "moonshot-ai/kimi-k2.7-code-highspeed",
                        "usage": {
                            "inputOther": 2390,
                            "output": 280,
                            "inputCacheRead": 14336,
                            "inputCacheCreation": 7,
                        },
                    }
                ),
                # Non-turn scopes must be ignored to avoid double counting.
                json.dumps(
                    {
                        "type": "usage.record",
                        "usageScope": "session",
                        "time": 1782024520202,
                        "model": "moonshot-ai/kimi-k2.7-code-highspeed",
                        "usage": {"inputOther": 999999, "output": 999999},
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    records = list(iter_usage_records(tmp_path))

    assert len(records) == 1
    r = records[0]
    assert r.source == "kimi-code"
    assert r.workspace == "wd_abc123"
    assert r.session_id == "session_s1"
    assert r.model == "moonshot-ai/kimi-k2.7-code-highspeed"
    assert (r.input_tokens, r.output_tokens) == (2390, 280)
    assert (r.cache_read_tokens, r.cache_write_5m_tokens) == (14336, 7)
    assert r.date == "2026-06-21"
    assert r.record_id.startswith("session_s1/main@1782024520201#")


def _kimi_session(root, workspace: str, session: str, state: dict | None) -> None:
    """A Kimi session with one per-turn usage record and, if given, a state.json."""
    folder = root / workspace / session
    log = folder / "agents" / "main" / "wire.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text(
        json.dumps(
            {
                "type": "usage.record",
                "usageScope": "turn",
                "time": 1782024520201,
                "model": "moonshot-ai/kimi-k2.7-code-highspeed",
                "usage": {"inputOther": 10, "output": 5},
            }
        ),
        encoding="utf-8",
    )
    if state is not None:
        (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")


def test_the_working_directory_comes_from_the_session_state(tmp_path):
    """A session's own state.json names its directory; one that predates
    that field takes its siblings' when they agree; disagreement names none."""
    _kimi_session(tmp_path, "wd_app_1", "session_new", {"cwd": "/home/dev/app"})
    _kimi_session(tmp_path, "wd_app_1", "session_old", {"title": "[redacted]"})
    _kimi_session(tmp_path, "wd_odd_2", "session_a", {"cwd": "/home/dev/a"})
    _kimi_session(tmp_path, "wd_odd_2", "session_b", {"cwd": "/home/dev/b"})
    _kimi_session(tmp_path, "wd_odd_2", "session_c", None)
    (tmp_path / "wd_bad_3" / "session_x").mkdir(parents=True)
    (tmp_path / "wd_bad_3" / "session_x" / "state.json").write_text("{not json")

    cwds = {r.session_id: r.cwd for r in iter_usage_records(tmp_path)}

    assert cwds == {
        "session_new": "/home/dev/app",
        "session_old": "/home/dev/app",
        "session_a": "/home/dev/a",
        "session_b": "/home/dev/b",
        "session_c": "",
    }
