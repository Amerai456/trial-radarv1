"""Stage 5 - Analyze.
Part 1: catalyst calendar and impact score.
Part 2: event study (how sponsor stocks moved around past trial completions).
Part 3: date slippage (how often sponsors push their expected dates back).

The catalyst calendar lists upcoming trial readouts that investors can
trade: open trials whose sponsor has a US stock ticker, with an expected
primary completion date in the next 12 months, plus a separate section for
trials whose date passed within the last 12 months (older ones have almost
certainly reported results already).

The impact score (0-100) estimates how much a readout is likely to move the
sponsor's stock. Higher = likely bigger move. It adds up three parts:

  company size   50%  smaller companies move more on the same news
  concentration  30%  1 / the company's number of open trials (all diseases)
  phase          20%  Phase 3 results decide whether a drug gets approved

Warning flags (overdue, month-only date, thinly traded, data collected) are
shown next to the score but do not change it.
(Decisions logged in notes/decisions.md, 2026-11-11.)

Run it from the project folder with:
    python -m src.analytics           (catalyst calendar)
    python -m src.analytics events    (event study)
    python -m src.analytics slippage  (date slippage)
"""

import json
import math
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from src.enrich import BENCHMARK
from src.fetch import PAUSE_SECONDS, get_json_with_retries
from src.store import DB_PATH, connect

OPEN_STATUSES = ["RECRUITING", "ACTIVE_NOT_RECRUITING", "NOT_YET_RECRUITING",
                 "ENROLLING_BY_INVITATION"]
WINDOW_MONTHS = 12        # how far ahead the calendar looks
PAST_WINDOW_MONTHS = 12   # how far back the "past date" section looks

# Score weights (must add up to 1).
WEIGHT_SIZE = 0.50
WEIGHT_CONCENTRATION = 0.30
WEIGHT_PHASE = 0.20

# Company size scale (log scale): at or below SMALL_CAP scores 1.0,
# at or above LARGE_CAP scores 0.0, proportionally in between.
SMALL_CAP = 50e6     # $50 million
LARGE_CAP = 500e9    # $500 billion

PHASE_SCORES = {"PHASE3": 1.0, "PHASE2/PHASE3": 0.8, "PHASE2": 0.5}

THIN_TRADING_SHARE = 0.05   # flag if no trading on more than 5% of recent days

REFERENCE_DATA_DIR = Path("data") / "reference"
OUTPUT_DIR = Path("data") / "clean"


# --- score parts ----------------------------------------------------------

def size_part(market_cap):
    """0-1, smaller company -> higher. None if the market cap is unknown.

    A log scale means $100M -> $1B counts the same as $1B -> $10B
    (each is "10 times bigger").
    """
    if market_cap is None or pd.isna(market_cap) or market_cap <= 0:
        return None
    position = (math.log10(LARGE_CAP) - math.log10(market_cap)) / \
               (math.log10(LARGE_CAP) - math.log10(SMALL_CAP))
    return round(min(max(position, 0.0), 1.0), 4)


def concentration_part(open_trials):
    """0-1: 1 / number of open trials. A company's only trial scores 1.0."""
    if not open_trials or open_trials < 1:
        return 1.0
    return round(1 / open_trials, 4)


def phase_part(phase):
    return PHASE_SCORES.get(phase, 0.0)


def impact_score(size, concentration, phase):
    """Weighted total on a 0-100 scale. None if company size is unknown,
    because guessing the biggest part would make the score misleading."""
    if size is None:
        return None
    total = WEIGHT_SIZE * size + WEIGHT_CONCENTRATION * concentration + WEIGHT_PHASE * phase
    return round(100 * total, 1)


# --- concentration: open trials across ALL diseases -------------------------

def count_open_trials_all_diseases(sponsor_names):
    """Ask ClinicalTrials.gov how many open Phase 2/3 trials these sponsor
    names lead, in any disease. Several names can belong to one ticker
    (e.g. "Novartis" and "Novartis Pharmaceuticals"), so they are searched
    together and each trial is counted once."""
    names = " OR ".join('"' + n.replace('"', "") + '"' for n in sponsor_names)
    params = {
        "filter.advanced": (
            f"AREA[LeadSponsorName]({names}) "
            f"AND AREA[OverallStatus]({' OR '.join(OPEN_STATUSES)}) "
            "AND AREA[Phase](PHASE2 OR PHASE3) AND NOT AREA[Phase]PHASE1"
        ),
        "countTotal": "true",
        "pageSize": 1,
    }
    return get_json_with_retries(params).get("totalCount")


