"""Biotech Catalyst Tracker - Streamlit dashboard (stage 6).

Reads ONLY dashboard_data/dashboard.db, the small file made by
`python -m src.export`, so it works the same on your computer and online.

Run it from the project folder with:
    streamlit run app.py
"""

import os
import sqlite3
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from src.analytics import OPEN_STATUSES, size_group

DB_FILE = Path(os.environ.get("DASHBOARD_DB",
                              Path(__file__).parent / "dashboard_data" / "dashboard.db"))
DISCLAIMER = ("For educational purposes only. Not investment advice. Data from "
              "ClinicalTrials.gov and Yahoo Finance may be incomplete or delayed.")
DISEASES = {"obesity": "Obesity", "type1_diabetes": "Type 1 diabetes",
            "type2_diabetes": "Type 2 diabetes"}
TRIAL_URL = "https://clinicaltrials.gov/study/"

# Fixed colors per category (a category keeps its color whatever is filtered).
BLUE, ORANGE, AQUA, GRAY = "#2a78d6", "#eb6834", "#1baf7a", "#8a8984"
PHASE_COLORS = {"PHASE2": BLUE, "PHASE2/PHASE3": ORANGE, "PHASE3": AQUA}
SIZE_COLORS = {"small (<$2B)": BLUE, "mid ($2-20B)": ORANGE, "large (>$20B)": AQUA, "all": GRAY}
SIZE_ORDER = ["small (<$2B)", "mid ($2-20B)", "large (>$20B)"]


# --- loading --------------------------------------------------------------

@st.cache_data
def load_tables(path, modified_time):
    """Every table of dashboard.db as a DataFrame. `modified_time` makes
    Streamlit reload automatically after a new export."""
    conn = sqlite3.connect(path)
    names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
    tables = {name: pd.read_sql(f'SELECT * FROM "{name}"', conn) for name in names}
    conn.close()
    return tables


def with_links(table):
    table = table.copy()
    table["link"] = TRIAL_URL + table["nct_id"]
    return table


def link_column():
    return st.column_config.LinkColumn("ClinicalTrials.gov", display_text="open")


def pct(x, signed=True):
    if x is None or pd.isna(x):
        return "n/a"
    return f"{x:+.1%}" if signed else f"{x:.1%}"


def disease_labels(matched):
    if not isinstance(matched, str):
        return ""
    return ", ".join(DISEASES.get(k.strip(), k) for k in matched.split(";"))


# --- tab 1: catalyst calendar ---------------------------------------------

