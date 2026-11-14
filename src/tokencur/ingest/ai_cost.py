"""Ingest the admin cost reports of Anthropic and OpenAI: billed cost.

Three documented response shapes read here (ADR 0018):

- Anthropic ``GET /v1/organizations/cost_report`` (marked Legacy): pages
  of daily buckets, ``{starting_at, ending_at, results: [...]}``, each
  result an ``amount`` in cents as text ("123.45" is $1.23) with the
  dimensions it was grouped by (``description``, ``model``,
  ``token_type``, ``workspace_id``, ...), null when not grouped.
- Anthropic ``GET /v1/organizations/analytics/cost`` (beta): flat rows,
  ``{starting_at, ending_at, group: {...}, cost: {amount, list_amount,
  currency, requests}}``, amounts in cents as text, ``amount`` after
  discounts and before credits, ``list_amount`` at list price.
- OpenAI ``GET /v1/organization/costs``: pages of daily buckets,
  ``{start_time, end_time, results: [...]}`` in Unix seconds, each result
  ``{amount: {value, currency}, line_item, project_id}``. The spec does
  not state the unit of ``value``; its example reads as dollars, and
  dollars is what this reads.

These shapes come from the providers' documentation, not from a real
export: the fixtures are built from it. A file holds one response page,
a list of pages, or either wrapped as ``{"captured_at", "request_url",
"response"}`` like ``scripts/fetch_runpod_billing.py`` saves. A line
that is not money in USD is skipped, never guessed at.

The same money can be reported under different groupings (by model, by
workspace, per hour or per day, legacy or beta). Each charge's record id
starts with its grouping, so an import can refuse a grouping that would
count days already in the ledger a second time (``conflicts``).
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from tokencur.ingest.fields import obj, text
from tokencur.records import BilledCharge, parse_timestamp

# Dimensions of a legacy Anthropic result, in the order a detail names them.
_LEGACY_DIMENSIONS = (
    "description",
    "cost_type",
    "model",
    "token_type",
    "context_window",
    "service_tier",
    "speed",
    "inference_geo",
    "workspace_id",
)
_OPENAI_DIMENSIONS = ("line_item", "project_id")
_OPENAI_RESULT = "organization.costs.result"
# The sources this module ingests: AI model usage whose charges carry
# their grouping (and so can clash).
SOURCES = frozenset({"anthropic", "openai"})
_WIDTHS = {"1d": "86400s", "1h": "3600s", "1m": "60s"}


@dataclass(frozen=True, slots=True)
class _Line:
    """One priced line of a cost report, before it becomes a charge."""

    start: datetime
    end: datetime
    dimensions: dict[str, str]
    amount: float
    list_amount: float | None = None
    requests: int | None = None


@dataclass(frozen=True, slots=True)
class Conflict:
    """Two groupings of the same source's cost covering the same time."""

    source: str
    day: str  # first UTC day both cover
    kept: str  # grouping already in the ledger (or in another file)
    incoming: str  # grouping being imported


def iter_anthropic(path: Path) -> Iterator[BilledCharge]:
    """Yield one BilledCharge per line of an Anthropic cost report, in
    either the legacy ``cost_report`` shape or the beta ``analytics/cost``
    one."""
    pages, url = _pages(path)
    rows = [row for page in pages for row in _list(page.get("data"))]
    if any("cost" in row or "group" in row for row in rows):
        shape, lines = "analytics", [_analytics_line(r) for r in rows]
        width = "time_bucket_width"
    else:
        shape, width = "cost_report", "bucket_width"
        lines = [
            _legacy_line(bucket, result)
            for bucket in rows
            for result in _list(bucket.get("results"))
        ]
    yield from _charges(
        "anthropic", shape, [x for x in lines if x], _grouping(url, width)
    )


def iter_openai(path: Path) -> Iterator[BilledCharge]:
    """Yield one BilledCharge per line of an OpenAI organization costs
    report."""
    pages, url = _pages(path)
    lines = [
        _openai_line(bucket, result)
        for page in pages
        for bucket in _list(page.get("data"))
        # The spec names the array ``result``; its examples, ``results``.
        for result in _list(bucket.get("results", bucket.get("result")))
    ]
    yield from _charges(
        "openai", "costs", [x for x in lines if x], _grouping(url, "bucket_width")
    )


def grouping(charge: BilledCharge) -> str:
    """The grouping a charge was reported under: the start of its id."""
    return charge.record_id.split("|", 1)[0]


def conflicts(
    incoming: Iterable[BilledCharge], kept: Iterable[BilledCharge]
) -> list[Conflict]:
    """Where incoming charges cover time that charges of the same source
    under another grouping already cover, in the ledger or in the import
    itself: summing both would count that money twice."""
    new = [c for c in incoming if c.source in SOURCES]
    seen: dict[tuple[str, str], list[BilledCharge]] = {}
    for charge in [*(c for c in kept if c.source in SOURCES), *new]:
        seen.setdefault((charge.source, grouping(charge)), []).append(charge)
    found: dict[tuple[str, str, str], Conflict] = {}
    for charge in new:
        mine = grouping(charge)
        for (source, other), charges in seen.items():
            if source != charge.source or other == mine:
                continue
            for c in charges:
                if (
                    c.period_start < charge.period_end
                    and charge.period_start < c.period_end
                ):
                    day = max(c.period_start, charge.period_start)[:10]
                    first, second = sorted((mine, other))
                    key = (source, first, second)
                    if key not in found or day < found[key].day:
                        found[key] = Conflict(source, day, other, mine)
                    break
    return sorted(found.values(), key=lambda c: (c.source, c.day))


