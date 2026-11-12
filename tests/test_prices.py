"""Tests for the stock-price part of src/enrich.py.

They use a fake yfinance download, so no internet is needed.
Run with:  python -m pytest
"""

import sqlite3

import numpy as np
import pandas as pd

from src import enrich


def fake_download(tickers):
    """Build a table shaped like yfinance's: tickers side by side, blanks
    on days before a stock existed. 'BAD' always fails (no data)."""
    days = pd.to_datetime(["2026-11-09", "2026-11-10", "2026-11-11"])
    parts = {}
    for ticker in tickers:
        if ticker == "BAD":
            continue
        if ticker == "NEW":   # only started trading on the last day
            adj = [np.nan, np.nan, 10.0]
            volume = [np.nan, np.nan, 500]
        elif ticker == "THIN":  # trades on only one of three days
            adj = [5.0, 5.0, 5.2]
            volume = [0, 0, 100]
        else:
            adj = [100.0, 101.0, 102.0]
            volume = [1000, 1200, 900]
        parts[ticker] = pd.DataFrame(
            {"Open": adj, "High": adj, "Low": adj, "Close": adj,
             "Adj Close": adj, "Volume": volume}, index=days)
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, axis=1)   # two-level columns: (ticker, field)


def no_pause(monkeypatch):
    monkeypatch.setattr(enrich.time, "sleep", lambda seconds: None)


def fake_info(ticker):
    return {"market_cap": 1e9, "currency": "USD", "exchange": "NMS"}


def query(db, sql):
    conn = sqlite3.connect(db)
    rows = conn.execute(sql).fetchall()
    conn.close()
    return rows


def test_split_download_drops_days_before_a_stock_existed():
    frames = enrich.split_download(fake_download(["XBI", "NEW"]), ["XBI", "NEW"])
    assert len(frames["XBI"]) == 3
    assert len(frames["NEW"]) == 1


def test_zero_volume_share():
    frames = enrich.split_download(fake_download(["THIN", "XBI"]), ["THIN", "XBI"])
    assert enrich.zero_volume_share(frames["THIN"], "2026-11-11") == round(2 / 3, 4)
    assert enrich.zero_volume_share(frames["XBI"], "2026-11-11") == 0.0
    assert enrich.zero_volume_share(pd.DataFrame(), "2026-11-11") is None


def test_failed_ticker_does_not_crash_the_run(monkeypatch, tmp_path):
    no_pause(monkeypatch)
    db = tmp_path / "test.db"
    failed = enrich.update_prices(["XBI", "BAD"], "2026-11-11", db_path=db,
                                  download=fake_download, get_info=fake_info)
    assert failed == ["BAD"]
    assert query(db, "SELECT COUNT(*) FROM prices WHERE ticker = 'XBI'") == [(3,)]
    # The failure is recorded in ticker_info with 0 price rows.
    assert query(db, "SELECT price_rows FROM ticker_info WHERE ticker = 'BAD'") == [(0,)]


def test_prices_are_stored_with_correct_types(monkeypatch, tmp_path):
    no_pause(monkeypatch)
    db = tmp_path / "test.db"
    enrich.update_prices(["XBI"], "2026-11-11", db_path=db,
                         download=fake_download, get_info=fake_info)
    assert query(db, "SELECT date, adj_close, volume FROM prices ORDER BY date LIMIT 1") \
        == [("2026-11-09", 100.0, 1000)]
    assert query(db, "SELECT market_cap, first_price_date, last_price_date, price_rows "
                     "FROM ticker_info WHERE ticker = 'XBI'") \
        == [(1e9, "2026-11-09", "2026-11-11", 3)]


def test_rerun_replaces_history_instead_of_duplicating(monkeypatch, tmp_path):
    no_pause(monkeypatch)
    db = tmp_path / "test.db"
    for _ in range(2):
        enrich.update_prices(["XBI", "THIN"], "2026-11-11", db_path=db,
                             download=fake_download, get_info=fake_info)
    assert query(db, "SELECT COUNT(*) FROM prices") == [(6,)]
    assert query(db, "SELECT COUNT(*) FROM ticker_info") == [(2,)]


def test_tickers_are_downloaded_in_batches(monkeypatch):
    no_pause(monkeypatch)
    monkeypatch.setattr(enrich, "BATCH_SIZE", 2)
    calls = []

    def counting_download(tickers):
        calls.append(list(tickers))
        return fake_download(tickers)

    enrich.download_all_prices(["A", "B", "C"], download=counting_download)
    assert calls == [["A", "B"], ["C"]]
