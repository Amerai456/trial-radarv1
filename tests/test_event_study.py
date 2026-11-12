"""Tests for the event study in src/analytics.py.

They use made-up prices where we know the right answer in advance:
the stock moves 1.5x XBI every day, plus a +10% jump on the event day,
so the code should find beta = 1.5 and an abnormal return of about +10%.
Run with:  python -m pytest
"""

import numpy as np
import pandas as pd
import pytest

from src import analytics, store
from src.enrich import CREATE_PRICES, CREATE_SPONSOR_TICKERS, CREATE_TICKER_INFO

DAYS = [d.date().isoformat() for d in pd.bdate_range("2020-01-01", periods=400)]
EVENT_DAY = 300   # GOOD's event is on trading day number 300
JUMP = 0.10


# --- the math on its own --------------------------------------------------

def test_market_model_recovers_known_alpha_and_beta():
    rng = np.random.default_rng(1)
    market = rng.normal(0, 0.01, 500)
    stock = 0.002 + 1.5 * market + rng.normal(0, 0.0001, 500)
    alpha, beta, sigma = analytics.market_model(stock, market)
    assert alpha == pytest.approx(0.002, abs=1e-4)
    assert beta == pytest.approx(1.5, abs=0.01)
    assert sigma == pytest.approx(0.0001, rel=0.2)


def test_day0_moves_to_next_trading_day_after_a_weekend():
    days = np.array(["2026-11-06", "2026-11-09", "2026-11-10"])   # Fri, Mon, Tue
    assert analytics.day0_position(days, "2026-11-07") == 1        # Sat -> Mon
    assert analytics.day0_position(days, "2026-11-06") == 0


def test_summarize_group():
    group = pd.DataFrame({"car_post": [0.1, -0.1, 0.2, -0.2],
                          "scar_post": [3.0, -0.5, 0.5, -2.5]})
    stats = analytics.summarize_group(group, "post")
    assert stats["events"] == 4
    assert stats["mean_car"] == pytest.approx(0.0)           # ups and downs cancel
    assert stats["mean_abs_car"] == pytest.approx(0.15)      # the size of moves doesn't
    assert stats["share_significant"] == 0.5                 # 3.0 and -2.5


def test_size_groups():
    assert analytics.size_group(1e9) == "small (<$2B)"
    assert analytics.size_group(5e9) == "mid ($2-20B)"
    assert analytics.size_group(500e9) == "large (>$20B)"
    assert analytics.size_group(None) is None


# --- the whole event study on a tiny database ------------------------------

def make_prices():
    """Daily adjusted prices and volumes for each made-up ticker."""
    rng = np.random.default_rng(7)
    xbi_r = rng.normal(0, 0.01, len(DAYS))
    good_r = 1.5 * xbi_r + rng.normal(0, 0.001, len(DAYS))
    good_r[EVENT_DAY] += JUMP

    def to_prices(r):
        return 100 * np.cumprod(1 + r)

    tables = {
        "XBI": (to_prices(xbi_r), np.full(len(DAYS), 1000)),
        "GOOD": (to_prices(good_r), np.full(len(DAYS), 1000)),
        # No trading on every 5th day (20%), above the 10% limit.
        "THIN": (to_prices(xbi_r), np.where(np.arange(len(DAYS)) % 5 == 0, 0, 1000)),
    }
    rows = []
    for ticker, (adj, vol) in tables.items():
        rows += [(ticker, d, a, a, int(v)) for d, a, v in zip(DAYS, adj, vol)]
    # NEWCO only started trading on day 200, too late for the estimation window.
    good_rows = [row for row in rows if row[0] == "GOOD"]
    rows += [("NEWCO",) + row[1:] for row in good_rows[200:]]
    return rows