def calendar_tab(t):
    st.subheader("Catalyst calendar")
    st.caption("Open Phase 2/3 trials of US-listed companies, by expected primary "
               "completion date. Impact score 0-100 = 50% company size + 30% "
               "concentration + 20% phase. Higher = likely bigger stock move.")
    cal = t["calendar"].copy()
    cal["size_group"] = cal["market_cap"].map(size_group)
    cal["diseases"] = cal["matched_searches"].map(disease_labels)

    section = st.radio("Show", ["Upcoming (next 12 months)",
                                "Date already passed (last 12 months)"], horizontal=True)
    cal = cal[cal["section"] == ("upcoming" if section.startswith("Upcoming") else "past date")]

    c1, c2, c3, c4 = st.columns(4)
    diseases = c1.multiselect("Disease", list(DISEASES), default=list(DISEASES),
                              format_func=DISEASES.get)
    phases = c2.multiselect("Phase", list(PHASE_COLORS), default=list(PHASE_COLORS))
    sizes = c3.multiselect("Company size", SIZE_ORDER, default=SIZE_ORDER)
    min_score = c4.slider("Minimum impact score", 0, 100, 0)

    keep = (cal["matched_searches"].fillna("").map(
                lambda m: any(d in m.split("; ") for d in diseases))
            & cal["phase"].isin(phases)
            & (cal["size_group"].isin(sizes) | cal["size_group"].isna())
            & ((cal["impact_score"] >= min_score)
               | (cal["impact_score"].isna() & (min_score == 0))))
    cal = cal[keep].sort_values("impact_score", ascending=False)
    st.write(f"**{len(cal)} trials**")
    if cal.empty:
        st.info("No trials match these filters.")
        return

    chart = cal.dropna(subset=["impact_score"]).copy()
    chart["market cap ($B)"] = (chart["market_cap"] / 1e9).round(2)
    fig = px.scatter(
        chart, x="primary_completion_date", y="impact_score", color="phase",
        size=chart["size_part"].clip(lower=0.05) * 20, size_max=20,
        color_discrete_map=PHASE_COLORS,
        category_orders={"phase": list(PHASE_COLORS)},
        hover_name="ticker",
        hover_data={"lead_sponsor": True, "market cap ($B)": True, "interventions": True,
                    "flags": True, "primary_completion_date": True, "impact_score": True,
                    "phase": False},
        labels={"primary_completion_date": "Expected primary completion",
                "impact_score": "Impact score", "phase": "Phase", "lead_sponsor": "Sponsor"})
    fig.update_traces(marker={"opacity": 0.8})
    fig.update_layout(height=380, margin={"t": 10, "b": 10}, yaxis_range=[0, 100])
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Bigger bubble = smaller company (a bigger share of its value rides on the trial).")

    cal = with_links(cal)
    cal["market_cap_b"] = cal["market_cap"] / 1e9
    cal["flags"] = cal["flags"].fillna("")
    st.dataframe(
        cal[["primary_completion_date", "ticker", "lead_sponsor", "impact_score", "flags",
             "phase", "diseases", "interventions", "market_cap_b",
             "open_trials_all_diseases", "enrollment", "link"]],
        hide_index=True, use_container_width=True,
        column_config={
            "primary_completion_date": "Date", "ticker": "Ticker", "lead_sponsor": "Sponsor",
            "impact_score": st.column_config.NumberColumn("Score", format="%.1f"),
            "flags": "Warnings", "phase": "Phase", "diseases": "Disease",
            "interventions": "Drugs",
            "market_cap_b": st.column_config.NumberColumn("Market cap ($B)", format="%.2f"),
            "open_trials_all_diseases": "Open trials (all diseases)",
            "enrollment": "Patients", "link": link_column(),
        })
    with st.expander("What do the warnings mean?"):
        st.markdown(
            "- **overdue**: the expected date passed but the sponsor never updated it.\n"
            "- **data collected**: the trial finished collecting data; results can come any day.\n"
            "- **month-only date**: the sponsor gave only a month; we use its last day.\n"
            "- **thinly traded**: the stock had no trades on over 5% of days in the last 2 years.\n"
            "- **no market cap**: size unknown, so no score.\n"
            "- **cash may run out before readout**: at its recent cash burn, the company's "
            "cash (from its latest quarterly filing) runs out before this trial's expected "
            "primary completion date. It may have raised money since; check newer filings.")


# --- tab 2: company view --------------------------------------------------

