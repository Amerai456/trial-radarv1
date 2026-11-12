"""Tests for src/clean.py, using small hand-made trial records.

Run with:  python -m pytest
"""

from datetime import date

from src import clean


def make_study(nct_id, phases=("PHASE3",), primary_completion=None):
    """A tiny fake trial with just the fields clean.py reads."""
    status = {"overallStatus": "RECRUITING"}
    if primary_completion:
        status["primaryCompletionDateStruct"] = {"date": primary_completion, "type": "ESTIMATED"}
    return {
        "protocolSection": {
            "identificationModule": {"nctId": nct_id, "briefTitle": "Test trial"},
            "statusModule": status,
            "sponsorCollaboratorsModule": {"leadSponsor": {"name": "Acme Bio"}},
            "designModule": {"phases": list(phases)},
        },
        "hasResults": False,
    }


# --- dates ---------------------------------------------------------------

def test_full_date_has_day_precision():
    assert clean.parse_partial_date("2027-03-15") == (date(2027, 3, 15), "day")


def test_month_only_date_becomes_last_day_of_month():
    assert clean.parse_partial_date("2027-03") == (date(2027, 3, 31), "month")
    assert clean.parse_partial_date("2027-04") == (date(2027, 4, 30), "month")


def test_month_only_february_handles_leap_years():
    assert clean.parse_partial_date("2028-02") == (date(2028, 2, 29), "month")
    assert clean.parse_partial_date("2027-02") == (date(2027, 2, 28), "month")


def test_missing_or_bad_date_gives_none():
    assert clean.parse_partial_date(None) == (None, None)
    assert clean.parse_partial_date("") == (None, None)
    assert clean.parse_partial_date("2027-13") == (None, None)


# --- flattening ----------------------------------------------------------

def test_flatten_keeps_raw_text_and_precision():
    row = clean.flatten_study(make_study("NCT1", primary_completion="2027-03"))
    assert row["primary_completion_date"] == date(2027, 3, 31)
    assert row["primary_completion_date_raw"] == "2027-03"
    assert row["primary_completion_date_type"] == "ESTIMATED"
    assert row["primary_completion_date_precision"] == "month"


def test_flatten_survives_missing_fields():
    row = clean.flatten_study({"protocolSection": {"identificationModule": {"nctId": "NCT2"}}})
    assert row["nct_id"] == "NCT2"
    assert row["lead_sponsor"] is None
    assert row["phase"] is None
    assert row["start_date"] is None
    assert row["has_results"] is False


def test_multi_phase_trial_is_joined():
    row = clean.flatten_study(make_study("NCT3", phases=("PHASE2", "PHASE3")))
    assert row["phase"] == "PHASE2/PHASE3"


# --- duplicates ----------------------------------------------------------

def test_trial_found_by_two_searches_appears_once():
    raw = {
        "obesity": [make_study("NCT1"), make_study("NCT2")],
        "type2_diabetes": [make_study("NCT2"), make_study("NCT3")],
    }
    table = clean.build_trials_table(raw)
    assert list(table["nct_id"]) == ["NCT1", "NCT2", "NCT3"]
    matched = dict(zip(table["nct_id"], table["matched_searches"]))
    assert matched["NCT1"] == "obesity"
    assert matched["NCT2"] == "obesity; type2_diabetes"
    assert matched["NCT3"] == "type2_diabetes"
