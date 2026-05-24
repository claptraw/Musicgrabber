"""
Bulk import tests.

Fast tests verify the API contract (create, list, status shape).
Slow tests submit real tracks and verify they get searched and queued,
but do NOT wait for downloads to complete.
"""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _import_bulk_import_or_skip():
    try:
        import bulk_import
    except ModuleNotFoundError as exc:
        pytest.skip(f"bulk_import module dependencies unavailable: {exc.name}")
    return bulk_import


# ---------------------------------------------------------------------------
# Unit tests for the priority-source boost (no live server needed)
# ---------------------------------------------------------------------------

def test_priority_boost_lets_soulseek_win_close_call():
    bulk_import = _import_bulk_import_or_skip()
    results = [
        {"source": "youtube",  "quality_score": 120, "title": "yt"},
        {"source": "soulseek", "quality_score": 100, "title": "slsk"},
    ]
    out = bulk_import.apply_priority_source_boost(results, "soulseek")
    assert out[0]["source"] == "soulseek", \
        "Boosted source should overtake a higher-scoring non-priority result"
    assert out[0]["quality_score"] == 100 + bulk_import.PRIORITY_SOURCE_BOOST


def test_priority_boost_noop_when_priority_source_is_empty():
    bulk_import = _import_bulk_import_or_skip()
    results = [
        {"source": "youtube",  "quality_score": 120},
        {"source": "soulseek", "quality_score": 100},
    ]
    out = bulk_import.apply_priority_source_boost(list(results), None)
    assert out[0]["source"] == "youtube"
    out = bulk_import.apply_priority_source_boost(list(results), "")
    assert out[0]["source"] == "youtube"


def test_priority_boost_does_not_invent_results():
    """If the priority source returned nothing, the next best wins."""
    bulk_import = _import_bulk_import_or_skip()
    results = [
        {"source": "youtube",  "quality_score": 120},
        {"source": "mp3phoenix", "quality_score": 80},
    ]
    out = bulk_import.apply_priority_source_boost(results, "soulseek")
    assert out[0]["source"] == "youtube", \
        "Boost should not magic up a Soulseek result that does not exist"


def test_priority_boost_is_case_insensitive():
    bulk_import = _import_bulk_import_or_skip()
    results = [
        {"source": "YouTube", "quality_score": 120},
        {"source": "Soulseek", "quality_score": 100},
    ]
    out = bulk_import.apply_priority_source_boost(results, "SOULSEEK")
    assert out[0]["source"] == "Soulseek"


# Well-known tracks that should always be searchable
_TEST_TRACKS = "\n".join([
    "Radiohead - Creep",
    "Nirvana - Come As You Are",
    "The Beatles - Let It Be",
])


def test_bulk_imports_list(api, base_url):
    r = api.get(f"{base_url}/api/bulk-imports", timeout=10)
    assert r.status_code == 200
    d = r.json()
    assert "imports" in d
    assert isinstance(d["imports"], list)


def test_bulk_imports_list_shape(api, base_url):
    imports = api.get(f"{base_url}/api/bulk-imports", timeout=10).json()["imports"]
    for imp in imports[:3]:
        for key in ("id", "status", "total_tracks", "searched", "queued", "failed"):
            assert key in imp, f"bulk import missing key: {key}"


def test_bulk_import_create_returns_id(api, base_url):
    """Verify POST returns an import_id and success flag immediately."""
    r = api.post(
        f"{base_url}/api/bulk-import-async",
        json={"songs": "Test Artist - Test Song", "convert_to_flac": True},
        timeout=15,
    )
    assert r.status_code == 200
    d = r.json()
    assert "import_id" in d, f"no import_id in response: {d}"
    import_id = d["import_id"]
    assert import_id  # not None or empty
    assert d.get("status") in ("pending", "processing", "completed")

    # Status endpoint should work immediately
    s = api.get(f"{base_url}/api/bulk-import/{import_id}/status", timeout=10)
    assert s.status_code == 200

    # Clean up the jobs created by this import
    api.delete(f"{base_url}/api/jobs/cleanup", timeout=10)