def company_tab(t):
    st.subheader("Company view")
    prices = t["prices"]
    companies = t["companies"].set_index("ticker")
    tickers = sorted(set(prices["ticker"]) - {"XBI"})
    upcoming = t["calendar"][t["calendar"]["section"] == "upcoming"]
    top = upcoming.sort_values("impact_score", ascending=False)["ticker"]
    default = top.iloc[0] if len(top) and top.iloc[0] in tickers else tickers[0]

    c1, c2 = st.columns([1, 2])
    ticker = c1.selectbox("Ticker", tickers, index=tickers.index(default))
    years = c2.radio("Period", [1, 3, 5], index=1, horizontal=True,
                     format_func=lambda y: f"{y} year{'s' if y > 1 else ''}")
    names = companies.loc[ticker, "sponsor_names"] if ticker in companies.index else ""
    st.caption(f"Sponsor names in ClinicalTrials.gov: {names}")

    trials = with_links(t["company_trials"][t["company_trials"]["ticker"] == ticker])
    sponsor = t["slippage_sponsors"].set_index("company")
    m1, m2, m3, m4 = st.columns(4)
    cap = companies.loc[ticker, "market_cap"] if ticker in companies.index else None
    m1.metric("Market cap today", "n/a" if cap is None or pd.isna(cap) else
              f"${cap / 1e9:,.2f}B" if cap >= 1e9 else f"${cap / 1e6:,.1f}M")
    m2.metric("Open trials (our diseases)",
              int(trials["overall_status"].isin(OPEN_STATUSES).sum()))
    m3.metric("Upcoming catalysts (12 mo)", int((upcoming["ticker"] == ticker).sum()))
    m4.metric("Overdue trials now",
              int(sponsor.loc[ticker, "overdue_now"]) if ticker in sponsor.index else 0)

    # Price chart: stock vs XBI, both starting at 100.
    end = pd.Timestamp(prices["date"].max())
    start = (end - pd.DateOffset(years=years)).date().isoformat()
    window = prices[(prices["date"] >= start) & prices["ticker"].isin([ticker, "XBI"])]
    wide = window.pivot(index="date", columns="ticker", values="adj_close").dropna()
    if wide.empty or ticker not in wide.columns:
        st.info("No price data for this period.")
    else:
        indexed = 100 * wide / wide.iloc[0]
        fig = go.Figure()
        fig.add_scatter(x=indexed.index, y=indexed["XBI"], name="XBI (biotech ETF)",
                        line={"color": GRAY, "width": 2})
        fig.add_scatter(x=indexed.index, y=indexed[ticker], name=ticker,
                        line={"color": BLUE, "width": 2})
        past = trials[(trials["primary_completion_date_type"] == "ACTUAL")
                      & (trials["primary_completion_date"] >= indexed.index[0])]
        for d in past["primary_completion_date"].unique():
            fig.add_vline(x=d, line={"color": GRAY, "width": 1, "dash": "dot"}, opacity=0.5)
        coming = upcoming[upcoming["ticker"] == ticker]
        for d in coming["primary_completion_date"].unique():
            fig.add_vline(x=d, line={"color": ORANGE, "width": 1.5, "dash": "dash"})
        fig.update_layout(height=380, margin={"t": 10, "b": 10}, hovermode="x unified",
                          yaxis_title="Value of $100 invested", legend={"orientation": "h", "y": 1.02, "yanchor": "bottom", "title": None})
        st.plotly_chart(fig, use_container_width=True)
        st.caption("Dotted gray lines: past trial completions (actual dates). "
                   "Dashed orange lines: upcoming expected completions.")

    runway_section(t, ticker)

    st.markdown("**Trials in obesity and diabetes**")
    st.dataframe(
        trials.sort_values("primary_completion_date", ascending=False)[
            ["primary_completion_date", "primary_completion_date_type", "phase",
             "overall_status", "interventions", "title", "link"]],
        hide_index=True, use_container_width=True,
        column_config={"primary_completion_date": "Primary completion",
                       "primary_completion_date_type": "Actual / estimated",
                       "phase": "Phase", "overall_status": "Status",
                       "interventions": "Drugs", "title": "Title", "link": link_column()})

    events = t["events"]
    mine = events[(events["ticker"] == ticker) & (events["included"] == 1)]
    if len(mine):
        st.markdown(f"**Past events in the event study ({len(mine)})**")
        st.dataframe(
            mine.sort_values("event_date", ascending=False)[
                ["event_date", "phase", "status_group", "n_trials", "car_short", "car_post"]],
            hide_index=True, use_container_width=True,
            column_config={
                "event_date": "Completion date", "phase": "Phase", "status_group": "Status",
                "n_trials": "Trials",
                "car_short": st.column_config.NumberColumn("CAR days -1..+1", format="percent"),
                "car_post": st.column_config.NumberColumn("CAR days 0..+60", format="percent")})


def runway_section(t, ticker):
    """Cash runway vs. readout, for companies under $1B with an open trial."""
    runway = t.get("cash_runway")
    if runway is None or runway.empty or ticker not in set(runway["ticker"]):
        return
    mine = runway[runway["ticker"] == ticker]
    first = mine.iloc[0]
    st.markdown("**Cash runway vs. readout**")
    r1, r2, r3, r4 = st.columns(4)
    r1.metric(f"Cash ({first['balance_sheet_date']})", f"${first['cash'] / 1e6:,.1f}M")
    burn = first["monthly_burn"]
    r2.metric("Monthly burn (avg. last 4 quarters)",
              f"${burn / 1e6:,.1f}M" if pd.notna(burn) else "n/a")
    r3.metric("Runway", f"{first['runway_months']:.1f} months"
              if pd.notna(first["runway_months"]) else "n/a")
    r4.metric("Cash runs out around", first["runway_end"] or "n/a")
    if mine["flagged"].astype(bool).any():
        st.warning("⚠️ **Cash may run out before readout** for at least one trial below. "
                   "Companies often raise money before a readout, which can dilute "
                   "existing shareholders. Check filings newer than "
                   f"{first['balance_sheet_date']}.")
    st.dataframe(
        mine[["nct_id", "phase", "primary_completion_date", "cushion_months",
              "completion_date", "cushion_study_months", "runway_status"]],
        hide_index=True, use_container_width=True,
        column_config={
            "nct_id": "Trial", "phase": "Phase",
            "primary_completion_date": "Expected primary completion",
            "cushion_months": st.column_config.NumberColumn(
                "Cash cushion at readout (months)", format="%+.1f",
                help="Runway end minus trial date. Negative = cash runs out first."),
            "completion_date": "Study completion",
            "cushion_study_months": st.column_config.NumberColumn(
                "Cushion at study completion", format="%+.1f"),
            "runway_status": "Status"})


