"""Stage 4a - Enrich: match each trial sponsor to a US stock ticker.

Sources:
  - The SEC's official list of US-listed companies and their tickers
    (includes foreign companies with US shares, called ADRs, such as Novo Nordisk).
  - reference/sponsor_aliases.csv: a hand-checked list for names that
    automatic matching can't handle (subsidiaries like "Merck Sharp & Dohme
    LLC" -> MRK, or companies known to have no US listing).

Each sponsor is matched in three tries, most reliable first:
  1. alias  - the sponsor is in the hand-checked alias list
  2. exact  - the cleaned-up names are identical
  3. review - a rapidfuzz similarity score of 85+ on the DISTINCTIVE words
     ("Novartis", not "Pharmaceuticals"). These are never accepted
     automatically: they go to a review file and stay unmatched until a
     human approves them by adding them to the alias list.

Results go into the sponsor_tickers table in data/trials.db, and the
borderline ones into data/reference/matches_to_review.csv.
(Decisions logged in notes/decisions.md, 2026-11-11.)

Run it from the project folder with:
    python -m src.enrich
"""

import csv
import json
import re
from datetime import date
from pathlib import Path

import pandas as pd
import requests
from rapidfuzz import fuzz, process

from src.store import DB_PATH, connect

SEC_URL = "https://www.sec.gov/files/company_tickers.json"
# The SEC requires automated downloads to say who is asking (name + email).
SEC_USER_AGENT = "Amerai456 Amerai456@users.noreply.github.com"

REFERENCE_DATA_DIR = Path("data") / "reference"        # downloaded files (git-ignored)
SEC_FILE = REFERENCE_DATA_DIR / "company_tickers.json"
REVIEW_FILE = REFERENCE_DATA_DIR / "matches_to_review.csv"
ALIASES_FILE = Path("reference") / "sponsor_aliases.csv"  # hand-checked (in Git)

REVIEW_SCORE = 85   # fuzzy score 85+: goes to the review file; below 85: no match
# (No fuzzy match is ever accepted automatically. In testing, about 1 in 7
#  perfect-score fuzzy matches was a different company, e.g. "Lotus
#  Pharmaceutical" -> Lotus Technology. Decision logged 2026-11-11.)

# Legal-form words that say nothing about WHICH company it is.
# "Eli Lilly and Company" and "ELI LILLY & Co" should look the same.
LEGAL_WORDS = {
    "inc", "incorporated", "corp", "corporation", "co", "company", "ltd",
    "limited", "llc", "lp", "plc", "sa", "ag", "as", "nv", "bv", "gmbh",
    "kk", "spa", "se", "de", "cv", "pty", "the", "and",
}

# Industry words shared by thousands of companies. Fuzzy matching ignores
# them, otherwise "Hanmi Pharmaceutical" looks like "ANI Pharmaceuticals".
GENERIC_WORDS = {
    "pharmaceutical", "pharmaceuticals", "pharma", "pharm", "therapeutics",
    "therapeutic", "biosciences", "bioscience", "biotechnology", "biotech",
    "biopharmaceuticals", "biopharmaceutical", "biopharma", "biologics", "bio",
    "laboratories", "labs", "medicine", "medical", "sciences", "science",
    "research", "development", "holdings", "holding", "group", "international",
    "technologies", "technology", "industries", "usa", "us", "global",
    "healthcare", "health",
}

CREATE_SPONSOR_TICKERS = """
CREATE TABLE IF NOT EXISTS sponsor_tickers (
    sponsor_name      TEXT PRIMARY KEY,  -- exactly as written in the trials table
    ticker            TEXT,              -- NULL when there is no accepted match
    sec_name          TEXT,              -- the SEC company name it matched
    cik               INTEGER,           -- SEC company ID number
    match_method      TEXT,              -- alias / exact / review / none
    match_score       REAL,              -- similarity 0-100 (100 for alias/exact)
    candidate_ticker  TEXT,              -- best guess for 'review' rows, for checking
    candidate_name    TEXT,
    trial_count       INTEGER,           -- how many trials this sponsor leads
    matched_on        TEXT               -- date this matching was run
)
"""


