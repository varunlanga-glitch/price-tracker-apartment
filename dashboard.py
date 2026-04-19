"""
Condo Tracker — Streamlit Dashboard
=====================================
Run locally:
    pip install streamlit plotly pandas
    streamlit run dashboard.py

Then open http://localhost:8501 in your browser.
The page auto-refreshes every 60 seconds while it's open.

Deploy free to the cloud (always-on, no computer needed):
    1. Push this whole folder to a GitHub repo
    2. Go to https://share.streamlit.io → "New app" → point at dashboard.py
    3. Done — accessible from any device, forever free on Streamlit Community Cloud
"""

import sqlite3
import os
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from datetime import date, datetime, timedelta

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Condo Tracker",
    page_icon="🏢",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Locate database ───────────────────────────────────────────────────────────
_HERE   = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_HERE, "condo_tracker.db")


# ── Data loader ───────────────────────────────────────────────────────────────
@st.cache_data(ttl=60)   # re-fetch every 60 s while the page is open
def load_data():
    if not os.path.exists(DB_PATH):
        return {k: pd.DataFrame() for k in
                ("listings", "snapshots", "trends", "drops", "cum_drops",
                 "delisted", "days_to_drop")}

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row

    listings = pd.read_sql_query("""
        SELECT * FROM listings
        WHERE is_active=1 AND is_senior_flagged=0
    """, con)

    snapshots = pd.read_sql_query("""
        SELECT ds.mls_number, ds.snapshot_date, ds.price, ds.dom,
               l.city, l.address_raw
        FROM daily_snapshots ds
        JOIN listings l ON l.mls_number = ds.mls_number
        WHERE l.is_senior_flagged=0
        ORDER BY ds.snapshot_date
    """, con)

    trends = pd.read_sql_query("""
        SELECT
            ds.snapshot_date,
            COUNT(DISTINCT ds.mls_number)   AS active_count,
            ROUND(AVG(ds.price), 0)         AS avg_price,
            ROUND(AVG(l.price_per_sqft), 2) AS avg_psf,
            ROUND(AVG(ds.dom), 1)           AS avg_dom
        FROM daily_snapshots ds
        JOIN listings l ON l.mls_number = ds.mls_number
        WHERE l.is_senior_flagged=0
        GROUP BY ds.snapshot_date
        ORDER BY ds.snapshot_date
    """, con)

    today = date.today().isoformat()
    drops = pd.read_sql_query("""
        SELECT
            l.address_raw, l.city, l.mls_number,
            t.price   AS price_today,
            y.price   AS price_yesterday,
            (t.price - y.price)                     AS drop_amount,
            ROUND(100.0*(t.price - y.price)/y.price, 2) AS drop_pct,
            l.dom, l.deal_score, l.listing_url
        FROM daily_snapshots t
        JOIN daily_snapshots y
             ON y.mls_number = t.mls_number
            AND y.snapshot_date = DATE(?, '-1 day')
        JOIN listings l ON l.mls_number = t.mls_number
        WHERE t.snapshot_date = ?
          AND t.price < y.price
          AND l.is_senior_flagged = 0
        ORDER BY drop_pct
    """, con, params=(today, today))

    delisted = pd.read_sql_query("""
        SELECT mls_number, address_raw, city,
               price          AS final_price,
               price_original AS orig_price,
               dom,
               deal_score,
               first_seen_date,
               last_seen_date,
               price_reduction_pct,
               listing_url
        FROM listings
        WHERE is_active=0 AND is_senior_flagged=0
          AND last_seen_date >= DATE('now', '-90 days')
        ORDER BY last_seen_date DESC
        LIMIT 200
    """, con)

    days_to_drop = pd.read_sql_query("""
        WITH ranked AS (
            SELECT mls_number, snapshot_date, price,
                   LAG(price) OVER (PARTITION BY mls_number ORDER BY snapshot_date) AS prev_price
            FROM daily_snapshots
        ),
        first_drops AS (
            SELECT mls_number, MIN(snapshot_date) AS first_drop_date
            FROM ranked
            WHERE price < prev_price AND prev_price IS NOT NULL
            GROUP BY mls_number
        )
        SELECT l.mls_number, l.city, l.first_seen_date, fd.first_drop_date,
               CAST(julianday(fd.first_drop_date) - julianday(l.first_seen_date) AS INTEGER)
                   AS days_to_first_drop,
               l.price_reduction_pct, l.deal_score, l.is_active
        FROM listings l
        JOIN first_drops fd ON fd.mls_number = l.mls_number
        WHERE l.is_senior_flagged=0
    """, con)

    cum_drops = pd.read_sql_query("""
        SELECT
            l.mls_number, l.address_raw, l.city,
            l.price          AS current_price,
            l.deal_score,
            l.listing_url,
            l.dom,
            MAX(ds.price)    AS peak_price,
            MIN(ds.snapshot_date) AS first_seen,
            COUNT(DISTINCT ds.snapshot_date) AS days_tracked,
            MAX(ds.price) - l.price AS drop_amount,
            ROUND(100.0*(l.price - MAX(ds.price))/MAX(ds.price), 1) AS total_drop_pct
        FROM listings l
        JOIN daily_snapshots ds ON ds.mls_number = l.mls_number
        WHERE l.is_active=1 AND l.is_senior_flagged=0
        GROUP BY l.mls_number
        HAVING MAX(ds.price) > l.price
        ORDER BY total_drop_pct
    """, con)

    con.close()
    return {"listings": listings, "snapshots": snapshots,
            "trends": trends, "drops": drops, "cum_drops": cum_drops,
            "delisted": delisted, "days_to_drop": days_to_drop}


