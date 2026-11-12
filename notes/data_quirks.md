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
