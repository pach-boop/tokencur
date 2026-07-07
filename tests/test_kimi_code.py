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