def _pages(path: Path) -> tuple[list[dict], str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except ValueError:
        return [], ""
    url = ""
    if isinstance(data, dict) and "response" in data:
        data, url = data["response"], text(data.get("request_url"))
    if isinstance(data, dict):
        return [data], url
    return [page for page in _list(data) if isinstance(page, dict)], url


def _list(value: object) -> list[dict]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def _grouping(url: str, width_parameter: str) -> tuple[list[str], str] | None:
    """The grouping a saved request asked for, when its URL was saved."""
    if not url:
        return None
    query = parse_qs(urlparse(url).query)
    groups = sorted(
        {g for key in ("group_by[]", "group_by") for g in query.get(key, [])}
    )
    width = query.get(width_parameter, [""])[0]
    # Named like the widths inferred from the lines, in seconds.
    return groups, _WIDTHS.get(width, width or "range")


def _legacy_line(bucket: dict, result: dict) -> _Line | None:
    span = _span(bucket.get("starting_at"), bucket.get("ending_at"))
    amount = _cents(result.get("amount"))
    if span is None or amount is None or not _usd(result.get("currency")):
        return None
    dimensions = {k: v for k in _LEGACY_DIMENSIONS if (v := _value(result.get(k)))}
    return _Line(*span, dimensions, amount)


def _analytics_line(row: dict) -> _Line | None:
    span = _span(row.get("starting_at"), row.get("ending_at"))
    cost = obj(row.get("cost"))
    amount = _cents(cost.get("amount"))
    if span is None or amount is None or not _usd(cost.get("currency")):
        return None
    group = obj(row.get("group"))
    dimensions = {k: v for k in sorted(group) if (v := _value(group[k]))}
    requests = cost.get("requests")
    return _Line(
        *span,
        dimensions,
        amount,
        list_amount=_cents(cost.get("list_amount")),
        requests=requests if type(requests) is int and requests >= 0 else None,
    )


def _openai_line(bucket: dict, result: dict) -> _Line | None:
    if result.get("object", _OPENAI_RESULT) != _OPENAI_RESULT:
        return None  # a usage result: the usage endpoints share this page shape
    start, end = (_unix(bucket.get(k)) for k in ("start_time", "end_time"))
    money = obj(result.get("amount"))
    value = money.get("value")
    if start is None or end is None or end <= start or not _usd(money.get("currency")):
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    dimensions = {k: v for k in _OPENAI_DIMENSIONS if (v := _value(result.get(k)))}
    return _Line(start, end, dimensions, float(value))


def _charges(
    source: str, shape: str, lines: list[_Line], asked: tuple[list[str], str] | None
) -> Iterator[BilledCharge]:
    if asked is not None:
        groups, width = asked
    else:
        # No saved request: infer it from what the lines are split by.
        seen = {k for line in lines for k in line.dimensions}
        if shape == "cost_report":
            # Grouping by description fills in every descriptive field.
            groups = sorted(
                {"description" for k in seen if k != "workspace_id"}
                | (seen & {"workspace_id"})
            )
        else:
            groups = sorted(seen)
        widths = {int((x.end - x.start).total_seconds()) for x in lines}
        width = f"{widths.pop()}s" if len(widths) == 1 else "mixed"
    prefix = f"{shape}:{'+'.join(groups) or 'total'}@{width}"
    provider = {"anthropic": "Anthropic", "openai": "OpenAI"}[source]
    service = {"anthropic": "Claude API", "openai": "OpenAI API"}[source]
    for line in lines:
        yield _charge(source, prefix, provider, service, line)


def _charge(
    source: str, prefix: str, provider: str, service: str, line: _Line
) -> BilledCharge:
    begin, end = _fmt(line.start), _fmt(line.end)
    dims = line.dimensions
    named = next((k for k in ("model", "line_item", "cost_type") if k in dims), "")
    notes = [
        v if k == "description" else f"{k} {v}" for k, v in dims.items() if k != named
    ]
    if line.requests:
        quantity, unit = float(line.requests), "Requests"
    else:
        # FOCUS wants a quantity on every usage row and the report has
        # none: the charge is stated over its period, in hours.
        quantity = (line.end - line.start).total_seconds() / 3600
        unit = "Hours"
        notes.append("no quantity in the report: stated over the period")
    key = ";".join(f"{k}={v}" for k, v in sorted(dims.items()))
    return BilledCharge(
        source=source,
        record_id=f"{prefix}|{begin}|{end}|{key}",
        period_start=begin,
        period_end=end,
        provider=provider,
        service=service,
        resource_id=dims.get(named, "organization"),
        resource_type="AI model" if dims.get("model") else "API cost line",
        quantity=quantity,
        unit=unit,
        amount_usd=line.amount,
        detail=", ".join(notes),
        list_usd=line.list_amount,
    )


def _span(start: object, end: object) -> tuple[datetime, datetime] | None:
    begin = parse_timestamp(start) if isinstance(start, str) else None
    finish = parse_timestamp(end) if isinstance(end, str) else None
    if begin is None or finish is None or finish <= begin:
        return None
    return begin, finish


def _cents(value: object) -> float | None:
    """Dollars from an amount in cents written as text ("123.45")."""
    if not isinstance(value, str):
        return None
    try:
        cents = float(value)
    except ValueError:
        return None
    return cents / 100 if math.isfinite(cents) and cents >= 0 else None


def _usd(value: object) -> bool:
    return isinstance(value, str) and value.upper() == "USD"


def _value(value: object) -> str:
    """A dimension's value as text; null, empty or odd values read as none."""
    if isinstance(value, bool):
        return ""
    if isinstance(value, int | str):
        return str(value)
    return ""


def _unix(value: object) -> datetime | None:
    if type(value) is not int or value < 0:
        return None
    return datetime.fromtimestamp(value, UTC)


def _fmt(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
