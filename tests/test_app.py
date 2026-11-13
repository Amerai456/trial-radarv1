"""Smoke tests for the Streamlit dashboard (app.py).

Streamlit's built-in test tool runs the whole app script without a browser
and records any error. We check that every tab draws without crashing,
both with the real exported data and with a missing data file.
Run with:  python -m pytest
"""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).parent.parent / "app.py")
REAL_DB = Path(__file__).parent.parent / "dashboard_data" / "dashboard.db"


@pytest.mark.skipif(not REAL_DB.exists(), reason="run python -m src.export first")
def test_dashboard_runs_without_errors(monkeypatch):
    monkeypatch.setenv("DASHBOARD_DB", str(REAL_DB))
    app = AppTest.from_file(APP, default_timeout=60).run()
    assert not app.exception
    assert len(app.tabs) == 5
    # The disclaimer is shown at the top.
    assert any("Not investment advice" in box.value for box in app.info)


def test_filters_that_match_nothing_do_not_crash(monkeypatch):
    if not REAL_DB.exists():
        pytest.skip("run python -m src.export first")
    monkeypatch.setenv("DASHBOARD_DB", str(REAL_DB))
    app = AppTest.from_file(APP, default_timeout=60).run()
    app.slider[0].set_value(100).run()     # minimum impact score 100
    assert not app.exception
    app.selectbox[0].set_value(app.selectbox[0].options[-1]).run()   # another company
    assert not app.exception


def test_company_with_cash_runway_section(monkeypatch):
    if not REAL_DB.exists():
        pytest.skip("run python -m src.export first")
    monkeypatch.setenv("DASHBOARD_DB", str(REAL_DB))
    app = AppTest.from_file(APP, default_timeout=60).run()
    ticker = app.selectbox[0]
    runway_tickers = {"SKYE", "VERU", "BMEA"} & set(ticker.options)
    if not runway_tickers:
        pytest.skip("no small company with cash data in this export")
    ticker.set_value(sorted(runway_tickers)[0]).run()
    assert not app.exception
    assert any("Monthly burn" in m.label for m in app.metric)


def test_dashboard_does_not_load_the_data_pipeline(tmp_path):
    """The deployed dashboard only reads dashboard.db. Loading the pipeline
    (yfinance, rapidfuzz, the fetch/clean/store/enrich code) made it crash
    on Streamlit Community Cloud, so check in a fresh Python process."""
    import subprocess
    import sys

    script = (
        "import sys\n"
        "from streamlit.testing.v1 import AppTest\n"
        f"AppTest.from_file({APP!r}, default_timeout=60).run()\n"
        "pipeline = ['yfinance', 'rapidfuzz', 'src.fetch', 'src.clean', 'src.store',\n"
        "            'src.enrich', 'src.analytics']\n"
        "print([m for m in pipeline if m in sys.modules])\n"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                            cwd=Path(APP).parent, timeout=120)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "[]"


def test_missing_data_file_shows_a_message(monkeypatch, tmp_path):
    monkeypatch.setenv("DASHBOARD_DB", str(tmp_path / "nothing.db"))
    app = AppTest.from_file(APP, default_timeout=60).run()
    assert not app.exception
    assert "python -m src.export" in app.error[0].value
