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


def test_record_ids_are_stable_and_distinct(tmp_path):
    """Ids come from the session, the event timestamp and the raw usage as
    logged: stable across scans, and two reports in the same millisecond
    with different usage never share one."""
    log = tmp_path / "rollout-x.jsonl"
    log.write_text(
        "\n".join(
            [
                json.dumps({"type": "session_meta", "payload": {"id": "sess-1"}}),
                _token_count("2026-02-06T22:43:51.000Z", 1000, 800, 50),
                _token_count("2026-02-06T22:43:51.000Z", 1000, 800, 60),
            ]
        ),
        encoding="utf-8",
    )

    first_scan = [r.record_id for r in iter_usage_records(tmp_path)]
    second_scan = [r.record_id for r in iter_usage_records(tmp_path)]

    assert first_scan == second_scan
    assert len(set(first_scan)) == 2
    assert all(rid.startswith("sess-1@2026-02-06T22:43:51.000Z#") for rid in first_scan)


def _usage(input_tokens: int, cached: int, output: int) -> dict:
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached,
        "output_tokens": output,
        "reasoning_output_tokens": 0,
        "total_tokens": input_tokens + output,
    }


def _report(ts: str, last: dict, total: dict) -> str:
    """A token_count event as current Codex writes it: the last call's usage
    plus the session's running total."""
    return json.dumps(
        {
            "type": "event_msg",
            "timestamp": ts,
            "payload": {
                "type": "token_count",
                "info": {"last_token_usage": last, "total_token_usage": total},
            },
        }
    )


def _write_session(tmp_path, lines: list[str]):
    log = tmp_path / "rollout-x.jsonl"
    meta = json.dumps({"type": "session_meta", "payload": {"id": "sess-1"}})
    log.write_text("\n".join([meta, *lines]), encoding="utf-8")


def test_unchanged_running_total_is_a_repeat_not_a_new_call(tmp_path):
    """Codex re-sends the last report under a new timestamp (alongside
    rate-limit updates). Only a moved running total is a new model call;
    counting the re-sends roughly doubled Codex usage."""
    call_a, call_b = _usage(1000, 800, 50), _usage(3000, 2500, 70)
    after_a = call_a
    after_b = _usage(4000, 3300, 120)
    _write_session(
        tmp_path,
        [
            _report("2026-02-06T22:43:51.000Z", call_a, after_a),
            _report("2026-02-06T22:43:52.100Z", call_a, after_a),  # re-send
            _report("2026-02-06T22:44:10.000Z", call_b, after_b),
            # A re-send whose billable fields are all zero: still no new call.
            _report("2026-02-06T22:44:11.000Z", _usage(0, 0, 0), after_b),
        ],
    )

    records = list(iter_usage_records(tmp_path))

    assert [r.timestamp for r in records] == [
        "2026-02-06T22:43:51.000Z",
        "2026-02-06T22:44:10.000Z",
    ]


def test_counted_calls_reconcile_with_codex_running_total(tmp_path):
    """The sum of what tokencur counts equals Codex's own running total at
    the end of the session — the check that exposed the double count."""
    calls = [_usage(1000 * i, 700 * i, 30 * i) for i in range(1, 6)]
    lines, running = (
        [],
        {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0},
    )
    for i, call in enumerate(calls):
        for field in running:
            running[field] += call[field]
        total = {**running, "reasoning_output_tokens": 0, "total_tokens": 0}
        lines.append(_report(f"2026-02-06T22:4{i}:00.000Z", call, total))
        lines.append(_report(f"2026-02-06T22:4{i}:01.000Z", call, total))  # re-send
    _write_session(tmp_path, lines)

    records = list(iter_usage_records(tmp_path))

    assert len(records) == len(calls)
    assert (
        sum(r.input_tokens + r.cache_read_tokens for r in records)
        == running["input_tokens"]
    )
    assert sum(r.cache_read_tokens for r in records) == running["cached_input_tokens"]
    assert sum(r.output_tokens for r in records) == running["output_tokens"]


def test_reports_without_billable_tokens_are_not_calls(tmp_path):
    """Codex sometimes moves only the running ``total_tokens`` with a report
    whose billable fields are all zero. That is no model call to count."""
    call = _usage(1000, 800, 50)
    no_tokens = {**_usage(0, 0, 0), "total_tokens": 5136}
    _write_session(
        tmp_path,
        [
            _report("2026-02-11T19:09:12.000Z", call, call),
            _report(
                "2026-02-11T19:09:13.229Z", no_tokens, {**call, "total_tokens": 6186}
            ),
        ],
    )

    records = list(iter_usage_records(tmp_path))

    assert [r.timestamp for r in records] == ["2026-02-11T19:09:12.000Z"]