def load_open_trial_counts(sponsors_by_ticker, as_of, lookup=count_open_trials_all_diseases,
                           cache_dir=REFERENCE_DATA_DIR):
    """{ticker: open trials in all diseases}, saved to a file per day so
    running the calendar again on the same day doesn't repeat ~35 requests.
    A ticker whose lookup fails gets None (the caller falls back)."""
    cache_dir = Path(cache_dir)
    cache_file = cache_dir / f"open_trial_counts_{as_of}.json"
    if cache_file.exists():
        cached = json.loads(cache_file.read_text(encoding="utf-8"))
        if set(sponsors_by_ticker) <= set(cached):
            return cached

    counts = {}
    print(f"  counting open trials in all diseases for {len(sponsors_by_ticker)} companies...")
    for ticker, names in sorted(sponsors_by_ticker.items()):
        try:
            counts[ticker] = lookup(names)
        except Exception as error:   # one failed company shouldn't stop the calendar
            print(f"  warning: count failed for {ticker} ({error}); using our own data")
            counts[ticker] = None
        time.sleep(PAUSE_SECONDS)

    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(counts, indent=1), encoding="utf-8")
    return counts


# --- the calendar ---------------------------------------------------------

CALENDAR_QUERY = f"""
SELECT t.nct_id, s.ticker, t.lead_sponsor, t.title, t.interventions, t.phase,
       t.matched_searches, t.enrollment, t.overall_status,
       t.primary_completion_date, t.primary_completion_date_type,
       t.primary_completion_date_precision,
       i.market_cap, i.exchange, i.zero_volume_share_2y
FROM trials t
JOIN sponsor_tickers s ON s.sponsor_name = t.lead_sponsor
LEFT JOIN ticker_info i ON i.ticker = s.ticker
WHERE s.ticker IS NOT NULL
  AND t.primary_completion_date IS NOT NULL
  AND t.overall_status IN ({", ".join("'" + s + "'" for s in OPEN_STATUSES)})
"""


def make_flags(row, as_of):
    """Warning labels shown next to the score (they don't change it)."""
    flags = []
    past = row["primary_completion_date"] < as_of
    if past and row["primary_completion_date_type"] == "ESTIMATED":
        flags.append("overdue")          # date passed, sponsor never updated it
    if past and row["primary_completion_date_type"] == "ACTUAL":
        flags.append("data collected")   # results could be announced any time
    if row["primary_completion_date_precision"] == "month":
        flags.append("month-only date")
    share = row["zero_volume_share_2y"]
    if share is not None and pd.notna(share) and share > THIN_TRADING_SHARE:
        flags.append("thinly traded")
    if row["market_cap"] is None or pd.isna(row["market_cap"]):
        flags.append("no market cap")
    return "; ".join(flags) or None


def build_catalyst_calendar(as_of=None, db_path=DB_PATH, lookup=count_open_trials_all_diseases,
                            cache_dir=REFERENCE_DATA_DIR):
    """Return the catalyst calendar as a table, one row per trial.

    section = "upcoming" (date within the next 12 months) or
              "past date" (date passed within the last 12 months; check the flags).
    """
    as_of = as_of or date.today().isoformat()
    window_end = (pd.Timestamp(as_of) + pd.DateOffset(months=WINDOW_MONTHS)).date().isoformat()
    window_start = (pd.Timestamp(as_of) - pd.DateOffset(months=PAST_WINDOW_MONTHS)).date().isoformat()

    conn = connect(db_path)
    trials = pd.read_sql(CALENDAR_QUERY, conn)
    sponsor_rows = conn.execute(
        "SELECT ticker, sponsor_name FROM sponsor_tickers WHERE ticker IS NOT NULL").fetchall()
    conn.close()
    if trials.empty:
        return trials

    # Concentration: open trials across all diseases, never fewer than the
    # open trials we can see in our own (obesity + diabetes) data.
    our_counts = trials.groupby("ticker")["nct_id"].count().to_dict()
    sponsors_by_ticker = {}
    for ticker, sponsor in sponsor_rows:
        if ticker in our_counts:
            sponsors_by_ticker.setdefault(ticker, []).append(sponsor)
    all_counts = load_open_trial_counts(sponsors_by_ticker, as_of, lookup=lookup,
                                        cache_dir=cache_dir)
    trials["open_trials_all_diseases"] = trials["ticker"].map(
        lambda t: max(all_counts.get(t) or 0, our_counts[t]))

    # Keep dates from 12 months ago to 12 months ahead.
    in_window = trials["primary_completion_date"].between(window_start, window_end)
    trials = trials[in_window].copy()
    trials["section"] = trials["primary_completion_date"].map(
        lambda d: "upcoming" if d >= as_of else "past date")

    trials["size_part"] = trials["market_cap"].map(size_part)
    trials["concentration_part"] = trials["open_trials_all_diseases"].map(concentration_part)
    trials["phase_part"] = trials["phase"].map(phase_part)
    trials["impact_score"] = [
        impact_score(s, c, p) for s, c, p in
        zip(trials["size_part"], trials["concentration_part"], trials["phase_part"])
    ]
    trials["flags"] = trials.apply(lambda row: make_flags(row, as_of), axis=1)

    columns = [
        "section", "primary_completion_date", "primary_completion_date_type",
        "primary_completion_date_precision", "ticker", "lead_sponsor", "impact_score",
        "flags", "phase", "interventions", "matched_searches", "enrollment",
        "overall_status", "market_cap", "open_trials_all_diseases",
        "size_part", "concentration_part", "phase_part", "nct_id", "title",
    ]
    trials = trials[columns].sort_values(["section", "primary_completion_date"],
                                         ascending=[False, True])
    return trials.reset_index(drop=True)


