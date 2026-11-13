"""Tests for the cash runway ("Zombie Biotech Identifier"): the Yahoo data
step in src/enrich.py and the analysis in src/analytics.py.

Made-up numbers with known answers, e.g. $12M of cash and $9M spent per
quarter ($3M a month) = exactly 4 months of runway.
Run with:  python -m pytest
"""

import pandas as pd
import pytest

from src import analytics, enrich, store
from src.enrich import CREATE_CASH_RUNWAY, CREATE_SPONSOR_TICKERS, CREATE_TICKER_INFO

AS_OF = "2026-11-12"
QUARTERS = pd.to_datetime(["2026-09-30", "2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30"])


# --- burn rate and months ---------------------------------------------------

def test_monthly_burn():
    assert analytics.monthly_burn([-9e6, -9e6, -9e6, -9e6]) == 3e6
    # Lumpy quarters are averaged: (-10 + 2) / 2 = -4 per quarter -> 1.33 per month
    assert analytics.monthly_burn([-10e6, 2e6, None, None]) == pytest.approx(4e6 / 3)
    assert analytics.monthly_burn([5e6, 1e6]) == 0.0        # cash coming in: no burn
    assert analytics.monthly_burn([None, None]) is None     # no data


def test_months_between():
    assert analytics.months_between("2027-01-01", "2028-01-01") == 12.0
    assert analytics.months_between("2027-06-30", "2027-01-29") == -5.0


# --- reading Yahoo's tables ---------------------------------------------------

def test_parse_cash_data_picks_latest_quarter_and_four_flows():
    balance = pd.DataFrame(
        {QUARTERS[0]: [12e6, 10e6], QUARTERS[1]: [20e6, 18e6]},
        index=enrich.CASH_ROWS)
    flows = pd.DataFrame({q: [-1e6 * (i + 1)] for i, q in enumerate(QUARTERS)},
                         index=[enrich.CASH_FLOW_ROW])
    data = enrich.parse_cash_data(balance, flows)
    assert (data["balance_sheet_date"], data["cash"]) == ("2026-09-30", 12e6)
    assert data["cash_source"] == enrich.CASH_ROWS[0]       # preferred row
    assert data["ocf_quarters"] == 4                         # 5th quarter ignored
    assert [data[f"ocf_{i}"] for i in range(1, 5)] == [-1e6, -2e6, -3e6, -4e6]


def test_parse_cash_data_uses_fallback_row_and_handles_missing():
    balance = pd.DataFrame({QUARTERS[0]: [7e6]}, index=[enrich.CASH_ROWS[1]])
    data = enrich.parse_cash_data(balance, None)
    assert data["cash"] == 7e6 and data["cash_source"] == enrich.CASH_ROWS[1]
    assert data["ocf_quarters"] == 0
    assert enrich.parse_cash_data(pd.DataFrame(), pd.DataFrame())["cash"] is None


def test_update_cash_runway_reports_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(enrich.time, "sleep", lambda seconds: None)

    def fake_fetch(ticker):
        if ticker == "NODATA":
            return enrich.parse_cash_data(None, None)
        balance = pd.DataFrame({QUARTERS[0]: [12e6]}, index=[enrich.CASH_ROWS[0]])
        flows = pd.DataFrame({QUARTERS[0]: [-9e6]}, index=[enrich.CASH_FLOW_ROW])
        return enrich.parse_cash_data(balance, flows)

    db = tmp_path / "test.db"
    missing = enrich.update_cash_runway(["GOOD", "NODATA"], AS_OF, db_path=db, fetch=fake_fetch)
    assert missing == ["NODATA"]
    conn = store.connect(db)
    rows = conn.execute("SELECT ticker, cash, ocf_1 FROM cash_runway ORDER BY ticker").fetchall()
    conn.close()
    assert rows == [("GOOD", 12e6, -9e6), ("NODATA", None, None)]


# --- the whole analysis on a tiny database ------------------------------------

def make_db(tmp_path):
    db = tmp_path / "test.db"
    conn = store.connect(db)
    for create in (CREATE_SPONSOR_TICKERS, CREATE_TICKER_INFO, CREATE_CASH_RUNWAY):
        conn.execute(create)
    companies = {   # ticker: (market cap, cash, quarterly operating cash flow)
        "SHORT": (100e6, 12e6, -9e6),     # $3M/month -> 4 months of cash
        "RICH": (500e6, 120e6, -9e6),     # 40 months of cash
        "PROFIT": (300e6, 50e6, 4e6),     # cash coming in
        "PAST": (100e6, 12e6, -9e6),      # short of cash, but readout date passed
        "BIG": (5e9, 1e6, -9e6),          # over $1B -> not analysed
    }
    for ticker, (cap, cash, flow) in companies.items():
        conn.execute("INSERT INTO sponsor_tickers (sponsor_name, ticker) VALUES (?, ?)",
                     (ticker + " Bio", ticker))
        conn.execute("INSERT INTO ticker_info (ticker, market_cap) VALUES (?, ?)", (ticker, cap))
        conn.execute("INSERT INTO cash_runway VALUES (?, '2026-09-30', ?, 'x', '2026-09-30', "
                     "?, ?, ?, ?, 4, ?)", (ticker, cash, flow, flow, flow, flow, AS_OF))
        pcd = "2026-01-31" if ticker == "PAST" else "2027-06-30"
        conn.execute(
            "INSERT INTO trials (nct_id, lead_sponsor, overall_status, phase, "
            "primary_completion_date, primary_completion_date_type, "
            "primary_completion_date_precision, completion_date) "
            "VALUES (?, ?, 'RECRUITING', 'PHASE2', ?, 'ESTIMATED', 'day', '2027-12-31')",
            ("NCT_" + ticker, ticker + " Bio", pcd))
    conn.commit()
    conn.close()
    return db


def test_runway_numbers_and_flag(tmp_path):
    table = analytics.build_runway_table(AS_OF, db_path=make_db(tmp_path)).set_index("ticker")
    short = table.loc["SHORT"]
    assert short["monthly_burn"] == 3e6
    assert short["runway_months"] == 4.0
    assert short["runway_end"] == "2027-01-29"        # 2026-09-30 + 4 average months
    assert short["cushion_months"] == -5.0            # runs out 5 months before readout
    assert short["cushion_study_months"] == -11.0     # study completion 2027-12-31
    assert short["runway_status"] == analytics.RUNWAY_FLAG
    assert bool(short["flagged"])


def test_runway_statuses(tmp_path):
    table = analytics.build_runway_table(AS_OF, db_path=make_db(tmp_path)).set_index("ticker")
    assert table.loc["RICH", "runway_status"] == "enough cash to readout"
    assert table.loc["PROFIT", "runway_status"] == "no burn (cash flow positive)"
    assert table.loc["PAST", "runway_status"] == "readout date already passed"
    assert "BIG" not in table.index
    assert table.index[0] == "SHORT"                  # flagged trials listed first


def test_no_cash_table_gives_empty_result(tmp_path):
    db = tmp_path / "empty.db"
    store.connect(db).close()
    assert analytics.build_runway_table(AS_OF, db_path=db).empty


def test_calendar_shows_the_runway_warning(tmp_path):
    calendar = analytics.build_catalyst_calendar(
        AS_OF, db_path=make_db(tmp_path), lookup=lambda names: 1,
        cache_dir=tmp_path / "cache").set_index("nct_id")
    assert analytics.RUNWAY_FLAG in calendar.loc["NCT_SHORT", "flags"]
    flags = calendar.loc["NCT_RICH", "flags"]
    assert not isinstance(flags, str) or analytics.RUNWAY_FLAG not in flags
