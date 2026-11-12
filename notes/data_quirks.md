# Data quirks

## ClinicalTrials.gov API v2 (seen 2026-11-11)
- The `AREA[Phase]PHASE2` filter also matches Phase 1/2 trials (phases list `["PHASE1","PHASE2"]`). Fixed with `AND NOT AREA[Phase]PHASE1`.
- Phase 2/3 trials are stored as a list of two phases: `["PHASE2","PHASE3"]`.
- One trial can match several condition searches: 335 of 2,747 unique trials appeared in 2+ of our 3 searches. Remove duplicates by NCT ID in clean.py.
- The full raw JSON is large: about 168 MB for 3,099 records, most of it in type 2 diabetes (two pages at the maximum page size of 1,000).