# ── Colour helpers ────────────────────────────────────────────────────────────
NAVY    = "#1F3864"
BLUE    = "#2E75B6"
GREEN   = "#63BE7B"
YELLOW  = "#FFEB84"
RED     = "#F8696B"
AMBER   = "#FFEB9C"


def _score_color(s):
    """Map deal score 0–100 to a hex colour (red → yellow → green)."""
    if s is None or s != s:
        return "#CCCCCC"
    s = max(0, min(100, float(s)))
    if s < 50:
        t = s / 50
        r = int(248 + (255 - 248) * t)
        g = int(105 + (235 - 105) * t)
        b = int(107 + (132 - 107) * t)
    else:
        t = (s - 50) / 50
        r = int(255 + (99 - 255) * t)
        g = int(235 + (190 - 235) * t)
        b = int(132 + (123 - 132) * t)
    return f"#{r:02X}{g:02X}{b:02X}"


# ── Load data + sidebar filters ───────────────────────────────────────────────
data = load_data()
df   = data["listings"].copy()

st.sidebar.image(
    "https://img.icons8.com/ios-filled/100/1F3864/building.png",
    width=60
)
st.sidebar.title("Condo Tracker")
st.sidebar.caption(f"Last refreshed: {datetime.now().strftime('%I:%M %p')}")
st.sidebar.markdown("---")

if df.empty:
    st.warning("No data yet — run `python run.py --test` first, then come back.")
    st.stop()

# Sidebar filters
cities = sorted(df["city"].dropna().unique().tolist())
sel_cities = st.sidebar.multiselect("Cities", cities, default=cities)

price_min_v = int(df["price"].min()) if not df.empty else 100_000
price_max_v = int(df["price"].max()) if not df.empty else 500_000
price_range = st.sidebar.slider(
    "Price range ($)", price_min_v, price_max_v,
    (price_min_v, price_max_v), step=5_000,
    format="$%d"
)

min_score = st.sidebar.slider("Minimum deal score", 0, 100, 0)
show_relists = st.sidebar.checkbox("Show relists only", value=False)

_sqft_vals = df["sqft"].replace(0, pd.NA).dropna()
if not _sqft_vals.empty:
    _sqft_lo, _sqft_hi = int(_sqft_vals.min()), int(_sqft_vals.max())
    sqft_range = st.sidebar.slider("Sqft range", _sqft_lo, _sqft_hi,
                                   (_sqft_lo, _sqft_hi), step=50)
else:
    sqft_range = None

st.sidebar.markdown("---")
st.sidebar.markdown("**Quick links**")
st.sidebar.markdown("- [realtor.ca map](https://www.realtor.ca/map)")
st.sidebar.markdown("- [config.py](config.py) — edit search params")

# Apply filters
df_f = df[df["city"].isin(sel_cities)] if sel_cities else df
df_f = df_f[df_f["price"].between(*price_range)]
df_f = df_f[df_f["deal_score"].fillna(0) >= min_score]
if show_relists:
    df_f = df_f[df_f["is_relist"] == 1]
