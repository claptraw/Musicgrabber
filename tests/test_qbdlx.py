"""Unit tests for the qbdlx direct-Qobuz fallback. No network: the token fetch
and the signed Qobuz call are both monkeypatched."""

import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import qbdlx


_TOKEN = {"token": "T", "app_id": "798273057", "app_secret": "SEC", "country": "GB"}


def setup_function():
    qbdlx._token_cache = []
    qbdlx._token_cache_at = 0.0
    qbdlx._good_token = None  # otherwise one test's favourite skews the next


def test_resolve_returns_none_when_disabled(monkeypatch):
    monkeypatch.setattr(qbdlx, "qbdlx_enabled", lambda: False)
    assert qbdlx.resolve_qobuz_stream_url("GBAYE9200070", 6) is None


def test_resolve_returns_none_on_empty_isrc(monkeypatch):
    monkeypatch.setattr(qbdlx, "qbdlx_enabled", lambda: True)
    assert qbdlx.resolve_qobuz_stream_url("", 6) is None


def test_resolve_returns_none_when_pool_empty(monkeypatch):
    monkeypatch.setattr(qbdlx, "qbdlx_enabled", lambda: True)
    monkeypatch.setattr(qbdlx, "_fetch_shared_tokens", lambda force=False: [])
    assert qbdlx.resolve_qobuz_stream_url("GBAYE9200070", 6) is None


def test_resolve_signs_and_returns_url(monkeypatch):
    monkeypatch.setattr(qbdlx, "qbdlx_enabled", lambda: True)
    monkeypatch.setattr(qbdlx, "_fetch_shared_tokens", lambda force=False: [_TOKEN])

    calls = []

    def fake_signed_call(token, path, params, signed_concat=None):
        calls.append((path, params, signed_concat))
        if path == "catalog/search":
            return {"tracks": {"items": [{"id": 33933680, "isrc": "GBAYE9200070"}]}}
        if path == "track/getFileUrl":
            # The signed concat must match the verified scheme exactly.
            assert signed_concat == "trackgetFileUrlformat_id6intentstreamtrack_id33933680"
            return {"url": "https://streaming-qobuz-std.akamaized.net/file?x=1", "format_id": 6}
        return None

    monkeypatch.setattr(qbdlx, "_signed_call", fake_signed_call)

    url = qbdlx.resolve_qobuz_stream_url("GBAYE9200070", 6)
    assert url == "https://streaming-qobuz-std.akamaized.net/file?x=1"
    assert [c[0] for c in calls] == ["catalog/search", "track/getFileUrl"]


def test_resolve_falls_through_to_next_token(monkeypatch):
    monkeypatch.setattr(qbdlx, "qbdlx_enabled", lambda: True)
    bad = {"token": "B", "app_id": "1", "app_secret": "x", "country": "FR"}
    monkeypatch.setattr(qbdlx, "_fetch_shared_tokens", lambda force=False: [bad, _TOKEN])

    def fake_signed_call(token, path, params, signed_concat=None):
        if token["country"] == "FR":
            return None  # first token is a dud all round
        if path == "catalog/search":
            return {"tracks": {"items": [{"id": 1, "isrc": "GBAYE9200070"}]}}
        if path == "track/getFileUrl":
            return {"url": "https://cdn/ok.flac", "format_id": 6}
        return None

    monkeypatch.setattr(qbdlx, "_signed_call", fake_signed_call)
    assert qbdlx.resolve_qobuz_stream_url("GBAYE9200070", 6) == "https://cdn/ok.flac"


def test_resolve_skips_tokens_downgraded_to_a_sample(monkeypatch):
    monkeypatch.setattr(qbdlx, "qbdlx_enabled", lambda: True)
    sample_token = {"token": "S", "app_id": "1", "app_secret": "x", "country": "US"}
    monkeypatch.setattr(qbdlx, "_fetch_shared_tokens", lambda force=False: [sample_token, _TOKEN])

    def fake_signed_call(token, path, params, signed_concat=None):
        if path == "catalog/search":
            return {"tracks": {"items": [{"id": 33933680, "isrc": "GBAYE9200070"}]}}
        if path == "track/getFileUrl":
            if token["country"] == "US":
                # Downgraded entitlement: Qobuz hands back a 30-second preview
                # regardless of the requested format_id.
                return {"url": "https://cdn/preview.mp3", "format_id": 5, "sample": True}
            return {"url": "https://cdn/real.flac", "format_id": 6}
        return None

    monkeypatch.setattr(qbdlx, "_signed_call", fake_signed_call)
    assert qbdlx.resolve_qobuz_stream_url("GBAYE9200070", 6) == "https://cdn/real.flac"


def test_resolve_returns_none_when_every_token_only_offers_a_sample(monkeypatch):
    monkeypatch.setattr(qbdlx, "qbdlx_enabled", lambda: True)
    monkeypatch.setattr(qbdlx, "_fetch_shared_tokens", lambda force=False: [_TOKEN])

    def fake_signed_call(token, path, params, signed_concat=None):
        if path == "catalog/search":
            return {"tracks": {"items": [{"id": 33933680, "isrc": "GBAYE9200070"}]}}
        if path == "track/getFileUrl":
            return {"url": "https://cdn/preview.mp3", "format_id": 5, "sample": True}
        return None

    monkeypatch.setattr(qbdlx, "_signed_call", fake_signed_call)
    assert qbdlx.resolve_qobuz_stream_url("GBAYE9200070", 6) is None


