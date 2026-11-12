"""Stage 1 - Fetch: download trials from the ClinicalTrials.gov API v2.

For each disease we care about, ask the API for industry-sponsored
Phase 2 / 2-3 / 3 trials, follow every page of results, and save the raw
JSON to data/raw/. Nothing is cleaned or de-duplicated here; that is
clean.py's job. Keeping an untouched copy of what the API sent means we can
always re-run the later stages without downloading again.

Run it from the project folder with:
    python -m src.fetch
"""

import json
import time
from datetime import date
from pathlib import Path

import requests

API_URL = "https://clinicaltrials.gov/api/v2/studies"

# The diseases to search. The key is a short name used in file names;
# the value is the exact search term sent to the API.
# (Decision logged in notes/decisions.md, 2026-11-11.)
CONDITIONS = {
    "obesity": "obesity",
    "type1_diabetes": "type 1 diabetes",
    "type2_diabetes": "type 2 diabetes",
}

# Filters written in the API's "Essie" search language:
#  - Phase 2 or Phase 3 (this includes Phase 2/3 trials),
#  - but NOT any trial that is also Phase 1 (removes Phase 1/2 trials),
#  - and the LEAD sponsor must be a company (class INDUSTRY).
TRIAL_FILTER = (
    "AREA[Phase](PHASE2 OR PHASE3) "
    "AND NOT AREA[Phase]PHASE1 "
    "AND AREA[LeadSponsorClass]INDUSTRY"
)

PAGE_SIZE = 1000        # the most trials the API will return per page
TIMEOUT_SECONDS = 30    # give up on a single request after this long
MAX_RETRIES = 3         # how many extra tries after a failed request
PAUSE_SECONDS = 0.5     # polite pause between requests

RAW_DIR = Path("data") / "raw"


def build_params(search_term, page_token=None):
    """Build the query settings (URL parameters) for one API request."""
    params = {
        "query.cond": search_term,
        "filter.advanced": TRIAL_FILTER,
        "pageSize": PAGE_SIZE,
        "countTotal": "true",   # ask the API to tell us the total number
    }
    if page_token:
        params["pageToken"] = page_token
    return params


def get_json_with_retries(params):
    """Send one request to the API and return the JSON answer.

    If the request fails for a temporary reason (network hiccup, timeout,
    server busy), wait and try again: 2 seconds, then 4, then 8.
    A permanent error (like a typo in our filter, HTTP 400) fails right away,
    because retrying would just fail the same way.
    """
    for attempt in range(MAX_RETRIES + 1):
        try:
            response = requests.get(API_URL, params=params, timeout=TIMEOUT_SECONDS)
            # 429 = "too many requests", 5xx = server-side problem: temporary.
            temporary_problem = response.status_code == 429 or response.status_code >= 500
            problem = f"HTTP {response.status_code}"
        except (requests.ConnectionError, requests.Timeout) as error:
            temporary_problem = True
            problem = type(error).__name__

        if not temporary_problem:
            response.raise_for_status()   # any other error code: stop now
            return response.json()

        if attempt == MAX_RETRIES:
            raise RuntimeError(f"API request failed after {MAX_RETRIES + 1} tries ({problem})")
        wait = 2 ** (attempt + 1)
        print(f"  request failed ({problem}); retrying in {wait}s...")
        time.sleep(wait)


def fetch_all_pages(search_term, get_page=get_json_with_retries):
    """Download every page of results for one search term.

    The API sends at most PAGE_SIZE trials at a time. If there are more,
    the answer includes a "nextPageToken"; we send that back to get the next
    page, and stop when there is no token. Returns (studies, total_count).

    `get_page` is the function that does the actual request. Tests swap in
    a fake one so they can run without the internet.
    """
    studies = []
    total_count = None
    page_token = None
    page_number = 0

    while True:
        page_number += 1
        data = get_page(build_params(search_term, page_token))
        if total_count is None:
            total_count = data.get("totalCount")
        page_studies = data.get("studies", [])
        studies.extend(page_studies)
        print(f"  page {page_number}: {len(page_studies)} trials (running total {len(studies)})")

        page_token = data.get("nextPageToken")
        if not page_token:
            break
        time.sleep(PAUSE_SECONDS)

    return studies, total_count


def save_raw(name, search_term, studies, total_count, run_date):
    """Save one disease's trials to data/raw/<name>_<date>.json."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / f"{name}_{run_date}.json"
    record = {
        "condition_name": name,
        "search_term": search_term,
        "filter": TRIAL_FILTER,
        "fetched_on": run_date,
        "api_total_count": total_count,
        "studies": studies,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f)
    return path


def main():
    run_date = date.today().isoformat()
    summary = []

    for name, search_term in CONDITIONS.items():
        print(f"Fetching '{search_term}'...")
        studies, total_count = fetch_all_pages(search_term)
        path = save_raw(name, search_term, studies, total_count, run_date)
        summary.append((search_term, len(studies), total_count, path))
        time.sleep(PAUSE_SECONDS)

    print("\nSummary")
    for search_term, n_downloaded, total_count, path in summary:
        check = "OK" if n_downloaded == total_count else "MISMATCH - check this!"
        print(f"  {search_term:18} downloaded {n_downloaded:5}  API total {total_count}  {check}  -> {path}")


if __name__ == "__main__":
    main()
