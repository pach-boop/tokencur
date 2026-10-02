"""Save RunPod's billing history as an export ``tokencur import`` reads.

Usage:
    python scripts/fetch_runpod_billing.py [--since 2026-01-01] [--until 2026-10-02]
        [--bucket day] [--out FILE]
    tokencur import runpod FILE

Calls RunPod's REST billing API, ``GET /v1/billing/pods`` (one row per
pod and bucket: amount, timeBilledMs, diskSpaceBilledGB, podId, time),
and writes the response wrapped as ``{"captured_at", "request_url",
"response"}``, readable by its owner only. The API key comes from
``$RUNPOD_API_KEY`` or ``~/.config/runpod/api_key`` and is sent only in
the Authorization header, never written to the file. tokencur itself
makes no network calls; this script is the one step that does, when you
run it.

Findings that shaped it: the GraphQL ``myself.dailyCharges`` query
returned an empty list for a period with real charges, so the REST
endpoint is the source; and Cloudflare rejects urllib's default
User-Agent (HTTP 403, code 1010), so a real one is sent.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.request
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

ENDPOINT = "https://rest.runpod.io/v1/billing/pods"
USER_AGENT = "tokencur (+https://github.com/pach-boop/tokencur)"
RAW = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / (
    "tokencur/raw/runpod"
)


def billing_url(since: date, until: date, bucket: str = "day") -> str:
    """The request for [since, until), as the API expects it (UTC, ISO 8601)."""
    query = {
        "bucketSize": bucket,
        "startTime": f"{since.isoformat()}T00:00:00Z",
        "endTime": f"{until.isoformat()}T00:00:00Z",
    }
    return f"{ENDPOINT}?{urlencode(query)}"


def _api_key() -> str:
    env = os.environ.get("RUNPOD_API_KEY")
    if env:
        return env.strip()
    return (Path.home() / ".config/runpod/api_key").read_text(encoding="utf-8").strip()


def main() -> int:
    today = datetime.now(UTC).date()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--since", type=date.fromisoformat, default=today.replace(month=1, day=1)
    )
    parser.add_argument(
        "--until", type=date.fromisoformat, default=today + timedelta(days=1)
    )
    parser.add_argument(
        "--bucket", choices=["hour", "day", "week", "month"], default="day"
    )
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    url = billing_url(args.since, args.until, args.bucket)
    request = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {_api_key()}", "User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        rows = json.load(response)

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = args.out or RAW / f"billing_pods_{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    out.write_text(
        json.dumps(
            {"captured_at": stamp, "request_url": url, "response": rows}, indent=1
        ),
        encoding="utf-8",
    )
    out.chmod(0o600)
    print(f"{out}: {len(rows) if isinstance(rows, list) else 0} rows")
    print(f"next: tokencur import runpod {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
