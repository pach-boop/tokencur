"""Scan cache: what each log file gave, so a rescan reads only what changed.

Every command scans the agents' logs, and Claude Code's grow to gigabytes:
reading them all again each time took most of a run (2026-11-17: 13 of
17 s for 3.2 GB). Transcripts only grow while a session is open and are
left alone after it, so what a file gave is kept here with the file's size
and modification time, and reused while both still match (ADR 0019).

- What is kept per file is what an ingester already takes from it:
  usage metadata, counters, ids and times, never message content.
- A file modified within ``SETTLED`` of being read is not kept: a later
  rewrite in the same tick of the file system's clock, at the same size,
  would look unchanged (the "racy git" case).
- The cache is keyed on the reading code itself, one key per root. Any
  change to the modules that parse a source's logs, an upgrade or a local
  edit, discards what that source's files gave.
- It is a cache: deleting it costs one full scan, and an error reading or
  writing it only means the logs are read in full.

Location: ``$TOKENCUR_CACHE`` if set (``off`` turns the cache off), else
``$XDG_CACHE_HOME/tokencur/scan-cache.sqlite3`` (``~/.cache/...``).
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import time
from collections.abc import Callable, Iterable
from contextlib import closing
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol, TypeVar

#: How long a file must have stood unmodified before what it gave is kept.
SETTLED_NS = 2_000_000_000

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, size INTEGER NOT NULL,"
    " mtime_ns INTEGER NOT NULL, part TEXT NOT NULL)",
)


def default_path() -> Path | None:
    """Where the cache lives, or None when ``TOKENCUR_CACHE=off``."""
    override = os.environ.get("TOKENCUR_CACHE")
    if override == "off":
        return None
    if override:
        return Path(override).expanduser()
    cache_home = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(cache_home) / "tokencur" / "scan-cache.sqlite3"


def code_key(modules: Iterable[ModuleType]) -> str:
    """A hash of the modules' source: the cache's key."""
    digest = hashlib.sha256()
    for module in modules:
        digest.update(Path(str(module.__file__)).read_bytes())
    return digest.hexdigest()


class Part(Protocol):
    """What one log file gives a scan, before the files are joined."""

    def to_json(self) -> Any: ...


P = TypeVar("P", bound=Part)


def file_parts(
    root: Path, key: str, read: Callable[[Path], P], load: Callable[[Any], P]
) -> list[P]:
    """Each log file's part under ``root``, in path order: kept from the
    cache when the file is unchanged, read with ``read`` otherwise.

    ``key`` names the code that reads the files (see ``code_key``). ``load``
    rebuilds a kept part; a ``KeyError``, ``TypeError`` or ``ValueError``
    from it means the part was kept by other code, and the file is read
    again.
    """
    files = FileCache(root, key)
    parts = []
    for path in sorted(root.rglob("*.jsonl")):
        stat = path.stat()  # before reading: a later write makes it stale
        part = None
        kept = files.get(path, stat)
        if kept is not None:
            try:
                part = load(kept)
            except (KeyError, TypeError, ValueError):
                part = None
        if part is None:
            part = read(path)
            files.put(path, stat, part.to_json())
        parts.append(part)
    files.save()
    return parts


class FileCache:
    """The parts kept for the files under one root, and the ones to keep.

    ``get`` returns a file's kept part when its size and modification time
    still match; ``put`` keeps a new one; ``save`` writes the new parts and
    forgets the files under the root that are gone. Parts are JSON values.
    """

    def __init__(self, root: Path, key: str, path: Path | None = None) -> None:
        self._root = str(root.absolute())
        self._key = key
        self._path = default_path() if path is None else path
        self._kept: dict[str, tuple[int, int, str]] = {}
        self._new: dict[str, tuple[int, int, str]] = {}
        self._seen: set[str] = set()
        if self._path is not None:
            self._load()

    def get(self, path: Path, stat: os.stat_result) -> Any:
        name = str(path.absolute())
        self._seen.add(name)
        kept = self._kept.get(name)
        if kept is None or kept[:2] != (stat.st_size, stat.st_mtime_ns):
            return None
        try:
            return json.loads(kept[2])
        except ValueError:
            return None

    def put(self, path: Path, stat: os.stat_result, part: Any) -> None:
        if time.time_ns() - stat.st_mtime_ns < SETTLED_NS:
            return  # still being written, or racy: read it again next time
        name = str(path.absolute())
        self._new[name] = (stat.st_size, stat.st_mtime_ns, json.dumps(part))

    def save(self) -> None:
        if self._path is None:
            return
        gone = [(name,) for name in self._kept if name not in self._seen]
        if not self._new and not gone:
            return
        rows = [(name, *entry) for name, entry in self._new.items()]
        try:
            with closing(self._connect()) as conn, conn:
                conn.executemany(
                    "INSERT OR REPLACE INTO files VALUES (?, ?, ?, ?)", rows
                )
                conn.executemany("DELETE FROM files WHERE path = ?", gone)
        except (OSError, sqlite3.Error) as exc:
            _warn(self._path, exc)

    def _load(self) -> None:
        assert self._path is not None
        # Each root keeps its own key: one source's reading code changing
        # leaves what another source's files gave.
        meta_key = f"code:{self._root}"
        prefix = self._root + os.sep
        under_root = "substr(path, 1, ?) = ?"
        try:
            with closing(self._connect()) as conn:
                row = conn.execute(
                    "SELECT value FROM meta WHERE key = ?", (meta_key,)
                ).fetchone()
                if row is None or row[0] != self._key:
                    with conn:  # parsed by other code: none of it holds
                        conn.execute(
                            f"DELETE FROM files WHERE {under_root}",
                            (len(prefix), prefix),
                        )
                        conn.execute(
                            "INSERT OR REPLACE INTO meta VALUES (?, ?)",
                            (meta_key, self._key),
                        )
                    return
                self._kept = {
                    name: (size, mtime_ns, part)
                    for name, size, mtime_ns, part in conn.execute(
                        f"SELECT path, size, mtime_ns, part FROM files WHERE {under_root}",
                        (len(prefix), prefix),
                    )
                }
        except (OSError, sqlite3.Error) as exc:
            _warn(self._path, exc)
            self._path = None  # this run reads the logs in full

    def _connect(self) -> sqlite3.Connection:
        assert self._path is not None
        created = not self._path.exists()
        if created:
            self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        conn = sqlite3.connect(self._path, timeout=5)
        try:
            if created:
                os.chmod(self._path, 0o600)
            for statement in _SCHEMA:
                conn.execute(statement)
        except (OSError, sqlite3.Error):
            conn.close()
            raise
        return conn


def _warn(path: Path, exc: Exception) -> None:
    print(
        f"tokencur: scan cache {path} not usable ({exc}); reading the logs in full",
        file=sys.stderr,
    )
