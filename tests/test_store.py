"""Tests for src/store.py. Each test uses its own temporary database file,
so your real data/trials.db is never touched.

Run with:  python -m pytest
"""

import sqlite3
from datetime import date

import pandas as pd

from src import store


def make_trials(primary_completion=date(2027, 3, 31), ids=("NCT1", "NCT2")):
    """A tiny cleaned-trials table with only the columns the tests care about."""
    return pd.DataFrame([
        {
            "nct_id": nct_id,
            "lead_sponsor": "Acme Bio",
            "overall_status": "RECRUITING",
            "enrollment": 120.0,          # pandas often stores counts as floats
            "primary_completion_date": primary_completion,
            "primary_completion_date_type": "ESTIMATED",
            "primary_completion_date_precision": "month",
            "has_results": False,
            "why_stopped": float("nan"),  # how pandas stores a blank
        }
        for nct_id in ids
    ])


def query(db_path, sql):
    conn = sqlite3.connect(db_path)
    rows = conn.execute(sql).fetchall()
    conn.close()
    return rows


def test_first_run_fills_both_tables(tmp_path):
    db = tmp_path / "test.db"
    counts = store.store(make_trials(), "2026-11-11", db_path=db)

    assert counts == {"new": 2, "updated": 0, "missing": 0, "snapshot_rows": 2}
    assert query(db, "SELECT COUNT(*) FROM trials") == [(2,)]
    assert query(db, "SELECT COUNT(*) FROM snapshots") == [(2,)]
    # Values are converted properly: date -> text, 120.0 -> 120, NaN -> NULL.
    assert query(db, "SELECT primary_completion_date, enrollment, why_stopped, has_results "
                     "FROM trials WHERE nct_id = 'NCT1'") == [("2027-03-31", 120, None, 0)]


def test_second_run_updates_instead_of_duplicating(tmp_path):
    db = tmp_path / "test.db"
    store.store(make_trials(), "2026-11-11", db_path=db)
    counts = store.store(make_trials(), "2026-11-18", db_path=db)

    assert counts["new"] == 0 and counts["updated"] == 2
    assert query(db, "SELECT COUNT(*) FROM trials") == [(2,)]
    # first_seen stays the same, last_seen moves forward.
    assert query(db, "SELECT first_seen_date, last_seen_date FROM trials WHERE nct_id = 'NCT1'") \
        == [("2026-11-11", "2026-11-18")]


def test_same_day_rerun_does_not_double_snapshots(tmp_path):
    db = tmp_path / "test.db"
    store.store(make_trials(), "2026-11-11", db_path=db)
    store.store(make_trials(), "2026-11-11", db_path=db)
    assert query(db, "SELECT COUNT(*) FROM snapshots") == [(2,)]


def test_changed_date_shows_up_as_new_snapshot(tmp_path):
    db = tmp_path / "test.db"
    store.store(make_trials(primary_completion=date(2027, 3, 31)), "2026-11-11", db_path=db)
    store.store(make_trials(primary_completion=date(2027, 6, 30)), "2026-11-18", db_path=db)

    history = query(db, "SELECT snapshot_date, primary_completion_date FROM snapshots "
                        "WHERE nct_id = 'NCT1' ORDER BY snapshot_date")
    assert history == [("2026-11-11", "2027-03-31"), ("2026-11-18", "2027-06-30")]
    # The trials table holds only the latest date.
    assert query(db, "SELECT primary_completion_date FROM trials WHERE nct_id = 'NCT1'") \
        == [("2027-06-30",)]


def test_trial_that_drops_out_is_kept(tmp_path):
    db = tmp_path / "test.db"
    store.store(make_trials(ids=("NCT1", "NCT2")), "2026-11-11", db_path=db)
    counts = store.store(make_trials(ids=("NCT1",)), "2026-11-18", db_path=db)

    assert counts["missing"] == 1
    assert query(db, "SELECT nct_id, last_seen_date FROM trials ORDER BY nct_id") \
        == [("NCT1", "2026-11-18"), ("NCT2", "2026-11-11")]
