"""Stage 3 - Store: save cleaned trials into a SQLite database.

The database (data/trials.db) has two tables:

  trials     - the LATEST version of every trial we have ever found,
               one row per trial (nct_id is the unique key).
  snapshots  - the HISTORY of key dates and status: one row per trial per
               run, stamped with the day the data was fetched. Comparing
               snapshots over time shows when sponsors push dates back.

Trials that stop matching our search are kept, not deleted; their
last_seen_date simply stops moving. (Decision logged in notes/decisions.md.)

Run it from the project folder with:
    python -m src.store
"""

import math
import sqlite3
from datetime import date
from pathlib import Path

from src.clean import build_trials_table, load_latest_raw

DB_PATH = Path("data") / "trials.db"

# SQLite has no date type, so dates are stored as TEXT like "2027-03-31".
# That format still sorts and compares correctly.
CREATE_TRIALS = """
CREATE TABLE IF NOT EXISTS trials (
    nct_id                            TEXT PRIMARY KEY,
    title                             TEXT,
    acronym                           TEXT,
    lead_sponsor                      TEXT,
    overall_status                    TEXT,
    why_stopped                       TEXT,
    phase                             TEXT,
    enrollment                        INTEGER,
    enrollment_type                   TEXT,
    conditions                        TEXT,
    interventions                     TEXT,
    start_date                        TEXT,
    start_date_raw                    TEXT,
    start_date_type                   TEXT,
    start_date_precision              TEXT,
    primary_completion_date           TEXT,
    primary_completion_date_raw       TEXT,
    primary_completion_date_type      TEXT,
    primary_completion_date_precision TEXT,
    completion_date                   TEXT,
    completion_date_raw               TEXT,
    completion_date_type              TEXT,
    completion_date_precision         TEXT,
    first_posted_date                 TEXT,
    last_update_date                  TEXT,
    results_posted_date               TEXT,
    has_results                       INTEGER,   -- 1 = yes, 0 = no
    matched_searches                  TEXT,
    first_seen_date                   TEXT,      -- first run that found this trial
    last_seen_date                    TEXT       -- most recent run that found it
)
"""

CREATE_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_date                     TEXT NOT NULL,   -- day the data was fetched
    nct_id                            TEXT NOT NULL,
    overall_status                    TEXT,
    start_date                        TEXT,
    start_date_type                   TEXT,
    start_date_precision              TEXT,
    primary_completion_date           TEXT,
    primary_completion_date_type      TEXT,
    primary_completion_date_precision TEXT,
    completion_date                   TEXT,
    completion_date_type              TEXT,
    completion_date_precision         TEXT,
    last_update_date                  TEXT,
    PRIMARY KEY (snapshot_date, nct_id)   -- one row per trial per day
)
"""

# The columns copied from the cleaned table (everything except the two
# first/last_seen columns, which store.py fills in itself).
TRIAL_COLUMNS = [
    "nct_id", "title", "acronym", "lead_sponsor", "overall_status", "why_stopped",
    "phase", "enrollment", "enrollment_type", "conditions", "interventions",
    "start_date", "start_date_raw", "start_date_type", "start_date_precision",
    "primary_completion_date", "primary_completion_date_raw",
    "primary_completion_date_type", "primary_completion_date_precision",
    "completion_date", "completion_date_raw", "completion_date_type",
    "completion_date_precision", "first_posted_date", "last_update_date",
    "results_posted_date", "has_results", "matched_searches",
]

SNAPSHOT_COLUMNS = [
    "nct_id", "overall_status",
    "start_date", "start_date_type", "start_date_precision",
    "primary_completion_date", "primary_completion_date_type",
    "primary_completion_date_precision",
    "completion_date", "completion_date_type", "completion_date_precision",
    "last_update_date",
]


def connect(db_path=DB_PATH):
    """Open (or create) the database file and make sure both tables exist."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(CREATE_TRIALS)
    conn.execute(CREATE_SNAPSHOTS)
    return conn