# --- tab 3: event study ---------------------------------------------------

def event_tab(t, meta):
    st.subheader("Event study: how stocks moved around past trial completions")
    st.caption("Day 0 = a trial's actual primary completion date. Abnormal return = the "
               "stock's return minus what XBI predicted (market model). CAR = abnormal "
               "returns added up.")
    m1, m2, m3 = st.columns(3)
    m1.metric("Events measured", meta.get("events_measured"))
    m2.metric("Events excluded", meta.get("events_excluded"))
    m3.metric("Impact score vs. size of move (rank corr., 60 days)",
              meta.get("score_backtest_post"))

    paths = t["car_paths"]
    stat = st.radio("Average", ["median", "mean"], horizontal=True,
                    help="The mean is pulled up by a few huge winners; the median is the typical event.")
    shown = paths[paths["size_group"].isin(SIZE_ORDER)].copy()
    shown["CAR"] = shown[f"{stat}_car"]
    fig = px.line(shown, x="day", y="CAR", color="size_group",
                  color_discrete_map=SIZE_COLORS, category_orders={"size_group": SIZE_ORDER},
                  hover_data={"events": True},
                  labels={"day": "Trading days from trial completion",
                          "size_group": "Company size (at the time)"})
    fig.update_traces(line={"width": 2})
    fig.add_vline(x=0, line={"color": GRAY, "width": 1, "dash": "dot"})
    fig.update_layout(height=380, margin={"t": 10, "b": 10}, yaxis_tickformat=".0%",
                      hovermode="x unified", legend={"orientation": "h", "y": 1.02, "yanchor": "bottom", "title": None})
    st.plotly_chart(fig, use_container_width=True)

    window = st.radio("Window", ["post", "short"], horizontal=True,
                      format_func={"post": "Days 0 to +60", "short": "Days -1 to +1"}.get)
    summary = t["event_summary"]
    summary = summary[summary["window"] == window].copy()
    summary["group"] = [g if b == "all" else f"{b.replace('_', ' ')}: {g}"
                        for b, g in zip(summary["breakdown"], summary["group"])]
    st.dataframe(
        summary[["group", "events", "mean_car", "t_stat", "mean_abs_car",
                 "median_abs_car", "share_significant"]],
        hide_index=True, use_container_width=True,
        column_config={
            "group": "Group", "events": "Events",
            "mean_car": st.column_config.NumberColumn("Mean CAR", format="percent"),
            "t_stat": st.column_config.NumberColumn("t-stat", format="%.2f"),
            "mean_abs_car": st.column_config.NumberColumn("Mean |CAR| (size of move)", format="percent"),
            "median_abs_car": st.column_config.NumberColumn("Median |CAR|", format="percent"),
            "share_significant": st.column_config.NumberColumn(
                "Unusually big moves (5% expected by chance)", format="percent")})
    st.warning("**Survivorship bias:** only companies still listed today are included, so "
               "small companies whose trials failed and that later disappeared are missing. "
               "That is why small-company moves look mostly positive.")
    with st.expander("All events, including excluded ones and why"):
        st.dataframe(t["events"], hide_index=True, use_container_width=True)


# --- tab 4: date slippage -------------------------------------------------