# --- loading the reference lists -----------------------------------------

def download_sec_file():
    """Download the SEC ticker list and save it to data/reference/."""
    response = requests.get(
        SEC_URL,
        headers={"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"},
        timeout=30,
    )
    if response.status_code == 403:
        raise SystemExit(
            "The SEC blocked the automatic download (HTTP 403).\n"
            f"Open {SEC_URL} in your web browser, save the page as\n"
            f"{SEC_FILE}, and run this again."
        )
    response.raise_for_status()
    REFERENCE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    SEC_FILE.write_bytes(response.content)


def load_sec_companies():
    """Return a table of US-listed companies: ticker, sec_name, cik, clean_name.

    Uses the saved copy in data/reference/ if there is one; downloads otherwise.
    Some companies have several tickers (e.g. share classes); we keep the
    first one the SEC lists for each company.
    """
    if not SEC_FILE.exists():
        print("Downloading the SEC ticker list...")
        download_sec_file()
    with open(SEC_FILE, encoding="utf-8") as f:
        data = json.load(f)

    companies = pd.DataFrame(list(data.values())).rename(
        columns={"cik_str": "cik", "title": "sec_name"})
    companies = companies.drop_duplicates(subset="cik", keep="first")
    companies["clean_name"] = companies["sec_name"].map(normalize_name)
    companies["core_name"] = companies["clean_name"].map(core_name)
    return companies[["ticker", "sec_name", "cik", "clean_name", "core_name"]].reset_index(drop=True)


