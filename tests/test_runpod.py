import dataclasses
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from tokencur import cli, ledger, sources
from tokencur.focus import charge_rows
from tokencur.ingest import claude_code, runpod

EXPORT = Path(__file__).parent / "fixtures" / "billing" / "runpod-billing-pods.json"


def test_an_export_becomes_billed_charges():
    charges = list(runpod.iter_charges(EXPORT))

    # The fifth row's amount is text, not money: skipped, never guessed at.
    assert [c.record_id for c in charges] == [
        "pod-alpha@2026-09-27T00:00:00Z",
        "pod-alpha@2026-09-30T00:00:00Z",
        "pod-beta@2026-09-30T00:00:00Z",
        "pod-beta@2026-10-01T00:00:00Z",
    ]
    first = charges[0]
    assert (first.period_start, first.period_end) == (
        "2026-09-27T00:00:00Z",
        "2026-09-28T00:00:00Z",
    )
    assert (first.provider, first.service, first.resource_id) == (
        "RunPod",
        "RunPod Pods",
        "pod-alpha",
    )
    assert (first.quantity, first.unit, first.amount_usd) == (1.0, "Hours", 0.42)
    assert sum(c.amount_usd for c in charges) == pytest.approx(1.8125)


def test_a_bare_api_response_and_an_hourly_bucket_also_read(tmp_path):
    rows = json.loads(EXPORT.read_text(encoding="utf-8"))["response"][:1]
    bare = tmp_path / "bare.json"
    bare.write_text(json.dumps(rows), encoding="utf-8")
    hourly = tmp_path / "hourly.json"
    url = "https://rest.runpod.io/v1/billing/pods?bucketSize=hour"
    hourly.write_text(
        json.dumps({"request_url": url, "response": rows}), encoding="utf-8"
    )

    (from_bare,) = runpod.iter_charges(bare)
    (from_hourly,) = runpod.iter_charges(hourly)

    assert from_bare.period_end == "2026-09-28T00:00:00Z"
    assert from_hourly.period_end == "2026-09-27T01:00:00Z"


def test_charges_are_kept_once_in_the_ledger(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    charges = list(runpod.iter_charges(EXPORT))

    assert ledger.record_charges(charges, path) == 4
    assert ledger.record_charges(charges, path) == 0
    assert ledger.read_charges(path) == charges
    with closing(sqlite3.connect(path)) as conn:
        assert (
            conn.execute("PRAGMA user_version").fetchone()[0] == ledger.SCHEMA_VERSION
        )


def test_billed_charges_are_focus_compute_rows_with_real_billed_cost():
    rows = list(charge_rows(runpod.iter_charges(EXPORT)))

    first = rows[0]
    assert first["BilledCost"] == first["EffectiveCost"] == first["ListCost"] == 0.42
    assert (first["ServiceCategory"], first["ServiceName"]) == (
        "Compute",
        "RunPod Pods",
    )
    assert (first["ConsumedQuantity"], first["ConsumedUnit"]) == (1.0, "Hours")
    assert (first["ChargePeriodStart"], first["ChargePeriodEnd"]) == (
        "2026-09-27T00:00:00Z",
        "2026-09-28T00:00:00Z",
    )
    # A stopped pod billed only for its disk: priced per GB, since FOCUS
    # 1.3 wants a unit price on every usage row with a SKU price.
    disk_only = rows[2]
    assert (disk_only["ConsumedQuantity"], disk_only["ConsumedUnit"]) == (20.0, "GB")
    assert disk_only["ListUnitPrice"] == pytest.approx(0.0125 / 20)
    assert (disk_only["SkuId"], disk_only["SkuMeter"]) == ("runpod/gb", "GPU pod disk")
    assert disk_only["BilledCost"] == 0.0125


def test_list_and_contracted_cost_are_the_unit_price_times_the_quantity():
    """FOCUS checks cost = unit price x quantity exactly. 0.07 / 1.1 * 1.1
    is not 0.07 in floating point, so ListCost and ContractedCost come
    from the unit price; BilledCost stays what RunPod took."""
    charge = next(runpod.iter_charges(EXPORT))
    odd = dataclasses.replace(charge, quantity=1.1, amount_usd=0.07)

    (row,) = charge_rows([odd])

    assert row["BilledCost"] == row["EffectiveCost"] == 0.07
    for cost, price in (
        ("ListCost", "ListUnitPrice"),
        ("ContractedCost", "ContractedUnitPrice"),
    ):
        assert row[cost] == row[price] * row["PricingQuantity"]
        assert row[cost] == pytest.approx(0.07)


def _agent_call(root: Path) -> None:
    """One $5.00 Claude Code call on 2026-09-30."""
    log = root / "workspace" / "a.jsonl"
    log.parent.mkdir(parents=True)
    line = {
        "type": "assistant",
        "requestId": "req_a",
        "timestamp": "2026-09-30T12:00:00.000Z",
        "message": {
            "id": "msg_a",
            "model": "claude-opus-4-8",
            "usage": {"input_tokens": 1_000_000},
        },
    }
    log.write_text(json.dumps(line), encoding="utf-8")


def test_import_then_report_and_export_show_real_money(tmp_path, monkeypatch, capsys):
    _agent_call(tmp_path / "projects")
    monkeypatch.setattr(
        sources,
        "DEFAULT_SOURCES",
        ((tmp_path / "projects", claude_code.iter_usage_records),),
    )

    assert cli.main(["import", "runpod", str(EXPORT)]) == 0
    assert "4 billed charges (4 new), $1.81 from RunPod" in capsys.readouterr().out

    assert cli.main(["report"]) == 0
    out = capsys.readouterr().out
    assert "API-EQUIVALENT TOTAL (showback): $5.00" in out
    assert "BILLED (real money, from provider bills): $1.81" in out

    csv_out = tmp_path / "all.csv"
    assert cli.main(["export", str(csv_out), "--since", "2026-10-01"]) == 0
    text = csv_out.read_text(encoding="utf-8")
    assert "RunPod Pods" in text  # the 2026-10-01 charge
    assert "2026-09-27" not in text  # outside the period


def test_the_fetch_script_asks_for_the_period_the_way_the_api_expects():
    import importlib.util
    from datetime import date

    script = Path(__file__).parent.parent / "scripts" / "fetch_runpod_billing.py"
    spec = importlib.util.spec_from_file_location("fetch_runpod_billing", script)
    fetch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fetch)

    url = fetch.billing_url(date(2026, 1, 1), date(2026, 10, 2))

    # The same request that produced the export saved on 2026-10-01.
    assert url == (
        "https://rest.runpod.io/v1/billing/pods?bucketSize=day"
        "&startTime=2026-01-01T00%3A00%3A00Z&endTime=2026-10-02T00%3A00%3A00Z"
    )