# ===========================================================================
# Part 2: event study - how did sponsor stocks move around past milestones?
# ===========================================================================
#
# Day 0 = a trial's ACTUAL primary completion date (exact day only). Top-line
# results usually come out within ~3 months, so we measure two windows:
#   short  trading days -1 to +1   (right around the date)
#   post   trading days  0 to +60  (the period results usually come out in)
#
# Abnormal return (AR) = how much the stock moved beyond what XBI predicted.
# The prediction comes from the "market model": over trading days -250 to -30
# we measure how the stock normally moves with XBI (alpha and beta), then
#   AR = stock return - (alpha + beta x XBI return)
# CAR (cumulative abnormal return) = the ARs added up over a window.
# (Decisions logged in notes/decisions.md, 2026-11-12.)

EVENT_STATUSES = ["COMPLETED", "TERMINATED", "ACTIVE_NOT_RECRUITING"]
ESTIMATION_WINDOW = (-250, -30)   # trading days used to learn alpha and beta
MIN_ESTIMATION_DAYS = 120         # fewer usable days -> beta too unreliable
EVENT_WINDOWS = {"short": (-1, 1), "post": (0, 60)}
MAX_ZERO_VOLUME_SHARE = 0.10      # exclude if no trading on >10% of days
SIGNIFICANT_Z = 1.96              # |standardized CAR| above this = "significant"
SIZE_GROUPS = [(2e9, "small (<$2B)"), (20e9, "mid ($2-20B)"), (math.inf, "large (>$20B)")]

EVENT_QUERY = """
SELECT t.nct_id, s.ticker, t.phase, t.overall_status,
       t.primary_completion_date AS event_date
FROM trials t
JOIN sponsor_tickers s ON s.sponsor_name = t.lead_sponsor
WHERE s.ticker IS NOT NULL
  AND t.primary_completion_date_type = 'ACTUAL'
  AND t.primary_completion_date_precision = 'day'
  AND t.primary_completion_date < ?
"""

RUNNING_TRIALS_QUERY = """
SELECT s.ticker, t.start_date, t.primary_completion_date
FROM trials t
JOIN sponsor_tickers s ON s.sponsor_name = t.lead_sponsor
WHERE s.ticker IS NOT NULL AND t.overall_status != 'WITHDRAWN'
  AND t.start_date IS NOT NULL AND t.primary_completion_date IS NOT NULL
"""


def combine_same_day(trials):
    """One event per ticker per date. Two trials of one company finishing on
    the same day are one stock-market event, not two.

    Returns (events, excluded): trials with a status we don't study
    (withdrawn, suspended, ...) are returned separately with a reason."""
    usable = trials["overall_status"].isin(EVENT_STATUSES)
    excluded = trials[~usable].copy()
    excluded["exclude_reason"] = "status " + excluded["overall_status"]

    rows = []
    for (ticker, event_date), group in trials[usable].groupby(["ticker", "event_date"]):
        # Use the most important phase among the trials (Phase 3 > Phase 2).
        phase = max(group["phase"], key=phase_part)
        all_terminated = (group["overall_status"] == "TERMINATED").all()
        rows.append({
            "ticker": ticker, "event_date": event_date, "phase": phase,
            "status_group": "terminated" if all_terminated else "completed or active",
            "n_trials": len(group), "nct_ids": "; ".join(sorted(group["nct_id"])),
        })
    events = pd.DataFrame(rows, columns=["ticker", "event_date", "phase", "status_group",
                                         "n_trials", "nct_ids"])
    excluded = excluded.rename(columns={"nct_id": "nct_ids"})
    excluded["n_trials"] = 1
    excluded["status_group"] = None
    return events, excluded[["ticker", "event_date", "phase", "status_group",
                             "n_trials", "nct_ids", "exclude_reason"]]


