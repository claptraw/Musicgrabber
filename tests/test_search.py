"""
Search endpoint tests. All tests here hit real external services so they're
marked 'slow'. Run the fast suite with: pytest -m "not slow"
"""

import pytest


RESULT_KEYS = ["video_id", "title", "quality_score", "source"]


@pytest.mark.slow
def test_search_returns_results(api, base_url):
    r = api.post(
        f"{base_url}/api/search",
        json={"query": "Bohemian Rhapsody Queen", "limit": 5, "source": "youtube"},
        timeout=30,
    )
    assert r.status_code == 200
    results = r.json().get("results", [])
    assert len(results) >= 1, "YouTube search returned no results"


@pytest.mark.slow
def test_search_result_shape(api, base_url):
    r = api.post(
        f"{base_url}/api/search",
        json={"query": "Bohemian Rhapsody Queen", "limit": 3, "source": "youtube"},
        timeout=30,
    )
    assert r.status_code == 200
    results = r.json().get("results", [])
    assert results, "no results returned"
    for item in results:
        for key in RESULT_KEYS:
            assert key in item, f"search result missing key: {key}"


@pytest.mark.slow
def test_search_all_sources(api, base_url):
    """Multi-source search should return results from at least one source."""
    r = api.post(
        f"{base_url}/api/search",
        json={"query": "Nirvana Come As You Are", "limit": 10, "source": "all"},
        timeout=45,
    )
    assert r.status_code == 200
    results = r.json().get("results", [])
    assert len(results) >= 1


@pytest.mark.slow
def test_search_scores_are_numeric(api, base_url):
    r = api.post(
        f"{base_url}/api/search",
        json={"query": "Radiohead Creep", "limit": 5, "source": "youtube"},
        timeout=30,
    )
    results = r.json().get("results", [])
    for item in results:
        assert isinstance(item["quality_score"], (int, float)), (
            f"quality_score is not numeric: {item['quality_score']}"
        )


def test_search_empty_query_rejected(api, base_url):
    r = api.post(
        f"{base_url}/api/search",
        json={"query": "", "limit": 5},
        timeout=10,
    )
    assert r.status_code == 422  # Pydantic min_length=1 validation error


def test_search_invalid_body(api, base_url):
    r = api.post(f"{base_url}/api/search", json={}, timeout=10)
    assert r.status_code == 422


@pytest.mark.slow
def test_search_response_includes_unavailable_sources(api, base_url):
    r = api.post(
        f"{base_url}/api/search",
        json={"query": "shape check", "limit": 1, "source": "all"},
        timeout=30,
    )
    assert r.status_code == 200
    d = r.json()
    assert "unavailable_sources" in d
    assert isinstance(d["unavailable_sources"], list)
