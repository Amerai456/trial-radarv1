"""Tests for src/fetch.py. These use fake API answers, so no internet is needed.

Run with:  python -m pytest
"""

from src import fetch


def test_build_params_first_page_has_no_token():
    params = fetch.build_params("obesity")
    assert params["query.cond"] == "obesity"
    assert params["filter.advanced"] == fetch.TRIAL_FILTER
    assert "pageToken" not in params


def test_build_params_includes_token_when_given():
    params = fetch.build_params("obesity", page_token="abc")
    assert params["pageToken"] == "abc"


def test_fetch_all_pages_follows_tokens_until_last_page(monkeypatch):
    # Don't actually pause during the test.
    monkeypatch.setattr(fetch.time, "sleep", lambda seconds: None)

    # Three fake pages. The first two point to the next page; the last has no token.
    fake_pages = {
        None: {"totalCount": 5, "studies": [{"id": 1}, {"id": 2}], "nextPageToken": "p2"},
        "p2": {"studies": [{"id": 3}, {"id": 4}], "nextPageToken": "p3"},
        "p3": {"studies": [{"id": 5}]},
    }
    tokens_requested = []

    def fake_get_page(params):
        token = params.get("pageToken")
        tokens_requested.append(token)
        return fake_pages[token]

    studies, total = fetch.fetch_all_pages("obesity", get_page=fake_get_page)

    assert tokens_requested == [None, "p2", "p3"]
    assert [s["id"] for s in studies] == [1, 2, 3, 4, 5]
    assert total == 5


def test_fetch_all_pages_handles_empty_result(monkeypatch):
    monkeypatch.setattr(fetch.time, "sleep", lambda seconds: None)
    studies, total = fetch.fetch_all_pages(
        "nothing", get_page=lambda params: {"totalCount": 0, "studies": []}
    )
    assert studies == []
    assert total == 0