def load_price_tables(tickers, db_path=DB_PATH):
    """Adjusted prices and volumes as wide tables (one column per ticker),
    on XBI's trading days. Daily returns = % change of adjusted price; a day
    right after a missing price gets no return rather than a 2-day return."""
    conn = connect(db_path)
    wanted = sorted(set(tickers) | {BENCHMARK})
    prices = pd.read_sql(
        f"SELECT ticker, date, adj_close, volume FROM prices "
        f"WHERE ticker IN ({', '.join('?' * len(wanted))})", conn, params=wanted)
    conn.close()
    adj = prices.pivot(index="date", columns="ticker", values="adj_close").sort_index()
    if BENCHMARK not in adj.columns:
        raise SystemExit(f"No {BENCHMARK} prices in the database. Run: python -m src.enrich")
    adj = adj[adj[BENCHMARK].notna()]
    volume = prices.pivot(index="date", columns="ticker", values="volume").reindex(adj.index)
    returns = adj.pct_change(fill_method=None)
    return adj, returns, volume


def day0_position(trading_days, event_date):
    """Position of day 0: the event date, or the next trading day if the
    event fell on a weekend or holiday."""
    return int(np.searchsorted(trading_days, event_date))


def market_model(stock, market):
    """Fit stock = alpha + beta x market by least squares.
    Returns alpha, beta and sigma (the typical size of a day's leftover
    wiggle, used to judge whether a CAR is unusually big)."""
    beta = np.cov(stock, market)[0, 1] / np.var(market, ddof=1)
    alpha = stock.mean() - beta * market.mean()
    residuals = stock - (alpha + beta * market)
    sigma = residuals.std(ddof=2)
    return alpha, beta, sigma


def measure_event(returns, volume, ticker, event_date):
    """Measure one event. Returns a dict of results, or a dict with only
    'exclude_reason' if the event can't be measured fairly."""
    if ticker not in returns.columns:
        return {"exclude_reason": "no price data"}
    days = returns.index.to_numpy()
    day0 = day0_position(days, event_date)
    last_day = day0 + max(end for _, end in EVENT_WINDOWS.values())
    if last_day >= len(days):
        return {"exclude_reason": "event window not finished yet"}

    stock = returns[ticker].to_numpy()
    market = returns[BENCHMARK].to_numpy()

    est = slice(max(day0 + ESTIMATION_WINDOW[0], 0), day0 + ESTIMATION_WINDOW[1] + 1)
    usable = ~np.isnan(stock[est]) & ~np.isnan(market[est])
    if usable.sum() < MIN_ESTIMATION_DAYS:
        return {"exclude_reason": "too little price history before event"}
    alpha, beta, sigma = market_model(stock[est][usable], market[est][usable])

    # Thin trading check over the estimation and event windows together.
    vol = volume[ticker].to_numpy()[est.start:last_day + 1]
    vol = vol[~np.isnan(vol)]
    zero_share = float((vol == 0).mean()) if len(vol) else 1.0
    if zero_share > MAX_ZERO_VOLUME_SHARE:
        return {"exclude_reason": f"thinly traded ({zero_share:.0%} zero-volume days)"}

    result = {"day0_date": days[day0], "estimation_days": int(usable.sum()),
              "alpha": alpha, "beta": beta, "sigma": sigma,
              "zero_volume_share": round(zero_share, 4)}
    for name, (start, end) in EVENT_WINDOWS.items():
        window = slice(day0 + start, day0 + end + 1)
        if np.isnan(stock[window]).any():
            return {"exclude_reason": "missing prices in event window"}
        abnormal = stock[window] - (alpha + beta * market[window])
        car = abnormal.sum()
        result[f"car_{name}"] = car
        # Standardized CAR: the CAR divided by how big it would typically be
        # on ordinary days. Above 1.96 (either sign) happens only ~5% of the
        # time by chance.
        result[f"scar_{name}"] = car / (sigma * math.sqrt(end - start + 1))
    return result


def size_group(market_cap):
    if market_cap is None or pd.isna(market_cap):
        return None
    return next(label for limit, label in SIZE_GROUPS if market_cap < limit)


def running_trial_counts(events, running):
    """For each event, how many of the company's trials (in our data) were
    running that day. A stand-in for "concentration" at the time, because
    we can't look up past open-trial counts across all diseases."""
    counts = []
    for ticker, event_date in zip(events["ticker"], events["event_date"]):
        mine = running[running["ticker"] == ticker]
        counts.append(int(((mine["start_date"] <= event_date) &
                           (mine["primary_completion_date"] >= event_date)).sum()))
    return counts


