# Data quirks

## ClinicalTrials.gov API v2 (seen 2026-11-11)
- The `AREA[Phase]PHASE2` filter also matches Phase 1/2 trials (phases list `["PHASE1","PHASE2"]`). Fixed with `AND NOT AREA[Phase]PHASE1`.
- Phase 2/3 trials are stored as a list of two phases: `["PHASE2","PHASE3"]`.
- One trial can match several condition searches: 335 of 2,747 unique trials appeared in 2+ of our 3 searches. Remove duplicates by NCT ID in clean.py.
- The full raw JSON is large: about 168 MB for 3,099 records, most of it in type 2 diabetes (two pages at the maximum page size of 1,000).
- Key dates (start, primary completion, completion) come as `YYYY-MM-DD` or `YYYY-MM`; no other formats seen. 127 trials have no primary completion date.
- Start dates on 1,341 trials (mostly older records) have no ACTUAL/ESTIMATED label: treat it as unknown, not as estimated.
- `resultsFirstPostDateStruct` (date results were posted) exists for 1,101 trials and is always a full day.
- Stale estimates: on 2026-11-11, 47 of 337 open trials with an ESTIMATED primary completion date had that date already in the past (oldest: Dec 2023). The sponsor hasn't updated the record. The catalyst calendar must decide how to show these; they are also a signal for the date-slippage analysis.

## SEC company_tickers.json (seen 2026-11-11)
- Automatic download returned HTTP 403 (blocked) with the GitHub no-reply contact line, so the file was saved by hand from a browser to `data/reference/company_tickers.json`. Refresh it by hand every few months.
- 10,405 tickers for 8,002 companies; some companies have several tickers (share classes), and we keep the first.
- SEC names carry state or ADR tags after a slash ("VERTEX PHARMACEUTICALS INC / MA", "Zealand Pharma A/S/ADR"); the name cleanup strips them.
- Some tickers are OTC ADRs (e.g. JHPCY, ZLDPF, MCUJF) or bankruptcy tickers ending in Q (SGMOQ): price data for these may be thin.
- Coverage on 2026-11-11 (after approving 10 review matches): 83 of 436 sponsors have a ticker, covering 1,709 of 2,747 trials (62%); 200 of 358 open trials have a ticker.

## yfinance prices (seen 2026-11-11)
- 78 tickers (77 sponsor tickers + XBI), 293,490 daily rows, about 75 seconds to download. The database grew to about 23 MB.
- HNPHY (Hansoh ADR) has no Yahoo data ("possibly delisted"), so Hansoh trials have no prices.
- Thin trading (share of the last 2 years with zero volume): JHPCY 89% (Hengrui; trading only since 2026-04), MCUJF 68%, ZLDPF 40%. All other tickers are under 5%.
- XBI has no market cap (it's a fund, not a company); that's expected.
- Market caps range from about $0.8M (EVFM) to $1.28T (LLY).
- In the combined yfinance table, days before a stock existed are blank rows; `split_download` drops them.
