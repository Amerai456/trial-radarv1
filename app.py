"""TOPLINE - biotech catalyst dashboard (stage 6), terminal style.

Reads ONLY dashboard_data/dashboard.db, the small file made by
`python -m src.export`, so it works the same on your computer and online.
The look (black screen, monospace font, square corners) is set in
.streamlit/config.toml plus a few lines of CSS below.

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

# Only the tiny shared file: the dashboard must not load the data pipeline.
from src.common import OPEN_STATUSES, size_group

APP_NAME = "TOPLINE"   # the dashboard's name; change it here only
DB_FILE = Path(os.environ.get("DASHBOARD_DB",
                              Path(__file__).parent / "dashboard_data" / "dashboard.db"))
DISCLAIMER = ("For educational purposes only. Not investment advice. Data from "
              "ClinicalTrials.gov and Yahoo Finance may be incomplete or delayed.")
DISEASES = {"obesity": "Obesity", "type1_diabetes": "Type 1 diabetes",
            "type2_diabetes": "Type 2 diabetes"}
TRIAL_URL = "https://clinicaltrials.gov/study/"

# Terminal colors. The three series colors passed the colorblind-safety
# checks on a black background; green/red only ever go with a +/- sign.
AMBER, BLUE, MAGENTA = "#d07c00", "#1e90ff", "#d43fbf"   # chart series
HILITE = "#ffa028"                                       # bright amber for text
GRAY, DIM, GRID = "#8c8c8c", "#5a5a5a", "#1c1c1c"
GREEN, RED = "#33d17a", "#ff4040"
PHASE_COLORS = {"PHASE2": AMBER, "PHASE2/PHASE3": MAGENTA, "PHASE3": BLUE}
SIZE_COLORS = {"small (<$2B)": AMBER, "mid ($2-20B)": MAGENTA, "large (>$20B)": BLUE}
SIZE_ORDER = ["small (<$2B)", "mid ($2-20B)", "large (>$20B)"]

# A few things the theme file can't do: the status bar, small uppercase
# section labels, plain NOTE/WARN lines, and tighter spacing.
CSS = f"""
<style>
.block-container {{ padding-top: 1.2rem; padding-bottom: 1rem; max-width: 100%; }}
header[data-testid="stHeader"] {{ background: transparent; height: 2rem; }}
.block-container {{ padding-top: 2.2rem !important; }}
* {{ border-radius: 0 !important; }}
.tl-bar {{ border-bottom: 1px solid #333; padding: 2px 0 6px 0; margin-bottom: 4px;
          white-space: nowrap; overflow-x: auto; font-size: 0.85rem; }}
