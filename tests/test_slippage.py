"""Tests for the date-slippage analysis in src/analytics.py.

They build made-up weekly snapshots where we know how far each date moved.
Run with:  python -m pytest
"""

import pandas as pd
import pytest

from src import analytics, store
from src.enrich import CREATE_SPONSOR_TICKERS

AS_OF = "2026-11-25"
WEEKS = ["2026-11-11", "2026-11-18", "2026-11-25"]


# --- moving dates ---------------------------------------------------------

def test_date_move_days():
    assert analytics.date_move_days("2027-01-31", "day", "2027-05-01", "day") == 90
    assert analytics.date_move_days("2027-06-30", "day", "2027-04-30", "day") == -61
    assert analytics.date_move_days(None, None, "2027-01-01", "day") is None


def test_more_exact_date_in_same_month_is_not_a_move():
    # "March 2027" (stored as March 31) -> "March 15, 2027"
    assert analytics.date_move_days("2027-03-31", "month", "2027-03-15", "day") == 0
    # ...but moving to a different month is a real move.
    assert analytics.date_move_days("2027-03-31", "month", "2027-04-15", "day") == 15


# --- the whole analysis on a tiny database ---------------------------------

def snap(conn, week, nct_id, pcd, pcd_type="ESTIMATED", precision="day",
         status="RECRUITING"):
    conn.execute(
        "INSERT INTO snapshots (snapshot_date, nct_id, overall_status, primary_completion_date, "
        "primary_completion_date_type, primary_completion_date_precision) "
        "VALUES (?, ?, ?, ?, ?, ?)", (week, nct_id, status, pcd, pcd_type, precision))


def make_db(tmp_path):
    db = tmp_path / "test.db"
    conn = store.connect(db)
    conn.execute(CREATE_SPONSOR_TICKERS)
    conn.executemany("INSERT INTO sponsor_tickers (sponsor_name, ticker) VALUES (?, ?)",
                     [("Good Bio", "GOOD"), ("Few Bio", None)])
    for week in WEEKS:
        snap(conn, week, "NCT_SLIP", "2027-01-31" if week == WEEKS[0] else "2027-05-01")
        snap(conn, week, "NCT_EXACT", *(("2027-03-31", "ESTIMATED", "month") if week == WEEKS[0]
                                        else ("2027-03-15", "ESTIMATED", "day")))
        snap(conn, week, "NCT_EARLY", "2027-06-30" if week == WEEKS[0] else "2027-04-30")
        snap(conn, week, "NCT_OLD", "2020-01-01", "ACTUAL", status="COMPLETED")
        snap(conn, week, "NCT_FEW", "2026-10-01")   # overdue, never updated
    # NCT_DONE finishes in week 3, 30 days after its last estimate.
    snap(conn, WEEKS[0], "NCT_DONE", "2026-12-15")
    snap(conn, WEEKS[1], "NCT_DONE", "2026-12-15")
    snap(conn, WEEKS[2], "NCT_DONE", "2027-01-14", "ACTUAL", status="COMPLETED")
    snap(conn, WEEKS[2], "NCT_NEW", "2028-01-01")   # only seen once

    latest = {
        "NCT_SLIP": ("Good Bio", "RECRUITING", "2027-05-01", "ESTIMATED"),
        "NCT_EXACT": ("Good Bio", "RECRUITING", "2027-03-15", "ESTIMATED"),
        "NCT_EARLY": ("Good Bio", "RECRUITING", "2027-04-30", "ESTIMATED"),
        "NCT_OLD": ("Good Bio", "COMPLETED", "2020-01-01", "ACTUAL"),
        "NCT_DONE": ("Good Bio", "COMPLETED", "2027-01-14", "ACTUAL"),
        "NCT_NEW": ("Good Bio", "RECRUITING", "2028-01-01", "ESTIMATED"),
        "NCT_FEW": ("Few Bio", "RECRUITING", "2026-10-01", "ESTIMATED"),
    }
    conn.executemany(
        "INSERT INTO trials (nct_id, lead_sponsor, overall_status, primary_completion_date, "
        "primary_completion_date_type) VALUES (?, ?, ?, ?, ?)",
        [(k,) + v for k, v in latest.items()])
    conn.commit()
    conn.close()
    return db


def build(tmp_path):
    return analytics.build_slippage(AS_OF, db_path=make_db(tmp_path))


def test_changes_list_only_real_changes(tmp_path):
    changes, _, _, dates = build(tmp_path)
    assert dates == WEEKS
    moves = dict(zip(changes["nct_id"], changes["days_moved"]))
    assert moves == {"NCT_SLIP": 90, "NCT_EARLY": -61, "NCT_DONE": 30}
    done = changes[changes["nct_id"] == "NCT_DONE"].iloc[0]
    assert done["snapshot_date"] == WEEKS[2]
    assert bool(done["became_actual"]) and bool(done["status_changed"])


def test_trial_slippage(tmp_path):
    _, trials, _, _ = build(tmp_path)
    t = trials.set_index("nct_id")
    assert t.loc["NCT_SLIP", "total_slip_days"] == 90
    assert t.loc["NCT_SLIP", "date_changes"] == 1          # moved once, then stayed
    assert t.loc["NCT_EXACT", "total_slip_days"] == 0
    assert t.loc["NCT_DONE", "final_slip_days"] == 30
    assert not t.loc["NCT_OLD", "comparable"]               # already finished
    assert not t.loc["NCT_NEW", "comparable"]               # seen only once
    assert set(t.index[t["delayed"]]) == {"NCT_SLIP", "NCT_DONE"}


def test_sponsor_reliability(tmp_path):
    _, _, sponsors, _ = build(tmp_path)
    s = sponsors.set_index("company")
    # GOOD: 4 comparable trials (SLIP, EXACT, EARLY, DONE), 2 delayed by 90 and 30 days.
    assert s.loc["GOOD", "comparable_trials"] == 4
    assert s.loc["GOOD", "share_delayed"] == 0.5
    assert s.loc["GOOD", "median_delay_days"] == 60
    assert s.loc["GOOD", "overdue_now"] == 0
    # Few Bio: only 1 comparable trial -> too few to judge, but overdue still shows.
    assert pd.isna(s.loc["Few Bio", "share_delayed"])
    assert s.loc["Few Bio", "overdue_now"] == 1


def test_single_snapshot_gives_no_moves(tmp_path):
    db = tmp_path / "one.db"
    conn = store.connect(db)
    conn.execute(CREATE_SPONSOR_TICKERS)
    snap(conn, WEEKS[0], "NCT_A", "2027-01-31")
    conn.execute("INSERT INTO trials (nct_id, lead_sponsor, overall_status) "
                 "VALUES ('NCT_A', 'X', 'RECRUITING')")
    conn.commit()
    conn.close()
    changes, trials, _, dates = analytics.build_slippage(AS_OF, db_path=db)
    assert dates == [WEEKS[0]]
    assert changes.empty
    assert not trials["comparable"].any()
