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

## 2026-11-11 - Database structure (stage 3)
- SQLite file `data/trials.db` (git-ignored) with two tables:
  - `trials`: latest version of each trial, `nct_id` as primary key, the 28 cleaned columns, plus `first_seen_date` and `last_seen_date`.
  - `snapshots`: one row per trial per fetch, with status, the three key dates (with type and precision) and the last-update date. Primary key is (`snapshot_date`, `nct_id`), so re-running on the same day replaces that day's rows instead of duplicating them.
- `snapshot_date` is the day the data was **fetched** (stored in the raw file), not the day `store.py` ran.
- Trials that stop matching the search are **kept** in `trials`. Their `last_seen_date` stops moving, so no history is lost.
- Dates are stored as `YYYY-MM-DD` text, because SQLite has no date type and this format sorts correctly.
- History starts 2026-11-11, and slippage analysis needs repeated (e.g. weekly) runs.

## 2026-11-11 - Sponsor -> ticker matching (stage 4a)
- **Markets:** US-listed only, meaning companies in the SEC's `company_tickers.json` (this includes foreign companies with US shares, called ADRs). Sponsors with only a foreign listing (most Chinese, Korean and Japanese sponsors, Bayer, Roche) and private companies (e.g. Boehringer Ingelheim) stay unmatched. Reason: the XBI benchmark is a US fund, and foreign stocks trade in other currencies and hours.
- **Subsidiaries -> parent ticker** through a hand-checked alias list, `reference/sponsor_aliases.csv` (committed to Git). A blank ticker means "checked: no US listing". Rows marked VERIFY need confirming.
- **Acquired or bankrupt companies stay unmatched** (e.g. Solvay -> Abbott, Orexigen). Their old trials can't be used in the event study.
- **Matching order:** alias list -> exact cleaned name -> fuzzy.
- **Fuzzy matches are never accepted automatically** (this replaces the original "95+ auto-accept" rule). Even when comparing only distinctive words, about 1 in 7 perfect-score fuzzy matches was a different company (Lotus Pharmaceutical -> Lotus Technology, Hudson Biotech -> Hudson Technologies). All fuzzy matches scoring 85+ go to `data/reference/matches_to_review.csv`, and approving one means adding it to the alias list.
- **Fuzzy compares distinctive words only.** Generic words like "Pharmaceuticals" and "Therapeutics" are ignored. Before this change, "Novartis Pharmaceuticals" matched "ARS Pharmaceuticals".
- **SEC contact line:** `Amerai456 Amerai456@users.noreply.github.com`.

## 2026-11-11 - Stock prices (stage 4b)
- **Source:** yfinance (unofficial Yahoo data, which can occasionally break or slow down). Covers every matched ticker plus **XBI** as the benchmark.
- **Start date:** 2006-01-01. XBI's prices begin 2006-02-06, so earlier events can't be measured against it.
- **Stored per day:** close, adjusted close (corrected for splits and dividends; use it for all returns) and volume. Table `prices`, key (ticker, date).
- **Full refresh each run:** a ticker's whole history is replaced, because Yahoo rewrites past adjusted prices after dividends and splits.
- **Market cap: today's value only** (Yahoo has no history). Table `ticker_info`, which also holds currency, exchange, first and last price dates, row count and the zero-volume share.
- **Thinly traded stocks are kept and flagged** (`zero_volume_share_2y`). The rule for excluding them from the event study will be decided in stage 5.

## 2026-11-11 - Catalyst calendar and impact score (stage 5, part 1)
- **Which trials:** open trials (recruiting, active, not yet recruiting, enrolling by invitation) whose sponsor has a US ticker, with a primary completion date in the next 12 months ("upcoming"), plus every open trial whose date has already passed ("past date" section). Trials without a ticker are left out because investors can't trade them.
- **Catalyst date** = the primary completion date, unchanged, labeled "expected primary completion". Top-line results usually follow weeks to months later; we don't guess that gap.
- **Impact score (0-100)** = 100 x (0.5 x company size + 0.3 x concentration + 0.2 x phase):
  - Company size: log scale of today's market cap; $50M or less = 1, $500B or more = 0.
  - Concentration: 1 / the company's open Phase 2/3 trials in **all** diseases (ClinicalTrials.gov lookup by lead-sponsor name), never fewer than the open trials in our own data.
  - Phase: Phase 3 = 1.0, Phase 2/3 = 0.8, Phase 2 = 0.5.
  - Unknown market cap -> no score (not a guess).
