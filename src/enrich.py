"""Stage 4 - Enrich: match sponsors to US stock tickers, then get stock prices.

Part A (matching) and Part B (prices) both run with:
    python -m src.enrich

PART A - match each trial sponsor to a US stock ticker.

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
PART B - download daily stock prices for every matched ticker plus the XBI
biotech fund (the benchmark), from 2006 on, using yfinance. Each run
replaces each ticker's whole price history, because Yahoo rewrites old
adjusted prices after dividends and splits. Also records today's market cap
and how often each stock has days with no trading at all.

(Decisions logged in notes/decisions.md, 2026-11-11.)
"""

import csv
import json
import re
import time
import warnings
from datetime import date
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf
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


# =========================================================================
# PART B - stock prices
# =========================================================================

BENCHMARK = "XBI"            # SPDR S&P Biotech ETF; its prices start Feb 2006
PRICE_START = "2006-01-01"   # earlier prices can't be compared with XBI
BATCH_SIZE = 20              # tickers per yfinance request
PRICE_PAUSE_SECONDS = 2      # polite pause between batches
INFO_PAUSE_SECONDS = 0.3     # pause between market-cap lookups

CREATE_PRICES = """
CREATE TABLE IF NOT EXISTS prices (
    ticker     TEXT NOT NULL,
    date       TEXT NOT NULL,     -- trading day, "YYYY-MM-DD"
    close      REAL,              -- the price as quoted that day
    adj_close  REAL,              -- corrected for splits and dividends: use for returns
    volume     INTEGER,           -- shares traded that day (0 = no trading)
    PRIMARY KEY (ticker, date)
)
"""

CREATE_TICKER_INFO = """
CREATE TABLE IF NOT EXISTS ticker_info (
    ticker                TEXT PRIMARY KEY,
    market_cap            REAL,      -- today's value only (Yahoo has no history)
    currency              TEXT,
    exchange              TEXT,      -- e.g. NYQ = NYSE, NMS = Nasdaq, PNK = over-the-counter
    first_price_date      TEXT,
    last_price_date       TEXT,
    price_rows            INTEGER,   -- 0 means the download failed
    zero_volume_share_2y  REAL,      -- share of the last 2 years with no trading (0-1)
    updated_on            TEXT
)
"""


def download_batch(tickers):
    """Ask yfinance for daily prices of several tickers at once.

    auto_adjust=False keeps BOTH the quoted close and the adjusted close.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return yf.download(
            tickers, start=PRICE_START, auto_adjust=False, group_by="ticker",
            progress=False, threads=False,
        )


def split_download(data, tickers):
    """Turn yfinance's combined table into {ticker: that ticker's own table}.

    The combined table has every ticker side by side on the same dates, so a
    stock that started trading in 2017 has blank rows before 2017. We drop
    those blanks. A ticker that failed comes back empty.
    """
    result = {}
    for ticker in tickers:
        if data is None or data.empty:
            result[ticker] = pd.DataFrame()
            continue
        try:
            if isinstance(data.columns, pd.MultiIndex):
                frame = data[ticker]
            else:
                frame = data   # only one ticker was downloaded
            frame = frame[["Close", "Adj Close", "Volume"]].dropna(subset=["Adj Close"])
        except KeyError:
            frame = pd.DataFrame()
        result[ticker] = frame
    return result


def zero_volume_share(frame, as_of, years=2):
    """Share of trading days in the last `years` years with zero volume.

    A stock that rarely trades shows the same price day after day, which
    looks like a "0% move" and would fake a "no reaction" in the event study.
    """
    if frame.empty:
        return None
    cutoff = pd.Timestamp(as_of) - pd.DateOffset(years=years)
    recent = frame[frame.index >= cutoff]
    if recent.empty:
        return None
    return round(float((recent["Volume"].fillna(0) == 0).mean()), 4)


def price_rows(ticker, frame):
    """Turn one ticker's table into database rows."""
    rows = []
    for day, values in frame.iterrows():
        volume = values["Volume"]
        rows.append((
            ticker,
            day.date().isoformat(),
            float(values["Close"]) if pd.notna(values["Close"]) else None,
            float(values["Adj Close"]),
            int(volume) if pd.notna(volume) else None,
        ))
    return rows


def fetch_market_info(ticker):
    """Today's market cap, currency and exchange. Blanks if Yahoo has none."""
    try:
        info = yf.Ticker(ticker).fast_info
        market_cap = info.get("marketCap")
        return {
            "market_cap": float(market_cap) if market_cap else None,
            "currency": info.get("currency"),
            "exchange": info.get("exchange"),
        }
    except Exception:   # yfinance raises many different errors for missing data
        return {"market_cap": None, "currency": None, "exchange": None}


