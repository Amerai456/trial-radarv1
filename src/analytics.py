"""Stage 5 - Analyze. Part 1: catalyst calendar and impact score.

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
    python -m src.analytics
"""

import json
import math
import time
from datetime import date
from pathlib import Path

import pandas as pd

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


def main():
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


if __name__ == "__main__":
    main()
