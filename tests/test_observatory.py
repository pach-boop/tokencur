import json
from datetime import date
from pathlib import Path

from tokencur.observatory import (
    DAYS_PER_MONTH,
    load_subscriptions,
    render_html,
    snapshot,
    write_site,
)
from tokencur.records import UsageRecord


def _record(
    day: str, model: str = "claude-opus-4-8", output_tokens: int = 1000
) -> UsageRecord:
    return UsageRecord(
        timestamp=f"{day}T10:00:00.000Z",
        workspace="SECRET-workspace-name",
        session_id="SECRET-session-id",
        model=model,
        input_tokens=500,
        output_tokens=output_tokens,
        cache_read_tokens=2000,
        cache_write_5m_tokens=300,
        cache_write_1h_tokens=0,
    )


def test_snapshot_aggregates_by_day_model_and_bucket():
    records = [
        _record("2026-07-01"),
        _record("2026-07-01", output_tokens=500),
        _record("2026-07-02"),
    ]

    snap = snapshot(records)

    assert snap["kpis"]["days"] == 2
    assert snap["kpis"]["messages"] == 3
    assert snap["kpis"]["total_usd"] > 0
    assert [d["day"] for d in snap["daily"]] == ["2026-07-01", "2026-07-02"]
    assert "Claude Code" in snap["daily"][0]["services"]
    assert snap["by_model"][0]["model"] == "claude-opus-4-8"
    assert {b["bucket"] for b in snap["by_bucket"]} >= {"input", "output"}
    # Totals must reconcile across views (aggregation, not re-pricing).
    total = snap["kpis"]["total_usd"]
    assert abs(sum(m["cost_usd"] for m in snap["by_model"]) - total) < 0.05
    assert abs(sum(b["cost_usd"] for b in snap["by_bucket"]) - total) < 0.05


def test_money_block_scales_subscriptions_to_the_window():
    """Leverage must compare usage value against the real outlay over the
    same calendar window — flat fees scaled by span, not by active days."""
    records = [_record("2026-05-01"), _record("2026-06-30")]

    snap = snapshot(records, subscriptions={"monthly_usd": {"Claude Code": 50.0}})

    money = snap["money"]
    assert money["window_days"] == 61
    outlay = 50.0 * (61 / (365.25 / 12))
    assert money["estimated_outlay_usd"] == round(outlay, 2)
    assert money["leverage"] == round(snap["kpis"]["total_usd"] / outlay, 1)
    assert "history_gaps" not in snap
    page = render_html(snap)
    assert "What is actually paid" in page
    assert "subscription leverage" in page
    assert "History gap" not in page


def test_history_gap_is_disclosed_and_its_fee_not_charged():
    """Usage that happened but whose logs were lost must be disclosed,
    and its fee must not count across the gap — a lost record is not a
    month paid for nothing. Other fees still count over the full window."""
    records = [_record("2026-05-01"), _record("2026-06-30")]  # 61-day window
    subscriptions = {
        "monthly_usd": {"Claude Code": 50.0, "Codex CLI": 20.0},
        "history_gaps": {
            "Claude Code": {"before": "2026-06-01", "why": "Logs were deleted."}
        },
    }

    snap = snapshot(records, subscriptions)

    [gap] = snap["history_gaps"]
    assert (gap["service"], gap["excluded_days"]) == ("Claude Code", 31)
    outlay = (50.0 * (61 - 31) + 20.0 * 61) / DAYS_PER_MONTH
    assert snap["money"]["estimated_outlay_usd"] == round(outlay, 2)
    page = render_html(snap)
    assert "History gap · Claude Code" in page
    assert "Logs were deleted." in page
    assert "fees not counted across the history gap" in page


def test_gap_outside_the_window_changes_nothing():
    records = [_record("2026-05-01"), _record("2026-06-30")]
    subscriptions = {
        "monthly_usd": {"Claude Code": 50.0},
        "history_gaps": {"Claude Code": {"before": "2026-01-01", "why": "old"}},
    }

    snap = snapshot(records, subscriptions)

    assert "history_gaps" not in snap
    assert snap["money"]["estimated_outlay_usd"] == round(50.0 * 61 / DAYS_PER_MONTH, 2)


