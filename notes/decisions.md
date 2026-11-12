# Decisions log

Record important decisions and the reasoning behind them.

## 2026-11-11 - Project setup
- Python virtual environment lives in `.venv` (not committed to Git).
- Raw JSON and the SQLite database live in `data/` and are not committed: they can be re-downloaded, and they would bloat the repo.

## 2026-11-11 - Which trials to fetch (stage 1)
- **Diseases:** obesity, type 1 diabetes, type 2 diabetes. Each is searched separately with its exact term (not plain "diabetes", which also pulls in gestational and unspecified diabetes). Each trial is tagged with the term(s) it matched, and duplicates are removed by NCT ID.
- **Phases:** Phase 2, Phase 2/3 and Phase 3. Phase 1/2 trials are left out because early, small readouts rarely move stocks.
- **Sponsor:** only trials whose *lead* sponsor is industry. The lead sponsor reports the results and is the stock most likely to react.
- **Status:** all statuses. Completed trials are needed for the event study, and the catalyst calendar can filter to upcoming trials later.
- **Size check (API, 2026-11-11):** 724 obesity, 425 type 1, and 1,950 type 2 trials before removing duplicates (about 2,700 unique trials expected).

## 2026-11-11 - Month-only dates (stage 2)
- About half of start/completion dates are month-only (e.g. `2027-03`); 1,393 of 2,620 primary completion dates.
- **Decision:** a month-only date is stored as the **last day of that month** (`2027-03` -> `2027-03-31`), read as "by the end of March".
- The original text and a precision flag (`day` / `month`) are always kept next to the parsed date, so a filled-in day is never mistaken for a real one.
- The event study will only use dates with `day` precision; filling in a day would put the stock-move window on the wrong date.
