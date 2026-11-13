"""Tests for src/export.py (the small database the dashboard reads).

Run with:  python -m pytest
"""

import sqlite3

import pandas as pd

from src import export, store
from src.enrich import CREATE_PRICES


def test_write_dashboard_db_replaces_old_file(tmp_path):
    target = tmp_path / "dash" / "dashboard.db"
    export.write_dashboard_db({"a": pd.DataFrame({"x": [1, 2]})}, target)
    export.write_dashboard_db({"b": pd.DataFrame({"y": ["hi"]})}, target)   # second export

    conn = sqlite3.connect(target)
    names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    rows = conn.execute("SELECT y FROM b").fetchall()
    conn.close()
    assert names == ["b"]                      # old tables are gone, not mixed in
    assert rows == [("hi",)]
    assert not (target.parent / "dashboard.db.tmp").exists()


def test_recent_prices_keeps_only_last_years(tmp_path):
    db = tmp_path / "test.db"
    conn = store.connect(db)
    conn.execute(CREATE_PRICES)
    conn.executemany("INSERT INTO prices (ticker, date, adj_close) VALUES (?, ?, ?)",
                     [("XBI", "2020-01-02", 1.0), ("XBI", "2022-01-03", 2.0),
                      ("XBI", "2026-11-11", 3.0)])
    conn.commit()
    conn.close()
    prices = export.recent_prices(db, "2026-11-12", years=5)
    assert list(prices["date"]) == ["2022-01-03", "2026-11-11"]