def to_sql_value(value):
    """Convert one pandas/Python value into something SQLite can store.

    pandas uses NaN ("not a number") for blanks; SQLite wants None (NULL).
    Dates become "YYYY-MM-DD" text. Whole-number floats like 120.0 become 120.
    """
    if value is None:
        return None
    if isinstance(value, float):
        if math.isnan(value):
            return None
        if value.is_integer():
            return int(value)
    if isinstance(value, date):
        return value.isoformat()
    if hasattr(value, "item"):   # numpy numbers/booleans -> plain Python
        return to_sql_value(value.item())
    return value


def table_rows(trials, columns):
    """Turn the pandas table into a list of tuples, in the given column order."""
    return [
        tuple(to_sql_value(record.get(col)) for col in columns)
        for record in trials.to_dict("records")
    ]


def save_trials(conn, trials, snapshot_date):
    """Insert new trials and update existing ones ("upsert").

    first_seen_date is only set when a trial is added for the first time;
    last_seen_date is set to this run's date every time the trial appears.
    Returns counts of new, updated, and missing trials.
    """
    existing = {row[0] for row in conn.execute("SELECT nct_id FROM trials")}
    current = set(trials["nct_id"])

    columns = TRIAL_COLUMNS + ["first_seen_date", "last_seen_date"]
    placeholders = ", ".join("?" for _ in columns)
    # On a repeat nct_id, overwrite every column EXCEPT first_seen_date.
    updates = ", ".join(f"{c} = excluded.{c}" for c in columns if c != "first_seen_date")
    sql = (
        f"INSERT INTO trials ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT(nct_id) DO UPDATE SET {updates}"
    )
    rows = [row + (snapshot_date, snapshot_date) for row in table_rows(trials, TRIAL_COLUMNS)]
    conn.executemany(sql, rows)

    return {
        "new": len(current - existing),
        "updated": len(current & existing),
        "missing": len(existing - current),   # in the database, not in this run (kept)
    }


def save_snapshots(conn, trials, snapshot_date):
    """Add one snapshot row per trial for this fetch date.

    INSERT OR REPLACE means re-running on the same day overwrites that day's
    rows instead of adding duplicates.
    """
    columns = ["snapshot_date"] + SNAPSHOT_COLUMNS
    placeholders = ", ".join("?" for _ in columns)
    sql = f"INSERT OR REPLACE INTO snapshots ({', '.join(columns)}) VALUES ({placeholders})"
    rows = [(snapshot_date,) + row for row in table_rows(trials, SNAPSHOT_COLUMNS)]
    conn.executemany(sql, rows)
    return len(rows)


def store(trials, snapshot_date, db_path=DB_PATH):
    """Save one run's cleaned trials into both tables, all-or-nothing."""
    conn = connect(db_path)
    try:
        with conn:   # one transaction: if anything fails, nothing is half-saved
            counts = save_trials(conn, trials, snapshot_date)
            counts["snapshot_rows"] = save_snapshots(conn, trials, snapshot_date)
    finally:
        conn.close()
    return counts


def main():
    print("Loading and cleaning raw files...")
    raw, fetch_date = load_latest_raw()
    trials = build_trials_table(raw)

    counts = store(trials, fetch_date)

    conn = connect()
    total_trials = conn.execute("SELECT COUNT(*) FROM trials").fetchone()[0]
    snapshot_days = conn.execute("SELECT COUNT(DISTINCT snapshot_date) FROM snapshots").fetchone()[0]
    conn.close()

    print(f"\nSaved snapshot for {fetch_date} to {DB_PATH}")
    print(f"  new trials:      {counts['new']}")
    print(f"  updated trials:  {counts['updated']}")
    print(f"  missing (kept):  {counts['missing']}")
    print(f"  snapshot rows:   {counts['snapshot_rows']}")
    print(f"Database now holds {total_trials} trials and {snapshot_days} snapshot day(s).")


if __name__ == "__main__":
    main()