def build_event_study(as_of=None, db_path=DB_PATH):
    """One row per event (ticker + date), including excluded events with the
    reason, so nothing disappears silently."""
    as_of = as_of or date.today().isoformat()
    conn = connect(db_path)
    trials = pd.read_sql(EVENT_QUERY, conn, params=[as_of])
    running = pd.read_sql(RUNNING_TRIALS_QUERY, conn)
    caps = dict(conn.execute("SELECT ticker, market_cap FROM ticker_info").fetchall())
    conn.close()

    events, excluded = combine_same_day(trials)
    adj, returns, volume = load_price_tables(events["ticker"].unique(), db_path)

    results = [measure_event(returns, volume, t, d)
               for t, d in zip(events["ticker"], events["event_date"])]
    events = pd.concat([events.reset_index(drop=True), pd.DataFrame(results)], axis=1)

    # Company size AT THE TIME of the event, estimated as
    # today's market cap x (price on day 0 / latest price).
    # Using today's size would be "lookahead bias": a small company whose
    # trial succeeded may be big today. Ignores shares issued since.
    def cap_then(row):
        cap_now = caps.get(row["ticker"])
        if pd.isna(row.get("day0_date")) or not cap_now:
            return None
        series = adj[row["ticker"]].dropna()
        return cap_now * adj.at[row["day0_date"], row["ticker"]] / series.iloc[-1]

    events["market_cap_then"] = events.apply(cap_then, axis=1)
    events["size_group"] = events["market_cap_then"].map(size_group)
    events["running_trials_then"] = running_trial_counts(events, running)
    events["impact_score_then"] = [
        impact_score(size_part(cap), concentration_part(n), phase_part(p))
        for cap, n, p in zip(events["market_cap_then"], events["running_trials_then"],
                             events["phase"])]

    all_rows = pd.concat([events, excluded], ignore_index=True)
    if "exclude_reason" not in all_rows.columns:   # every event was measurable
        all_rows["exclude_reason"] = None
    all_rows["included"] = all_rows["exclude_reason"].isna()
    columns = [
        "included", "exclude_reason", "ticker", "event_date", "day0_date", "phase",
        "status_group", "n_trials", "nct_ids", "size_group", "market_cap_then",
        "running_trials_then", "impact_score_then", "car_short", "car_post",
        "scar_short", "scar_post", "alpha", "beta", "sigma", "estimation_days",
        "zero_volume_share",
    ]
    return all_rows[columns].sort_values(["event_date", "ticker"]).reset_index(drop=True)


def summarize_group(group, window):
    """Average move statistics for one group of events in one window."""
    car = group[f"car_{window}"].dropna()
    scar = group[f"scar_{window}"].dropna()
    n = len(car)
    # t-statistic: is the average signed CAR different from zero?
    # (mean / standard error). Above ~2 in size suggests a real effect.
    t_stat = car.mean() / (car.std(ddof=1) / math.sqrt(n)) if n > 1 and car.std() > 0 else None
    return {
        "events": n,
        "mean_car": car.mean() if n else None,
        "t_stat": t_stat,
        "mean_abs_car": car.abs().mean() if n else None,
        "median_abs_car": car.abs().median() if n else None,
        # Share of events with an unusually big move. If trial milestones
        # didn't matter, this would be about 5% by chance alone.
        "share_significant": (scar.abs() > SIGNIFICANT_Z).mean() if n else None,
    }


def summarize_event_study(events):
    """Summary table: every window x (all events, and each breakdown)."""
    included = events[events["included"]].copy()
    scored = included["impact_score_then"].notna()
    included.loc[scored, "score_group"] = pd.qcut(
        included.loc[scored, "impact_score_then"].rank(method="first"), 3,
        labels=["low third", "middle third", "top third"]).astype(str)

    rows = []
    for window in EVENT_WINDOWS:
        rows.append({"window": window, "breakdown": "all", "group": "all",
                     **summarize_group(included, window)})
        for breakdown in ["size_group", "phase", "status_group", "score_group"]:
            for value, group in included.groupby(breakdown):
                rows.append({"window": window, "breakdown": breakdown, "group": value,
                             **summarize_group(group, window)})
    return pd.DataFrame(rows)


def score_backtest(events, window="post"):
    """Rank correlation between the impact score at the time and the size of
    the move (|CAR|). +1 = higher score always meant a bigger move, 0 = no link."""
    included = events[events["included"] & events["impact_score_then"].notna()]
    # Spearman = ordinary correlation of the RANKS (1st, 2nd, 3rd...), done
    # by hand because pandas' built-in version needs the extra scipy library.
    score_rank = included["impact_score_then"].rank()
    move_rank = included[f"car_{window}"].abs().rank()
    return score_rank.corr(move_rank)