def load_aliases(path=ALIASES_FILE):
    """Read the hand-checked alias list into {sponsor_name: ticker or None}.

    A blank ticker means "checked: this company has no US stock listing".
    """
    aliases = {}
    if not Path(path).exists():
        return aliases
    with open(path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            name = (row.get("sponsor_name") or "").strip()
            if name:
                aliases[name] = (row.get("ticker") or "").strip().upper() or None
    return aliases


# --- matching -------------------------------------------------------------

def normalize_name(name):
    """Clean a company name so different spellings can be compared.

    "Eli Lilly and Company"   -> "eli lilly"
    "NOVO NORDISK A/S"        -> "novo nordisk"
    "Merck Sharp & Dohme LLC" -> "merck sharp dohme"
    "VERTEX PHARMACEUTICALS INC / MA" -> "vertex pharmaceuticals"
    """
    if not name:
        return ""
    text = name.lower().replace("&", " and ")
    # The SEC adds a state or "ADR" tag after a slash: "/ MA", "/DE/", "/ADR".
    text = re.sub(r"\s*/\s*[a-z]{2,3}\s*/?\s*$", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)   # punctuation -> spaces ("a/s" -> "a s")
    text = re.sub(r"\ba s\b", "as", text)     # Danish "A/S" form
    words = [w for w in text.split() if w not in LEGAL_WORDS]
    return " ".join(words)


def core_name(clean_name):
    """Keep only the distinctive words: "novartis pharmaceuticals" -> "novartis"."""
    return " ".join(w for w in clean_name.split() if w not in GENERIC_WORDS)


def match_sponsor(sponsor, companies, aliases):
    """Find the ticker for one sponsor name. Returns a dictionary (one row)."""
    result = {
        "sponsor_name": sponsor, "ticker": None, "sec_name": None, "cik": None,
        "match_method": "none", "match_score": None,
        "candidate_ticker": None, "candidate_name": None,
    }

    def fill_from(company_row):
        result["ticker"] = company_row["ticker"]
        result["sec_name"] = company_row["sec_name"]
        result["cik"] = int(company_row["cik"])

    # 1. Hand-checked alias list.
    if sponsor in aliases:
        result["match_method"] = "alias"
        result["match_score"] = 100.0
        ticker = aliases[sponsor]
        if ticker:
            found = companies[companies["ticker"] == ticker]
            if found.empty:
                print(f"  warning: alias ticker {ticker} for '{sponsor}' is not in the SEC list")
                result["ticker"] = ticker
            else:
                fill_from(found.iloc[0])
        return result   # a blank alias ticker means "known: no US listing"

    clean = normalize_name(sponsor)
    if not clean:
        return result

    # 2. Exact match on cleaned names.
    exact = companies[companies["clean_name"] == clean]
    if not exact.empty:
        fill_from(exact.iloc[0])
        result["match_method"] = "exact"
        result["match_score"] = 100.0
        return result

    # 3. Fuzzy match on the distinctive words only. Never accepted
    #    automatically: a good score just puts it on the review list.
    core = core_name(clean)
    if not core:
        return result   # name was nothing but generic words
    best = process.extractOne(core, companies["core_name"], scorer=fuzz.ratio)
    if best is None:
        return result
    _, score, position = best
    result["match_score"] = round(score, 1)
    if score >= REVIEW_SCORE:
        company = companies.iloc[position]
        result["match_method"] = "review"
        result["candidate_ticker"] = company["ticker"]
        result["candidate_name"] = company["sec_name"]
    return result


def match_all_sponsors(sponsor_counts, companies, aliases):
    """Match every sponsor. sponsor_counts = {sponsor_name: number of trials}."""
    rows = []
    for sponsor, count in sponsor_counts.items():
        row = match_sponsor(sponsor, companies, aliases)
        row["trial_count"] = count
        rows.append(row)
    table = pd.DataFrame(rows)
    return table.sort_values("trial_count", ascending=False).reset_index(drop=True)


# --- saving ---------------------------------------------------------------

def save_matches(matches, run_date, db_path=DB_PATH):
    """Replace the sponsor_tickers table with this run's matches.

    The table is fully rebuilt each run (not updated), because it is worked
    out entirely from the trials table, the SEC list and the alias file.
    """
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(CREATE_SPONSOR_TICKERS)
            conn.execute("DELETE FROM sponsor_tickers")
            columns = ["sponsor_name", "ticker", "sec_name", "cik", "match_method",
                       "match_score", "candidate_ticker", "candidate_name",
                       "trial_count", "matched_on"]
            rows = []
            for record in matches.to_dict("records"):
                record["matched_on"] = run_date
                rows.append(tuple(None if pd.isna(record.get(c)) else record.get(c)
                                  for c in columns))
            conn.executemany(
                f"INSERT INTO sponsor_tickers ({', '.join(columns)}) "
                f"VALUES ({', '.join('?' for _ in columns)})", rows)
    finally:
        conn.close()


def write_review_file(matches):
    """Save the possible (fuzzy, score 85+) matches for a human to check."""
    review = matches[matches["match_method"] == "review"][
        ["sponsor_name", "trial_count", "candidate_ticker", "candidate_name", "match_score"]]
    REFERENCE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    review.to_csv(REVIEW_FILE, index=False)
    return len(review)


def main():
    companies = load_sec_companies()
    aliases = load_aliases()
    print(f"SEC list: {len(companies)} companies | alias list: {len(aliases)} sponsors")

    conn = connect()
    sponsor_counts = dict(conn.execute(
        "SELECT lead_sponsor, COUNT(*) FROM trials "
        "WHERE lead_sponsor IS NOT NULL GROUP BY lead_sponsor").fetchall())
    conn.close()

    matches = match_all_sponsors(sponsor_counts, companies, aliases)
    save_matches(matches, date.today().isoformat())
    n_review = write_review_file(matches)

    with_ticker = matches["ticker"].notna()
    print(f"\n{len(matches)} sponsors matched:")
    print(matches["match_method"].value_counts().to_string())
    print(f"\nSponsors with a ticker: {with_ticker.sum()} of {len(matches)}")
    print(f"Trials covered by a ticker: {matches.loc[with_ticker, 'trial_count'].sum()} "
          f"of {matches['trial_count'].sum()}")
    print(f"{n_review} borderline matches to check in {REVIEW_FILE}")


if __name__ == "__main__":
    main()