def test_each_plan_counts_only_while_it_was_active():
    """A plan change, a cancellation and a late start: each fee counts for
    the days its plan was active, and "per month now" is what is paid on
    the window's last day."""
    records = [_record("2026-05-01"), _record("2026-06-30")]  # 61-day window
    subscriptions = {
        "plans": {
            "Claude Code": [
                {"monthly_usd": 20.0, "until": "2026-06-01"},
                {"monthly_usd": 100.0, "from": "2026-06-01"},
            ],
            "Codex CLI": [{"monthly_usd": 20.0, "until": "2026-05-16"}],
            "Kimi Code": [{"monthly_usd": 10.0, "from": "2026-06-21"}],
        }
    }

    snap = snapshot(records, subscriptions)

    money = snap["money"]
    days = {(p["service"], p["monthly_usd"]): p["days_counted"] for p in money["plans"]}
    assert days == {
        ("Claude Code", 20.0): 31,
        ("Claude Code", 100.0): 30,
        ("Codex CLI", 20.0): 15,
        ("Kimi Code", 10.0): 10,
    }
    outlay = (20.0 * 31 + 100.0 * 30 + 20.0 * 15 + 10.0 * 10) / DAYS_PER_MONTH
    assert money["estimated_outlay_usd"] == round(outlay, 2)
    assert money["subscriptions_monthly_usd"] == {
        "Claude Code": 100.0,
        "Kimi Code": 10.0,
    }
    assert money["monthly_total_usd"] == 110.0
    page = render_html(snap)
    assert "subscriptions / month now" in page
    assert "Claude Code $20/mo until 2026-05-31, then $100/mo from 2026-06-01" in page
    assert "Codex CLI $20/mo until 2026-05-15" in page


def test_a_plan_note_states_its_assumption_on_the_page():
    records = [_record("2026-05-01"), _record("2026-06-30")]
    note = "Cancellation date not recorded: counted through the month of last use."
    subscriptions = {
        "plans": {
            "Codex CLI": [{"monthly_usd": 20.0, "until": "2026-06-01", "note": note}]
        }
    }

    snap = snapshot(records, subscriptions)

    assert snap["money"]["plans"][0]["note"] == note
    assert f"Codex CLI: {note}" in render_html(snap)


def test_plans_still_skip_a_history_gap():
    """A plan active before a declared gap ends counts only after it."""
    records = [_record("2026-05-01"), _record("2026-06-30")]
    subscriptions = {
        "plans": {"Claude Code": [{"monthly_usd": 50.0, "from": "2026-05-10"}]},
        "history_gaps": {"Claude Code": {"before": "2026-06-01", "why": "Lost."}},
    }

    snap = snapshot(records, subscriptions)

    assert snap["money"]["plans"][0]["days_counted"] == 30


def test_a_plan_outside_the_window_costs_nothing():
    records = [_record("2026-05-01"), _record("2026-06-30")]
    subscriptions = {
        "plans": {
            "Codex CLI": [{"monthly_usd": 20.0, "until": "2026-04-01"}],
            "Claude Code": [{"monthly_usd": 20.0}],
        }
    }

    money = snapshot(records, subscriptions)["money"]

    assert [p["days_counted"] for p in money["plans"]] == [61, 0]
    assert money["subscriptions_monthly_usd"] == {"Claude Code": 20.0}