# ===========================================================================
# Part 3: date slippage - do sponsors keep pushing their dates back?
# ===========================================================================
#
# Every run of `python -m src.store` saves a snapshot of each trial's key
# dates. Comparing snapshots shows when a sponsor moves a trial's expected
# primary completion date. History started 2026-11-11, so this only shows
# results after the second weekly run.
# (Decisions logged in notes/decisions.md, 2026-11-12.)

MIN_SPONSOR_TRIALS = 3   # fewer comparable trials -> too few to judge a sponsor

SNAPSHOT_QUERY = """
SELECT snapshot_date, nct_id, overall_status,
       primary_completion_date AS pcd,
       primary_completion_date_type AS pcd_type,
       primary_completion_date_precision AS pcd_precision
FROM snapshots
"""

TRIAL_INFO_QUERY = """
SELECT t.nct_id, t.lead_sponsor, s.ticker, t.title, t.phase, t.overall_status,
       t.primary_completion_date, t.primary_completion_date_type
FROM trials t
LEFT JOIN sponsor_tickers s ON s.sponsor_name = t.lead_sponsor
"""


def date_move_days(old_date, old_precision, new_date, new_precision):
    """How many days a date moved: positive = later (a delay), negative =
    earlier. None if either date is missing.

    Month-only dates are stored as the month's last day, so "March 2027"
    becoming "March 15, 2027" would look like a 16-day pull-in. A change
    within the same month where either date is month-only counts as 0."""
    if not isinstance(old_date, str) or not isinstance(new_date, str):
        return None
    if "month" in (old_precision, new_precision) and old_date[:7] == new_date[:7]:
        return 0
    return (date.fromisoformat(new_date) - date.fromisoformat(old_date)).days


def snapshot_pairs(snaps):
    """Each snapshot of a trial next to the trial's previous snapshot."""
    snaps = snaps.sort_values(["nct_id", "snapshot_date"]).reset_index(drop=True)
    fields = ["snapshot_date", "overall_status", "pcd", "pcd_type", "pcd_precision"]
    previous = snaps.groupby("nct_id")[fields].shift(1)
    previous.columns = ["prev_" + c for c in fields]
    pairs = pd.concat([snaps, previous], axis=1)
    pairs = pairs[pairs["prev_snapshot_date"].notna()].copy()
    pairs["days_moved"] = [
        date_move_days(od, op, nd, np_) for od, op, nd, np_ in
        zip(pairs["prev_pcd"], pairs["prev_pcd_precision"], pairs["pcd"], pairs["pcd_precision"])]
    pairs["status_changed"] = (pairs["overall_status"].fillna("")
                               != pairs["prev_overall_status"].fillna(""))
    # The trial finished: the sponsor replaced the estimate with the real date.
    pairs["became_actual"] = ((pairs["prev_pcd_type"] == "ESTIMATED")
                              & (pairs["pcd_type"] == "ACTUAL"))
    return pairs


def trial_slippage(snaps, pairs):
    """One row per trial: its date when we first saw it vs. now.

    'comparable' = seen in 2+ snapshots, and the first date was still an
    ESTIMATE (a trial that had already finished can't slip)."""
    snaps = snaps.sort_values(["nct_id", "snapshot_date"])
    first = snaps.drop_duplicates("nct_id", keep="first").set_index("nct_id")
    last = snaps.drop_duplicates("nct_id", keep="last").set_index("nct_id")
    out = pd.DataFrame({
        "snapshots": snaps.groupby("nct_id").size(),
        "first_snapshot": first["snapshot_date"],
        "first_date": first["pcd"],
        "first_date_type": first["pcd_type"],
        "latest_date": last["pcd"],
        "latest_date_type": last["pcd_type"],
    })
    out["total_slip_days"] = [
        date_move_days(od, op, nd, np_) for od, op, nd, np_ in
        zip(first["pcd"], first["pcd_precision"], last["pcd"], last["pcd_precision"])]
    moved = pairs["days_moved"].fillna(0) != 0
    out["date_changes"] = pairs[moved].groupby("nct_id").size()
    out["date_changes"] = out["date_changes"].fillna(0).astype(int)
    # Final slip: last estimate vs. the actual date, when the trial finished.
    out["final_slip_days"] = pairs[pairs["became_actual"]].groupby("nct_id")["days_moved"].last()
    out["comparable"] = ((out["snapshots"] >= 2) & (out["first_date_type"] == "ESTIMATED")
                         & out["total_slip_days"].notna())
    out["delayed"] = out["comparable"] & (out["total_slip_days"] > 0)
    return out.reset_index()