if sqft_range:
    df_f = df_f[df_f["sqft"].fillna(0).eq(0) | df_f["sqft"].between(*sqft_range)]

# ── Header ────────────────────────────────────────────────────────────────────
st.markdown(
    f"<h1 style='color:{NAVY};margin-bottom:0'>🏢 Condo Tracker</h1>"
    f"<p style='color:#666;margin-top:2px'>"
    f"Abbotsford · Mission · Langley &nbsp;|&nbsp; 1-Bedroom &lt; $450K &nbsp;|&nbsp; "
    f"As of {date.today().strftime('%B %d, %Y')}</p>",
    unsafe_allow_html=True
)
st.markdown("---")


# ── KPI cards ─────────────────────────────────────────────────────────────────
def _kpi(label, value, delta=None, delta_label=""):
    delta_html = ""
    if delta is not None:
        color = GREEN if delta >= 0 else RED
        sign  = "▲" if delta >= 0 else "▼"
        delta_html = (f"<div style='font-size:12px;color:{color}'>"
                      f"{sign} {abs(delta)} {delta_label}</div>")
    return (
        f"<div style='background:{NAVY};border-radius:8px;padding:16px 20px;"
        f"text-align:center;color:white'>"
        f"<div style='font-size:13px;opacity:0.75'>{label}</div>"
        f"<div style='font-size:26px;font-weight:700;margin:4px 0'>{value}</div>"
        f"{delta_html}</div>"
    )


_trends = data["trends"]
_delta_listings = _delta_price = _delta_psf = _delta_dom = None
if len(_trends) >= 2:
    _t0, _t1 = _trends.iloc[-2], _trends.iloc[-1]
    _delta_listings = int(_t1["active_count"] - _t0["active_count"])
    _delta_price    = int(_t1["avg_price"]    - _t0["avg_price"])
    _delta_psf      = round(float(_t1["avg_psf"] - _t0["avg_psf"]), 0)
    _delta_dom      = round(float(_t1["avg_dom"]  - _t0["avg_dom"]), 1)

med_price  = int(df_f["price"].median()) if not df_f.empty else 0
med_psf    = round(df_f["price_per_sqft"].replace(0, pd.NA).median() or 0, 0)
avg_dom    = round(df_f["dom"].median() or 0, 0)
relists    = int((df_f["is_relist"] == 1).sum())
top_score  = round(df_f["deal_score"].max() or 0, 1)
med_cost   = round(
    (df_f["maintenance_fee"].fillna(0) + df_f["taxes_annual"].fillna(0) / 12).median(), 0
)

c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.markdown(_kpi("Active listings",      len(df_f),              _delta_listings, "vs yesterday"), unsafe_allow_html=True)
c2.markdown(_kpi("Median price",         f"${med_price:,}",      _delta_price,    "vs yesterday"), unsafe_allow_html=True)
c3.markdown(_kpi("Median $/sqft",        f"${med_psf:,.0f}",     _delta_psf,      "vs yesterday"), unsafe_allow_html=True)
c4.markdown(_kpi("Median DOM",           f"{avg_dom:.0f} days",  _delta_dom,      "days vs yesterday"), unsafe_allow_html=True)
c5.markdown(_kpi("Relists",              relists), unsafe_allow_html=True)
c6.markdown(_kpi("Median monthly cost",  f"${med_cost:,.0f}"), unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)


# ── Tab layout ────────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "🏆 Top Deals", "📊 Market Charts", "🗺️ Map", "⚠️ Alerts", "📈 Trends"
])


