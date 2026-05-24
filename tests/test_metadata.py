"""
Unit tests for MusicBrainz retry / unavailable handling on the Albums tab.
These are pure unit tests; httpx is monkeypatched so no network calls happen.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _import_metadata_or_skip():
    try:
        import metadata
    except ModuleNotFoundError as exc:
        pytest.skip(f"metadata module dependencies unavailable: {exc.name}")
    return metadata


class _FakeResp:
    def __init__(self, status_code, json_body=None):
        self.status_code = status_code
        self._json = json_body or {}

    def json(self):
        return self._json


class _FakeClient:
    """Drop-in replacement for httpx.Client. Each call returns the next item
    from the shared `responses` list -- can be a _FakeResp to return, or an
    Exception to raise. Shared list because the retry helper opens a fresh
    Client per attempt; if each had its own list, retries would never advance."""

    def __init__(self, responses):
        self._responses = responses  # NB: shared reference, not copy

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, params=None, headers=None):
        if not self._responses:
            raise RuntimeError("Test ran out of canned responses")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _patch_httpx(monkeypatch, metadata, responses, sleep_calls=None):
    """Patch metadata.httpx.Client to return our fake. The retry helper opens
    a fresh Client per attempt, so all instances must share one response queue
    or each retry would see the same first response.

    Optionally capture sleep calls so we can assert backoff was applied without
    actually waiting."""
    shared = list(responses)
    monkeypatch.setattr(metadata.httpx, "Client", lambda *a, **kw: _FakeClient(shared))
    # The retry helper does a local `import time as _time`; stub its sleep so
    # tests do not actually wait 1+3 seconds.
    import time
    if sleep_calls is None:
        sleep_calls = []
    monkeypatch.setattr(time, "sleep", lambda s: sleep_calls.append(s))
    return sleep_calls


def test_retry_helper_returns_first_success(monkeypatch):
    metadata = _import_metadata_or_skip()
    sleeps = _patch_httpx(monkeypatch, metadata, [_FakeResp(200, {"artists": []})])

    resp = metadata._mb_get_with_retry(
        "https://example/", params={}, headers={}, timeout=1,
    )
    assert resp.status_code == 200
    assert sleeps == []  # No retries needed, no backoff


def test_retry_helper_retries_on_503_then_succeeds(monkeypatch):
    metadata = _import_metadata_or_skip()
    sleeps = _patch_httpx(monkeypatch, metadata, [
        _FakeResp(503), _FakeResp(200, {"ok": True}),
    ])

    resp = metadata._mb_get_with_retry(
        "https://example/", params={}, headers={}, timeout=1,
    )
    assert resp.status_code == 200
    assert sleeps == [1]  # One backoff between two attempts


def test_retry_helper_retries_on_timeout_then_succeeds(monkeypatch):
    metadata = _import_metadata_or_skip()
    import httpx
    sleeps = _patch_httpx(monkeypatch, metadata, [
        httpx.TimeoutException("slow"),
        _FakeResp(200, {"ok": True}),
    ])

    resp = metadata._mb_get_with_retry(
        "https://example/", params={}, headers={}, timeout=1,
    )
    assert resp.status_code == 200
    assert sleeps == [1]


def test_retry_helper_raises_unavailable_after_persistent_failures(monkeypatch):
    metadata = _import_metadata_or_skip()
    sleeps = _patch_httpx(monkeypatch, metadata, [
        _FakeResp(503), _FakeResp(502), _FakeResp(504),
    ])

    with pytest.raises(metadata.MusicBrainzUnavailable):
        metadata._mb_get_with_retry(
            "https://example/", params={}, headers={}, timeout=1,
        )
    # Two backoffs (after attempts 1 and 2)
    assert sleeps == [1, 3]


def test_retry_helper_does_not_swallow_unexpected_exceptions(monkeypatch):
    metadata = _import_metadata_or_skip()
    _patch_httpx(monkeypatch, metadata, [ValueError("boom")])

    with pytest.raises(ValueError):
        metadata._mb_get_with_retry(
            "https://example/", params={}, headers={}, timeout=1,
        )


def test_search_artist_propagates_unavailable(monkeypatch):
    """The Albums tab endpoint relies on this propagation to send back HTTP 503."""
    metadata = _import_metadata_or_skip()
    _patch_httpx(monkeypatch, metadata, [_FakeResp(503), _FakeResp(503), _FakeResp(503)])

    with pytest.raises(metadata.MusicBrainzUnavailable):
        metadata.search_artist_mbid("Bowie")


def test_search_artist_returns_empty_list_on_4xx(monkeypatch):
    """4xx is a definitive 'no data', not a network problem."""
    metadata = _import_metadata_or_skip()
    _patch_httpx(monkeypatch, metadata, [_FakeResp(400, {})])

    assert metadata.search_artist_mbid("???") == []


def test_fetch_album_tracks_propagates_unavailable(monkeypatch):
    metadata = _import_metadata_or_skip()
    _patch_httpx(monkeypatch, metadata, [_FakeResp(503), _FakeResp(503), _FakeResp(503)])

    with pytest.raises(metadata.MusicBrainzUnavailable):
        metadata.fetch_album_tracks("fake-mbid")
