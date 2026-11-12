# Biotech Catalyst Tracker

## What this project is
A Python tool that collects clinical trial data from the ClinicalTrials.gov API v2, links each trial's sponsor to its stock ticker, and helps answer investor questions: which trial readouts are coming up, how much each one likely matters (catalyst impact score), and how sponsor stocks have reacted around past trial milestones (event study vs. the XBI biotech ETF). It ends in a deployed Streamlit dashboard. This is a resume project for a finance internship.

## About me (the user)
- I'm a first-year pre-pharmacy student and new to Python.
- You write the code. I make the design decisions, verify the data, and interpret results.
- I need to be able to explain this project in interviews, so understanding matters as much as working code.

## How to work with me
- Before writing code for anything non-trivial, show me a short plan and wait for my OK.
- Work in small steps: one pipeline stage or feature at a time.
- After each change, explain in plain English what you did and why, as if to a beginner.
- Tell me how to run and check the result myself.
- Ask me before: adding new libraries, changing the database structure, deleting files, or making design decisions about the impact score, event dates, or which trials to include.
- If the API's real data disagrees with these notes, trust the real data and tell me.
- Commit to Git after each working step with a clear message.

## Tech stack
Python 3.10+, requests, pandas, SQLite, rapidfuzz, yfinance, Streamlit, Plotly, pytest, GitHub Actions.
Use a virtual environment in `.venv` and keep `requirements.txt` up to date.

## Folder structure
```
data/        raw JSON, database (git-ignored)
notes/       field map, data quirks, decisions log
src/
  fetch.py      download trials from the API
  clean.py      turn raw JSON into flat rows
  store.py      SQLite: trials table + weekly snapshots table
  enrich.py     sponsor -> ticker matching, stock prices
  analytics.py  catalyst score, event study, delay analysis
tests/
app.py       Streamlit dashboard
```

## Pipeline stages
1. Fetch: Phase 2/3 industry-sponsored trials for chosen diseases, all pages (nextPageToken), with timeouts, retries, and polite delays.
2. Clean: one flat row per trial. Use .get() for every field; handle partial dates and multi-phase trials.
3. Store: `trials` table (latest version) + `snapshots` table (key dates per run, with snapshot_date).
4. Enrich: match sponsors to the SEC company tickers list; pull prices/market caps with yfinance, plus XBI.
5. Analyze: catalyst calendar + impact score, event study (abnormal return vs XBI), date-slippage analysis.
6. Present: Streamlit dashboard reading from the database. Include a "not investment advice" disclaimer.

## Current phase
Phase: stage 5, Analyze (catalyst calendar + impact score, event study, date-slippage analysis). Plan each piece separately, because they carry the biggest design decisions (impact score, event dates, exclusion rules).

Done so far (2026-11-11): stages 1-4 for obesity, type 1 diabetes and type 2 diabetes.
- 2,747 unique trials are in `data/trials.db`, with the first snapshot dated 2026-11-11.
- 83 of 436 sponsors are matched to US tickers (62% of trials; 200 of 358 open trials). Table `sponsor_tickers`.
- Daily prices from 2006 for 77 tickers + XBI are in table `prices`; today's market cap and thin-trading flags are in `ticker_info`.

Run the pipeline in this order:
1. `python -m src.fetch`
2. `python -m src.store` (runs the cleaning itself; `python -m src.clean` only writes a CSV for checking)
3. `python -m src.enrich` (matching + prices)
4. `python -m src.analytics` (catalyst calendar), `python -m src.analytics events` (event study), `python -m src.analytics slippage` (date slippage; needs 2+ weekly snapshots)

Other things to know:
- **SEC ticker list:** it's saved by hand to `data/reference/company_tickers.json` because the SEC blocks the automatic download.
- **Matching:** new fuzzy matches go to `data/reference/matches_to_review.csv`. Approve or reject them by adding a row to `reference/sponsor_aliases.csv`.
- **Open item:** 5 alias rows are marked VERIFY (Hengrui group, Zealand, Hansoh).
- **Snapshots:** history only builds up over repeated runs, so plan regular (e.g. weekly) runs.

## Decisions log
Record important decisions and the reasoning in `notes/decisions.md`.
