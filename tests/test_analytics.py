"""Tests for the catalyst calendar and impact score in src/analytics.py.

They build a tiny database of made-up trials and use a fake trial-count
lookup, so no internet is needed.
Run with:  python -m pytest
"""

import sqlite3

import pytest

from src import analytics, store
from src.enrich import CREATE_SPONSOR_TICKERS, CREATE_TICKER_INFO

AS_OF = "2026-11-11"


# --- score parts ----------------------------------------------------------

def test_size_part_uses_log_scale_between_anchors():
    assert analytics.size_part(50e6) == 1.0        # $50M or smaller
    assert analytics.size_part(10e6) == 1.0
    assert analytics.size_part(500e9) == 0.0       # $500B or bigger
    assert analytics.size_part(1.3e12) == 0.0
    # $5B is exactly halfway between $50M and $500B on a log scale.
    assert analytics.size_part(5e9) == pytest.approx(0.5)


def test_size_part_unknown_market_cap():
    assert analytics.size_part(None) is None
    assert analytics.size_part(float("nan")) is None


def test_concentration_and_phase_parts():
    assert analytics.concentration_part(1) == 1.0
    assert analytics.concentration_part(4) == 0.25
    assert analytics.phase_part("PHASE3") == 1.0
    assert analytics.phase_part("PHASE2/PHASE3") == 0.8
    assert analytics.phase_part("PHASE2") == 0.5


def test_impact_score_examples_from_the_plan():
    # Tiny company, its only trial, Phase 2: 50 + 30 + 10 = 90
    assert analytics.impact_score(1.0, 1.0, 0.5) == 90.0
    # Giant company, 1 of 140 trials, Phase 3: 0 + ~0.2 + 20
    assert analytics.impact_score(0.0, 1 / 140, 1.0) == 20.2
    # Unknown size -> no score rather than a guess
    assert analytics.impact_score(None, 1.0, 1.0) is None


# --- the calendar ---------------------------------------------------------

def add_trial(conn, nct_id, sponsor, date, date_type="ESTIMATED", precision="day",
              status="RECRUITING", phase="PHASE3"):
    conn.execute(
        "INSERT INTO trials (nct_id, lead_sponsor, overall_status, phase, "
        "primary_completion_date, primary_completion_date_type, "
        "primary_completion_date_precision) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (nct_id, sponsor, status, phase, date, date_type, precision))


def make_db(tmp_path):
    db = tmp_path / "test.db"
    conn = store.connect(db)
    conn.execute(CREATE_SPONSOR_TICKERS)
    conn.execute(CREATE_TICKER_INFO)
    conn.executemany("INSERT INTO sponsor_tickers (sponsor_name, ticker) VALUES (?, ?)",
                     [("Tiny Bio", "TINY"), ("Big Pharma", "BIG"), ("Private Co", None)])
    conn.executemany("INSERT INTO ticker_info (ticker, market_cap, zero_volume_share_2y) "
                     "VALUES (?, ?, ?)", [("TINY", 20e6, 0.0), ("BIG", 1e12, 0.5)])
    add_trial(conn, "NCT_SOON", "Tiny Bio", "2027-01-15", phase="PHASE2")
    add_trial(conn, "NCT_BIG", "Big Pharma", "2027-03-31", precision="month")
    add_trial(conn, "NCT_LATE", "Big Pharma", "2028-06-30")                    # beyond 12 months
    add_trial(conn, "NCT_OVERDUE", "Big Pharma", "2026-06-30")                 # estimated, passed
    add_trial(conn, "NCT_OLD", "Big Pharma", "2016-01-24", date_type="ACTUAL",
              status="ACTIVE_NOT_RECRUITING")                                  # over 12 months ago
    add_trial(conn, "NCT_DONE", "Tiny Bio", "2026-10-01", date_type="ACTUAL",
              status="ACTIVE_NOT_RECRUITING")
    add_trial(conn, "NCT_PRIVATE", "Private Co", "2027-01-01")                 # no ticker
    add_trial(conn, "NCT_CLOSED", "Tiny Bio", "2027-02-01", status="COMPLETED")  # not open
    conn.commit()
    conn.close()
    return db


def fake_lookup(names):
    return {"Tiny Bio": 0, "Big Pharma": 140}.get(names[0])


def build(tmp_path):
    db = make_db(tmp_path)
    calendar = analytics.build_catalyst_calendar(
        AS_OF, db_path=db, lookup=fake_lookup, cache_dir=tmp_path / "cache")
    return calendar.set_index("nct_id")


def test_calendar_includes_only_open_traded_trials_in_window(tmp_path):
    # Left out: NCT_LATE (over 12 months ahead), NCT_OLD (over 12 months ago),
    # NCT_PRIVATE (no ticker), NCT_CLOSED (not open).
    calendar = build(tmp_path)
    assert set(calendar.index) == {"NCT_SOON", "NCT_BIG", "NCT_OVERDUE", "NCT_DONE"}


def test_sections_and_flags(tmp_path):
    calendar = build(tmp_path)
    assert calendar.loc["NCT_SOON", "section"] == "upcoming"
    assert calendar.loc["NCT_OVERDUE", "section"] == "past date"
    assert calendar.loc["NCT_OVERDUE", "flags"].startswith("overdue")
    assert calendar.loc["NCT_DONE", "flags"] == "data collected"
    assert "month-only date" in calendar.loc["NCT_BIG", "flags"]
    assert "thinly traded" in calendar.loc["NCT_BIG", "flags"]


def test_concentration_never_below_our_own_open_trials(tmp_path):
    calendar = build(tmp_path)
    # The fake API says Tiny Bio has 0 open trials, but we can see 2 ourselves.
    assert calendar.loc["NCT_SOON", "open_trials_all_diseases"] == 2
    assert calendar.loc["NCT_BIG", "open_trials_all_diseases"] == 140


def test_small_company_scores_higher_than_giant(tmp_path):
    calendar = build(tmp_path)
    # Tiny Bio: 1.0 size, 1/2 concentration, Phase 2 -> 50 + 15 + 10 = 75
    assert calendar.loc["NCT_SOON", "impact_score"] == 75.0
    assert calendar.loc["NCT_BIG", "impact_score"] < 25


def test_trial_counts_are_cached_per_day(tmp_path):
    calls = []

    def counting_lookup(names):
        calls.append(names)
        return 1

    db = make_db(tmp_path)
    for _ in range(2):
        analytics.build_catalyst_calendar(AS_OF, db_path=db, lookup=counting_lookup,
                                          cache_dir=tmp_path / "cache")
    assert len(calls) == 2   # 2 companies looked up once, not twice