def test_resolve_remembers_the_token_that_worked(monkeypatch):
    """The pool is mostly duds, so the winner goes to the front of the queue."""
    monkeypatch.setattr(qbdlx, "qbdlx_enabled", lambda: True)
    dud = {"token": "DUD", "app_id": "1", "app_secret": "x", "country": "US"}
    good = {"token": "GOOD", "app_id": "2", "app_secret": "y", "country": "FR"}
    monkeypatch.setattr(qbdlx, "_fetch_shared_tokens", lambda force=False: [dud, good])

    tried = []

    def fake_signed_call(token, path, params, signed_concat=None):
        if path == "catalog/search":
            tried.append(token["token"])
            return {"tracks": {"items": [{"id": 33933680, "isrc": "GBAYE9200070"}]}}
        if path == "track/getFileUrl":
            if token["token"] == "DUD":
                return {"url": "https://cdn/preview.mp3", "format_id": 5, "sample": True}
            return {"url": "https://cdn/real.flac", "format_id": 6}
        return None

    monkeypatch.setattr(qbdlx, "_signed_call", fake_signed_call)

    assert qbdlx.resolve_qobuz_stream_url("GBAYE9200070", 6) == "https://cdn/real.flac"
    assert tried == ["DUD", "GOOD"]  # cold start walks the pool

    tried.clear()
    assert qbdlx.resolve_qobuz_stream_url("GBAYE9200070", 6) == "https://cdn/real.flac"
    assert tried == ["GOOD"]  # second time we go straight to the winner


def test_resolve_falls_back_when_remembered_token_goes_stale(monkeypatch):
    """Tokens rot; a stale favourite must cost one attempt, not the whole result."""
    monkeypatch.setattr(qbdlx, "qbdlx_enabled", lambda: True)
    first = {"token": "FIRST", "app_id": "1", "app_secret": "x", "country": "FR"}
    second = {"token": "SECOND", "app_id": "2", "app_secret": "y", "country": "NL"}
    monkeypatch.setattr(qbdlx, "_fetch_shared_tokens", lambda force=False: [first, second])

    downgraded = {"yes": False}
    tried = []

    def fake_signed_call(token, path, params, signed_concat=None):
        if path == "catalog/search":
            tried.append(token["token"])
            return {"tracks": {"items": [{"id": 33933680, "isrc": "GBAYE9200070"}]}}
        if path == "track/getFileUrl":
            if token["token"] == "FIRST" and downgraded["yes"]:
                return {"url": "https://cdn/preview.mp3", "format_id": 5, "sample": True}
            if token["token"] == "FIRST":
                return {"url": "https://cdn/first.flac", "format_id": 6}
            return {"url": "https://cdn/second.flac", "format_id": 6}
        return None

    monkeypatch.setattr(qbdlx, "_signed_call", fake_signed_call)

    assert qbdlx.resolve_qobuz_stream_url("GBAYE9200070", 6) == "https://cdn/first.flac"
    assert qbdlx._good_token == "FIRST"

    downgraded["yes"] = True  # Qobuz quietly pulls FIRST's entitlement
    tried.clear()
    assert qbdlx.resolve_qobuz_stream_url("GBAYE9200070", 6) == "https://cdn/second.flac"
    assert tried == ["FIRST", "SECOND"]
    assert qbdlx._good_token == "SECOND"  # favourite moves on


def test_tokens_best_first_survives_a_rotated_pool(monkeypatch):
    """The upstream pool rotates; a favourite that's gone must not lose the rest."""
    qbdlx._good_token = "VANISHED"
    pool = [{"token": "A"}, {"token": "B"}]
    assert qbdlx._tokens_best_first(pool) == pool


def test_resolve_prefers_exact_isrc_match(monkeypatch):
    monkeypatch.setattr(qbdlx, "qbdlx_enabled", lambda: True)
    monkeypatch.setattr(qbdlx, "_fetch_shared_tokens", lambda force=False: [_TOKEN])

    captured = {}

    def fake_signed_call(token, path, params, signed_concat=None):
        if path == "catalog/search":
            return {"tracks": {"items": [
                {"id": 111, "isrc": "WRONGISRC0001"},
                {"id": 222, "isrc": "GBAYE9200070"},
            ]}}
        if path == "track/getFileUrl":
            captured["track_id"] = params["track_id"]
            return {"url": "https://cdn/match.flac", "format_id": 6}
        return None

    monkeypatch.setattr(qbdlx, "_signed_call", fake_signed_call)
    qbdlx.resolve_qobuz_stream_url("GBAYE9200070", 6)
    assert captured["track_id"] == 222


def test_resolve_qobuz_track_id_returns_first_working_token_match(monkeypatch):
    monkeypatch.setattr(qbdlx, "qbdlx_enabled", lambda: True)
    monkeypatch.setattr(qbdlx, "_fetch_shared_tokens", lambda force=False: [_TOKEN])

    def fake_signed_call(token, path, params, signed_concat=None):
        assert path == "catalog/search"
        return {"tracks": {"items": [
            {"id": 111, "isrc": "WRONGISRC0001"},
            {"id": 222, "isrc": "GBAYE9200070"},
        ]}}

    monkeypatch.setattr(qbdlx, "_signed_call", fake_signed_call)

    assert qbdlx.resolve_qobuz_track_id("GBAYE9200070") == 222