def add_trial(conn, nct_id, sponsor, day, status="COMPLETED", phase="PHASE3",
              date_type="ACTUAL", precision="day"):
    conn.execute(
        "INSERT INTO trials (nct_id, lead_sponsor, overall_status, phase, start_date, "
        "primary_completion_date, primary_completion_date_type, "
        "primary_completion_date_precision) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (nct_id, sponsor, status, phase, DAYS[0], DAYS[day], date_type, precision))


def make_db(tmp_path):
    db = tmp_path / "test.db"
    conn = store.connect(db)
    for create in (CREATE_SPONSOR_TICKERS, CREATE_TICKER_INFO, CREATE_PRICES):
        conn.execute(create)
    conn.executemany("INSERT INTO sponsor_tickers (sponsor_name, ticker) VALUES (?, ?)",
                     [("Good Bio", "GOOD"), ("Thin Bio", "THIN"), ("New Co", "NEWCO")])
    conn.executemany("INSERT INTO ticker_info (ticker, market_cap) VALUES (?, ?)",
                     [("GOOD", 1e9), ("THIN", 1e9), ("NEWCO", 1e9)])
    conn.executemany("INSERT INTO prices (ticker, date, close, adj_close, volume) "
                     "VALUES (?, ?, ?, ?, ?)", make_prices())
    # Two GOOD trials finishing the same day = one event.
    add_trial(conn, "NCT_GOOD_A", "Good Bio", EVENT_DAY, phase="PHASE2")
    add_trial(conn, "NCT_GOOD_B", "Good Bio", EVENT_DAY, status="TERMINATED")
    add_trial(conn, "NCT_WITHDRAWN", "Good Bio", 250, status="WITHDRAWN")
    add_trial(conn, "NCT_RECENT", "Good Bio", 380)                # +60 days not reached
    add_trial(conn, "NCT_MONTH", "Good Bio", 280, precision="month")  # not exact -> ignored
    add_trial(conn, "NCT_EST", "Good Bio", 290, date_type="ESTIMATED")  # not actual -> ignored
    add_trial(conn, "NCT_THIN", "Thin Bio", EVENT_DAY)
    add_trial(conn, "NCT_NEW", "New Co", EVENT_DAY)
    conn.commit()
    conn.close()
    return db


def build(tmp_path):
    return analytics.build_event_study(as_of="2026-11-12", db_path=make_db(tmp_path))


def test_finds_the_known_jump_and_beta(tmp_path):
    events = build(tmp_path)
    good = events[events["included"]].iloc[0]
    assert good["ticker"] == "GOOD"
    assert good["beta"] == pytest.approx(1.5, abs=0.05)
    assert good["car_short"] == pytest.approx(JUMP, abs=0.01)
    assert good["car_post"] == pytest.approx(JUMP, abs=0.02)
    assert good["scar_short"] > analytics.SIGNIFICANT_Z


def test_same_day_trials_become_one_event(tmp_path):
    good = build(tmp_path).query("included").iloc[0]
    assert good["n_trials"] == 2
    assert good["nct_ids"] == "NCT_GOOD_A; NCT_GOOD_B"
    assert good["phase"] == "PHASE3"                      # the more important phase
    assert good["status_group"] == "completed or active"  # not all were terminated


def test_exclusion_reasons(tmp_path):
    events = build(tmp_path)
    reasons = dict(zip(events["nct_ids"], events["exclude_reason"]))
    assert reasons["NCT_WITHDRAWN"] == "status WITHDRAWN"
    assert reasons["NCT_RECENT"] == "event window not finished yet"
    assert reasons["NCT_THIN"].startswith("thinly traded")
    assert reasons["NCT_NEW"] == "too little price history before event"
    # Month-only and estimated dates are never events at all.
    assert "NCT_MONTH" not in reasons and "NCT_EST" not in reasons
    assert events["included"].sum() == 1


def test_market_cap_then_uses_price_at_the_time(tmp_path):
    events = build(tmp_path)
    good = events[events["included"]].iloc[0]
    conn = store.connect(tmp_path / "test.db")
    prices = dict(conn.execute(
        "SELECT date, adj_close FROM prices WHERE ticker = 'GOOD'").fetchall())
    conn.close()
    expected = 1e9 * prices[DAYS[EVENT_DAY]] / prices[DAYS[-1]]
    assert good["market_cap_then"] == pytest.approx(expected)
    assert good["impact_score_then"] is not None
