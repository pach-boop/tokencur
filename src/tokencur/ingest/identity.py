"""Stable identities for usage events.

The ledger (``tokencur.ledger``) keeps every usage event it has ever
seen, so it must recognise the same event on every later scan — even
after a source copies, rewrites or deletes its log files. Each ingester
builds a ``record_id`` from the raw identity fields its source logs;
this module keeps the shared part of that construction in one place.
"""

from __future__ import annotations

import hashlib
import json


def fingerprint(raw: dict) -> str:
    """Short, stable hash of a raw usage object exactly as logged.

    Used as a tiebreaker inside record ids: two distinct reports that
    share a session and a timestamp still differ in their raw usage, so
    they can never collapse into one ledger row. It hashes the logged
    values, not tokencur's mapping of them, so improving a parser never
    changes the id of an event already in the ledger.
    """
    canonical = json.dumps(raw, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