# ═══════════════════════════════
# TAB 1 — Top Deals
# ═══════════════════════════════
with tab1:
    _n_col, _dl_col = st.columns([1, 4])
    with _n_col:
        top_n = st.selectbox("Show top", [25, 50, 100, 200], index=1, key="top_n")
    top = df_f.sort_values("deal_score", ascending=False).head(top_n).copy()

    # Monthly cost column
    top["monthly_cost"] = (
        top["maintenance_fee"].fillna(0) +
        top["taxes_annual"].fillna(0) / 12
    ).round(0)

    display_cols = {
        "deal_score":        "Score",
        "address_raw":       "Address",
        "city":              "City",
        "price":             "Price",
        "price_per_sqft":    "$/sqft",
        "sqft":              "Sqft",
        "year_built":        "Built",
        "dom":               "DOM",
        "monthly_cost":      "Mo. Cost",
        "maintenance_fee":   "Maint. Fee",
        "taxes_annual":      "Tax/yr",
        "price_reduction_pct": "Drop %",
        "is_relist":         "Relist",
    }

    top_disp = top[list(display_cols.keys())].rename(columns=display_cols)
    top_disp["Address"] = top_disp["Address"].str.replace("|", " ", regex=False)
    top_disp["Relist"]  = top_disp["Relist"].map({1: "YES", 0: ""})
    top_disp["Score"]   = top_disp["Score"].round(1)
    top_disp["_url"]    = top["listing_url"].values

    sort_cols = [c for c in top_disp.columns if c != "_url"]
    sc1, sc2, sc3 = st.columns([3, 1, 3])
    with sc1:
        sort_col = st.selectbox("Sort by", sort_cols, index=0, key="top_sort_col")
    with sc2:
        sort_asc = st.radio("Order", ["↓ Desc", "↑ Asc"], index=0, key="top_sort_asc") == "↑ Asc"
    with sc3:
        _csv = top_disp.drop(columns=["_url"]).to_csv(index=False)
        st.download_button("⬇ Export CSV", _csv, "top_deals.csv", "text/csv", key="dl_csv")

    top_disp = top_disp.sort_values(sort_col, ascending=sort_asc, na_position="last")

    st.markdown(f"**Showing top {len(top_disp)} deals** (score ≥ {min_score}, "
                f"sorted by **{sort_col}** {'↑' if sort_asc else '↓'})")

    _num_fmt = {
        "Price":      "${:,.0f}",
        "$/sqft":     "${:,.0f}",
        "Sqft":       "{:,.0f}",
        "Built":      "{:.0f}",
        "DOM":        "{:.0f}",
        "Mo. Cost":   "${:,.0f}",
        "Maint. Fee": "${:,.0f}",
        "Tax/yr":     "${:,.0f}",
        "Drop %":     "{:.1f}%",
    }

    vis_cols = [c for c in top_disp.columns if c != "_url"]
    header_html = "".join(f"<th>{c}</th>" for c in vis_cols)
    rows_html = ""
    for _, row in top_disp.iterrows():
        score_pct = max(0, min(100, float(row["Score"]) if pd.notna(row["Score"]) else 0))
        r = int(248 - (248 - 99) * score_pct / 100)
        g = int(105 + (190 - 105) * score_pct / 100)
        b = int(107 - (107 - 123) * score_pct / 100)
        cells = ""
        for col in vis_cols:
            val = row[col]
            if col == "Score":
                cell = (
                    f'<td style="background:rgb({r},{g},{b});color:#fff;'
                    f'font-weight:600;text-align:center">'
                    f'{val if pd.notna(val) else "—"}</td>'
                )
            elif col == "Address":
                url = row["_url"]
                addr = str(val) if pd.notna(val) else "—"
                if pd.notna(url) and url:
                    cell = (f'<td><a href="{url}" target="_blank" rel="noopener"'
                            f' style="color:#4da6ff;text-decoration:none">{addr}</a></td>')
                else:
                    cell = f"<td>{addr}</td>"
            elif col in _num_fmt and pd.notna(val):
                try:
                    cell = f"<td>{_num_fmt[col].format(float(val))}</td>"
                except (ValueError, TypeError):
                    cell = "<td>—</td>"
            else:
                cell = f"<td>{'—' if pd.isna(val) else val}</td>"
            cells += cell
        rows_html += f"<tr>{cells}</tr>"

    st.markdown(
        f"""
        <div style="overflow-x:auto;overflow-y:auto;max-height:600px;font-size:13px">
        <table style="width:100%;border-collapse:collapse;white-space:nowrap">
          <thead>
            <tr style="position:sticky;top:0;background:#0e1117;z-index:1;
                       border-bottom:1px solid #333">
              {header_html}
            </tr>
          </thead>
          <tbody>{rows_html}</tbody>
        </table>
        </div>
        <style>
          table td, table th {{padding:6px 10px;border-bottom:1px solid #1e1e2e}}
          table tr:hover td {{background:#1a1a2e}}
        </style>
        """,
        unsafe_allow_html=True,
    )