def test_bulk_import_status_shape(api, base_url):
    """Status response should contain all expected fields."""
    r = api.post(
        f"{base_url}/api/bulk-import-async",
        json={"songs": "Test Artist - Test Song", "convert_to_flac": True},
        timeout=15,
    )
    import_id = r.json()["import_id"]

    s = api.get(f"{base_url}/api/bulk-import/{import_id}/status", timeout=10).json()
    for key in ("import_id", "status", "total_tracks", "searched", "queued", "completed", "failed"):
        assert key in s, f"status response missing key: {key}"

    assert s["total_tracks"] == 1, f"expected 1 track, got {s['total_tracks']}"

    api.delete(f"{base_url}/api/jobs/cleanup", timeout=10)


def test_bulk_import_multiline_track_count(api, base_url):
    """total_tracks should match the number of submitted lines."""
    songs = "Radiohead - Creep\nNirvana - Come As You Are\nThe Beatles - Let It Be"
    r = api.post(
        f"{base_url}/api/bulk-import-async",
        json={"songs": songs, "convert_to_flac": True},
        timeout=15,
    )
    assert r.status_code == 200
    import_id = r.json()["import_id"]

    s = api.get(f"{base_url}/api/bulk-import/{import_id}/status", timeout=10).json()
    assert s["total_tracks"] == 3, (
        f"expected 3 tracks for 3 input lines, got {s['total_tracks']}"
    )

    api.delete(f"{base_url}/api/jobs/cleanup", timeout=10)


def test_bulk_import_bad_format_graceful(api, base_url):
    """Blank lines and malformed entries shouldn't crash the import."""
    songs = "\n\nRadiohead - Creep\n\nThis line has no dash\n\n"
    r = api.post(
        f"{base_url}/api/bulk-import-async",
        json={"songs": songs},
        timeout=15,
    )
    assert r.status_code == 200
    api.delete(f"{base_url}/api/jobs/cleanup", timeout=10)


def test_bulk_import_status_404_unknown_id(api, base_url):
    r = api.get(f"{base_url}/api/bulk-import/doesnotexist/status", timeout=10)
    assert r.status_code == 404


@pytest.mark.slow
def test_bulk_import_tracks_get_searched(api, base_url):
    """
    Submit 3 well-known tracks, wait up to 60s, verify all were searched and
    at least some were queued. Does NOT wait for downloads to complete.
    """
    r = api.post(
        f"{base_url}/api/bulk-import-async",
        json={"songs": _TEST_TRACKS, "convert_to_flac": True},
        timeout=15,
    )
    assert r.status_code == 200
    import_id = r.json()["import_id"]

    # Poll until the search phase is done (status = "completed" or "error")
    # The search worker runs at 1 track/sec, so 3 tracks = ~3-5s; give 60s headroom
    deadline = time.time() + 60
    status = {}
    while time.time() < deadline:
        time.sleep(3)
        status = api.get(f"{base_url}/api/bulk-import/{import_id}/status", timeout=10).json()
        if status.get("status") in ("completed", "error"):
            break

    assert status.get("searched", 0) == 3, (
        f"expected all 3 tracks to be searched, got searched={status.get('searched')}"
    )
    assert status.get("failed", 0) < 3, (
        f"all 3 tracks failed to search - something is wrong: {status}"
    )
    # Some tracks should have been queued for download
    queued_or_completed = (status.get("queued", 0) or 0) + (status.get("completed", 0) or 0)
    assert queued_or_completed >= 1, (
        f"no tracks were queued or completed: {status}"
    )

    # Clean up jobs without waiting for them to finish
    api.delete(f"{base_url}/api/jobs/cleanup", timeout=10)
