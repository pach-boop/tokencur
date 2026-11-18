"""Defensive readers for the fields of a parsed log line.

Agent logs are third-party formats that change without notice, and a
log file can be truncated or corrupted. Every field is read through
these helpers so that one odd line can never stop a scan or slip a
wrong type into a record. A usage line whose counts are not counts is
skipped as malformed, never guessed at: a string where a token count
belongs is not a count, and neither is a negative number, a fraction or
a boolean.
"""

from __future__ import annotations

import json
from datetime import datetime

from tokencur.records import parse_timestamp


class Malformed(ValueError):
    """A usage line whose fields cannot be read as usage."""


def entry(line: str) -> dict:
    """A log line as a JSON object; anything else reads as an empty one."""
    try:
        value = json.loads(line)
    except ValueError:  # JSONDecodeError, or text json cannot decode
        return {}
    return value if isinstance(value, dict) else {}


def obj(value: object) -> dict:
    """``value`` if it is a JSON object, else an empty one."""
    return value if isinstance(value, dict) else {}


def text(value: object, default: str = "") -> str:
    """A text field; any other type reads as ``default``."""
    return value if isinstance(value, str) else default


def time_span(start: object, end: object) -> tuple[datetime, datetime] | None:
    """A period from two timestamp fields; None unless it ends after it starts."""
    begin = parse_timestamp(start) if isinstance(start, str) else None
    finish = parse_timestamp(end) if isinstance(end, str) else None
    if begin is None or finish is None or finish <= begin:
        return None
    return begin, finish


def count(value: object) -> int:
    """A token count: a non-negative integer, or 0 when absent.

    Raises Malformed for anything else, so the caller skips the line.
    """
    if value is None:
        return 0
    if type(value) is not int or value < 0:
        raise Malformed(f"not a token count: {value!r}")
    return value
