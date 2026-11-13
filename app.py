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
MONO = "'IBM Plex Mono', Consolas, monospace"   # numbers, status bar, charts
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
.tl-bar {{ border-bottom: 1px solid #333; padding: 2px 0 6px 0; margin-bottom: 2px;
          white-space: nowrap; overflow-x: auto; font-size: 0.85rem; font-family: {MONO}; }}
.tl-hint {{ color: {GRAY}; font-size: 0.78rem; margin-bottom: 6px; }}
.tl-hint b {{ color: {HILITE}; font-weight: 500; }}
.tl-bar .name {{ color: #000; background: {HILITE}; font-weight: 700; padding: 1px 6px; }}
.tl-bar .item {{ color: {GRAY}; margin-left: 18px; }}
.tl-bar .item b {{ color: #e0e0e0; font-weight: 600; }}
.tl-bar .risk {{ color: {RED}; margin-left: 18px; }}
.tl-h {{ color: {HILITE}; text-transform: uppercase; letter-spacing: 0.06em;
         font-size: 0.78rem; font-weight: 600; border-bottom: 1px solid #333;
         margin: 18px 0 8px 0; padding-bottom: 3px; }}
.tl-desc {{ color: #b5b5b5; font-size: 0.85rem; margin: -2px 0 8px 0; }}
.tl-note {{ color: {GRAY}; font-size: 0.8rem; }}
.tl-warn {{ color: {HILITE}; font-size: 0.8rem; }}
.tl-warn b, .tl-note b {{ color: #e0e0e0; }}
[data-testid="stMetricLabel"] p {{ text-transform: uppercase; font-size: 0.7rem;
                                   letter-spacing: 0.04em; color: {GRAY}; }}
[data-testid="stMetricValue"] {{ font-family: {MONO}; }}
[data-testid="stExpander"] summary p {{ font-weight: 500; }}
.tl-help p, .tl-help li {{ line-height: 1.55; }}
button[data-baseweb="tab"] p {{ font-size: 0.8rem; letter-spacing: 0.04em; }}
</style>
"""


# --- small display helpers ------------------------------------------------

def section(label):
    """A small uppercase amber label with a thin line (instead of big headings)."""
    st.markdown(f'<div class="tl-h">{label}</div>', unsafe_allow_html=True)


def describe(text):
    """One plain line under a section label saying what the chart or table shows."""
    st.markdown(f'<div class="tl-desc">{text}</div>', unsafe_allow_html=True)


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
        font={"family": MONO, "size": 11, "color": "#e0e0e0"},
        legend={"orientation": "h", "y": 1.02, "yanchor": "bottom", "x": 0, "title": None,
                "font": {"color": "#e0e0e0"}},
        hoverlabel={"bgcolor": "#000", "bordercolor": HILITE,
                    "font": {"family": MONO, "color": "#e0e0e0"}},
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

CAL_SECTIONS = {"Upcoming (next 12 months)": "upcoming",
                "Date already passed (last 12 months)": "past date"}
CAL_DEFAULTS = {"cal_section": list(CAL_SECTIONS)[0], "cal_diseases": list(DISEASES),
                "cal_phases": list(PHASE_COLORS), "cal_sizes": SIZE_ORDER,
                "cal_min_score": 0}


def reset_calendar_filters():
    """Put every calendar filter back to 'show everything'."""
    for key, value in CAL_DEFAULTS.items():
        st.session_state[key] = list(value) if isinstance(value, list) else value


def calendar_tab(t):
    section("Catalyst calendar · open Phase 2/3 trials of US-listed companies")
    note("Impact score 0-100 = 50% company size + 30% concentration + 20% phase. "
         "Higher = likely bigger stock move.")
    for key, value in CAL_DEFAULTS.items():
        st.session_state.setdefault(key, list(value) if isinstance(value, list) else value)
    cal = t["calendar"].copy()
    cal["size_group"] = cal["market_cap"].map(size_group)
    cal["diseases"] = cal["matched_searches"].map(disease_labels)

    with st.container(border=True):
        st.markdown('<div class="tl-h" style="margin-top:0">Filters</div>',
                    unsafe_allow_html=True)
        section_choice = st.radio(
            "Show", list(CAL_SECTIONS), horizontal=True, key="cal_section",
            help="Upcoming = expected to finish in the next 12 months. Date already passed "
                 "= expected date was in the last 12 months; results may be out or due.")
        c1, c2, c3, c4 = st.columns(4)
        diseases = c1.multiselect("Disease", list(DISEASES), key="cal_diseases",
                                  format_func=DISEASES.get,
                                  help="A trial can count for more than one disease.")
        phases = c2.multiselect("Phase", list(PHASE_COLORS), key="cal_phases",
                                help="Phase 3 results usually decide whether a drug is "
                                     "approved; Phase 2 shows whether it works at all.")
        sizes = c3.multiselect("Company size", SIZE_ORDER, key="cal_sizes",
                               help="By today's market cap. Small companies usually move "
                                    "much more on one trial result.")
        min_score = c4.slider("Minimum impact score", 0, 100, key="cal_min_score",
                              help="0-100. Higher = the result is likely to move the "
                                   "stock more. Trials without a score show only at 0.")
        st.button("Reset filters", on_click=reset_calendar_filters)
    cal = cal[cal["section"] == CAL_SECTIONS[section_choice]]

    keep = (cal["matched_searches"].fillna("").map(
                lambda m: any(d in m.split("; ") for d in diseases))
            & cal["phase"].isin(phases)
            & (cal["size_group"].isin(sizes) | cal["size_group"].isna())
            & ((cal["impact_score"] >= min_score)
               | (cal["impact_score"].isna() & (min_score == 0))))
    cal = cal[keep].sort_values("impact_score", ascending=False)
    section(f"{len(cal)} trials · timeline")
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
    describe("Each square is one trial: left to right = expected completion date, "
             "higher = bigger likely stock move, bigger square = smaller company. "
             "Hover for details.")
    st.plotly_chart(terminal_chart(fig), width="stretch")

    section("Trials ranked by impact score")
    describe("Click a column name to sort. 'open' goes to the trial's page on "
             "ClinicalTrials.gov.")

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
            "impact_score": st.column_config.NumberColumn(
                "SCORE", format="%.1f", help="Impact score 0-100: 50% company size, "
                "30% concentration, 20% phase."),
            "phase": "PHASE",
            "flags": st.column_config.Column(
                "WARNINGS", help="Red flags that don't change the score. See the legend "
                "below the table."),
            "lead_sponsor": "SPONSOR",
            "diseases": "DISEASE", "interventions": "DRUGS",
            "market_cap_b": st.column_config.NumberColumn("MKT CAP $B", format="%.2f"),
            "open_trials_all_diseases": st.column_config.Column(
                "OPEN TRIALS (ALL)", help="The company's open Phase 2/3 trials in any "
                "disease. Fewer = each trial matters more."),
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
    with st.container(border=True):
        m1, m2, m3, m4 = st.columns(4)
        cap = companies.loc[ticker, "market_cap"] if ticker in companies.index else None
        m1.metric("Market cap", money(cap),
                  help="Total value of all the company's shares today (Yahoo Finance).")
        m2.metric("Open trials (our diseases)",
                  int(trials["overall_status"].isin(OPEN_STATUSES).sum()),
                  help="Open Phase 2/3 trials in obesity and diabetes.")
        m3.metric("Catalysts next 12M", int((upcoming["ticker"] == ticker).sum()),
                  help="Trials expected to finish in the next 12 months.")
        m4.metric("Overdue trials",
                  int(sponsor.loc[ticker, "overdue_now"]) if ticker in sponsor.index else 0,
                  help="Open trials whose expected date passed without an update.")

    # Price chart: stock vs XBI, both starting at 100.
    end = pd.Timestamp(prices["date"].max())
    start = (end - pd.DateOffset(years=years)).date().isoformat()
    window = prices[(prices["date"] >= start) & prices["ticker"].isin([ticker, "XBI"])]
    wide = window.pivot(index="date", columns="ticker", values="adj_close").dropna()
    section(f"{ticker} vs XBI · value of $100 invested")
    describe(f"If {ticker} (amber) pulls away from XBI (gray, biotech overall), the stock "
             "moved on its own news rather than with the sector.")
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
    with st.container(border=True):
        r1, r2, r3, r4 = st.columns(4)
        r1.metric("Cash", money(first["cash"]),
                  help="Cash, cash equivalents and short-term investments on the "
                       "balance-sheet date.")
        r2.metric("Burn / month (4Q avg)", money(first["monthly_burn"]),
                  help="Average cash spent running the company per month, over the "
                       "last 4 quarters.")
        r3.metric("Runway", f"{first['runway_months']:.1f} mo"
                  if pd.notna(first["runway_months"]) else "n/a",
                  help="Cash / monthly burn: months until the money runs out.")
        r4.metric("Cash out ~", first["runway_end"] or "n/a",
                  help="Balance-sheet date + runway.")
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
    with st.container(border=True):
        m1, m2, m3 = st.columns(3)
        m1.metric("Events measured", meta.get("events_measured"),
                  help="Past trial completions with clean price data.")
        m2.metric("Events excluded", meta.get("events_excluded"),
                  help="Dropped for a stated reason (too little price history, thin "
                       "trading, ...). See 'All events' below.")
        m3.metric("Score vs |move| (rank corr, 60D)", meta.get("score_backtest_post"),
                  help="Did higher impact scores go with bigger moves? +1 = always, "
                       "0 = no link. Positive = the score works.")

    paths = t["car_paths"]
    stat = st.radio("Average", ["median", "mean"], horizontal=True,
                    help="The mean is pulled up by a few huge winners; the median is the typical event.")
    shown = paths[paths["size_group"].isin(SIZE_ORDER)].copy()
    shown["CAR"] = shown[f"{stat}_car"]
    section(f"{stat} CAR by trading day · by company size at the time")
    describe("How far the stock moved beyond what XBI predicted, added up day by day, "
             "from 10 trading days before a trial finished to 60 after.")
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


# --- tab 6: how to use ----------------------------------------------------

def help_tab(t, meta):
    upcoming = t["calendar"][t["calendar"]["section"] == "upcoming"]
    top = upcoming.dropna(subset=["impact_score"]).sort_values("impact_score",
                                                                ascending=False)
    example = top.iloc[0] if len(top) else None

    section("How to use TOPLINE")
    st.markdown(
        "TOPLINE lists upcoming clinical-trial results for obesity and diabetes drugs, "
        "estimates how much each one could move the company's stock, and checks those "
        "estimates against how stocks actually moved around past trials. It helps you "
        "decide **what to research**, not what to buy.")

    section("Quick start")
    st.markdown(
        "1. **1 CAL** - set *Minimum impact score* to about 70 to see the trials most "
        "likely to move a stock.\n"
        "2. **2 CO** - pick one of those tickers. Check the price chart, the cash runway "
        "and the company's other trials.\n"
        "3. **3 EVT** - see how similar companies moved around past trial completions.\n"
        "4. **4 SLIP** - check whether the company usually hits its dates.")

    section("The tabs in detail")
    with st.expander("1 CAL - Calendar: what's coming up"):
        st.markdown(
            "- **Show** switches between trials expected in the next 12 months and trials "
            "whose expected date passed in the last 12 months (their results may be out, "
            "or overdue).\n"
            "- **Filters** narrow the list by disease, phase, company size and minimum "
            "score. *Reset filters* shows everything again.\n"
            "- **Timeline chart** - each square is one trial. Left to right is the "
            "expected completion date; higher means a bigger likely stock move; a bigger "
            "square means a smaller company. Amber = Phase 2, blue = Phase 3, "
            "magenta = Phase 2/3.\n"
            "- **Table** - the same trials sorted by score. *WARNINGS* lists red flags "
            "(the legend under the table explains each one). *open* goes to the trial on "
            "ClinicalTrials.gov, where you can read the drug, the patients and what is "
            "being measured.")
    with st.expander("2 CO - Company: research one stock"):
        st.markdown(
            "- **Ticker** picks the company; **Period** sets 1, 3 or 5 years of history.\n"
            "- **Key numbers** - market cap, open trials, catalysts in the next 12 months "
            "and overdue trials.\n"
            "- **Price chart** - what $100 in the stock (amber) would be worth against $100 "
            "in XBI (gray, an ETF of biotech stocks). When the lines split, the company "
            "moved on its own news. Dotted gray lines = past trial completions; dashed "
            "blue lines = upcoming ones.\n"
            "- **Cash runway** (companies under $1B) - cash, monthly spending, months of "
            "cash left and the date it runs out. *Cushion* is runway end minus the trial "
            "date: a red negative number means the money runs out before the result.\n"
            "- **Trials** and **Past events** - every obesity/diabetes trial, and how the "
            "stock moved after earlier completions (green = up, red = down).")
    with st.expander("3 EVT - Event study: does history back the score?"):
        st.markdown(
            f"- Covers **{meta.get('events_measured')} past trial completions**. Day 0 is "
            "the date a trial finished collecting its main data.\n"
            "- **Chart** - the typical move beyond what XBI predicted, added up day by day, "
            "split by company size at the time. *Median* = the typical event; *mean* = the "
            "average, which a few huge winners pull up.\n"
            "- **Table** - the same numbers by group. Switch *Window* between days 0 to +60 "
            "and days -1 to +1.\n"
            "- **Score vs |move|** - a positive number means trials with higher scores "
            "really did have bigger moves.")
    with st.expander("4 SLIP - Slippage: do companies keep delaying?"):
        st.markdown(
            "- Every weekly update saves each trial's dates, so moved dates show up after "
            "the second update.\n"
            "- **Sponsor reliability** - open trials, overdue trials (expected date passed "
            "with no update), and, once a company has 3+ trials with history, how often "
            "and by how many days it delays.\n"
            "- A company that often slips makes its 'upcoming' dates less reliable.")
    with st.expander("5 METH - Method: how it works"):
        st.markdown("Where the data comes from, how every number is calculated, and the "
                    "known limitations. Useful before explaining the project to someone.")

    section("How to read the numbers")
    with st.expander("Glossary"):
        st.markdown(
            "- **Impact score (0-100)** - 50% company size (smaller = higher), 30% "
            "concentration (fewer open trials = higher), 20% phase (Phase 3 = higher).\n"
            "- **Primary completion date** - when the trial finishes collecting the data "
            "for its main question. Headline ('top-line') results usually follow within "
            "weeks to a few months.\n"
            "- **XBI** - an ETF holding many US biotech stocks; used as 'the market' for "
            "biotech.\n"
            "- **Abnormal return** - the stock's return minus what XBI predicted for it "
            "that day.\n"
            "- **CAR** - cumulative abnormal return: abnormal returns added up over a "
            "window. **|CAR|** is its size, ignoring direction.\n"
            "- **t-stat** - how sure we can be the average isn't zero by chance; above "
            "about 2 (either sign) is usually taken as meaningful.\n"
            "- **Unusual moves** - share of events whose move was bigger than 95% of normal "
            "days; about 5% would happen by chance alone.\n"
            "- **Runway** - months until the cash runs out at the recent spending rate. "
            "**Cushion** - runway end minus the trial date, in months.\n"
            "- **Overdue** - an open trial whose expected date passed without an update.")

    if example is not None:
        section("Worked example")
        cap = money(example["market_cap"])
        flags = example["flags"] if isinstance(example["flags"], str) else "none"
        st.markdown(
            f"1. In **1 CAL**, the highest-scoring upcoming trial is **{example['ticker']}** "
            f"({example['lead_sponsor']}), score **{example['impact_score']:.1f}**, "
            f"{example['phase']}, expected **{example['primary_completion_date']}**.\n"
            f"2. Why so high: market cap {cap} ({size_group(example['market_cap']) or 'size unknown'}) and "
            f"{int(example['open_trials_all_diseases'])} open trial(s) in all diseases, "
            "so this result matters a lot to the company. Warnings: "
            f"*{flags}*.\n"
            f"3. In **2 CO**, pick {example['ticker']}: check the price against XBI, and "
            "whether the cash lasts until the result.\n"
            "4. In **3 EVT**, the 'small' line shows how companies of this size typically "
            "moved around past trial completions.\n"
            "5. Next step outside TOPLINE: read the company's latest SEC filing and press "
            "releases for anything newer than this data.")

    section("What TOPLINE can't tell you")
    st.markdown(
        "- Whether a trial will succeed, or which way the stock will move.\n"
        "- The exact announcement date - expected dates often slip.\n"
        "- Money raised after the latest quarterly filing (cash data can be months old).\n"
        "- Companies that no longer exist: they're missing from the history "
        "(survivorship bias), so past small-company moves look better than reality.")

    section("When the data updates")
    st.markdown(
        f"Weekly. The current data is from **{meta.get('data_as_of')}** (shown in the "
        "status bar). Prices and cash figures come from Yahoo Finance; trials from "
        "ClinicalTrials.gov.")


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
    st.markdown('<div class="tl-hint">New here? Start with <b>6 HELP</b> for a guide '
                'to every tab.</div>', unsafe_allow_html=True)


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
                    "4 SLIP  SLIPPAGE", "5 METH  METHOD", "6 HELP  HOW TO USE"])
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
    with tabs[5]:
        help_tab(t, meta)
    st.markdown(f'<div class="tl-note" style="border-top:1px solid #333;margin-top:16px;'
                f'padding-top:4px">{DISCLAIMER}</div>', unsafe_allow_html=True)


main()