.tl-bar .name {{ color: #000; background: {HILITE}; font-weight: 700; padding: 1px 6px; }}
.tl-bar .item {{ color: {GRAY}; margin-left: 18px; }}
.tl-bar .item b {{ color: #e0e0e0; font-weight: 600; }}
.tl-bar .risk {{ color: {RED}; margin-left: 18px; }}
.tl-h {{ color: {HILITE}; text-transform: uppercase; letter-spacing: 0.06em;
         font-size: 0.8rem; border-bottom: 1px solid #333; margin: 10px 0 6px 0;
         padding-bottom: 2px; }}
.tl-note {{ color: {GRAY}; font-size: 0.8rem; }}
.tl-warn {{ color: {HILITE}; font-size: 0.8rem; }}
.tl-warn b, .tl-note b {{ color: #e0e0e0; }}
[data-testid="stMetricLabel"] p {{ text-transform: uppercase; font-size: 0.7rem;
                                   color: {GRAY}; }}
button[data-baseweb="tab"] p {{ font-size: 0.8rem; letter-spacing: 0.04em; }}
</style>
"""


# --- small display helpers ------------------------------------------------

def section(label):
    """A small uppercase amber label with a thin line (instead of big headings)."""
    st.markdown(f'<div class="tl-h">{label}</div>', unsafe_allow_html=True)


def note(text):
    st.markdown(f'<div class="tl-note">NOTE: {text}</div>', unsafe_allow_html=True)


def warn(text):
    st.markdown(f'<div class="tl-warn">WARN: {text}</div>', unsafe_allow_html=True)


def terminal_chart(fig, height=340):
    """Black background, faint grid, monospace text, no decoration."""
    axis = {"gridcolor": GRID, "linecolor": "#444", "zerolinecolor": "#444",
            "tickfont": {"color": GRAY}, "title": {"font": {"color": GRAY}}}
    fig.update_layout(
        height=height, margin={"t": 24, "b": 8, "l": 8, "r": 8},
        paper_bgcolor="#000", plot_bgcolor="#000",
        font={"family": "Consolas, 'Courier New', monospace", "size": 11, "color": "#e0e0e0"},
        legend={"orientation": "h", "y": 1.02, "yanchor": "bottom", "x": 0, "title": None,
                "font": {"color": "#e0e0e0"}},
        hoverlabel={"bgcolor": "#000", "bordercolor": HILITE,
                    "font": {"family": "Consolas, monospace", "color": "#e0e0e0"}},
        xaxis=axis, yaxis=axis)
    return fig


def signed_colors(value):
    """Green for positive, red for negative (the +/- sign carries it too)."""
    if value is None or pd.isna(value) or value == 0:
        return ""
    return f"color: {GREEN}" if value > 0 else f"color: {RED}"


def signed_table(table, percent_cols=(), number_cols=(), other_formats=None):
    """A table where signed numbers are green/red and formatted with a sign.
    other_formats = formats for the remaining columns (all set in one call,
    because a second .format() call would wipe out the first)."""
    formats = {c: "{:+.1%}" for c in percent_cols}
    formats.update({c: "{:+.1f}" for c in number_cols})
    formats.update(other_formats or {})
    return (table.style.format(formats, na_rep="n/a")
            .map(signed_colors, subset=list(percent_cols) + list(number_cols)))


def money(x):
    if x is None or pd.isna(x):
        return "n/a"
    return f"${x / 1e9:,.2f}B" if abs(x) >= 1e9 else f"${x / 1e6:,.1f}M"


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
    return st.column_config.LinkColumn("CT.GOV", display_text="open")


def disease_labels(matched):
    if not isinstance(matched, str):
        return ""
    return ", ".join(DISEASES.get(k.strip(), k) for k in matched.split(";"))


# --- tab 1: catalyst calendar ---------------------------------------------

def calendar_tab(t):
    section("Catalyst calendar · open Phase 2/3 trials of US-listed companies")
    note("Impact score 0-100 = 50% company size + 30% concentration + 20% phase. "
         "Higher = likely bigger stock move.")
    cal = t["calendar"].copy()
    cal["size_group"] = cal["market_cap"].map(size_group)
    cal["diseases"] = cal["matched_searches"].map(disease_labels)

    section_choice = st.radio("Show", ["Upcoming (next 12 months)",
                                       "Date already passed (last 12 months)"],
                              horizontal=True)
    cal = cal[cal["section"] == ("upcoming" if section_choice.startswith("Upcoming")
                                 else "past date")]

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
    section(f"{len(cal)} trials")
    if cal.empty:
        note("No trials match these filters.")
        return

    chart = cal.dropna(subset=["impact_score"]).copy()
    chart["market cap ($B)"] = (chart["market_cap"] / 1e9).round(2)
    fig = px.scatter(
        chart, x="primary_completion_date", y="impact_score", color="phase",
        size=chart["size_part"].clip(lower=0.05) * 14, size_max=14,
        color_discrete_map=PHASE_COLORS,
        category_orders={"phase": list(PHASE_COLORS)},
        hover_name="ticker",
        hover_data={"lead_sponsor": True, "market cap ($B)": True, "interventions": True,
                    "flags": True, "primary_completion_date": True, "impact_score": True,
                    "phase": False},
        labels={"primary_completion_date": "EXPECTED PRIMARY COMPLETION",
                "impact_score": "IMPACT SCORE", "phase": "Phase", "lead_sponsor": "Sponsor"})
    fig.update_traces(marker={"symbol": "square", "opacity": 0.9, "line": {"width": 0}})
    fig.update_layout(yaxis_range=[0, 100])
    st.plotly_chart(terminal_chart(fig), width="stretch")
    note("Bigger square = smaller company (a bigger share of its value rides on the trial).")

    cal = with_links(cal)
    cal["market_cap_b"] = cal["market_cap"] / 1e9
    cal["flags"] = cal["flags"].fillna("")
    st.dataframe(
        cal[["primary_completion_date", "ticker", "impact_score", "phase", "flags",
             "lead_sponsor", "diseases", "interventions", "market_cap_b",
             "open_trials_all_diseases", "enrollment", "link"]],
        hide_index=True, width="stretch",
        column_config={
            "primary_completion_date": "DATE", "ticker": "TICKER",
            "impact_score": st.column_config.NumberColumn("SCORE", format="%.1f"),
            "phase": "PHASE", "flags": "WARNINGS", "lead_sponsor": "SPONSOR",
            "diseases": "DISEASE", "interventions": "DRUGS",
            "market_cap_b": st.column_config.NumberColumn("MKT CAP $B", format="%.2f"),
            "open_trials_all_diseases": "OPEN TRIALS (ALL)",
            "enrollment": "PATIENTS", "link": link_column(),
        })
    with st.expander("Warnings legend"):
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
    prices = t["prices"]
    companies = t["companies"].set_index("ticker")
    tickers = sorted(set(prices["ticker"]) - {"XBI"})
    upcoming = t["calendar"][t["calendar"]["section"] == "upcoming"]
    top = upcoming.sort_values("impact_score", ascending=False)["ticker"]
    default = top.iloc[0] if len(top) and top.iloc[0] in tickers else tickers[0]

    c1, c2 = st.columns([1, 2])
    ticker = c1.selectbox("Ticker", tickers, index=tickers.index(default))
    years = c2.radio("Period", [1, 3, 5], index=1, horizontal=True,
                     format_func=lambda y: f"{y}Y")
    names = companies.loc[ticker, "sponsor_names"] if ticker in companies.index else ""
    section(f"{ticker} · {names}")

    trials = with_links(t["company_trials"][t["company_trials"]["ticker"] == ticker])
    sponsor = t["slippage_sponsors"].set_index("company")
    m1, m2, m3, m4 = st.columns(4)
    cap = companies.loc[ticker, "market_cap"] if ticker in companies.index else None
    m1.metric("Market cap", money(cap))
    m2.metric("Open trials (our diseases)",
              int(trials["overall_status"].isin(OPEN_STATUSES).sum()))
    m3.metric("Catalysts next 12M", int((upcoming["ticker"] == ticker).sum()))
    m4.metric("Overdue trials",
              int(sponsor.loc[ticker, "overdue_now"]) if ticker in sponsor.index else 0)

    # Price chart: stock vs XBI, both starting at 100.
    end = pd.Timestamp(prices["date"].max())
    start = (end - pd.DateOffset(years=years)).date().isoformat()
    window = prices[(prices["date"] >= start) & prices["ticker"].isin([ticker, "XBI"])]
    wide = window.pivot(index="date", columns="ticker", values="adj_close").dropna()
    section(f"{ticker} vs XBI · value of $100 invested")
    if wide.empty or ticker not in wide.columns:
        note("No price data for this period.")
    else:
        indexed = 100 * wide / wide.iloc[0]
        fig = go.Figure()
        fig.add_scatter(x=indexed.index, y=indexed["XBI"], name="XBI",
                        line={"color": GRAY, "width": 1.5})
        fig.add_scatter(x=indexed.index, y=indexed[ticker], name=ticker,
                        line={"color": HILITE, "width": 1.5})
        past = trials[(trials["primary_completion_date_type"] == "ACTUAL")
                      & (trials["primary_completion_date"] >= indexed.index[0])]
        for d in past["primary_completion_date"].unique():
            fig.add_vline(x=d, line={"color": DIM, "width": 1, "dash": "dot"})
        coming = upcoming[upcoming["ticker"] == ticker]
        for d in coming["primary_completion_date"].unique():
            fig.add_vline(x=d, line={"color": BLUE, "width": 1, "dash": "dash"})
        fig.update_layout(hovermode="x unified")
        st.plotly_chart(terminal_chart(fig), width="stretch")
        note("Dotted gray = past trial completions (actual). "
             "Dashed blue = upcoming expected completions.")

    runway_section(t, ticker)

    section("Trials in obesity and diabetes")
    st.dataframe(
        trials.sort_values("primary_completion_date", ascending=False)[
            ["primary_completion_date", "primary_completion_date_type", "phase",
             "overall_status", "interventions", "title", "link"]],
        hide_index=True, width="stretch",
        column_config={"primary_completion_date": "PRIMARY COMPLETION",
                       "primary_completion_date_type": "ACT/EST",
                       "phase": "PHASE", "overall_status": "STATUS",
                       "interventions": "DRUGS", "title": "TITLE", "link": link_column()})

    events = t["events"]
    mine = events[(events["ticker"] == ticker) & (events["included"] == 1)]
    if len(mine):
        section(f"Past events in the event study ({len(mine)})")
        table = mine.sort_values("event_date", ascending=False)[
            ["event_date", "phase", "status_group", "n_trials", "car_short", "car_post"]]
        st.dataframe(
            signed_table(table, percent_cols=["car_short", "car_post"]),
            hide_index=True, width="stretch",
            column_config={"event_date": "COMPLETION DATE", "phase": "PHASE",
                           "status_group": "STATUS", "n_trials": "TRIALS",
                           "car_short": "CAR -1..+1", "car_post": "CAR 0..+60"})


def runway_section(t, ticker):
    """Cash runway vs. readout, for companies under $1B with an open trial."""
    runway = t.get("cash_runway")
    if runway is None or runway.empty or ticker not in set(runway["ticker"]):
        return
    mine = runway[runway["ticker"] == ticker]
    first = mine.iloc[0]
    section(f"Cash runway vs. readout · balance sheet {first['balance_sheet_date']}")
    r1, r2, r3, r4 = st.columns(4)
    r1.metric("Cash", money(first["cash"]))
    r2.metric("Burn / month (4Q avg)", money(first["monthly_burn"]))
    r3.metric("Runway", f"{first['runway_months']:.1f} mo"
              if pd.notna(first["runway_months"]) else "n/a")
    r4.metric("Cash out ~", first["runway_end"] or "n/a")
    if mine["flagged"].astype(bool).any():
        warn("<b>Cash may run out before readout</b> for at least one trial below. "
             "Companies often raise money before a readout, which can dilute existing "
             f"shareholders. Check filings newer than {first['balance_sheet_date']}.")
    table = mine[["nct_id", "phase", "primary_completion_date", "cushion_months",
                  "completion_date", "cushion_study_months", "runway_status"]]
    st.dataframe(
        signed_table(table, number_cols=["cushion_months", "cushion_study_months"]),
        hide_index=True, width="stretch",
        column_config={
            "nct_id": "TRIAL", "phase": "PHASE",
            "primary_completion_date": "PRIMARY COMPLETION",
            "cushion_months": st.column_config.Column(
                "CUSHION AT READOUT (MO)",
                help="Runway end minus trial date. Negative = cash runs out first."),
            "completion_date": "STUDY COMPLETION",
            "cushion_study_months": "CUSHION AT STUDY END (MO)",
            "runway_status": "STATUS"})


# --- tab 3: event study ---------------------------------------------------

def event_tab(t, meta):
    section("Event study · stock moves around past trial completions")
    note("Day 0 = a trial's actual primary completion date. Abnormal return = the "
         "stock's return minus what XBI predicted (market model). CAR = abnormal "
         "returns added up.")
    m1, m2, m3 = st.columns(3)
    m1.metric("Events measured", meta.get("events_measured"))
    m2.metric("Events excluded", meta.get("events_excluded"))
    m3.metric("Score vs |move| (rank corr, 60D)", meta.get("score_backtest_post"))

    paths = t["car_paths"]
    stat = st.radio("Average", ["median", "mean"], horizontal=True,
                    help="The mean is pulled up by a few huge winners; the median is the typical event.")
    shown = paths[paths["size_group"].isin(SIZE_ORDER)].copy()
    shown["CAR"] = shown[f"{stat}_car"]
    section(f"{stat} CAR by trading day · by company size at the time")
    fig = px.line(shown, x="day", y="CAR", color="size_group",
                  color_discrete_map=SIZE_COLORS, category_orders={"size_group": SIZE_ORDER},
                  hover_data={"events": True},
                  labels={"day": "TRADING DAYS FROM TRIAL COMPLETION", "CAR": "CAR",
                          "size_group": "Company size"})
    fig.update_traces(line={"width": 1.5})
    fig.add_vline(x=0, line={"color": DIM, "width": 1, "dash": "dot"})
    fig.add_hline(y=0, line={"color": "#444", "width": 1})
    fig.update_layout(yaxis_tickformat=".0%", hovermode="x unified")
    st.plotly_chart(terminal_chart(fig), width="stretch")

    window = st.radio("Window", ["post", "short"], horizontal=True,
                      format_func={"post": "Days 0 to +60", "short": "Days -1 to +1"}.get)
    summary = t["event_summary"]
    summary = summary[summary["window"] == window].copy()
    summary["group"] = [g if b == "all" else f"{b.replace('_', ' ')}: {g}"
                        for b, g in zip(summary["breakdown"], summary["group"])]
    table = summary[["group", "events", "mean_car", "t_stat", "mean_abs_car",
                     "median_abs_car", "share_significant"]]
    styled = signed_table(
        table, percent_cols=["mean_car"], number_cols=["t_stat"],
        other_formats={"t_stat": "{:+.2f}", "mean_abs_car": "{:.1%}",
                       "median_abs_car": "{:.1%}", "share_significant": "{:.1%}"})
    st.dataframe(
        styled, hide_index=True, width="stretch",
        column_config={"group": "GROUP", "events": "N", "mean_car": "MEAN CAR",
                       "t_stat": "T-STAT", "mean_abs_car": "MEAN |CAR|",
                       "median_abs_car": "MEDIAN |CAR|",
                       "share_significant": "UNUSUAL MOVES (5% BY CHANCE)"})
    warn("<b>Survivorship bias:</b> only companies still listed today are included, so "
         "small companies whose trials failed and that later disappeared are missing. "
         "That is why small-company moves look mostly positive.")
    with st.expander("All events, including excluded ones and why"):
        st.dataframe(t["events"], hide_index=True, width="stretch")


# --- tab 4: date slippage -------------------------------------------------

def slippage_tab(t, meta):
    section("Date slippage · do sponsors keep pushing their dates back?")
    snapshots = int(meta.get("snapshots", 0))
    if snapshots < 2:
        note(f"History is still building: only one weekly snapshot so far "
             f"({meta.get('first_snapshot')}). Moved dates appear after the next weekly run.")
    else:
        changes = t["slippage_changes"]
        latest = changes[changes["snapshot_date"] == changes["snapshot_date"].max()]
        section(f"Changes in the latest snapshot ({len(latest)})")
        st.dataframe(latest, hide_index=True, width="stretch")

    sponsors = t["slippage_sponsors"]
    traded_only = st.checkbox("Only companies with a US ticker", value=True)
    if traded_only:
        sponsors = sponsors[sponsors["ticker"].notna()]
    section("Sponsor reliability")
    st.dataframe(
        sponsors[["company", "open_trials", "overdue_now", "comparable_trials",
                  "share_delayed", "median_delay_days", "sponsor_names"]],
        hide_index=True, width="stretch",
        column_config={
            "company": "COMPANY", "sponsor_names": "SPONSOR NAMES",
            "open_trials": "OPEN", "overdue_now": "OVERDUE",
            "comparable_trials": "WITH DATE HISTORY",
            "share_delayed": st.column_config.NumberColumn("SHARE DELAYED", format="percent"),
            "median_delay_days": st.column_config.NumberColumn("MEDIAN DELAY (D)",
                                                                format="%.0f")})
    note("Overdue = open trial whose estimated completion date already passed. "
         "Share delayed and median delay appear once a company has 3+ trials with "
         "date history.")


# --- tab 5: method --------------------------------------------------------

def method_tab(meta):
    section("Method and limitations")
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

def status_bar(t, meta):
    """One dense line across the top, like a terminal status bar."""
    calendar = t["calendar"]
    upcoming = int((calendar["section"] == "upcoming").sum())
    runway = t.get("cash_runway")
    cash_flags = int(runway["flagged"].astype(bool).sum()) if runway is not None and len(runway) else 0
    items = [("DATA", meta.get("data_as_of")),
             ("TRIALS", f"{len(t['company_trials']):,} W/ TICKER"),
             ("TICKERS", f"{t['companies']['ticker'].nunique()}"),
             ("CATALYSTS 12M", upcoming),
             ("EVENTS", meta.get("events_measured")),
             ("CASH FLAGS", cash_flags)]
    html = f'<span class="name">{APP_NAME}</span>' + "".join(
        f'<span class="item">{k} <b>{v}</b></span>' for k, v in items)
    html += '<span class="risk">NOT INVESTMENT ADVICE</span>'
    st.markdown(f'<div class="tl-bar">{html}</div>', unsafe_allow_html=True)


def main():
    st.set_page_config(page_title=APP_NAME, layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)
    if not DB_FILE.exists():
        st.error(f"No dashboard data found at {DB_FILE}. Run `python -m src.export` first.")
        st.stop()
    t = load_tables(str(DB_FILE), DB_FILE.stat().st_mtime)
    meta = dict(zip(t["meta"]["key"], t["meta"]["value"]))

    status_bar(t, meta)
    tabs = st.tabs(["1 CAL  CALENDAR", "2 CO  COMPANY", "3 EVT  EVENT STUDY",
                    "4 SLIP  SLIPPAGE", "5 METH  METHOD"])
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
    st.markdown(f'<div class="tl-note" style="border-top:1px solid #333;margin-top:16px;'
                f'padding-top:4px">{DISCLAIMER}</div>', unsafe_allow_html=True)


main()