def download_all_prices(tickers, download=download_batch):
    """Download every ticker in batches; retry the failed ones once.

    Returns {ticker: price table}; failed tickers have an empty table.
    `download` can be swapped for a fake one in tests.
    """
    frames = {}
    batches = [tickers[i:i + BATCH_SIZE] for i in range(0, len(tickers), BATCH_SIZE)]
    for number, batch in enumerate(batches, start=1):
        print(f"  price batch {number} of {len(batches)} ({len(batch)} tickers)...")
        frames.update(split_download(download(batch), batch))
        time.sleep(PRICE_PAUSE_SECONDS)

    failed = [t for t, f in frames.items() if f.empty]
    if failed:
        print(f"  retrying {len(failed)} failed ticker(s) one at a time...")
        for ticker in failed:
            time.sleep(PRICE_PAUSE_SECONDS)
            frames.update(split_download(download([ticker]), [ticker]))
    return frames


def save_prices(frames, infos, run_date, db_path=DB_PATH):
    """Replace each ticker's price history and info row, all in one transaction."""
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(CREATE_PRICES)
            conn.execute(CREATE_TICKER_INFO)
            for ticker, frame in frames.items():
                if not frame.empty:
                    # Full refresh: old adjusted prices may have been rewritten.
                    conn.execute("DELETE FROM prices WHERE ticker = ?", (ticker,))
                    conn.executemany(
                        "INSERT INTO prices (ticker, date, close, adj_close, volume) "
                        "VALUES (?, ?, ?, ?, ?)", price_rows(ticker, frame))
                info = infos.get(ticker, {})
                conn.execute(
                    "INSERT OR REPLACE INTO ticker_info (ticker, market_cap, currency, "
                    "exchange, first_price_date, last_price_date, price_rows, "
                    "zero_volume_share_2y, updated_on) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        ticker, info.get("market_cap"), info.get("currency"),
                        info.get("exchange"),
                        frame.index.min().date().isoformat() if not frame.empty else None,
                        frame.index.max().date().isoformat() if not frame.empty else None,
                        len(frame), zero_volume_share(frame, run_date), run_date,
                    ),
                )
    finally:
        conn.close()


def update_prices(tickers, run_date, db_path=DB_PATH,
                  download=download_batch, get_info=fetch_market_info):
    """Part B from start to finish. Returns the list of tickers that failed."""
    frames = download_all_prices(tickers, download=download)
    print("  looking up market caps...")
    infos = {}
    for ticker in tickers:
        infos[ticker] = get_info(ticker)
        time.sleep(INFO_PAUSE_SECONDS)
    save_prices(frames, infos, run_date, db_path=db_path)
    return [t for t, f in frames.items() if f.empty]


def match_step(run_date):
    """Part A from start to finish (sponsor -> ticker matching)."""
    companies = load_sec_companies()
    aliases = load_aliases()
    print(f"SEC list: {len(companies)} companies | alias list: {len(aliases)} sponsors")

    conn = connect()
    sponsor_counts = dict(conn.execute(
        "SELECT lead_sponsor, COUNT(*) FROM trials "
        "WHERE lead_sponsor IS NOT NULL GROUP BY lead_sponsor").fetchall())
    conn.close()

    matches = match_all_sponsors(sponsor_counts, companies, aliases)
    save_matches(matches, run_date)
    n_review = write_review_file(matches)

    with_ticker = matches["ticker"].notna()
    print(f"\n{len(matches)} sponsors matched:")
    print(matches["match_method"].value_counts().to_string())
    print(f"\nSponsors with a ticker: {with_ticker.sum()} of {len(matches)}")
    print(f"Trials covered by a ticker: {matches.loc[with_ticker, 'trial_count'].sum()} "
          f"of {matches['trial_count'].sum()}")
    print(f"{n_review} possible matches to check in {REVIEW_FILE}")
    return sorted(matches["ticker"].dropna().unique())


def main():
    run_date = date.today().isoformat()

    print("PART A: matching sponsors to tickers")
    tickers = match_step(run_date)

    all_tickers = [BENCHMARK] + [t for t in tickers if t != BENCHMARK]
    print(f"\nPART B: downloading prices for {len(all_tickers)} tickers "
          f"(including {BENCHMARK}) from {PRICE_START}")
    failed = update_prices(all_tickers, run_date)

    conn = connect()
    n_rows = conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
    thin = conn.execute(
        "SELECT ticker, zero_volume_share_2y FROM ticker_info "
        "WHERE zero_volume_share_2y > 0.05 ORDER BY zero_volume_share_2y DESC").fetchall()
    conn.close()

    print(f"\nPrice rows in database: {n_rows}")
    print(f"Tickers with no price data: {failed if failed else 'none'}")
    print("Thinly traded (no trading on more than 5% of days in the last 2 years):")
    for ticker, share in thin:
        print(f"  {ticker:6} {share:.0%}")


if __name__ == "__main__":
    main()