# ═══════════════════════════════
# TAB 2 — Market Charts
# ═══════════════════════════════
with tab2:
    col_l, col_r = st.columns(2)

    # Price distribution by city
    with col_l:
        st.subheader("Price distribution by city")
        fig = px.box(
            df_f[df_f["price"] > 0], x="city", y="price",
            color="city",
            color_discrete_sequence=px.colors.qualitative.Bold,
            labels={"price": "Price ($)", "city": "City"},
        )
        fig.update_layout(showlegend=False, margin=dict(t=30, b=0))
        fig.update_yaxes(tickformat="$,.0f")
        st.plotly_chart(fig, use_container_width=True)

    # Price per sqft scatter
    with col_r:
        st.subheader("Price vs sqft (coloured by deal score)")
        _sc = df_f[(df_f["sqft"] > 0) & (df_f["price"] > 0)].copy()
        fig = px.scatter(
            _sc, x="sqft", y="price", color="deal_score",
            hover_data=["address_raw", "city", "year_built", "dom"],
            color_continuous_scale=["#F8696B", "#FFEB84", "#63BE7B"],
            range_color=[0, 100],
            labels={"price": "Price ($)", "sqft": "Sqft", "deal_score": "Score"},
        )
        fig.update_layout(margin=dict(t=30, b=0))
        fig.update_yaxes(tickformat="$,.0f")
        st.plotly_chart(fig, use_container_width=True)

    col_l2, col_r2 = st.columns(2)

    # Monthly carrying cost histogram
    with col_l2:
        st.subheader("Monthly carrying cost (fee + tax/12)")
        _mc = df_f.copy()
        _mc["monthly_cost"] = (
            _mc["maintenance_fee"].fillna(0) + _mc["taxes_annual"].fillna(0) / 12
        )
        _mc = _mc[_mc["monthly_cost"] > 0]
        fig = px.histogram(
            _mc, x="monthly_cost", color="city", nbins=30,
            barmode="overlay", opacity=0.75,
            labels={"monthly_cost": "Monthly Cost ($)", "city": "City"},
        )
        fig.update_layout(margin=dict(t=30, b=0))
        fig.update_xaxes(tickformat="$,.0f")
        st.plotly_chart(fig, use_container_width=True)

    # Year built histogram
    with col_r2:
        st.subheader("Year built distribution")
        _yb = df_f[df_f["year_built"].fillna(0) > 1900].copy()
        fig = px.histogram(
            _yb, x="year_built", color="city", nbins=20,
            barmode="stack",
            labels={"year_built": "Year Built", "city": "City"},
        )
        fig.update_layout(margin=dict(t=30, b=0))
        st.plotly_chart(fig, use_container_width=True)

    col_l3, col_r3 = st.columns(2)

    # DOM distribution
    with col_l3:
        st.subheader("Days on market")
        _dom = df_f[df_f["dom"].fillna(0) > 0]
        fig = px.histogram(
            _dom, x="dom", color="city", nbins=30,
            barmode="overlay", opacity=0.75,
            labels={"dom": "Days on Market", "city": "City"},
        )
        fig.add_vline(x=21, line_dash="dot", line_color="green",
                      annotation_text="21d (motivated)", annotation_position="top right")
        fig.add_vline(x=90, line_dash="dot", line_color="orange",
                      annotation_text="90d (stale)")
        fig.update_layout(margin=dict(t=30, b=0))
        st.plotly_chart(fig, use_container_width=True)

    # Deal score histogram
    with col_r3:
        st.subheader("Deal score distribution")
        fig = px.histogram(
            df_f[df_f["deal_score"] > 0], x="deal_score", color="city",
            nbins=20, barmode="overlay", opacity=0.75,
            labels={"deal_score": "Deal Score (0–100)", "city": "City"},
        )
        fig.add_vline(x=65, line_dash="dot", line_color="green",
                      annotation_text="65 = Top Deal")
        fig.update_layout(margin=dict(t=30, b=0))
        st.plotly_chart(fig, use_container_width=True)

    col_l4, col_r4 = st.columns(2)

    # Motivated seller quadrant
    with col_l4:
        st.subheader("Motivated seller quadrant")
        _quad = df_f[(df_f["dom"].fillna(0) > 0) & (df_f["deal_score"].fillna(0) > 0)].copy()
        _quad["_drop_size"] = _quad["price_reduction_pct"].fillna(0).clip(lower=1)
        _quad["address_clean"] = _quad["address_raw"].str.replace("|", " ", regex=False)
        fig = px.scatter(
            _quad, x="dom", y="deal_score", color="city",
            size="_drop_size", size_max=22,
            hover_data={"address_clean": True, "price": True,
                        "price_reduction_pct": True, "_drop_size": False},
            labels={"dom": "Days on Market", "deal_score": "Deal Score",
                    "city": "City", "price_reduction_pct": "Drop %",
                    "address_clean": "Address"},
            color_discrete_sequence=px.colors.qualitative.Bold,
        )
        fig.add_vline(x=30, line_dash="dot", line_color="#555")
        fig.add_hline(y=60, line_dash="dot", line_color="#555")
        fig.add_annotation(x=_quad["dom"].quantile(0.75), y=88,
                           text="🎯 Motivated + Good Deal",
                           showarrow=False, font=dict(color="#63BE7B", size=11))
        fig.add_annotation(x=5, y=88, text="Fresh + Good",
                           showarrow=False, font=dict(color="#aaa", size=10))
        fig.add_annotation(x=_quad["dom"].quantile(0.75), y=10,
                           text="Stale + Poor Deal",
                           showarrow=False, font=dict(color="#F8696B", size=10))
        fig.update_layout(margin=dict(t=30, b=0))
        fig.update_yaxes(range=[0, 100])
        st.plotly_chart(fig, use_container_width=True)
        st.caption("Bubble size = price drop %. Top-right = best targets.")

    # Days to first price drop
    with col_r4:
        st.subheader("Days until first price cut")
        _dtd = data["days_to_drop"]
        if _dtd.empty:
            st.info("Need more snapshot history to compute this.")
        else:
            _dtd_f = _dtd[_dtd["days_to_first_drop"].between(0, 365)]
            fig = px.histogram(
                _dtd_f, x="days_to_first_drop", color="city",
                nbins=30, barmode="overlay", opacity=0.75,
                labels={"days_to_first_drop": "Days from listing to first cut", "city": "City"},
            )
            _med_days = int(_dtd_f["days_to_first_drop"].median()) if not _dtd_f.empty else 0
            fig.add_vline(x=_med_days, line_dash="dash", line_color="yellow",
                          annotation_text=f"Median {_med_days}d",
                          annotation_position="top right")
            fig.update_layout(margin=dict(t=30, b=0))
            st.plotly_chart(fig, use_container_width=True)
            st.caption(f"Median seller patience: {_med_days} days before cutting price.")


