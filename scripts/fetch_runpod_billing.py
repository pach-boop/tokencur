"""Save RunPod's billing history as exports ``tokencur import`` reads.

Usage:
    python scripts/fetch_runpod_billing.py [--since 2026-01-01] [--until 2026-10-02]
        [--bucket day] [--dir DIR]
    tokencur import runpod FILE...

Calls RunPod's API v2 twice, ``GET /v2/billing/pods`` (one record per pod
and bucket: GPU, CPU, disk and total dollars) and
``GET /v2/billing/network-volumes`` (one per network volume and bucket:
the storage that the pod records leave out). Each response is written
wrapped as ``{"captured_at", "request_url", "response"}``, one file per
request, readable by its owner only. The API key comes from
``$RUNPOD_API_KEY`` or ``~/.config/runpod/api_key`` and is sent only in
the Authorization header, never written to a file. tokencur itself makes
no network calls; this script is the one step that does, when you run it.

Findings that shaped it: the GraphQL ``myself.dailyCharges`` query
returned an empty list for a period with real charges, so the REST
endpoints are the source; Cloudflare rejects urllib's default User-Agent
(HTTP 403, code 1010), so a real one is sent; and REST API v1
(``rest.runpod.io/v1/billing/pods``, which also gave GPU time) went away
between 14 and 17 November 2026. Its saved captures still import.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.request
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

API = "https://api.runpod.io/v2/billing"
# File name prefix -> endpoint. Pods first: they are most of the money.
ENDPOINTS = {"billing_pods": "pods", "billing_network_volumes": "network-volumes"}
USER_AGENT = "tokencur (+https://github.com/pach-boop/tokencur)"
RAW = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / (
    "tokencur/raw/runpod"
)


def billing_url(endpoint: str, since: date, until: date, bucket: str = "day") -> str:
    """The request for [since, until), as the API expects it (UTC, RFC 3339)."""
    query = {
        "bucketSize": bucket,
        "startTime": f"{since.isoformat()}T00:00:00Z",
        "endTime": f"{until.isoformat()}T00:00:00Z",
    }
    return f"{API}/{endpoint}?{urlencode(query)}"


def _api_key() -> str:
    env = os.environ.get("RUNPOD_API_KEY")
    if env:
        return env.strip()
    return (Path.home() / ".config/runpod/api_key").read_text(encoding="utf-8").strip()


def _get(url: str, key: str) -> object:
    request = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {key}", "User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def _save(out: Path, stamp: str, url: str, response: object) -> None:
    out.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    out.write_text(
        json.dumps(
            {"captured_at": stamp, "request_url": url, "response": response},
            indent=1,
        ),
        encoding="utf-8",
    )
    out.chmod(0o600)


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
    parser.add_argument("--dir", type=Path, default=RAW, help=f"default: {RAW}")
    args = parser.parse_args()

    key = _api_key()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    saved = []
    for prefix, endpoint in ENDPOINTS.items():
        url = billing_url(endpoint, args.since, args.until, args.bucket)
        response = _get(url, key)
        out = args.dir / f"{prefix}_{stamp}.json"
        _save(out, stamp, url, response)
        records = response.get("records") if isinstance(response, dict) else None
        print(f"{out}: {len(records) if isinstance(records, list) else 0} records")
        saved.append(str(out))
    print(f"next: tokencur import runpod {' '.join(saved)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
