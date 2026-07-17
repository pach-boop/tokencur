"""Static public snapshot of the FOCUS dataset — the observatory.

``python -m tokencur.observatory [output_dir]`` scans every known local
source, aggregates the FOCUS charge rows, and writes a self-contained
``index.html`` + ``data.json`` suitable for static hosting (GitHub
Pages). The page has no runtime dependencies: chart geometry is computed
here; the browser only draws tooltips.

Privacy: the snapshot publishes aggregates only — day x service, model
and token-bucket totals, and the recommendation headlines. Workspace
names, session ids and message content never enter the output.

The snapshot is committed deliberately, like the pricing snapshot: the
public site changes when the maintainer decides, not on a schedule.
"""

from __future__ import annotations

import html
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from tokencur.focus import to_focus_rows, unpriced_models
from tokencur.pricing import AS_OF
from tokencur.recommend import recommendations
from tokencur.report import DEFAULT_SOURCES

DEFAULT_OUTPUT = Path("docs") / "observatory"

# Same entity->color mapping as the local dashboard (color follows the
# service, never the rank). Both palettes pass the six-check validator
# against their own surface.
SERVICE_ORDER = ["Claude Code", "Codex CLI", "Kimi Code"]
SERVICE_CLASS = {"Claude Code": "sv-claude", "Codex CLI": "sv-codex", "Kimi Code": "sv-kimi"}


def snapshot(records: list) -> dict:
    """Aggregate usage records into the public snapshot dict.

    Reads only day, service, model, token bucket, cost and quantity from
    the FOCUS rows — nothing identifying survives the aggregation.
    """
    daily: dict[str, dict[str, float]] = {}
    by_model: dict[str, dict[str, float]] = {}
    by_bucket: dict[str, float] = {}
    total = 0.0

    for row in to_focus_rows(records):
        day = row["ChargePeriodStart"][:10]
        service = row["ServiceName"]
        model, _, bucket = row["SkuId"].rpartition("/")
        cost = row["BilledCost"]
        tokens = row["ConsumedQuantity"]

        daily.setdefault(day, {})
        daily[day][service] = daily[day].get(service, 0.0) + cost
        agg = by_model.setdefault(model, {"cost_usd": 0.0, "tokens": 0})
        agg["cost_usd"] += cost
        agg["tokens"] += tokens
        by_bucket[bucket] = by_bucket.get(bucket, 0.0) + cost
        total += cost

    recs = recommendations(records)
    days = len(daily)
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "rates_as_of": AS_OF,
        "kpis": {
            "total_usd": round(total, 2),
            "days": days,
            "avg_day_usd": round(total / days, 2) if days else 0.0,
            "messages": len(records),
            "achieved_savings_usd": round(
                sum(r.savings_usd for r in recs if r.kind == "achieved"), 2
            ),
            "potential_savings_usd": round(
                sum(r.savings_usd for r in recs if r.kind == "potential"), 2
            ),
        },
        "daily": [
            {"day": day, "services": {s: round(c, 4) for s, c in sorted(services.items())}}
            for day, services in sorted(daily.items())
        ],
        "by_model": [
            {"model": m, "cost_usd": round(a["cost_usd"], 2), "tokens": int(a["tokens"])}
            for m, a in sorted(by_model.items(), key=lambda kv: -kv[1]["cost_usd"])
        ],
        "by_bucket": [
            {"bucket": b, "cost_usd": round(c, 2)}
            for b, c in sorted(by_bucket.items(), key=lambda kv: -kv[1])
        ],
        "recommendations": [
            {
                "kind": r.kind,
                "title": r.title,
                "savings_usd": round(r.savings_usd, 2),
                "savings_pct": round(r.savings_pct, 1),
            }
            for r in recs
        ],
        "unpriced": dict(sorted(unpriced_models(records).items())),
    }


def _usd(x: float) -> str:
    return f"${x:,.2f}"


def _nice_ceiling(x: float) -> float:
    """Round up to a 1/2/2.5/5 x 10^k boundary for a clean axis."""
    if x <= 0:
        return 1.0
    exp = len(str(int(x))) - 1
    base = 10.0**exp
    for mult in (1, 2, 2.5, 5, 10):
        if x <= mult * base:
            return mult * base
    return 10 * base