# ═══════════════════════════════
# TAB 3 — Map
# ═══════════════════════════════
with tab3:
    st.subheader("Listing map — colour = deal score, size = price")
    _map = df_f[
        df_f["latitude"].fillna(0).abs() > 0.1
    ].copy()

    if _map.empty:
        st.info("No coordinates yet — run a full scrape first.")
    else:
        _map["label"] = (
            _map["address_raw"].str.replace("|", " ", regex=False) + "<br>"
            + "Price: $" + _map["price"].apply(lambda x: f"{x:,.0f}") + "<br>"
            + "Score: " + _map["deal_score"].round(1).astype(str) + "<br>"
            + "DOM: " + _map["dom"].fillna(0).astype(int).astype(str) + " days"
        )
        fig = px.scatter_mapbox(
            _map,
            lat="latitude", lon="longitude",
            color="deal_score",
            size="price",
            size_max=18,
            color_continuous_scale=["#F8696B", "#FFEB84", "#63BE7B"],
            range_color=[0, 100],
            hover_name="label",
            hover_data={"latitude": False, "longitude": False,
                        "deal_score": True, "price": True, "city": True},
            zoom=10,
            mapbox_style="carto-positron",
        )
        fig.update_layout(margin=dict(t=0, b=0), height=600)
        st.plotly_chart(fig, use_container_width=True)


