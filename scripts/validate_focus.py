"""Conformance gate: run the FinOps Foundation validator on our export.

Builds a small synthetic dataset (no real data), exports it as FOCUS
CSV and runs ``focus-validator`` against spec 1.2. Passes when every
composite column rule passes; the only tolerated raw failure is
``InvoiceId-C-005-C`` — the not-null branch of the OR rule
``InvoiceId-C-003-C``, which showback data legitimately does not take
(no invoice exists, so InvoiceId is an explicit null and the OR is
satisfied by ``InvoiceId-C-004-C``).

Usage:
    python scripts/validate_focus.py
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokencur.export import export_csv  # noqa: E402
from tokencur.ingest.claude_code import UsageRecord  # noqa: E402

TOLERATED_OR_BRANCHES = {"InvoiceId-C-005-C"}


def _synthetic_records() -> list[UsageRecord]:
    base = dict(
        input_tokens=1200,
        output_tokens=340,
        cache_read_tokens=9000,
        cache_write_5m_tokens=500,
        cache_write_1h_tokens=250,
    )
    return [
        UsageRecord(
            timestamp="2026-07-01T10:00:00.000Z", workspace="ws-a",
            session_id="s1", model="claude-opus-4-8",
            source="claude-code", **base,
        ),
        UsageRecord(
            timestamp="2026-07-02T23:59:59.000Z", workspace="ws-b",
            session_id="s2", model="gpt-5.2-codex", source="codex", **base,
        ),
        UsageRecord(
            timestamp="2026-12-15T00:00:00.000Z", workspace="ws-c",
            session_id="s3", model="kimi-k2-0711-preview",
            source="kimi-code", **base,
        ),
    ]


def main() -> int:
    import focus_validator

    package_parent = Path(focus_validator.__file__).parent.parent
    with tempfile.TemporaryDirectory() as tmp:
        data_file = Path(tmp) / "focus_sample.csv"
        rows = export_csv(_synthetic_records(), data_file)
        print(f"exported {rows} synthetic FOCUS rows")

        # The validator resolves its rule files relative to the CWD.
        result = subprocess.run(
            ["focus-validator", "--data-file", str(data_file),
             "--validate-version", "1.2", "--output-type", "console"],
            cwd=package_parent, capture_output=True, text=True,
        )
    output = result.stdout + result.stderr

    failures = set(re.findall(r"^- (\S+): violations=", output, re.MULTILINE))
    unexpected = failures - TOLERATED_OR_BRANCHES
    or_satisfied = "InvoiceId-C-003-C: PASS" in output
    summary = re.search(r"Total: \d+ \| Pass: \d+ \| Fail: \d+ \| Skipped: \d+", output)
    print(summary.group(0) if summary else "no summary line found")

    if result.returncode != 0 or unexpected or not or_satisfied:
        print(f"FOCUS conformance FAILED — unexpected rule failures: {sorted(unexpected)}")
        print(output[-3000:])
        return 1
    print("FOCUS 1.2 conformance gate: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
