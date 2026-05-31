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