# ═══════════════════════════════
# TAB 4 — Alerts
# ═══════════════════════════════
with tab4:
    col_a, col_b = st.columns(2)

    with col_a:
        st.subheader("⚠️ Relist alerts (same address, new MLS#)")
        relists_df = df_f[df_f["is_relist"] == 1].copy()
        if relists_df.empty:
            st.success("No relists detected yet (need more run history).")
        else:
            relists_df["Address"] = relists_df["address_raw"].str.replace("|", " ", regex=False)
            relists_df["Orig→Now"] = relists_df.apply(
                lambda r: (f"${r['price_original']:,.0f} → ${r['price']:,.0f}"
                           if pd.notna(r.get("price_original")) and r["price_original"] > 0
                           else f"${r['price']:,.0f}"),
                axis=1,
            )
            st.dataframe(
                relists_df[["Address", "city", "Orig→Now", "effective_dom",
                             "price_reduction_pct", "deal_score"]]
                .rename(columns={
                    "city": "City",
                    "effective_dom": "Total DOM",
                    "price_reduction_pct": "Total Drop %",
                    "deal_score": "Score",
                })
                .style.format({
                    "Total Drop %": "{:.1f}%", "Score": "{:.1f}"
                }),
                use_container_width=True,
            )

    with col_b:
        st.subheader("💰 Price drops today")
        drops_df = data["drops"]
        if drops_df.empty:
            st.info("No price drops recorded today (need at least 2 days of data).")
        else:
            drops_df["Address"] = drops_df["address_raw"].str.replace("|", " ", regex=False)
            st.dataframe(
                drops_df[["Address", "city", "price_today", "drop_amount",
                           "drop_pct", "dom", "deal_score"]]
                .rename(columns={
                    "city": "City", "price_today": "Price Now",
                    "drop_amount": "Drop $", "drop_pct": "Drop %",
                    "dom": "DOM", "deal_score": "Score",
                })
                .style.format({
                    "Price Now": "${:,.0f}",
                    "Drop $":    "${:,.0f}",
                    "Drop %":    "{:.1f}%",
                    "Score":     "{:.1f}",
                }),
                use_container_width=True,
            )

    st.markdown("---")
    st.subheader("📉 Cumulative price drops (peak → current, active listings)")
    cum_df = data["cum_drops"]
    if cum_df.empty:
        st.info("No cumulative drops yet — need at least 2 days of snapshot data.")
    else:
        cum_df["Address"] = cum_df["address_raw"].str.replace("|", " ", regex=False)
        _pct_thresh = st.slider("Minimum total drop %", 0, 30, 0, key="cum_drop_thresh")
        _show = cum_df[cum_df["total_drop_pct"].abs() >= _pct_thresh].copy()
        st.caption(f"{len(_show)} listings dropped ≥ {_pct_thresh}% from their listed peak")
        st.dataframe(
            _show[["Address", "city", "current_price", "peak_price",
                   "drop_amount", "total_drop_pct", "dom", "days_tracked", "deal_score"]]
            .rename(columns={
                "city": "City", "current_price": "Now", "peak_price": "Peak",
                "drop_amount": "Drop $", "total_drop_pct": "Drop %",
                "dom": "DOM", "days_tracked": "Days Tracked", "deal_score": "Score",
            })
            .style.format({
                "Now": "${:,.0f}", "Peak": "${:,.0f}", "Drop $": "${:,.0f}",
                "Drop %": "{:.1f}%", "Score": "{:.1f}",
            }),
            use_container_width=True,
        )

    st.markdown("---")
    st.subheader("🏁 Recently delisted (last 90 days)")
    st.caption("Listings that disappeared from the market — likely sold or withdrawn. "
               "Quick delist after a price drop = probably sold.")
    dl_df = data["delisted"]
    if dl_df.empty:
        st.info("No delisted listings in the last 90 days yet.")
    else:
        dl_df["Address"] = dl_df["address_raw"].str.replace("|", " ", regex=False)
        dl_df["Days Active"] = dl_df["dom"].fillna(0).astype(int)
        dl_df["Off Market"] = dl_df["last_seen_date"]
        _dl_show = dl_df[["Address", "city", "final_price", "orig_price",
                           "price_reduction_pct", "Days Active", "Off Market", "deal_score"]]
        _dl_show = _dl_show.rename(columns={
            "city": "City", "final_price": "Final Price", "orig_price": "Orig Price",
            "price_reduction_pct": "Total Drop %", "deal_score": "Score (at delist)",
        })
        st.dataframe(
            _dl_show.style.format({
                "Final Price": "${:,.0f}", "Orig Price": "${:,.0f}",
                "Total Drop %": "{:.1f}%", "Score (at delist)": "{:.1f}",
            }),
            use_container_width=True,
        )
        _quick = dl_df[dl_df["Days Active"] <= 14]
        if not _quick.empty:
            st.success(f"**{len(_quick)} listings sold within 14 days** — "
                       f"avg ask: ${_quick['final_price'].mean():,.0f}")


