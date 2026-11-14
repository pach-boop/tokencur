"""Billed cost from Anthropic's and OpenAI's admin cost reports (ADR 0018).

The fixtures follow the response shapes the providers document; no real
export has been seen, so these tests pin the documented shape, not a
verified one.
"""

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from tokencur import cli, ledger
from tokencur.focus import charge_rows
from tokencur.ingest import ai_cost

BILLING = Path(__file__).parent / "fixtures" / "billing"
LEGACY = BILLING / "anthropic-cost-report.json"
ANALYTICS = BILLING / "anthropic-analytics-cost.json"
OPENAI = BILLING / "openai-organization-costs.json"


def test_the_legacy_report_reads_cents_across_pages():
    charges = list(ai_cost.iter_anthropic(LEGACY))

    # Two pages; an empty day has no line; text that is not money is skipped.
    assert [c.amount_usd for c in charges] == [1.2345, 2.5, 0.105]
    first, _, search = charges
    assert (first.period_start, first.period_end) == (
        "2026-11-01T00:00:00Z",
        "2026-11-02T00:00:00Z",
    )
    assert (first.provider, first.service, first.resource_id) == (
        "Anthropic",
        "Claude API",
        "claude-sonnet-4-5",
    )
    assert "token_type uncached_input_tokens" in first.detail
    assert "workspace_id wrkspc_fixture01" in first.detail
    # No quantity in the report: the charge is stated over its day.
    assert (first.quantity, first.unit, first.list_usd) == (24.0, "Hours", None)
    assert (search.resource_id, search.resource_type) == ("web_search", "API cost line")


def test_lines_that_differ_only_in_one_field_stay_apart():
    """The same description can repeat with another token type or tier:
    a line's identity is every field it was grouped by."""
    ids = [c.record_id for c in ai_cost.iter_anthropic(LEGACY)]

    assert len(set(ids)) == len(ids)


def test_the_beta_report_states_list_price_and_requests():
    big, small = ai_cost.iter_anthropic(ANALYTICS)

    # "41280.000000" cents is $412.80 billed; its list price, $516.00.
    assert (big.amount_usd, big.list_usd) == (412.8, 516.0)
    assert (big.quantity, big.unit) == (1200.0, "Requests")
    assert big.resource_id == "claude-sonnet-4-5"
    # requests is null (grouped by token type, say): stated over the day.
    assert (small.quantity, small.unit) == (24.0, "Hours")


def test_openai_reads_both_names_of_the_results_array():
    charges = list(ai_cost.iter_openai(OPENAI))

    # A usage result shares the page shape and a euro line is not USD:
    # both skipped.
    assert [(c.amount_usd, c.resource_id) for c in charges] == [
        (0.06, "gpt-fixture, input"),
        (1.5, "organization"),
    ]
    assert charges[0].provider == "OpenAI"
    assert "project_id proj_fixture01" in charges[0].detail
    assert charges[0].period_start == "2026-11-01T00:00:00Z"


def test_a_file_reads_the_same_with_or_without_its_saved_request(tmp_path):
    wrapped = json.loads(LEGACY.read_text(encoding="utf-8"))
    bare = tmp_path / "bare.json"
    bare.write_text(json.dumps(wrapped["response"]), encoding="utf-8")

    assert list(ai_cost.iter_anthropic(bare)) == list(ai_cost.iter_anthropic(LEGACY))


def test_unreadable_files_yield_nothing(tmp_path):
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    odd = tmp_path / "odd.json"
    odd.write_text(json.dumps({"data": "nope"}), encoding="utf-8")

    assert list(ai_cost.iter_anthropic(broken)) == []
    assert list(ai_cost.iter_openai(odd)) == []


