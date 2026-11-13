"""Stage 6 - Export a small database for the dashboard.

The full database (data/trials.db, ~23 MB) stays on your computer and out of
Git. This script runs all the analyses and saves only what the dashboard
needs into dashboard_data/dashboard.db (a few MB), which IS committed to
GitHub, so the deployed dashboard has data to show.

Tables in dashboard.db:
  calendar          catalyst calendar (upcoming + past date)
  company_trials    every trial of a company with a ticker
  companies         ticker, market cap, sponsor names
  prices            last 5 years of daily adjusted prices (+ XBI)
  events            event study, one row per event (incl. excluded ones)
  event_summary     event study summary table
  car_paths         average CAR by trading day (-10 to +60), by size group
  slippage_changes  date moves / status changes between snapshots
  slippage_sponsors sponsor reliability + overdue counts
  cash_runway       cash runway vs. readout, companies under $1B
  meta              data date, export date, and a few headline numbers

Run it from the project folder (after the pipeline) with:
    python -m src.export
"""

import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd

from src import analytics
from src.store import DB_PATH, connect

DASHBOARD_DB = Path("dashboard_data") / "dashboard.db"
PRICE_YEARS = 5

COMPANY_TRIALS_QUERY = """
SELECT s.ticker, t.nct_id, t.title, t.lead_sponsor, t.phase, t.overall_status,
       t.primary_completion_date, t.primary_completion_date_type,
       t.primary_completion_date_precision, t.matched_searches, t.interventions
FROM trials t
JOIN sponsor_tickers s ON s.sponsor_name = t.lead_sponsor
WHERE s.ticker IS NOT NULL
"""

COMPANIES_QUERY = """
SELECT i.ticker, i.market_cap, i.exchange, i.zero_volume_share_2y,
       GROUP_CONCAT(s.sponsor_name, '; ') AS sponsor_names
FROM ticker_info i
LEFT JOIN sponsor_tickers s ON s.ticker = i.ticker
GROUP BY i.ticker
"""


def read_query(db_path, sql, params=()):
    conn = connect(db_path)
    table = pd.read_sql(sql, conn, params=list(params))
    conn.close()
    return table


def recent_prices(db_path, as_of, years=PRICE_YEARS):
    """Daily adjusted prices from `years` years before as_of onwards."""
    start = (pd.Timestamp(as_of) - pd.DateOffset(years=years)).date().isoformat()
    return read_query(db_path, "SELECT ticker, date, adj_close FROM prices "
                               "WHERE date >= ? ORDER BY ticker, date", [start])


def write_dashboard_db(tables, target=DASHBOARD_DB):
    """Write {table name: DataFrame} into a fresh SQLite file.

    It writes to a temporary file first and then swaps it in, so a
    dashboard that is open never sees a half-written file."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + ".tmp")
    temp.unlink(missing_ok=True)
    conn = sqlite3.connect(temp)
    for name, table in tables.items():
        table.to_sql(name, conn, index=False)
    conn.commit()
    conn.execute("VACUUM")   # pack the file as small as possible
    conn.close()
    temp.replace(target)


def build_export(as_of=None, source_db=DB_PATH):
    """Run every analysis and return {table name: DataFrame}."""
    as_of = as_of or date.today().isoformat()
    print("  catalyst calendar...")
    calendar = analytics.build_catalyst_calendar(as_of, db_path=source_db)
    print("  event study...")
    events = analytics.build_event_study(as_of, db_path=source_db)
    summary = analytics.summarize_event_study(events)
    paths = analytics.build_car_paths(events, db_path=source_db)
    print("  date slippage...")
    changes, _, sponsors, snapshot_dates = analytics.build_slippage(as_of, db_path=source_db)

    meta = {
        "data_as_of": snapshot_dates[-1] if snapshot_dates else None,  # last fetch date
        "exported_on": as_of,
        "snapshots": len(snapshot_dates),
        "first_snapshot": snapshot_dates[0] if snapshot_dates else None,
        "events_measured": int(events["included"].sum()),
        "events_excluded": int((~events["included"]).sum()),
        "score_backtest_short": round(analytics.score_backtest(events, "short"), 3),
        "score_backtest_post": round(analytics.score_backtest(events, "post"), 3),
    }
    return {
        "calendar": calendar,
        "company_trials": read_query(source_db, COMPANY_TRIALS_QUERY),
        "companies": read_query(source_db, COMPANIES_QUERY),
        "prices": recent_prices(source_db, as_of),
        "events": events,
        "event_summary": summary,
        "car_paths": paths,
        "slippage_changes": changes,
        "slippage_sponsors": sponsors,
        "cash_runway": analytics.build_runway_table(as_of, db_path=source_db),
        "meta": pd.DataFrame({"key": list(meta), "value": [str(v) for v in meta.values()]}),
    }


def main():
    as_of = date.today().isoformat()
    print(f"Exporting dashboard data as of {as_of}...")
    tables = build_export(as_of)
    write_dashboard_db(tables)
    for name, table in tables.items():
        print(f"  {name:18} {len(table):>7,} rows")
    size_mb = DASHBOARD_DB.stat().st_size / 1e6
    print(f"Saved {DASHBOARD_DB} ({size_mb:.1f} MB). Commit it so the deployed dashboard updates.")


if __name__ == "__main__":
    main()
