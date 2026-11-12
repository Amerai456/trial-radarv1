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

## Catalyst calendar (seen 2026-11-11)
- 81 upcoming catalysts and 23 with a past date. Scores range from 10.2 (Lilly Phase 2) to 93.3 (Oramed Phase 3); the median is 24.
- The "past date" section includes very old "data collected" trials (e.g. an AstraZeneca trial with primary completion in 2016 that is still "active", probably a long follow-up). Their results are almost certainly public already.
- Counting open trials across all diseases changes concentration a lot: AZN 242, NVS 157, LLY 140, versus 13-51 in our obesity/diabetes data.
- Sponsor-name lookup is phrase-based, so "Sanofi" also counts "Sanofi Pasteur" (same company). Abbott returned 0, so it falls back to our own count.
- Hansoh (HNPHY) trials are unscored because Yahoo has no market cap.
- Watch out: a pandas column named `flags` must be read as `df["flags"]`, not `df.flags` (`.flags` is a built-in pandas attribute).

## Event study (2026-11-12)
- **CAR can go below -100%.** Oramed (ORMP) fell 76% in one day in Jan 2023 when its Phase 3 failed; adding up three days of abnormal returns gives -102%. This is a known quirk of summing simple returns; a real investor can't lose more than 100%. Buy-and-hold abnormal return (compounding) would avoid it.
- **Small companies show a large positive median move (+35% over 60 days).** Partly real winners (ProKidney +464%, Rhythm, Altimmune, Structure), but mostly **survivorship bias**: the SEC ticker list only contains companies listed today, so small companies whose trials failed and were delisted or acquired never enter the study.
- 2 trials with a past actual primary completion date have status UNKNOWN (sponsor stopped updating); they're excluded.
