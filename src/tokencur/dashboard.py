"""Local cost dashboard over the FOCUS dataset.

Usage:
    pip install -e .[dashboard]
    streamlit run src/tokencur/dashboard.py

Scans every known local source, normalizes to FOCUS charge rows and
lets DuckDB run the analytics — the same SQL any FinOps analyst would
write over a FOCUS dataset. Every view exposes its SQL and its table
(the table doubles as the accessibility fallback for chart colors).
"""

from __future__ import annotations

import duckdb
import pandas as pd
import streamlit as st

from tokencur.focus import to_focus_rows, unpriced_models
from tokencur.report import DEFAULT_SOURCES

# Fixed service→color mapping (color follows the entity, never the
# rank). Both palettes validated for their surface; see the dataviz
# palette notes in the commit message.
SERVICE_ORDER = ["Claude Code", "Codex CLI", "Kimi Code"]
SERVICE_COLORS = {
    "dark": {"Claude Code": "#3987e5", "Codex CLI": "#199e70", "Kimi Code": "#c98500"},
    "light": {"Claude Code": "#2a78d6", "Codex CLI": "#1baf7a", "Kimi Code": "#eda100"},
}
SINGLE_HUE = {"dark": "#3987e5", "light": "#2a78d6"}  # magnitude charts


def _mode() -> str:
    try:
        return st.context.theme.type or "dark"
    except Exception:
        return "dark"


@st.cache_data(show_spinner="Scanning local usage logs…")
def load() -> tuple[pd.DataFrame, dict[str, int]]:
    records = []
    for root, iter_records in DEFAULT_SOURCES:
        if root.exists():
            records.extend(iter_records(root))
    return pd.DataFrame(to_focus_rows(records)), unpriced_models(records)


def view(title: str, sql: str, frame: pd.DataFrame) -> pd.DataFrame:
    """Run one DuckDB query and render its SQL + table under the title."""
    result = duckdb.sql(sql.replace("FOCUS", "frame")).df()
    st.subheader(title)
    with st.expander("SQL + tabla"):
        st.code(sql, language="sql")
        st.dataframe(result, use_container_width=True)
    return result


st.set_page_config(page_title="tokencur", page_icon="🧾", layout="wide")
st.title("tokencur — local AI spend, FOCUS-shaped")

focus, unpriced = load()
if focus.empty:
    st.warning("No usage found in any known local source.")
    st.stop()

mode = _mode()
total = focus["BilledCost"].sum()
days = focus["ChargePeriodStart"].str[:10].nunique()

k1, k2, k3, k4 = st.columns(4)
k1.metric("API-equivalent cost", f"${total:,.2f}")
k2.metric("Charge rows", f"{len(focus):,}")
k3.metric("Days covered", days)
k4.metric("Avg cost / day", f"${total / max(days, 1):,.2f}")
if unpriced:
    st.caption(
        "Unpriced usage (excluded): "
        + ", ".join(f"{m} ×{n}" for m, n in sorted(unpriced.items()))
    )

daily = view(
    "Daily cost by service",
    """
    SELECT substr(ChargePeriodStart, 1, 10) AS day,
           ServiceName,
           round(sum(BilledCost), 4) AS cost_usd
    FROM FOCUS
    GROUP BY 1, 2
    ORDER BY 1
    """,
    focus,
)
pivot = daily.pivot(index="day", columns="ServiceName", values="cost_usd")
services = [s for s in SERVICE_ORDER if s in pivot.columns]
st.line_chart(
    pivot[services],
    color=[SERVICE_COLORS[mode][s] for s in services],
    height=320,
)

by_model = view(
    "Cost by model",
    """
    SELECT regexp_replace(SkuId, '/[^/]+$', '') AS model,
           round(sum(BilledCost), 2) AS cost_usd,
           sum(ConsumedQuantity)::BIGINT AS tokens
    FROM FOCUS
    GROUP BY 1
    ORDER BY 2 DESC
    """,
    focus,
)
st.bar_chart(
    by_model.set_index("model")["cost_usd"],
    color=SINGLE_HUE[mode],
    horizontal=True,
    height=320,
)

by_bucket = view(
    "Where the money goes (token type)",
    """
    SELECT regexp_extract(SkuId, '[^/]+$') AS token_type,
           round(sum(BilledCost), 2) AS cost_usd
    FROM FOCUS
    GROUP BY 1
    ORDER BY 2 DESC
    """,
    focus,
)
st.bar_chart(
    by_bucket.set_index("token_type")["cost_usd"],
    color=SINGLE_HUE[mode],
    height=260,
)

view(
    "Unit economics by model",
    """
    SELECT regexp_replace(SkuId, '/[^/]+$', '') AS model,
           round(sum(BilledCost), 2) AS cost_usd,
           round(sum(BilledCost) / count(DISTINCT substr(ChargePeriodStart, 1, 10)), 3)
               AS usd_per_active_day,
           round(1e6 * sum(BilledCost) / sum(ConsumedQuantity), 3)
               AS usd_per_mtok_effective
    FROM FOCUS
    GROUP BY 1
    ORDER BY 2 DESC
    """,
    focus,
)

st.caption(
    "Costs are API-equivalent list prices (showback) over local agent logs. "
    "Source: tokencur FOCUS export."
)
