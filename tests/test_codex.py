import json

from tokencur.ingest.codex import iter_usage_records


def _token_count(ts: str, input_tokens: int, cached: int, output: int) -> str:
    return json.dumps(
        {
            "type": "event_msg",
            "timestamp": ts,
            "payload": {
                "type": "token_count",
                "info": {
                    "last_token_usage": {
                        "input_tokens": input_tokens,
                        "cached_input_tokens": cached,
                        "output_tokens": output,
                        "reasoning_output_tokens": 3,
                    }
                },
            },
        }
    )


def test_parses_rollout_and_splits_cached_input(tmp_path):
    log = tmp_path / "2026" / "02" / "06" / "rollout-x.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {"id": "sess-1", "cwd": "/home/u/myproject"},
                    }
                ),
                json.dumps(
                    {"type": "turn_context", "payload": {"model": "gpt-5.2-codex"}}
                ),
                # Rate-limit-only update: no usage info, must be skipped.
                json.dumps(
                    {
                        "type": "event_msg",
                        "payload": {"type": "token_count", "info": None},
                    }
                ),
                _token_count("2026-02-06T22:43:51.000Z", 1000, 800, 50),
                # Identical consecutive report is skipped defensively.
                _token_count("2026-02-06T22:43:51.000Z", 1000, 800, 50),
                _token_count("2026-02-06T22:44:10.000Z", 2000, 1500, 70),
            ]
        ),
        encoding="utf-8",
    )

    records = list(iter_usage_records(tmp_path))

    assert len(records) == 2
    first = records[0]
    assert first.source == "codex"
    assert first.workspace == "myproject"
    assert first.session_id == "sess-1"
    assert first.model == "gpt-5.2-codex"
    # OpenAI input_tokens includes cached; non-cached input is the difference.
    assert (first.input_tokens, first.cache_read_tokens) == (200, 800)
    assert first.output_tokens == 50
    assert (first.cache_write_5m_tokens, first.cache_write_1h_tokens) == (0, 0)