def slippage_tab(t, meta):
    st.subheader("Date slippage: do sponsors keep pushing their dates back?")
    snapshots = int(meta.get("snapshots", 0))
    if snapshots < 2:
        st.info(f"History is still building: only one weekly snapshot so far "
                f"({meta.get('first_snapshot')}). Moved dates appear after the next weekly run.")
    else:
        changes = t["slippage_changes"]
        latest = changes[changes["snapshot_date"] == changes["snapshot_date"].max()]
        st.markdown(f"**Changes in the latest snapshot ({len(latest)})**")
        st.dataframe(latest, hide_index=True, use_container_width=True)

    sponsors = t["slippage_sponsors"]
    traded_only = st.checkbox("Only companies with a US ticker", value=True)
    if traded_only:
        sponsors = sponsors[sponsors["ticker"].notna()]
    st.markdown("**Sponsor reliability**")
    st.dataframe(
        sponsors[["company", "sponsor_names", "open_trials", "overdue_now",
                  "comparable_trials", "share_delayed", "median_delay_days"]],
        hide_index=True, use_container_width=True,
        column_config={
            "company": "Company", "sponsor_names": "Sponsor names",
            "open_trials": "Open trials", "overdue_now": "Overdue now",
            "comparable_trials": "Trials with date history",
            "share_delayed": st.column_config.NumberColumn("Share delayed", format="percent"),
            "median_delay_days": st.column_config.NumberColumn("Median delay (days)",
                                                                format="%.0f")})
    st.caption("Overdue = open trial whose estimated completion date already passed. "
               "Share delayed and median delay appear once a company has 3+ trials with "
               "date history.")


# --- tab 5: method --------------------------------------------------------

def method_tab(meta):
    st.subheader("Method and limitations")
    st.markdown(f"""
**Data.** Phase 2 and Phase 3 industry-sponsored trials for obesity, type 1 and
type 2 diabetes from the ClinicalTrials.gov API (first downloaded
{meta.get('first_snapshot')}, last updated {meta.get('data_as_of')}). Sponsors are
matched to US stock tickers using the SEC company list plus a hand-checked alias
list; fuzzy matches are never accepted without review. Daily prices come from
Yahoo Finance; **XBI** (a biotech ETF) is the benchmark.

**Impact score (0-100).** 50% company size (log scale of market cap: $50M or less = 1,
$500B or more = 0), 30% concentration (1 / the company's open Phase 2/3 trials across
all diseases) and 20% phase (Phase 3 = 1, Phase 2/3 = 0.8, Phase 2 = 0.5). Warnings
are shown next to the score but don't change it.

**Event study.** Day 0 = a trial's *actual* primary completion date (exact day only).
A market model fitted over trading days -250 to -30 predicts each stock's return from
XBI's return; the abnormal return is the difference. Results usually come out within
about 3 months, so the main window is days 0 to +60. Because we don't know whether
results were good or bad, the key measure is the *size* of the move (|CAR|).

**Date slippage.** Each weekly run saves a snapshot of every trial's dates; comparing
snapshots shows when sponsors push dates back.

**Cash runway vs. readout** (companies under $1B). Monthly burn = average quarterly
operating cash outflow over the last 4 quarters ÷ 3. Runway = cash (incl. short-term
investments) ÷ monthly burn, counted from the latest balance-sheet date. If the runway
ends before a trial's expected primary completion date, the trial is flagged "cash may
run out before readout" - a sign the company may need to raise money first. The cash
figure can be months old, so the company may already have raised money.

**Limitations.**
- *Survivorship bias*: only companies listed today are matched.
- Day 0 is the completion date, not the press-release date of the results.
- Company size at the time is estimated from today's market cap and the price change
  (ignores shares issued since).
- CAR adds daily returns, so a very large drop can show below -100%.
- Events of the same company can overlap in time, which makes t-statistics look stronger.
- Big pharma dominates the sample (about 470 of 538 events).
""")


# --- page -----------------------------------------------------------------

def main():
    st.set_page_config(page_title="Biotech Catalyst Tracker", layout="wide")
    st.title("Biotech Catalyst Tracker")
    if not DB_FILE.exists():
        st.error(f"No dashboard data found at {DB_FILE}. Run `python -m src.export` first.")
        st.stop()
    t = load_tables(str(DB_FILE), DB_FILE.stat().st_mtime)
    meta = dict(zip(t["meta"]["key"], t["meta"]["value"]))

    st.caption(f"Obesity and diabetes trials · data as of {meta.get('data_as_of')}")
    st.info(DISCLAIMER, icon="⚠️")

    tabs = st.tabs(["Catalyst calendar", "Company view", "Event study",
                    "Date slippage", "Method & limitations"])
    with tabs[0]:
        calendar_tab(t)
    with tabs[1]:
        company_tab(t)
    with tabs[2]:
        event_tab(t, meta)
    with tabs[3]:
        slippage_tab(t, meta)
    with tabs[4]:
        method_tab(meta)
    st.divider()
    st.caption(DISCLAIMER)


main()
