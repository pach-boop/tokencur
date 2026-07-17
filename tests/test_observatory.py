import json

from tokencur.ingest.claude_code import UsageRecord
from tokencur.observatory import render_html, snapshot, write_site


def _record(day: str, model: str = "claude-opus-4-8", output_tokens: int = 1000) -> UsageRecord:
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

    snap = snapshot(records, subscriptions={"Claude Code": 50.0})

    money = snap["money"]
    assert money["window_days"] == 61
    outlay = 50.0 * (61 / (365.25 / 12))
    assert money["estimated_outlay_usd"] == round(outlay, 2)
    assert money["leverage"] == round(snap["kpis"]["total_usd"] / outlay, 1)
    page = render_html(snap)
    assert "What is actually paid" in page
    assert "subscription leverage" in page


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
    for tag in ("src=\"http", "href=\"http://", "@import", "url(http"):
        assert tag not in page or tag == "href=\"http://"
    # The only http references are the footer links.
    assert page.count("https://") == 1  # repo link in footer
