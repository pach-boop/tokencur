import dataclasses
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from tokencur import cli, ledger, sources
from tokencur.focus import charge_rows
from tokencur.ingest import claude_code, runpod

BILLING = Path(__file__).parent / "fixtures" / "billing"
EXPORT = BILLING / "runpod-billing-pods.json"  # REST API v1, as saved until 2026-11
PODS_V2 = BILLING / "runpod-billing-pods-v2.json"
VOLUMES_V2 = BILLING / "runpod-billing-network-volumes.json"


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


def test_api_v2_pod_records_are_charges_stated_over_their_bucket():
    charges = list(runpod.iter_charges(PODS_V2))

    # A total written as text and a bucket that ends where it starts are
    # not charges: skipped, never guessed at.
    assert [c.record_id for c in charges] == [
        "pod-alpha@2026-09-27T00:00:00Z",
        "pod-delta@2026-11-15T00:00:00Z",
        "pod-epsilon@2026-11-16T00:00:00Z",
    ]
    first = charges[0]
    assert (first.period_start, first.period_end) == (
        "2026-09-27T00:00:00Z",
        "2026-09-28T00:00:00Z",
    )
    assert (first.service, first.resource_type, first.amount_usd) == (
        "RunPod Pods",
        "GPU pod",
        0.42,
    )
    # v2 gives no GPU time: a day's charge is stated over its 24 hours.
    assert (first.quantity, first.unit, first.quantity_stated) == (24.0, "Hours", True)
    assert first.detail == (
        "GPU $0.4000, disk $0.0200; "
        "no usage time in RunPod's API v2: stated over the period"
    )
    assert charges[2].resource_type == "CPU pod"


def test_a_bare_v2_response_also_reads(tmp_path):
    response = json.loads(PODS_V2.read_text(encoding="utf-8"))["response"]
    bare = tmp_path / "bare.json"
    bare.write_text(json.dumps(response), encoding="utf-8")

    assert list(runpod.iter_charges(bare)) == list(runpod.iter_charges(PODS_V2))


def test_network_volumes_are_their_own_service_and_focus_storage_rows():
    charges = list(runpod.iter_charges(VOLUMES_V2))

    assert [c.record_id for c in charges] == [
        "vol-one@2026-11-15T00:00:00Z",
        "vol-one@2026-11-16T00:00:00Z",
    ]
    assert (charges[0].service, charges[0].resource_type) == (
        "RunPod Network Volumes",
        "network volume",
    )
    assert charges[0].detail.startswith("standard storage $0.0467; ")

    row = next(charge_rows(charges))
    assert (row["ServiceCategory"], row["ServiceSubcategory"]) == (
        "Storage",
        "File Storage",
    )
    assert (row["SkuId"], row["SkuMeter"]) == (
        "runpod/network-volume/hours",
        "network volume time",
    )
    assert row["BilledCost"] == 0.0467
    assert row["ListCost"] == pytest.approx(0.0467)


def test_a_v2_capture_keeps_the_gpu_time_v1_measured(tmp_path):
    """The same pod and day in both APIs is the same charge. The newer
    bill sets the money; the usage stays the one a bill measured."""
    path = tmp_path / "ledger.sqlite3"
    measured = next(runpod.iter_charges(EXPORT))
    stated = next(runpod.iter_charges(PODS_V2))
    assert measured.record_id == stated.record_id
    revised = dataclasses.replace(stated, amount_usd=0.43)

    ledger.record_charges([measured], path)
    assert ledger.record_charges([revised], path) == 0

    (kept,) = ledger.read_charges(path)
    assert kept == dataclasses.replace(measured, amount_usd=0.43)
    assert (kept.quantity, kept.unit, kept.detail) == (1.0, "Hours", "50 GB disk")


def test_a_measured_charge_replaces_a_stated_one(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    measured = next(runpod.iter_charges(EXPORT))
    stated = next(runpod.iter_charges(PODS_V2))

    ledger.record_charges([stated], path)
    ledger.record_charges([measured], path)

    assert ledger.read_charges(path) == [measured]


def test_schema_9_charges_learn_which_quantities_were_stated(tmp_path, capsys):
    path = tmp_path / "ledger.sqlite3"
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute(ledger._V4_CHARGES)
        conn.execute("ALTER TABLE charges ADD COLUMN list_usd REAL")
        conn.execute("PRAGMA user_version = 9")
        for record_id, unit, detail in (
            ("pod-a@2026-09-27T00:00:00Z", "Hours", "50 GB disk"),
            (
                "line@2026-11-01",
                "Hours",
                "no quantity in the report: stated over the period",
            ),
        ):
            conn.execute(
                f"INSERT INTO charges VALUES ({', '.join('?' * 14)})",
                (
                    "runpod" if record_id.startswith("pod") else "anthropic",
                    record_id,
                    "2026-09-27T00:00:00Z",
                    "2026-09-28T00:00:00Z",
                    "Some provider",
                    "Some service",
                    "some resource",
                    "some type",
                    24.0,
                    unit,
                    0.42,
                    detail,
                    "2026-10-01T00:00:00Z",
                    None,
                ),
            )

    charges = ledger.read_charges(path)

    stated = {c.record_id: c.quantity_stated for c in charges}
    assert stated == {"pod-a@2026-09-27T00:00:00Z": False, "line@2026-11-01": True}
    assert "schema 10" in capsys.readouterr().err
    assert (tmp_path / "ledger.sqlite3.schema-9.bak").exists()


def _fetch_script():
    import importlib.util

    script = Path(__file__).parent.parent / "scripts" / "fetch_runpod_billing.py"
    spec = importlib.util.spec_from_file_location("fetch_runpod_billing", script)
    fetch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fetch)
    return fetch


def test_the_fetch_script_asks_for_the_period_the_way_the_api_expects():
    from datetime import date

    fetch = _fetch_script()

    urls = [
        fetch.billing_url(endpoint, date(2026, 1, 1), date(2026, 11, 19))
        for endpoint in fetch.ENDPOINTS.values()
    ]

    # The same requests that answered on 2026-11-18.
    query = "bucketSize=day&startTime=2026-01-01T00%3A00%3A00Z&endTime=2026-11-19T00%3A00%3A00Z"
    assert urls == [
        f"https://api.runpod.io/v2/billing/pods?{query}",
        f"https://api.runpod.io/v2/billing/network-volumes?{query}",
    ]


def test_the_fetch_script_files_import_together(tmp_path, monkeypatch, capsys):
    _agent_call(tmp_path / "projects")  # a report needs some usage
    monkeypatch.setattr(
        sources,
        "DEFAULT_SOURCES",
        ((tmp_path / "projects", claude_code.iter_usage_records),),
    )

    assert cli.main(["import", "runpod", str(PODS_V2), str(VOLUMES_V2)]) == 0
    assert "5 billed charges (5 new), $1.81 from RunPod" in capsys.readouterr().out

    assert cli.main(["report"]) == 0
    out = capsys.readouterr().out
    assert "  RunPod Network Volumes $0.09  (2 charges)" in out
    assert "  RunPod Pods            $1.72  (3 charges)" in out
