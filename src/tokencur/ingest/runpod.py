"""Ingest RunPod billing exports: billed cost, not showback.

RunPod's REST billing API (``GET https://rest.runpod.io/v1/billing/pods``
with ``bucketSize``, ``startTime`` and ``endTime``) returns one row per
pod and time bucket: the dollars billed (``amount``), the GPU time
billed (``timeBilledMs``), the disk billed (``diskSpaceBilledGB``), the
``podId`` and the bucket's start (``time``, UTC).
``scripts/fetch_runpod_billing.py`` saves such a response wrapped as
``{"captured_at", "request_url", "response"}``; a bare list of rows
reads too.

Amounts are credits RunPod actually charged, so they become billed cost
as they are. A malformed row is skipped, never guessed at.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from tokencur.ingest.fields import text
from tokencur.records import BilledCharge, parse_timestamp

_STEP = {
    "hour": timedelta(hours=1),
    "day": timedelta(days=1),
    "week": timedelta(weeks=1),
}


def iter_charges(path: Path) -> Iterator[BilledCharge]:
    """Yield one BilledCharge per pod and billing bucket in an export."""
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except ValueError:
        return
    if isinstance(data, dict):
        rows, url = data.get("response"), text(data.get("request_url"))
    else:
        rows, url = data, ""
    if not isinstance(rows, list):
        return
    bucket = parse_qs(urlparse(url).query).get("bucketSize", ["day"])[0]
    for row in rows:
        charge = _charge(row, bucket)
        if charge is not None:
            yield charge


def _charge(row: object, bucket: str) -> BilledCharge | None:
    if not isinstance(row, dict):
        return None
    pod = text(row.get("podId"))
    start = parse_timestamp(row["time"]) if isinstance(row.get("time"), str) else None
    amount, ms, disk = (
        row.get("amount"),
        row.get("timeBilledMs"),
        row.get("diskSpaceBilledGB"),
    )
    if not pod or start is None or not _money(amount):
        return None
    if ms is not None and (type(ms) is not int or ms < 0):
        return None
    begin = _fmt(start)
    return BilledCharge(
        source="runpod",
        record_id=f"{pod}@{begin}",
        period_start=begin,
        period_end=_fmt(_end(start, bucket)),
        provider="RunPod",
        service="RunPod Pods",
        resource_id=pod,
        resource_type="GPU pod",
        quantity=(ms or 0) / 3_600_000,
        unit="Hours",
        amount_usd=float(amount),
        detail=f"{disk:g} GB disk" if _money(disk) and disk else "",
    )


def _money(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _end(start: datetime, bucket: str) -> datetime:
    if bucket == "month":
        year, month = divmod(start.month, 12)
        return start.replace(year=start.year + year, month=month + 1, day=1)
    return start + _STEP.get(bucket, _STEP["day"])


def _fmt(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")