def test_ai_charges_are_focus_ai_rows_with_their_list_price():
    big, _ = charge_rows(ai_cost.iter_anthropic(ANALYTICS))

    assert (big["ServiceCategory"], big["ServiceSubcategory"]) == (
        "AI and Machine Learning",
        "Generative AI",
    )
    assert big["BilledCost"] == big["EffectiveCost"] == 412.8
    assert big["ListCost"] == pytest.approx(516.0)
    for cost, price in (
        ("ListCost", "ListUnitPrice"),
        ("ContractedCost", "ContractedUnitPrice"),
    ):
        assert big[cost] == big[price] * big["PricingQuantity"]
    assert (big["ConsumedQuantity"], big["ConsumedUnit"]) == (1200.0, "Requests")
    assert big["SkuId"] == "anthropic/claude-sonnet-4-5"
    assert big["SkuPriceId"] == "anthropic/claude-sonnet-4-5/requests"


def test_import_keeps_ai_cost_once(capsys):
    assert cli.main(["import", "anthropic-cost", str(LEGACY)]) == 0
    assert "3 billed charges (3 new), $3.84 from Anthropic" in capsys.readouterr().out
    assert cli.main(["import", "anthropic-cost", str(LEGACY)]) == 0
    assert "(0 new)" in capsys.readouterr().out
    assert cli.main(["import", "openai-costs", str(OPENAI)]) == 0
    assert "$1.56 from OpenAI" in capsys.readouterr().out

    assert len(ledger.read_charges()) == 5


def test_import_refuses_a_second_grouping_of_the_same_days(capsys):
    """Legacy and beta reports cover 2026-11-01 under different groupings:
    keeping both would count that day's money twice."""
    assert cli.main(["import", "anthropic-cost", str(LEGACY)]) == 0
    capsys.readouterr()

    assert cli.main(["import", "anthropic-cost", str(ANALYTICS)]) == 1

    err = capsys.readouterr().err
    assert "anthropic cost from 2026-11-01 is already grouped as cost_report:" in err
    assert "count the same money twice" in err
    assert len(ledger.read_charges()) == 3  # nothing of the refused import kept


def test_one_import_of_two_groupings_is_refused_too(capsys):
    assert cli.main(["import", "anthropic-cost", str(LEGACY), str(ANALYTICS)]) == 1
    assert ledger.read_charges() == []


def test_another_grouping_of_other_days_is_fine(tmp_path):
    later = json.loads(ANALYTICS.read_text(encoding="utf-8"))
    for row in later["response"]["data"]:
        row["starting_at"] = row["starting_at"].replace("2026-11-0", "2026-12-0")
        row["ending_at"] = row["ending_at"].replace("2026-11-0", "2026-12-0")
    december = tmp_path / "december.json"
    december.write_text(json.dumps(later), encoding="utf-8")

    assert cli.main(["import", "anthropic-cost", str(LEGACY)]) == 0
    assert cli.main(["import", "anthropic-cost", str(december)]) == 0


def test_conflicts_ignore_other_sources():
    anthropic = list(ai_cost.iter_anthropic(LEGACY))
    openai = list(ai_cost.iter_openai(OPENAI))

    assert ai_cost.conflicts(openai, anthropic) == []


def test_schema_8_charges_gain_an_empty_list_price(tmp_path, capsys):
    path = tmp_path / "ledger.sqlite3"
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute(ledger._V4_CHARGES)
        conn.execute("PRAGMA user_version = 8")
        conn.execute(
            f"INSERT INTO charges VALUES ({', '.join('?' * 13)})",
            (
                "runpod",
                "pod-a@2026-09-27T00:00:00Z",
                "2026-09-27T00:00:00Z",
                "2026-09-28T00:00:00Z",
                "RunPod",
                "RunPod Pods",
                "pod-a",
                "GPU pod",
                1.0,
                "Hours",
                0.42,
                "",
                "2026-10-01T00:00:00Z",
            ),
        )

    (kept,) = ledger.read_charges(path)

    assert (kept.amount_usd, kept.list_usd) == (0.42, None)
    assert "schema 9" in capsys.readouterr().err
    assert (tmp_path / "ledger.sqlite3.schema-8.bak").exists()
