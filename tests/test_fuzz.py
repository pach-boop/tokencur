"""Fuzz tests: no agent log, however malformed, may crash a scan.

Agent log formats are third-party and change without notice. Hypothesis
writes log files mixing well-formed lines, lines shaped like each
agent's usage lines but with arbitrary values in every field, arbitrary
JSON, and invalid UTF-8. A scan must never raise, and every record it
returns must be well typed: strings for text, non-negative integers for
token counts. A malformed usage line is skipped, never guessed at.
"""

import json
import tempfile
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tokencur.ingest import claude_code, codex, kimi_code

FUZZ = settings(
    deadline=None, max_examples=200, suppress_health_check=[HealthCheck.too_slow]
)
COUNTS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_5m_tokens",
    "cache_write_1h_tokens",
)

scalars = (
    st.none()
    | st.booleans()
    | st.integers(-(10**15), 10**15)
    | st.floats(allow_nan=True, allow_infinity=False)
    | st.text(max_size=12)
)
anything = st.recursive(
    scalars,
    lambda inner: (
        st.lists(inner, max_size=3)
        | st.dictionaries(st.text(max_size=8), inner, max_size=4)
    ),
    max_leaves=12,
)
counts = st.one_of(st.integers(0, 10**9), anything)


def _usage(keys):
    return st.one_of(anything, st.fixed_dictionaries(dict.fromkeys(keys, counts)))


claude_lines = st.fixed_dictionaries(
    {
        "type": st.one_of(st.just("assistant"), anything),
        "requestId": anything,
        "sessionId": anything,
        "timestamp": st.one_of(st.just("2026-09-15T10:00:00.000Z"), anything),
        "uuid": anything,
        "message": st.one_of(
            anything,
            st.fixed_dictionaries(
                {
                    "id": anything,
                    "model": st.one_of(st.just("claude-opus-5-5"), anything),
                    "usage": _usage(
                        [
                            "input_tokens",
                            "output_tokens",
                            "cache_read_input_tokens",
                            "cache_creation_input_tokens",
                        ]
                    ),
                }
            ),
        ),
    }
)
codex_lines = st.one_of(
    st.fixed_dictionaries(
        {
            "type": st.sampled_from(["session_meta", "turn_context"]),
            "payload": st.one_of(
                anything,
                st.fixed_dictionaries(
                    {"id": anything, "cwd": anything, "model": anything}
                ),
            ),
        }
    ),
    st.fixed_dictionaries(
        {
            "type": st.just("event_msg"),
            "timestamp": anything,
            "payload": st.fixed_dictionaries(
                {
                    "type": st.just("token_count"),
                    "info": st.one_of(
                        anything,
                        st.fixed_dictionaries(
                            {
                                "last_token_usage": _usage(
                                    [
                                        "input_tokens",
                                        "cached_input_tokens",
                                        "output_tokens",
                                    ]
                                ),
                                "total_token_usage": anything,
                            }
                        ),
                    ),
                }
            ),
        }
    ),
)
kimi_lines = st.fixed_dictionaries(
    {
        "type": st.just("usage.record"),
        "usageScope": st.one_of(st.just("turn"), anything),
        "time": st.one_of(st.integers(-(10**18), 10**18), anything),
        "model": anything,
        "usage": _usage(
            ["inputOther", "output", "inputCacheRead", "inputCacheCreation"]
        ),
    }
)


def _log(shaped):
    """A log file: shaped usage lines mixed with arbitrary JSON and bytes."""
    line = st.one_of(
        shaped.map(lambda d: json.dumps(d).encode()),
        anything.map(lambda d: json.dumps(d).encode()),
        st.binary(max_size=40),
    )
    return st.lists(line, max_size=12).map(lambda lines: b"\n".join(lines))


def _scan(ingester, layout: str, content: bytes):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / layout
        path.parent.mkdir(parents=True)
        path.write_bytes(content)
        return list(ingester.iter_usage_records(Path(tmp)))


def _well_typed(records) -> None:
    for record in records:
        for field in COUNTS:
            value = getattr(record, field)
            assert type(value) is int and value >= 0, (field, value)
        for field in (
            "timestamp",
            "workspace",
            "session_id",
            "model",
            "source",
            "record_id",
        ):
            assert isinstance(getattr(record, field), str), field


@FUZZ
@given(_log(claude_lines))
def test_claude_code_scans_survive_any_log(content):
    _well_typed(_scan(claude_code, "workspace/session.jsonl", content))


@FUZZ
@given(_log(codex_lines))
def test_codex_scans_survive_any_log(content):
    _well_typed(_scan(codex, "2026/09/15/rollout.jsonl", content))


@FUZZ
@given(_log(kimi_lines))
def test_kimi_code_scans_survive_any_log(content):
    _well_typed(_scan(kimi_code, "wd_x/session_y/agents/main/wire.jsonl", content))