def sponsor_reliability(trial_slips, info, as_of):
    """One row per company (ticker if it has one, otherwise sponsor name):
    how often its dates slipped, by how much, and how many trials are
    overdue right now."""
    table = info.merge(trial_slips, on="nct_id", how="left")
    table["company"] = table["ticker"].fillna(table["lead_sponsor"])
    table["overdue_now"] = (table["overall_status"].isin(OPEN_STATUSES)
                            & (table["primary_completion_date_type"] == "ESTIMATED")
                            & (table["primary_completion_date"] < as_of))
    table["comparable"] = table["comparable"].fillna(False).astype(bool)

    rows = []
    for company, group in table.groupby("company"):
        comparable = group[group["comparable"]]
        enough = len(comparable) >= MIN_SPONSOR_TRIALS
        delays = comparable.loc[comparable["total_slip_days"] > 0, "total_slip_days"]
        rows.append({
            "company": company,
            "ticker": group["ticker"].iloc[0],
            "sponsor_names": "; ".join(sorted(group["lead_sponsor"].dropna().unique())),
            "open_trials": int(group["overall_status"].isin(OPEN_STATUSES).sum()),
            "overdue_now": int(group["overdue_now"].sum()),
            "comparable_trials": len(comparable),
            # Shown only with enough trials to judge (otherwise blank).
            "share_delayed": round((comparable["total_slip_days"] > 0).mean(), 3) if enough else None,
            "median_delay_days": float(delays.median()) if enough and len(delays) else None,
        })
    sponsors = pd.DataFrame(rows)
    return sponsors.sort_values(["overdue_now", "open_trials"], ascending=False) \
                   .reset_index(drop=True)


def build_slippage(as_of=None, db_path=DB_PATH):
    """Returns (changes, trials, sponsors, snapshot_dates).

    changes  = every date move or status change between consecutive snapshots
    trials   = one row per trial, first-seen date vs latest date
    sponsors = the reliability table"""
    as_of = as_of or date.today().isoformat()
    conn = connect(db_path)
    snaps = pd.read_sql(SNAPSHOT_QUERY, conn)
    info = pd.read_sql(TRIAL_INFO_QUERY, conn)
    conn.close()

    pairs = snapshot_pairs(snaps)
    something_changed = ((pairs["days_moved"].fillna(0) != 0) | pairs["status_changed"]
                         | pairs["became_actual"])
    changes = pairs[something_changed].merge(
        info[["nct_id", "ticker", "lead_sponsor", "phase", "title"]], on="nct_id", how="left")
    changes = changes[[
        "snapshot_date", "prev_snapshot_date", "nct_id", "ticker", "lead_sponsor", "phase",
        "prev_pcd", "pcd", "days_moved", "became_actual",
        "prev_overall_status", "overall_status", "status_changed", "title",
    ]].sort_values(["snapshot_date", "days_moved"], ascending=[False, False])

    trials = trial_slippage(snaps, pairs)
    sponsors = sponsor_reliability(trials, info, as_of)
    return changes.reset_index(drop=True), trials, sponsors, sorted(snaps["snapshot_date"].unique())


# --- command line ---------------------------------------------------------

def calendar_main():
    as_of = date.today().isoformat()
    print(f"Building catalyst calendar as of {as_of}...")
    calendar = build_catalyst_calendar(as_of)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"catalysts_{as_of}.csv"
    calendar.to_csv(path, index=False)

    upcoming = calendar[calendar["section"] == "upcoming"]
    past = calendar[calendar["section"] == "past date"]
    print(f"\n{len(upcoming)} upcoming catalysts (next {WINDOW_MONTHS} months), "
          f"{len(past)} with a past date. Saved to {path}")

    print("\nTop 15 upcoming by impact score:")
    top = upcoming.sort_values("impact_score", ascending=False).head(15)
    for _, r in top.iterrows():
        cap = f"${r['market_cap'] / 1e9:,.2f}B" if pd.notna(r["market_cap"]) else "n/a"
        print(f"  {r['impact_score']:>5}  {r['primary_completion_date']}  {r['ticker']:6} "
              f"{r['phase']:14} cap {cap:>10}  open trials {r['open_trials_all_diseases']:>3}  "
              f"{r['flags'] if isinstance(r['flags'], str) else ''}")