- **Warning flags** don't change the score: overdue, data collected, month-only date, thinly traded (>5% zero-volume days), no market cap.
- No new database table: the calendar is calculated on demand and saved as `data/clean/catalysts_<date>.csv` for checking.
- **Update 2026-11-12:** the "past date" section now only shows trials whose primary completion date passed within the **last 12 months**. Older ones (e.g. an AstraZeneca trial from 2016 that's still "active" for long follow-up) have almost certainly reported results already, so listing them as catalysts would mislead. The section went from 23 to 13 trials.

## 2026-11-12 - Event study (stage 5, part 2)
- **Event date (day 0)** = a trial's ACTUAL primary completion date, exact-day precision only (next trading day if it falls on a weekend or holiday). The ClinicalTrials.gov results-posted date was rejected: it comes a median 434 days after primary completion, long after companies announce top-line results by press release. So this measures "stock behavior around trial completion", not the exact announcement day.
- **Windows:** short = trading days -1 to +1; post = trading days 0 to +60 (about 3 months, when top-line results usually come out).
- **Method: market model vs XBI.** Alpha and beta are estimated over trading days -250 to -30, needing at least 120 usable days. AR = stock return - (alpha + beta x XBI return); CAR = sum of ARs. Simple daily returns from adjusted close.
- **Standardized CAR** = CAR / (normal daily wiggle x square root of window length). |SCAR| > 1.96 counts as "significant"; if milestones didn't matter, about 5% of events would be significant by chance.
- **Which events:** completed, terminated and active-not-recruiting trials. Withdrawn, suspended and unknown-status trials are excluded. Trials of one company finishing on the same day = one event (highest phase is used; "terminated" only if all of them were).
- **Exclusions are kept in the output with a reason:** too little price history, missing prices in the window, thinly traded (more than 10% zero-volume days in the estimation + event windows), no price data, window not finished yet.
- **Main measure = size of the move (|CAR|)**, because we don't know if results were good or bad, so up and down moves cancel in the average.
- **Company size at the time** = today's market cap x (price on day 0 / latest price), to avoid lookahead bias. It ignores shares issued since. Size groups: small < $2B, mid $2-20B, large > $20B.
- **Impact score back-test:** the score is recalculated as of each event (size then, phase, and the company's trials in our data running that day as the concentration stand-in) and compared with |CAR| by rank (Spearman) correlation, done by hand to avoid adding scipy.
- No new database table: results are saved to `data/clean/event_study_<date>.csv` and `event_study_summary_<date>.csv`.
- **Known limitations:** survivorship bias (only companies still listed today are matched); one company's events can overlap in time (Novo has 177), which makes t-statistics look stronger than they are; CAR adds simple returns, so very big moves can exceed -100%.

## 2026-11-12 - Date slippage (stage 5, part 3)
- **Data source: our own weekly snapshots only.** ClinicalTrials.gov's change-history page blocks automated requests (403) and isn't part of the official API, so past date changes can't be downloaded. History starts with the first snapshot (2026-11-11); the first moves can be seen after the second run.
- **A move** = a change in the primary completion date between two consecutive snapshots, in days (positive = later). Status changes and "finished" (estimated -> actual) are listed too.
- **Not a move:** a change within the same month when either date is month-only (e.g. "March 2027" -> "March 15, 2027"), because month-only dates are stored as the last day of the month.
- **Two comparisons:** previous snapshot -> "what changed this week" list; first snapshot -> each trial's total slip and each sponsor's reliability record. Final slip = the actual date minus the last estimate, when a trial finishes.
- **Comparable trial** = seen in 2+ snapshots and its date was still an estimate when first seen (a finished trial can't slip).
- **Sponsor reliability** (one row per ticker, or per sponsor name without a ticker): share of comparable trials delayed and median delay, shown only with at least **3** comparable trials; overdue-now count (open, estimated date already passed) always shown.
- **Not part of the impact score for now.** Shown as a separate table; whether it should feed the score will be tested once months of history exist.
- No new database table: calculated from `snapshots` and saved as `data/clean/slippage_{changes,trials,sponsors}_<date>.csv`.