# ═══════════════════════════════
# TAB 5 — Trends
# ═══════════════════════════════
with tab5:
    trends_df = data["trends"]
    if trends_df.empty or len(trends_df) < 2:
        st.info("Trend charts need at least 2 days of data. Keep running daily!")
        st.markdown("**What you'll see here after a few days:**")
        st.markdown("- Active listing count over time")
        st.markdown("- Average price trend")
        st.markdown("- Average $/sqft trend")
        st.markdown("- Average DOM trend")
    else:
        trends_df["snapshot_date"] = pd.to_datetime(trends_df["snapshot_date"])

        col_t1, col_t2 = st.columns(2)
        with col_t1:
            st.subheader("Active listings over time")
            fig = px.line(trends_df, x="snapshot_date", y="active_count",
                          markers=True, color_discrete_sequence=[BLUE])
            fig.update_layout(margin=dict(t=30, b=0))
            st.plotly_chart(fig, use_container_width=True)

            st.subheader("Average $/sqft over time")
            fig = px.line(trends_df, x="snapshot_date", y="avg_psf",
                          markers=True, color_discrete_sequence=[GREEN])
            fig.update_layout(margin=dict(t=30, b=0))
            fig.update_yaxes(tickformat="$,.0f")
            st.plotly_chart(fig, use_container_width=True)

        with col_t2:
            st.subheader("Average price over time")
            fig = px.line(trends_df, x="snapshot_date", y="avg_price",
                          markers=True, color_discrete_sequence=[NAVY])
            fig.update_layout(margin=dict(t=30, b=0))
            fig.update_yaxes(tickformat="$,.0f")
            st.plotly_chart(fig, use_container_width=True)

            st.subheader("Average DOM over time")
            fig = px.line(trends_df, x="snapshot_date", y="avg_dom",
                          markers=True, color_discrete_sequence=[AMBER])
            fig.update_layout(margin=dict(t=30, b=0))
            st.plotly_chart(fig, use_container_width=True)

# ── Per-listing price history (bottom of Tab 5) ───────────────────────────────
with tab5:
    st.markdown("---")
    st.subheader("🔍 Per-listing price history")
    snaps = data["snapshots"].copy()
    if snaps.empty:
        st.info("No snapshot history yet.")
    else:
        snaps["label"] = snaps["address_raw"].str.replace("|", " ", regex=False) + " (" + snaps["city"] + ")"
        _label_map = (
            snaps[["mls_number", "label"]]
            .drop_duplicates("mls_number")
            .set_index("mls_number")["label"]
            .to_dict()
        )
        _sorted_labels = sorted(_label_map.values())
        _sel_label = st.selectbox("Select listing", _sorted_labels, key="hist_listing")
        _sel_mls = next(k for k, v in _label_map.items() if v == _sel_label)
        _hist = snaps[snaps["mls_number"] == _sel_mls].sort_values("snapshot_date")
        _hist["snapshot_date"] = pd.to_datetime(_hist["snapshot_date"])

        if len(_hist) < 2:
            st.info("Only one snapshot recorded for this listing — check back tomorrow.")
        else:
            _peak  = _hist["price"].max()
            _cur   = _hist["price"].iloc[-1]
            _drop  = round(100 * (_cur - _peak) / _peak, 1)
            _h1, _h2, _h3 = st.columns(3)
            _h1.metric("Peak price",    f"${_peak:,.0f}")
            _h2.metric("Current price", f"${_cur:,.0f}")
            _h3.metric("Change from peak", f"{_drop:.1f}%")

            fig_h = px.line(
                _hist, x="snapshot_date", y="price",
                markers=True,
                labels={"snapshot_date": "Date", "price": "Price ($)"},
                color_discrete_sequence=[BLUE],
            )
            fig_h.update_yaxes(tickformat="$,.0f")
            fig_h.update_layout(margin=dict(t=10, b=0))
            st.plotly_chart(fig_h, use_container_width=True)

            with st.expander("Raw snapshot data"):
                st.dataframe(
                    _hist[["snapshot_date", "price", "dom"]]
                    .rename(columns={"snapshot_date": "Date", "price": "Price", "dom": "DOM"})
                    .style.format({"Price": "${:,.0f}"}),
                    use_container_width=True,
                )

# ── Footer ────────────────────────────────────────────────────────────────────
st.markdown("---")
st.caption(
    f"Data from condo_tracker.db · {len(df):,} active listings loaded · "
    f"Auto-refreshes every 60 s · "
    f"[realtor.ca](https://www.realtor.ca)"
)
