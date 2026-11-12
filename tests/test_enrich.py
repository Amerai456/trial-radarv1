"""Tests for src/enrich.py, using a tiny made-up SEC company list.

Run with:  python -m pytest
"""

import sqlite3

import pandas as pd

from src import enrich


def make_companies():
    companies = pd.DataFrame([
        {"ticker": "LLY", "sec_name": "ELI LILLY & Co", "cik": 59478},
        {"ticker": "NVO", "sec_name": "NOVO NORDISK A S", "cik": 353278},
        {"ticker": "MRK", "sec_name": "Merck & Co., Inc.", "cik": 310158},
        {"ticker": "RYTM", "sec_name": "Rhythm Pharmaceuticals, Inc.", "cik": 1649904},
        {"ticker": "NVS", "sec_name": "NOVARTIS AG", "cik": 1114448},
        {"ticker": "SPRY", "sec_name": "ARS Pharmaceuticals, Inc.", "cik": 1671858},
    ])
    companies["clean_name"] = companies["sec_name"].map(enrich.normalize_name)
    companies["core_name"] = companies["clean_name"].map(enrich.core_name)
    return companies


# --- name cleanup ---------------------------------------------------------

def test_normalize_removes_legal_words_and_punctuation():
    assert enrich.normalize_name("Eli Lilly and Company") == "eli lilly"
    assert enrich.normalize_name("ELI LILLY & Co") == "eli lilly"
    assert enrich.normalize_name("Novo Nordisk A/S") == "novo nordisk"
    assert enrich.normalize_name("NOVO NORDISK A S") == "novo nordisk"


def test_normalize_strips_sec_state_and_adr_tags():
    assert enrich.normalize_name("VERTEX PHARMACEUTICALS INC / MA") == "vertex pharmaceuticals"
    assert enrich.normalize_name("HUDSON TECHNOLOGIES INC /NY") == "hudson technologies"
    assert enrich.normalize_name("Zealand Pharma A/S/ADR") == "zealand pharma"


def test_core_name_drops_generic_industry_words():
    assert enrich.core_name("novartis pharmaceuticals") == "novartis"
    assert enrich.core_name("xeris biopharma holdings") == "xeris"


def test_normalize_handles_blank():
    assert enrich.normalize_name(None) == ""
    assert enrich.normalize_name("") == ""


# --- matching -------------------------------------------------------------

def test_exact_match_after_cleanup():
    row = enrich.match_sponsor("Eli Lilly and Company", make_companies(), {})
    assert (row["ticker"], row["match_method"], row["cik"]) == ("LLY", "exact", 59478)


def test_alias_beats_automatic_matching():
    aliases = {"Merck Sharp & Dohme LLC": "MRK"}
    row = enrich.match_sponsor("Merck Sharp & Dohme LLC", make_companies(), aliases)
    assert (row["ticker"], row["match_method"], row["sec_name"]) == ("MRK", "alias", "Merck & Co., Inc.")


def test_blank_alias_means_known_no_listing():
    aliases = {"Boehringer Ingelheim": None}
    row = enrich.match_sponsor("Boehringer Ingelheim", make_companies(), aliases)
    assert row["ticker"] is None
    assert row["match_method"] == "alias"


def test_fuzzy_match_is_never_accepted_automatically():
    # A perfect score on the distinctive word still only goes to review.
    row = enrich.match_sponsor("Novartis Pharmaceuticals", make_companies(), {})
    assert row["match_method"] == "review"
    assert row["ticker"] is None
    assert row["candidate_ticker"] == "NVS"
    assert row["match_score"] == 100.0


def test_shared_industry_word_does_not_create_a_match():
    # Before the fix, "Novartis Pharmaceuticals" looked like "ARS Pharmaceuticals".
    row = enrich.match_sponsor("Novartis Pharmaceuticals", make_companies(), {})
    assert row["candidate_ticker"] != "SPRY"


def test_name_of_only_generic_words_has_no_match():
    row = enrich.match_sponsor("Pharmaceuticals Inc", make_companies(), {})
    assert row["match_method"] == "none"


def test_unrelated_name_has_no_match():
    row = enrich.match_sponsor("Jiangsu HengRui Medicine Co., Ltd.", make_companies(), {})
    assert row["ticker"] is None
    assert row["match_method"] == "none"


# --- alias file and database ---------------------------------------------

def test_load_aliases_reads_blank_ticker_as_none(tmp_path):
    path = tmp_path / "aliases.csv"
    path.write_text("sponsor_name,ticker,note\n"
                    "Merck Sharp & Dohme LLC,mrk,subsidiary\n"
                    "Boehringer Ingelheim,,private\n", encoding="utf-8")
    assert enrich.load_aliases(path) == {
        "Merck Sharp & Dohme LLC": "MRK", "Boehringer Ingelheim": None}


def test_save_matches_rebuilds_table(tmp_path):
    db = tmp_path / "test.db"
    matches = enrich.match_all_sponsors(
        {"Eli Lilly and Company": 5, "Unknown Startup": 1}, make_companies(), {})
    enrich.save_matches(matches, "2026-11-11", db_path=db)
    enrich.save_matches(matches, "2026-11-12", db_path=db)   # second run replaces

    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT sponsor_name, ticker, trial_count, matched_on "
                        "FROM sponsor_tickers ORDER BY trial_count DESC").fetchall()
    conn.close()
    assert rows == [("Eli Lilly and Company", "LLY", 5, "2026-11-12"),
                    ("Unknown Startup", None, 1, "2026-11-12")]
