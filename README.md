# Biotech Catalyst Tracker

**Live dashboard:** _link coming after deployment_

A Python pipeline and Streamlit dashboard that turns public clinical-trial data into
investor questions about biotech stocks, for obesity, type 1 diabetes and type 2
diabetes trials:

1. **Which trial readouts are coming up?** A catalyst calendar of open Phase 2/3
   trials by US-listed companies, with each trial's expected primary completion date.
2. **How much is each readout likely to matter?** A 0-100 impact score based on
   company size, how concentrated the company's pipeline is, and trial phase.
3. **How have stocks moved around past trial milestones?** An event study of 538
   past trial completions, measuring abnormal returns against the XBI biotech ETF.
4. **Do sponsors keep pushing dates back?** A date-slippage tracker built from
   weekly snapshots of every trial's dates.
5. **Can the company afford to reach its readout?** A cash-runway check for
   companies under $1B that flags trials where the cash may run out first.

> For educational purposes only. Not investment advice. Data from ClinicalTrials.gov
> and Yahoo Finance may be incomplete or delayed.

## Key findings (data as of November 2026)

- **Small companies move far more around trial completions.** Over the 60 trading
  days after a trial's primary completion, the median absolute abnormal move was
  about 40% for companies under $2B, versus about 8% for companies over $20B.
  For large companies, "unusually big" moves happened about as often as chance
  would predict (6% vs. 5%); for small companies, 24% of the time.
- **Terminated trials averaged about -8%** abnormal return over the next 60 days.
- **The impact score passes a back-test:** events with higher scores (recalculated
  as of each event) had bigger moves (rank correlation 0.21).
- **5 of 15 open trials at companies under $1B** may run out of cash before their
  readout, based on the latest quarterly filings.

## How it works

```
ClinicalTrials.gov API -> fetch -> clean -> SQLite (trials + weekly snapshots)
                                                  |
SEC ticker list + hand-checked aliases -> sponsor-to-ticker matching
Yahoo Finance -> daily prices, market caps, cash and cash flow
                                                  |
                     analytics: calendar + score, event study, slippage, runway
                                                  |
                     export -> dashboard_data/dashboard.db -> Streamlit app
```

| File | What it does |
|---|---|
| `src/fetch.py` | Downloads Phase 2/3 industry-sponsored trials from the ClinicalTrials.gov API v2 (all pages, retries, polite delays) |
| `src/clean.py` | Turns each trial's nested JSON into one flat row (handles month-only dates and multi-phase trials) |
| `src/store.py` | Saves trials to SQLite, plus a dated snapshot of every trial's key dates on each run |
| `src/enrich.py` | Matches sponsors to SEC tickers (fuzzy matches are reviewed by hand), downloads prices, market caps and cash data |
| `src/analytics.py` | Catalyst calendar and impact score, event study, date slippage, cash runway |
| `src/export.py` | Writes the small database the dashboard reads |
| `app.py` | The Streamlit dashboard |
| `notes/decisions.md` | Every design decision and the reasoning behind it |
| `notes/data_quirks.md` | Surprises found in the real data |

## Methods in brief

- **Impact score** = 50% company size (log scale of market cap) + 30% concentration
  (1 / open Phase 2/3 trials across all diseases) + 20% phase.
- **Event study:** day 0 = a trial's actual primary completion date. A market model
  fitted over trading days -250 to -30 predicts each stock's return from XBI's; the
  abnormal return is the difference, added up over days -1 to +1 and 0 to +60.
- **Cash runway:** monthly burn = average quarterly operating cash outflow (last 4
  quarters) / 3; runway = cash / monthly burn, counted from the balance-sheet date.

## Limitations

- **Survivorship bias:** only companies listed today are matched to tickers, so
  small companies whose trials failed and that later disappeared are missing.
- Day 0 is the trial completion date, not the date results were announced.
- Company size at the time of past events is estimated from today's market cap.
- Cash figures can be several months old; companies often raise money before a readout.
- Date-slippage history started in November 2026 and builds up with weekly runs.

## Run it yourself

```bash
python -m venv .venv
.venv/Scripts/activate          # Windows; on Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py            # uses the committed dashboard_data/dashboard.db
```

To rebuild the data from scratch:

```bash
python -m src.fetch
python -m src.store
python -m src.enrich      # needs data/reference/company_tickers.json from sec.gov
python -m src.export
```

Run the tests with `python -m pytest`.

## Weekly update

1. Run the four commands above (fetch, store, enrich, export).
2. Commit and push `dashboard_data/dashboard.db`. The live dashboard updates automatically.

## Built with

Python, pandas, SQLite, requests, rapidfuzz, yfinance, Streamlit, Plotly and pytest.
Built with AI assistance (Claude Code); the design decisions are logged in
`notes/decisions.md`.
