"""Ingest RunPod billing exports: billed cost, not showback.

``scripts/fetch_runpod_billing.py`` saves RunPod's billing responses
wrapped as ``{"captured_at", "request_url", "response"}``; a bare
response reads too. Two shapes read:

- API v2 (``GET https://api.runpod.io/v2/billing/pods`` and
  ``/v2/billing/network-volumes``): ``{"records": [...], "metadata"}``.
  A pod record has its bucket (``startTime``, ``endTime``), the
  ``podId`` and the dollars billed (``totalAmount``), split into
  ``gpuAmount``, ``cpuAmount`` and ``diskAmount``. A network volume
  record has the ``networkVolumeId`` and ``totalAmount``, split into
  ``standardAmount`` and ``highPerformanceAmount``: the storage the pod
  records leave out. v2 gives no usage time, so its charges are stated
  over their bucket, in hours (ADR 0020).
- REST API v1 (``GET https://rest.runpod.io/v1/billing/pods``, gone
  since November 2026), whose saved captures still import: a list of
  rows with the dollars (``amount``), the GPU time billed
  (``timeBilledMs``), the disk billed (``diskSpaceBilledGB``), the
  ``podId`` and the bucket's start (``time``, UTC). The bucket size is
  in the request.

A pod's charge for a bucket has the same id in both, so a v2 capture
updates the money of a charge a v1 capture brought, and the ledger keeps
the GPU time v1 measured. Amounts are credits RunPod actually charged,
so they become billed cost as they are. A malformed row is skipped,
never guessed at.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import TypeGuard
from urllib.parse import parse_qs, urlparse

from tokencur.ingest.fields import text, time_span
from tokencur.records import BilledCharge, parse_timestamp

_STEP = {
    "hour": timedelta(hours=1),
    "day": timedelta(days=1),
    "week": timedelta(weeks=1),
}
# How a v2 record's total splits, for the note people read.
_Parts = tuple[tuple[str, str], ...]
_POD_PARTS: _Parts = (
    ("gpuAmount", "GPU"),
    ("cpuAmount", "CPU"),
    ("diskAmount", "disk"),
)
_VOLUME_PARTS: _Parts = (
    ("standardAmount", "standard storage"),
    ("highPerformanceAmount", "high-performance storage"),
)
_STATED = "no usage time in RunPod's API v2: stated over the period"


def iter_charges(path: Path) -> Iterator[BilledCharge]:
    """Yield one BilledCharge per pod or volume and billing bucket in an export."""
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except ValueError:
        return
    if isinstance(data, dict) and "response" in data:
        response, url = data["response"], text(data.get("request_url"))
    else:
        response, url = data, ""
    if isinstance(response, dict):
        records = response.get("records")
        rows = records if isinstance(records, list) else []
        charges = (_v2_charge(row) for row in rows)
    elif isinstance(response, list):
        bucket = parse_qs(urlparse(url).query).get("bucketSize", ["day"])[0]
        charges = (_v1_charge(row, bucket) for row in response)
    else:
        return
    yield from (charge for charge in charges if charge is not None)


def _v2_charge(row: object) -> BilledCharge | None:
    if not isinstance(row, dict):
        return None
    period = time_span(row.get("startTime"), row.get("endTime"))
    amount = row.get("totalAmount")
    pod, volume = text(row.get("podId")), text(row.get("networkVolumeId"))
    if period is None or not _money(amount) or bool(pod) == bool(volume):
        return None
    start, end = period
    if pod:
        cpu_only = _part(row, "cpuAmount") and not _part(row, "gpuAmount")
        service, resource = "RunPod Pods", pod
        kind, parts = ("CPU pod" if cpu_only else "GPU pod"), _POD_PARTS
    else:
        service, resource = "RunPod Network Volumes", volume
        kind, parts = "network volume", _VOLUME_PARTS
    split = ", ".join(
        f"{label} ${_part(row, key):.4f}" for key, label in parts if _part(row, key)
    )
    begin = _fmt(start)
    return BilledCharge(
        source="runpod",
        record_id=f"{resource}@{begin}",
        period_start=begin,
        period_end=_fmt(end),
        provider="RunPod",
        service=service,
        resource_id=resource,
        resource_type=kind,
        quantity=(end - start).total_seconds() / 3600,
        unit="Hours",
        amount_usd=float(amount),
        detail="; ".join(note for note in (split, _STATED) if note),
        quantity_stated=True,
    )


def _part(row: dict, key: str) -> float:
    """One part of a v2 record's total, or 0 when it is not money."""
    value = row.get(key)
    return float(value) if _money(value) else 0.0


def _v1_charge(row: object, bucket: str) -> BilledCharge | None:
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
    disk_gb = disk if _money(disk) and disk else 0
    if ms or not disk_gb:
        quantity, unit = (ms or 0) / 3_600_000, "Hours"
        detail = f"{disk_gb:g} GB disk" if disk_gb else ""
    else:
        # No pod time billed (a stopped pod): the charge is for its disk.
        quantity, unit, detail = float(disk_gb), "GB", "disk only"
    return BilledCharge(
        source="runpod",
        record_id=f"{pod}@{begin}",
        period_start=begin,
        period_end=_fmt(_end(start, bucket)),
        provider="RunPod",
        service="RunPod Pods",
        resource_id=pod,
        resource_type="GPU pod",
        quantity=quantity,
        unit=unit,
        amount_usd=float(amount),
        detail=detail,
    )


def _money(value: object) -> TypeGuard[float]:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    return math.isfinite(value) and value >= 0


def _end(start: datetime, bucket: str) -> datetime:
    if bucket == "month":
        year, month = divmod(start.month, 12)
        return start.replace(year=start.year + year, month=month + 1, day=1)
    return start + _STEP.get(bucket, _STEP["day"])


def _fmt(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")