def test_published_subscriptions_file_is_valid():
    """subscriptions.json is public data: every fee must be positive, every
    plan's dates real and in order, and every declared gap needs a real
    date and a reason readers can see."""
    config = load_subscriptions(Path(__file__).parent.parent / "subscriptions.json")

    assert config
    assert all(fee > 0 for fee in (config.get("monthly_usd") or {}).values())
    for plans in (config.get("plans") or {}).values():
        for plan in plans:
            assert plan["monthly_usd"] > 0
            start, until = plan.get("from"), plan.get("until")
            if start and until:
                assert date.fromisoformat(start) < date.fromisoformat(until)
            else:
                for day in (start, until):
                    if day:
                        date.fromisoformat(day)
    for gap in (config.get("history_gaps") or {}).values():
        date.fromisoformat(gap["before"])
        assert gap["why"].strip()


def test_without_subscriptions_no_real_money_is_claimed():
    """With no declared fees the page must not invent an outlay — only
    the clearly-labeled showback and counterfactual sections remain."""
    snap = snapshot([_record("2026-07-01")])

    assert "money" not in snap
    page = render_html(snap)
    assert "What is actually paid" not in page
    assert "showback, not money spent" in page
    assert "Counterfactuals" in page


def test_output_never_contains_workspace_or_session(tmp_path):
    """The observatory is public: only aggregates may survive. Workspace
    names and session ids from the source records must never appear in
    the HTML or the JSON."""
    records = [_record("2026-07-01")]
    snap = snapshot(records)

    write_site(snap, tmp_path)
    html_text = (tmp_path / "index.html").read_text(encoding="utf-8")
    json_text = (tmp_path / "data.json").read_text(encoding="utf-8")

    for leak in ("SECRET-workspace-name", "SECRET-session-id"):
        assert leak not in html_text
        assert leak not in json_text
    # data.json round-trips and matches the snapshot it was written from.
    assert json.loads(json_text)["kpis"] == snap["kpis"]


def test_render_html_is_self_contained():
    """No external requests: the page must not reference any http(s)
    resource except plain hyperlinks (charts, styles and script inline)."""
    page = render_html(snapshot([_record("2026-07-01")]))

    assert "<svg" in page and "<style>" in page and "<script>" in page
    for tag in ('src="http', 'href="http://', "@import", "url(http"):
        assert tag not in page or tag == 'href="http://'
    # The only http references are the footer links.
    assert page.count("https://") == 1  # repo link in footer


def test_corrections_to_published_figures_are_disclosed():
    """A figure this page once published, then found wrong, is corrected in
    the open: dated, with what changed and by how much."""
    snap = snapshot([_record("2026-09-15")], None)
    html = render_html(snap)

    assert snap["corrections"][0]["date"] == "2026-10-02"
    assert "Correction · 2026-10-02" in html
    assert "$685.66" in html and "$332.95" in html
    assert "$240.49" in html and "$96.59" in html  # outlay, plan by plan


def test_billed_charges_are_shown_as_real_money_without_resource_ids():
    from tokencur.records import BilledCharge

    charge = BilledCharge(
        source="runpod",
        record_id="SECRET-pod@2026-09-30T00:00:00Z",
        period_start="2026-09-30T00:00:00Z",
        period_end="2026-10-01T00:00:00Z",
        provider="RunPod",
        service="RunPod Pods",
        resource_id="SECRET-pod",
        resource_type="GPU pod",
        quantity=2.5,
        unit="Hours",
        amount_usd=1.05,
    )
    snap = snapshot([_record("2026-09-30")], None, [charge])
    html = render_html(snap)

    assert snap["billed"] == {
        "total_usd": 1.05,
        "by_service": {"RunPod Pods": 1.05},
        "charges": 1,
    }
    assert "billed by providers" in html and "stay out of subscription leverage" in html
    assert "SECRET" not in html and "SECRET" not in json.dumps(snap)


def test_the_page_names_what_reproduces_its_figures():
    from tokencur import __version__
    from tokencur.pricing import provenance

    snap = snapshot([_record("2026-07-01")])

    assert snap["provenance"]["tokencur"] == __version__
    assert snap["provenance"]["snapshot_sha256"] == provenance().snapshot_sha256
    page = render_html(snap)
    assert f"computed with tokencur {__version__} and pricing snapshot" in page
    assert provenance().snapshot_sha256[:12] in page
