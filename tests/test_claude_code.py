import json

from tokencur.ingest.claude_code import iter_usage_records


def _assistant_line(
    request_id: str,
    usage: dict,
    message_id: str = "msg_1",
    session_id: str = "sess_1",
    model: str = "claude-opus-4-8",
) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "sessionId": session_id,
            "requestId": request_id,
            "timestamp": "2026-07-01T10:00:00.000Z",
            "message": {"id": message_id, "model": model, "usage": usage},
        }
    )


NEW_FORMAT_USAGE = {
    "input_tokens": 100,
    "output_tokens": 50,
    "cache_read_input_tokens": 1000,
    "cache_creation_input_tokens": 700,
    "cache_creation": {
        "ephemeral_5m_input_tokens": 200,
        "ephemeral_1h_input_tokens": 500,
    },
}

OLD_FORMAT_USAGE = {
    "input_tokens": 10,
    "output_tokens": 5,
    "cache_read_input_tokens": 0,
    "cache_creation_input_tokens": 300,
}


def test_parses_skips_and_dedups(tmp_path):
    log = tmp_path / "workspace-a" / "session.jsonl"
    log.parent.mkdir()
    log.write_text(
        "\n".join(
            [
                json.dumps({"type": "mode", "mode": "normal"}),  # no usage: skipped
                "not json at all",  # skipped
                _assistant_line("req_1", NEW_FORMAT_USAGE),
                _assistant_line("req_1", NEW_FORMAT_USAGE),  # duplicate: deduped
                _assistant_line("req_2", OLD_FORMAT_USAGE, message_id="msg_2"),
            ]
        ),
        encoding="utf-8",
    )

    records = list(iter_usage_records(tmp_path))

    assert len(records) == 2
    first, second = records
    assert first.workspace == "workspace-a"
    assert first.model == "claude-opus-4-8"
    assert first.date == "2026-07-01"
    assert (first.input_tokens, first.output_tokens) == (100, 50)
    assert first.cache_read_tokens == 1000
    # New format: cache writes split by TTL from the breakdown.
    assert (first.cache_write_5m_tokens, first.cache_write_1h_tokens) == (200, 500)
    # Old format: total attributed to the 5m tier (documented assumption).
    assert (second.cache_write_5m_tokens, second.cache_write_1h_tokens) == (300, 0)


def test_skips_synthetic_placeholder_messages(tmp_path):
    """Claude Code logs client-side stubs (API-error placeholders,
    interrupted turns) with model "<synthetic>" and all-zero usage.
    They are not API traffic and must not become unpriced rows."""
    log = tmp_path / "workspace-a" / "session.jsonl"
    log.parent.mkdir()
    zero_usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    log.write_text(
        "\n".join(
            [
                _assistant_line("req_1", zero_usage, model="<synthetic>"),
                _assistant_line("req_2", NEW_FORMAT_USAGE, message_id="msg_2"),
            ]
        ),
        encoding="utf-8",
    )

    records = list(iter_usage_records(tmp_path))

    assert [r.model for r in records] == ["claude-opus-4-8"]


def test_dedups_across_session_files(tmp_path):
    """A resumed session re-copies messages under a new session id;
    the same API request must not be double-counted."""
    workspace = tmp_path / "workspace-a"
    workspace.mkdir()
    (workspace / "original.jsonl").write_text(
        _assistant_line("req_1", NEW_FORMAT_USAGE, session_id="sess_1"),
        encoding="utf-8",
    )
    (workspace / "resumed.jsonl").write_text(
        _assistant_line("req_1", NEW_FORMAT_USAGE, session_id="sess_2"),
        encoding="utf-8",
    )

    assert len(list(iter_usage_records(tmp_path))) == 1
