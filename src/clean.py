"""Stage 2 - Clean: turn the raw API JSON into one flat row per trial.

Reads the newest raw file for each disease in data/raw/, pulls out the
fields we need, removes duplicate trials (a trial can match more than one
disease search), and saves a CSV to data/clean/ for checking in Excel.

Run it from the project folder with:
    python -m src.clean
"""

import calendar
import json
from datetime import date
from pathlib import Path

import pandas as pd

from src.fetch import CONDITIONS, RAW_DIR

CLEAN_DIR = Path("data") / "clean"


def parse_partial_date(text):
    """Turn an API date string into (date, precision).

    "2027-03-15" -> (2027-03-15, "day")
    "2027-03"    -> (2027-03-31, "month")   last day of the month
                    (decision logged in notes/decisions.md, 2026-11-11)
    missing or unrecognised -> (None, None)
    """
    if not text:
        return None, None
    parts = text.split("-")
    try:
        if len(parts) == 3:
            return date(int(parts[0]), int(parts[1]), int(parts[2])), "day"
        if len(parts) == 2:
            year, month = int(parts[0]), int(parts[1])
            last_day = calendar.monthrange(year, month)[1]   # 28, 29, 30 or 31
            return date(year, month, last_day), "month"
    except ValueError:
        pass   # not a real date (e.g. month 13): treat it like missing
    return None, None


def date_columns(prefix, date_struct):
    """Make the 4 columns for one key date, e.g. primary_completion_*.

    The API gives a small dictionary like {"date": "2027-03", "type": "ESTIMATED"}.
    """
    date_struct = date_struct or {}
    raw_text = date_struct.get("date")
    parsed, precision = parse_partial_date(raw_text)
    return {
        f"{prefix}_date": parsed,
        f"{prefix}_date_raw": raw_text,
        f"{prefix}_date_type": date_struct.get("type"),        # ACTUAL / ESTIMATED / None
        f"{prefix}_date_precision": precision,                 # day / month / None
    }


def join_list(items):
    """Join a list of text into one value, e.g. ["A", "B"] -> "A; B"."""
    items = [item for item in (items or []) if item]
    return "; ".join(items) if items else None


def flatten_study(study):
    """Turn one trial's nested JSON into a flat dictionary (one row).

    Every field is read with .get(), so a missing section or field gives
    None (a blank cell) instead of crashing.
    """
    protocol = study.get("protocolSection", {})
    ident = protocol.get("identificationModule", {})
    status = protocol.get("statusModule", {})
    sponsors = protocol.get("sponsorCollaboratorsModule", {})
    design = protocol.get("designModule", {})
    conditions = protocol.get("conditionsModule", {})
    arms = protocol.get("armsInterventionsModule", {})
    enrollment = design.get("enrollmentInfo", {})

    row = {
        "nct_id": ident.get("nctId"),
        "title": ident.get("briefTitle"),
        "acronym": ident.get("acronym"),
        "lead_sponsor": sponsors.get("leadSponsor", {}).get("name"),
        "overall_status": status.get("overallStatus"),
        "why_stopped": status.get("whyStopped"),
        # ["PHASE2", "PHASE3"] becomes "PHASE2/PHASE3"
        "phase": "/".join(design.get("phases", [])) or None,
        "enrollment": enrollment.get("count"),
        "enrollment_type": enrollment.get("type"),
        "conditions": join_list(conditions.get("conditions")),
        "interventions": join_list(
            [i.get("name") for i in arms.get("interventions", [])]
        ),
    }
    row.update(date_columns("start", status.get("startDateStruct")))
    row.update(date_columns("primary_completion", status.get("primaryCompletionDateStruct")))
    row.update(date_columns("completion", status.get("completionDateStruct")))

    # These dates are set by ClinicalTrials.gov itself and are always full days.
    row["first_posted_date"] = parse_partial_date(
        status.get("studyFirstPostDateStruct", {}).get("date"))[0]
    row["last_update_date"] = parse_partial_date(
        status.get("lastUpdatePostDateStruct", {}).get("date"))[0]
    row["results_posted_date"] = parse_partial_date(
        status.get("resultsFirstPostDateStruct", {}).get("date"))[0]
    row["has_results"] = bool(study.get("hasResults", False))
    return row


def load_latest_raw():
    """Read the newest raw file for each disease.

    Returns (raw, fetch_date):
      raw        = {condition_name: list of studies}
      fetch_date = the day that data was downloaded, e.g. "2026-11-11"

    File names end in the fetch date (e.g. obesity_2026-11-11.json), so
    sorting the names puts the newest one last.
    """
    raw = {}
    fetch_dates = set()
    for name in CONDITIONS:
        files = sorted(RAW_DIR.glob(f"{name}_*.json"))
        if not files:
            print(f"  warning: no raw file for {name}; run python -m src.fetch first")
            continue
        with open(files[-1], encoding="utf-8") as f:
            record = json.load(f)
        raw[name] = record["studies"]
        fetch_dates.add(record["fetched_on"])
        print(f"  {name}: {len(raw[name])} trials from {files[-1].name}")

    if not raw:
        raise SystemExit("No raw data found. Run: python -m src.fetch")
    if len(fetch_dates) > 1:
        print(f"  warning: raw files come from different days {sorted(fetch_dates)}; "
              "re-run python -m src.fetch so they match")
    return raw, max(fetch_dates)


def build_trials_table(raw_by_condition):
    """Flatten every trial and remove duplicates, keeping track of which
    disease search(es) found each trial in the matched_searches column."""
    rows = {}       # nct_id -> row
    matched = {}    # nct_id -> list of condition names

    for name, studies in raw_by_condition.items():
        for study in studies:
            row = flatten_study(study)
            nct_id = row["nct_id"]
            if not nct_id:
                continue   # can't identify it, so skip it
            if nct_id not in rows:
                rows[nct_id] = row
                matched[nct_id] = []
            if name not in matched[nct_id]:
                matched[nct_id].append(name)

    for nct_id, row in rows.items():
        row["matched_searches"] = "; ".join(matched[nct_id])

    table = pd.DataFrame(list(rows.values()))
    if not table.empty:
        table = table.sort_values("nct_id").reset_index(drop=True)
    return table


def main():
    print("Loading raw files...")
    raw, fetch_date = load_latest_raw()
    trials = build_trials_table(raw)

    CLEAN_DIR.mkdir(parents=True, exist_ok=True)
    path = CLEAN_DIR / f"trials_{fetch_date}.csv"   # named after the fetch day
    trials.to_csv(path, index=False)

    total_raw = sum(len(studies) for studies in raw.values())
    print(f"\n{total_raw} raw records -> {len(trials)} unique trials")
    print(f"Saved to {path}")
    print("\nPrimary completion date precision:")
    print(trials["primary_completion_date_precision"].value_counts(dropna=False).to_string())


if __name__ == "__main__":
    main()
