"""Probe RunPod's billing API — groundwork for the RunPod ingester.

Reads the API key from ~/.config/runpod/api_key (or $RUNPOD_API_KEY)
and prints the account's daily charge history. Findings so far:

- Auth: ``Authorization: Bearer <key>`` against the GraphQL endpoint;
  a real User-Agent is required (Cloudflare rejects urllib's default
  with HTTP 403 code 1010).
- ``myself.dailyCharges`` returns per-day ``ClientCreditCharge`` rows:
  amount, updatedAt, and a podCharges/diskCharges/apiCharges/
  serverlessCharges breakdown — the natural source for FOCUS rows
  (real *billed* credits, not showback).
- ``clientLifetimeSpend`` needs broader key permissions than a
  restricted read key; the ingester should not depend on it.
- The daily window only covers recent activity: an idle account
  returns an empty list.

Usage:
    python scripts/probe_runpod.py
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

QUERY = (
    "query { myself { clientBalance currentSpendPerHr dailyCharges { "
    "updatedAt amount podCharges diskCharges apiCharges serverlessCharges type } } }"
)


def _api_key() -> str:
    env = os.environ.get("RUNPOD_API_KEY")
    if env:
        return env.strip()
    return (Path.home() / ".config/runpod/api_key").read_text().strip()


def main() -> int:
    req = urllib.request.Request(
        "https://api.runpod.io/graphql",
        data=json.dumps({"query": QUERY}).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {_api_key()}",
            "User-Agent": "tokencur/0.1 (+https://github.com/pach-boop/tokencur)",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.load(resp)

    if payload.get("errors"):
        print(json.dumps(payload["errors"], indent=1))
        return 1
    me = payload["data"]["myself"]
    charges = me["dailyCharges"] or []
    print(f"balance: ${me['clientBalance']:.2f} | now: ${me['currentSpendPerHr']}/hr")
    print(f"daily charges: {len(charges)} rows")
    for charge in charges:
        print(json.dumps(charge))
    if not charges:
        print("(empty: no recent activity in the API's daily window)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