def _daily_chart(snap: dict) -> str:
    """Multi-line SVG of daily cost per service, with hover columns."""
    days = snap["daily"]
    services = [s for s in SERVICE_ORDER if any(s in d["services"] for d in days)]
    if not days or not services:
        return "<p class='muted'>No priced usage yet.</p>"

    width, height, pad_l, pad_r, pad_t, pad_b = 860, 280, 56, 16, 12, 28
    plot_w, plot_h = width - pad_l - pad_r, height - pad_t - pad_b
    y_max = _nice_ceiling(
        max(cost for d in days for cost in d["services"].values())
    )
    n = len(days)

    def x(i: int) -> float:
        return pad_l + (plot_w / 2 if n == 1 else i * plot_w / (n - 1))

    def y(cost: float) -> float:
        return pad_t + plot_h * (1 - cost / y_max)

    grid, labels = [], []
    for step in range(5):
        value = y_max * step / 4
        gy = y(value)
        grid.append(
            f'<line class="grid" x1="{pad_l}" y1="{gy:.1f}" x2="{width - pad_r}" y2="{gy:.1f}"/>'
        )
        labels.append(
            f'<text class="axis" x="{pad_l - 8}" y="{gy + 4:.1f}" text-anchor="end">${value:g}</text>'
        )
    every = max(1, (n - 1) // 5 or 1)
    for i, d in enumerate(days):
        if i % every == 0 or i == n - 1:
            labels.append(
                f'<text class="axis" x="{x(i):.1f}" y="{height - 8}" text-anchor="middle">'
                f"{d['day'][5:]}</text>"
            )

    lines = []
    for s in services:
        pts = " ".join(
            f"{x(i):.1f},{y(d['services'].get(s, 0.0)):.1f}" for i, d in enumerate(days)
        )
        lines.append(f'<polyline class="{SERVICE_CLASS[s]} line" points="{pts}"/>')

    hovers = []
    col_w = plot_w / n
    for i, d in enumerate(days):
        tip = " · ".join(
            [html.escape(d["day"])]
            + [f"{s} {_usd(d['services'][s])}" for s in services if s in d["services"]]
        )
        hovers.append(
            f'<rect class="hover-col" x="{x(i) - col_w / 2:.1f}" y="{pad_t}" '
            f'width="{col_w:.1f}" height="{plot_h}" data-tip="{tip}" data-x="{x(i):.1f}"/>'
        )

    legend = "".join(
        f'<span class="chip"><i class="{SERVICE_CLASS[s]}"></i>{html.escape(s)}</span>'
        for s in services
    )
    table = _table(
        ["day"] + services,
        [[d["day"]] + [_usd(d["services"].get(s, 0.0)) for s in services] for d in days],
    )
    return (
        f'<div class="legend">{legend}</div>'
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Daily cost by service">'
        f'{"".join(grid)}<line class="crosshair" y1="{pad_t}" y2="{pad_t + plot_h}" hidden/>'
        f'{"".join(lines)}{"".join(labels)}{"".join(hovers)}</svg>'
        f"{_details(table)}"
    )


def _bars(rows: list[tuple[str, float, str]], aria: str) -> str:
    """Horizontal single-hue bars: (label, cost, tip) per row."""
    if not rows:
        return "<p class='muted'>Nothing to show yet.</p>"
    top = max(cost for _, cost, _ in rows)
    out = ['<div class="bars" role="img" aria-label="%s">' % html.escape(aria)]
    for label, cost, tip in rows:
        pct = 100 * cost / top if top else 0
        out.append(
            '<div class="bar-row" data-tip="{tip}"><span class="bar-label">{label}</span>'
            '<span class="track"><span class="fill" style="width:{pct:.1f}%"></span></span>'
            '<span class="bar-value">{value}</span></div>'.format(
                tip=html.escape(tip),
                label=html.escape(label),
                pct=pct,
                value=_usd(cost),
            )
        )
    out.append("</div>")
    return "".join(out)


def _table(head: list[str], rows: list[list[str]]) -> str:
    thead = "".join(f"<th>{html.escape(h)}</th>" for h in head)
    body = "".join(
        "<tr>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in row) + "</tr>"
        for row in rows
    )
    return f"<table><thead><tr>{thead}</tr></thead><tbody>{body}</tbody></table>"


def _details(table: str) -> str:
    return f"<details><summary>table</summary>{table}</details>"


def _kpi(label: str, value: str) -> str:
    return (
        f'<div class="kpi"><div class="kpi-value">{html.escape(value)}</div>'
        f'<div class="kpi-label">{html.escape(label)}</div></div>'
    )


def render_html(snap: dict) -> str:
    k = snap["kpis"]
    kpis = "".join(
        [
            _kpi("API-equivalent cost", _usd(k["total_usd"])),
            _kpi("days covered", str(k["days"])),
            _kpi("avg cost / day", _usd(k["avg_day_usd"])),
            _kpi("assistant messages", f"{k['messages']:,}"),
            _kpi("caching savings (measured)", _usd(k["achieved_savings_usd"])),
            _kpi("potential savings (what-if)", _usd(k["potential_savings_usd"])),
        ]
    )
    model_bars = _bars(
        [
            (m["model"], m["cost_usd"], f"{m['model']} · {m['tokens']:,} tokens · {_usd(m['cost_usd'])}")
            for m in snap["by_model"]
        ],
        "Cost by model",
    )
    model_table = _details(
        _table(
            ["model", "cost USD", "tokens"],
            [[m["model"], _usd(m["cost_usd"]), f"{m['tokens']:,}"] for m in snap["by_model"]],
        )
    )
    bucket_bars = _bars(
        [(b["bucket"], b["cost_usd"], f"{b['bucket']} · {_usd(b['cost_usd'])}") for b in snap["by_bucket"]],
        "Cost by token type",
    )
    recs = snap["recommendations"]
    rec_table = (
        _table(
            ["kind", "recommendation", "savings USD", "% of baseline"],
            [[r["kind"], r["title"], _usd(r["savings_usd"]), f"{r['savings_pct']}%"] for r in recs],
        )
        if recs
        else "<p class='muted'>No recommendations yet.</p>"
    )
    unpriced = ""
    if snap["unpriced"]:
        pairs = ", ".join(f"{m} ×{n}" for m, n in snap["unpriced"].items())
        unpriced = (
            f'<p class="muted">Unpriced usage (excluded, never billed as $0): {html.escape(pairs)}</p>'
        )

    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>tokencur observatory</title>
<style>
:root {{
  --surface:#ffffff; --card:#f6f7f9; --ink:#1a2733; --ink-2:#51606e; --muted:#7a8794;
  --grid:#e3e7eb; --track:#e9edf1; --hue1:#2a78d6;
  --claude:#2a78d6; --codex:#1baf7a; --kimi:#eda100;
}}
@media (prefers-color-scheme: dark) {{ :root {{
  --surface:#101418; --card:#191f26; --ink:#e8edf2; --ink-2:#aab6c2; --muted:#7d8996;
  --grid:#2a323b; --track:#242c35; --hue1:#3987e5;
  --claude:#3987e5; --codex:#199e70; --kimi:#c98500;
}} }}
* {{ box-sizing:border-box; margin:0 }}
body {{ background:var(--surface); color:var(--ink); font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif; padding:32px 20px 48px; max-width:960px; margin:0 auto }}
h1 {{ font-size:1.5rem }} h2 {{ font-size:1.05rem; margin:36px 0 12px }}
.sub {{ color:var(--ink-2); margin:4px 0 24px }}
.muted {{ color:var(--muted); font-size:.85rem; margin-top:8px }}
.kpis {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px }}
.kpi {{ background:var(--card); border-radius:10px; padding:14px 16px }}
.kpi-value {{ font-size:1.35rem; font-weight:650; font-variant-numeric:tabular-nums }}
.kpi-label {{ color:var(--ink-2); font-size:.8rem; margin-top:2px }}
svg {{ width:100%; height:auto; display:block }}
.grid {{ stroke:var(--grid); stroke-width:1 }}
.axis {{ fill:var(--muted); font-size:11px }}
.line {{ fill:none; stroke-width:2; stroke-linejoin:round }}
polyline.sv-claude {{ stroke:var(--claude) }} polyline.sv-codex {{ stroke:var(--codex) }} polyline.sv-kimi {{ stroke:var(--kimi) }}
.crosshair {{ stroke:var(--muted); stroke-width:1; stroke-dasharray:3 3 }}
.hover-col {{ fill:transparent }}
.legend {{ display:flex; gap:14px; margin:0 0 8px; flex-wrap:wrap }}
.chip {{ color:var(--ink-2); font-size:.82rem; display:inline-flex; align-items:center; gap:6px }}
.chip i {{ width:10px; height:10px; border-radius:3px; display:inline-block }}
.chip i.sv-claude {{ background:var(--claude) }} .chip i.sv-codex {{ background:var(--codex) }} .chip i.sv-kimi {{ background:var(--kimi) }}
.bars {{ display:flex; flex-direction:column; gap:8px }}
.bar-row {{ display:grid; grid-template-columns:minmax(120px,220px) 1fr 90px; gap:10px; align-items:center }}
.bar-label {{ font-size:.85rem; color:var(--ink-2); overflow:hidden; text-overflow:ellipsis; white-space:nowrap }}
.track {{ background:var(--track); border-radius:4px; height:12px; overflow:hidden }}
.fill {{ background:var(--hue1); height:100%; display:block; border-radius:0 4px 4px 0 }}
.bar-value {{ font-size:.85rem; text-align:right; font-variant-numeric:tabular-nums }}
details {{ margin-top:10px; color:var(--ink-2) }} summary {{ cursor:pointer; font-size:.82rem }}
table {{ border-collapse:collapse; margin-top:8px; font-size:.82rem; width:100% }}
th,td {{ text-align:left; padding:4px 10px 4px 0; border-bottom:1px solid var(--grid); font-variant-numeric:tabular-nums }}
th {{ color:var(--muted); font-weight:500 }}
#tip {{ position:fixed; pointer-events:none; background:var(--ink); color:var(--surface); font-size:.78rem; padding:5px 9px; border-radius:6px; max-width:340px; z-index:9; display:none }}
footer {{ margin-top:40px; color:var(--muted); font-size:.8rem }}
footer a {{ color:var(--ink-2) }}
</style></head><body>
<h1>tokencur observatory</h1>
<p class="sub">This project's own AI spend, measured by tokencur and published as a FOCUS-shaped
snapshot. Aggregates only — no workspaces, no sessions, no content.</p>
<div class="kpis">{kpis}</div>
<h2>Daily cost by service</h2>
{_daily_chart(snap)}
<h2>Cost by model</h2>
{model_bars}{model_table}
<h2>Where the money goes (token type)</h2>
{bucket_bars}
<h2>Recommendations</h2>
{rec_table}
{unpriced}
<footer>API-equivalent list prices (showback) over local agent logs · rates as of {html.escape(snap["rates_as_of"])}
· generated {html.escape(snap["generated_at"])} · <a href="https://github.com/pach-boop/tokencur">tokencur</a>
· <a href="data.json">data.json</a></footer>
<div id="tip"></div>
<script>
(function () {{
  var tip = document.getElementById("tip");
  function show(e, text) {{
    tip.textContent = text; tip.style.display = "block";
    var pad = 12, w = tip.offsetWidth;
    var left = Math.min(e.clientX + pad, window.innerWidth - w - pad);
    tip.style.left = left + "px"; tip.style.top = (e.clientY + pad) + "px";
  }}
  function hide() {{ tip.style.display = "none"; }}
  document.querySelectorAll("[data-tip]").forEach(function (el) {{
    el.addEventListener("mousemove", function (e) {{ show(e, el.dataset.tip); }});
    el.addEventListener("mouseleave", hide);
  }});
  var svg = document.querySelector("svg");
  var cross = svg && svg.querySelector(".crosshair");
  if (cross) svg.querySelectorAll(".hover-col").forEach(function (col) {{
    col.addEventListener("mousemove", function () {{
      cross.setAttribute("x1", col.dataset.x); cross.setAttribute("x2", col.dataset.x);
      cross.removeAttribute("hidden");
    }});
    col.addEventListener("mouseleave", function () {{ cross.setAttribute("hidden", ""); }});
  }});
}})();
</script>
</body></html>
"""


def write_site(snap: dict, outdir: Path) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "index.html").write_text(render_html(snap), encoding="utf-8")
    (outdir / "data.json").write_text(
        json.dumps(snap, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def main(argv: list[str]) -> int:
    outdir = Path(argv[1]) if len(argv) > 1 else DEFAULT_OUTPUT
    records: list = []
    for root, iter_records in DEFAULT_SOURCES:
        if root.exists():
            records.extend(iter_records(root))
    if not records:
        print("error: no known usage-log locations found", file=sys.stderr)
        return 1
    write_site(snapshot(records), outdir)
    print(f"observatory written to {outdir} ({len(records)} records aggregated)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
