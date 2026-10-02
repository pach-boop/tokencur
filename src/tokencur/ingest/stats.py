"""What a scan saw, for ``tokencur doctor``.

Agents change their log formats without notice. A format change shows
up as usage lines tokencur can no longer read (malformed), or as log
files with no usage lines at all. Each ingester counts both while it
parses, plus the agent versions the logs name, so doctor can say
"Claude Code 2.1.280 wrote these" when something stops adding up.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field


@dataclass
class ScanStats:
    files: int = 0
    usage_lines: int = 0  # lines carrying the source's usage payload
    malformed: int = 0  # usage lines whose fields could not be read
    versions: Counter[str] = field(default_factory=Counter)

    def saw_version(self, version: str) -> None:
        if version:
            self.versions[version] += 1