def event_study_main():
    as_of = date.today().isoformat()
    print(f"Running event study on past trial completions (before {as_of})...")
    events = build_event_study(as_of)
    summary = summarize_event_study(events)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    events_path = OUTPUT_DIR / f"event_study_{as_of}.csv"
    summary_path = OUTPUT_DIR / f"event_study_summary_{as_of}.csv"
    events.to_csv(events_path, index=False)
    summary.to_csv(summary_path, index=False)

    included = events[events["included"]]
    print(f"\n{len(included)} events measured, {len(events) - len(included)} excluded.")
    print("Exclusion reasons:")
    reasons = events["exclude_reason"].dropna().str.replace(r"\(.*\)", "", regex=True)
    for reason, count in reasons.str.strip().value_counts().items():
        print(f"  {count:>4}  {reason}")

    def pct(x):
        return f"{x:+.1%}" if x is not None and pd.notna(x) else "n/a"

    for window, (start, end) in EVENT_WINDOWS.items():
        print(f"\nWindow '{window}' (trading days {start} to +{end}):")
        print(f"  {'group':34} {'events':>6} {'mean CAR':>9} {'t':>6} "
              f"{'mean |CAR|':>10} {'median |CAR|':>12} {'% signif.':>9}")
        for _, r in summary[summary["window"] == window].iterrows():
            label = r["group"] if r["breakdown"] == "all" else f"{r['breakdown']}: {r['group']}"
            t = f"{r['t_stat']:.2f}" if pd.notna(r["t_stat"]) else "n/a"
            print(f"  {label:34} {r['events']:>6} {pct(r['mean_car']):>9} {t:>6} "
                  f"{pct(r['mean_abs_car']).lstrip('+'):>10} "
                  f"{pct(r['median_abs_car']).lstrip('+'):>12} "
                  f"{r['share_significant']:>9.0%}")

    print(f"\nImpact score back-test (rank correlation with |CAR|): "
          f"short {score_backtest(events, 'short'):.2f}, post {score_backtest(events, 'post'):.2f}")
    print(f"Saved {events_path} and {summary_path}")


def slippage_main():
    as_of = date.today().isoformat()
    changes, trials, sponsors, snapshot_dates = build_slippage(as_of)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    changes.to_csv(OUTPUT_DIR / f"slippage_changes_{as_of}.csv", index=False)
    trials.to_csv(OUTPUT_DIR / f"slippage_trials_{as_of}.csv", index=False)
    sponsors.to_csv(OUTPUT_DIR / f"slippage_sponsors_{as_of}.csv", index=False)

    print(f"Snapshots in the database: {len(snapshot_dates)} "
          f"({', '.join(snapshot_dates)})")
    if len(snapshot_dates) < 2:
        print("Only one snapshot so far, so no date can have moved yet. Run\n"
              "  python -m src.fetch  and  python -m src.store\n"
              "again in about a week to start measuring slippage.")
    else:
        latest = changes[changes["snapshot_date"] == snapshot_dates[-1]]
        moved = latest[latest["days_moved"].fillna(0) != 0]
        print(f"\nSince the previous snapshot: {len(moved)} dates moved "
              f"({(moved['days_moved'] > 0).sum()} later, {(moved['days_moved'] < 0).sum()} earlier), "
              f"{int(latest['status_changed'].sum())} status changes, "
              f"{int(latest['became_actual'].sum())} trials finished.")
        for _, r in moved.head(15).iterrows():
            print(f"  {r['days_moved']:>+5.0f} days  {r['ticker'] or '':6} {r['nct_id']}  "
                  f"{r['prev_pcd']} -> {r['pcd']}  {r['lead_sponsor']}")
        comparable = trials[trials["comparable"]]
        print(f"\n{len(comparable)} trials comparable so far, "
              f"{int(comparable['delayed'].sum())} delayed since first seen.")

    traded = sponsors[sponsors["ticker"].notna() & (sponsors["overdue_now"] > 0)]
    print(f"\nTraded companies with overdue trials right now "
          f"(open, estimated date already passed): {int(traded['overdue_now'].sum())} trials")
    for _, r in traded.iterrows():
        print(f"  {r['ticker']:6} {r['overdue_now']} of {r['open_trials']} open trials overdue")
    print(f"\nSaved slippage_changes/trials/sponsors_{as_of}.csv in {OUTPUT_DIR}")


def main(args=None):
    """python -m src.analytics            -> catalyst calendar
       python -m src.analytics events     -> event study
       python -m src.analytics slippage   -> date slippage"""
    args = sys.argv[1:] if args is None else args
    if args and args[0] == "events":
        event_study_main()
    elif args and args[0] == "slippage":
        slippage_main()
    else:
        calendar_main()


if __name__ == "__main__":
    main()
