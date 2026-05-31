import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_qobuz_search_response(track_id=33933680, isrc="GBAYE9200070"):
    """Minimal /api/get-music response that passes the ISRC filter."""
    return {
        "success": True,
        "data": {
            "tracks": {
                "items": [{"id": track_id, "isrc": isrc}]
            }
        },
    }


def _make_qobuz_download_response(url="https://streaming-qobuz-std.akamaized.net/test.flac"):
    return {"success": True, "data": {"url": url}}


class _FakeHTTPResponse:
    """Pretend httpx.Response for proxy tests."""
    def __init__(self, json_body, status_code=200):
        self._body = json_body
        self.status_code = status_code

    @property
    def is_success(self):
        return self.status_code < 400

    def raise_for_status(self):
        if self.status_code >= 400:
            req = httpx.Request("GET", "https://example.test")
            resp = httpx.Response(self.status_code, request=req)
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=req, response=resp
            )

    def json(self):
        return self._body


# ---------------------------------------------------------------------------
# Qobuz proxy URL ordering
# ---------------------------------------------------------------------------

def test_qobuz_proxy_urls_returns_all_defaults(monkeypatch):
    import monochrome
    monkeypatch.setattr(monochrome, "_qobuz_probe_last_run", time.time())  # suppress probe
    monkeypatch.setattr(monochrome, "_qobuz_proxy_url_cache", None)
    monkeypatch.setattr(monochrome, "_qobuz_proxy_failures", {})
    monkeypatch.setattr("settings.get_setting", lambda key, default="", **kw: default)

    urls = monochrome._qobuz_proxy_urls()
    assert "https://qobuz.kennyy.com.br" in urls
    assert "https://mono.scavengerfurs.net" in urls
    assert "https://qdl-api.monochrome.tf" in urls


def test_qobuz_proxy_urls_puts_cached_first(monkeypatch):
    import monochrome
    monkeypatch.setattr(monochrome, "_qobuz_probe_last_run", time.time())
    monkeypatch.setattr(monochrome, "_qobuz_proxy_url_cache", "https://mono.scavengerfurs.net")
    monkeypatch.setattr(monochrome, "_qobuz_proxy_failures", {})
    monkeypatch.setattr("settings.get_setting", lambda key, default="", **kw: default)

    urls = monochrome._qobuz_proxy_urls()
    assert urls[0] == "https://mono.scavengerfurs.net"


def test_qobuz_proxy_urls_deprioritises_recently_failed(monkeypatch):
    import monochrome
    monkeypatch.setattr(monochrome, "_qobuz_probe_last_run", time.time())
    monkeypatch.setattr(monochrome, "_qobuz_proxy_url_cache", None)
    monkeypatch.setattr(monochrome, "_qobuz_proxy_failures",
                        {"https://qdl-api.monochrome.tf": time.time()})
    monkeypatch.setattr("settings.get_setting", lambda key, default="", **kw: default)

    urls = monochrome._qobuz_proxy_urls()
    assert urls[-1] == "https://qdl-api.monochrome.tf"


def test_mark_qobuz_proxy_failed_invalidates_cache(monkeypatch):
    import monochrome
    failures = {}
    monkeypatch.setattr(monochrome, "_qobuz_proxy_failures", failures)
    monkeypatch.setattr(monochrome, "_qobuz_proxy_url_cache", "https://qdl-api.monochrome.tf")

    monochrome._mark_qobuz_proxy_failed("https://qdl-api.monochrome.tf")

    assert monochrome._qobuz_proxy_url_cache is None
    assert "https://qdl-api.monochrome.tf" in failures


def test_remember_qobuz_proxy_clears_failure(monkeypatch):
    import monochrome
    failures = {"https://qobuz.kennyy.com.br": time.time() - 10}
    monkeypatch.setattr(monochrome, "_qobuz_proxy_failures", failures)

    monochrome._remember_qobuz_proxy_url("https://qobuz.kennyy.com.br")

    assert "https://qobuz.kennyy.com.br" not in failures
    assert monochrome._qobuz_proxy_url_cache == "https://qobuz.kennyy.com.br"


# ---------------------------------------------------------------------------
# _get_qobuz_stream_url fallback chain
# ---------------------------------------------------------------------------

def test_get_qobuz_stream_url_uses_first_healthy_proxy(monkeypatch):
    import monochrome

    calls = []
    monkeypatch.setattr(monochrome, "_qobuz_proxy_url_cache", None)
    monkeypatch.setattr(monochrome, "_qobuz_proxy_failures", {})
    monkeypatch.setattr(monochrome, "_qobuz_probe_last_run", time.time())
    monkeypatch.setattr("settings.get_setting", lambda key, default="", **kw: default)

    def fake_get(url, params, headers, timeout):
        calls.append(url)
        if "api/get-music" in url:
            return _FakeHTTPResponse(_make_qobuz_search_response())
        return _FakeHTTPResponse(_make_qobuz_download_response())

    monkeypatch.setattr(monochrome.httpx, "get", fake_get)

    cdn_url = monochrome._get_qobuz_stream_url("GBAYE9200070", 6)

    assert cdn_url == "https://streaming-qobuz-std.akamaized.net/test.flac"
    # Should have used the first proxy (kennyy) and not tried others
    assert all("kennyy" in u for u in calls)


def test_get_qobuz_stream_url_falls_back_on_http_error(monkeypatch):
    import monochrome

    failures = {}
    monkeypatch.setattr(monochrome, "_qobuz_proxy_url_cache", None)
    monkeypatch.setattr(monochrome, "_qobuz_proxy_failures", failures)
    monkeypatch.setattr(monochrome, "_qobuz_probe_last_run", time.time())
    monkeypatch.setattr("settings.get_setting", lambda key, default="", **kw: default)

    calls = []

    def fake_get(url, params, headers, timeout):
        calls.append(url)
        if "kennyy" in url:
            return _FakeHTTPResponse({}, status_code=400)
        if "api/get-music" in url:
            return _FakeHTTPResponse(_make_qobuz_search_response())
        return _FakeHTTPResponse(_make_qobuz_download_response())

    monkeypatch.setattr(monochrome.httpx, "get", fake_get)

    cdn_url = monochrome._get_qobuz_stream_url("GBAYE9200070", 6)

    assert cdn_url == "https://streaming-qobuz-std.akamaized.net/test.flac"
    # kennyy got blacklisted
    assert "https://qobuz.kennyy.com.br" in failures
    # scavengerfurs (second) returned the URL
    assert monochrome._qobuz_proxy_url_cache == "https://mono.scavengerfurs.net"


def test_get_qobuz_stream_url_raises_when_all_fail(monkeypatch):
    import monochrome

    monkeypatch.setattr(monochrome, "_qobuz_proxy_url_cache", None)
    monkeypatch.setattr(monochrome, "_qobuz_proxy_failures", {})
    monkeypatch.setattr(monochrome, "_qobuz_probe_last_run", time.time())
    monkeypatch.setattr("settings.get_setting", lambda key, default="", **kw: default)

    def fake_get(url, params, headers, timeout):
        return _FakeHTTPResponse({}, status_code=401)

    monkeypatch.setattr(monochrome.httpx, "get", fake_get)

    with pytest.raises(RuntimeError, match="all instances failed"):
        monochrome._get_qobuz_stream_url("GBAYE9200070", 6)


def test_get_qobuz_stream_url_connection_error_does_not_blacklist(monkeypatch):
    import monochrome

    failures = {}
    monkeypatch.setattr(monochrome, "_qobuz_proxy_url_cache", None)
    monkeypatch.setattr(monochrome, "_qobuz_proxy_failures", failures)
    monkeypatch.setattr(monochrome, "_qobuz_probe_last_run", time.time())
    monkeypatch.setattr("settings.get_setting", lambda key, default="", **kw: default)

    def fake_get(url, params, headers, timeout):
        if "kennyy" in url:
            raise httpx.ConnectError("connection refused")
        if "api/get-music" in url:
            return _FakeHTTPResponse(_make_qobuz_search_response())
        return _FakeHTTPResponse(_make_qobuz_download_response())

    monkeypatch.setattr(monochrome.httpx, "get", fake_get)

    cdn_url = monochrome._get_qobuz_stream_url("GBAYE9200070", 6)

    assert cdn_url  # scavengerfurs succeeded
    # Connection error must NOT blacklist kennyy
    assert "https://qobuz.kennyy.com.br" not in failures


def test_monochrome_preview_uses_qbdlx_when_proxies_fail(monkeypatch):
    import monochrome

    def fake_proxy(isrc, quality_fmt):
        raise monochrome.QobuzProxyError("all proxies down", transport_failure=True)

    calls = []

    def fake_qbdlx(isrc, quality_fmt):
        calls.append((isrc, quality_fmt))
        return "https://streaming-qobuz-std.akamaized.net/qbdlx-preview.flac"

    monkeypatch.setattr(monochrome, "_get_qobuz_stream_url", fake_proxy)
    monkeypatch.setattr("qbdlx.resolve_qobuz_stream_url", fake_qbdlx)

    url = monochrome.get_monochrome_preview_url("GBAYE9200070")

    assert url == "https://streaming-qobuz-std.akamaized.net/qbdlx-preview.flac"
    assert calls == [("GBAYE9200070", 7)]


def test_monochrome_preview_tries_qbdlx_format_6_after_7_fails(monkeypatch):
    import monochrome

    def fake_proxy(isrc, quality_fmt):
        raise monochrome.QobuzProxyError("all proxies down", transport_failure=True)

    calls = []

    def fake_qbdlx(isrc, quality_fmt):
        calls.append((isrc, quality_fmt))
        if quality_fmt == 7:
            return None
        return "https://streaming-qobuz-std.akamaized.net/qbdlx-format-6.flac"

    monkeypatch.setattr(monochrome, "_get_qobuz_stream_url", fake_proxy)
    monkeypatch.setattr("qbdlx.resolve_qobuz_stream_url", fake_qbdlx)

    url = monochrome.get_monochrome_preview_url("GBAYE9200070")

    assert url == "https://streaming-qobuz-std.akamaized.net/qbdlx-format-6.flac"
    assert calls == [("GBAYE9200070", 7), ("GBAYE9200070", 6)]


def test_monochrome_preview_raises_when_proxy_and_qbdlx_fail(monkeypatch):
    import monochrome

    def fake_proxy(isrc, quality_fmt):
        raise monochrome.QobuzProxyError("all proxies down", transport_failure=True)

    monkeypatch.setattr(monochrome, "_get_qobuz_stream_url", fake_proxy)
    monkeypatch.setattr("qbdlx.resolve_qobuz_stream_url", lambda isrc, quality_fmt: None)

    with pytest.raises(RuntimeError, match="all proxies down"):
        monochrome.get_monochrome_preview_url("GBAYE9200070")


def test_download_monochrome_raises_when_proxy_and_qbdlx_fail(monkeypatch, tmp_path):
    """With Lucida gone, qbdlx is the last resort; if it can't resolve either, the
    download fails cleanly rather than hanging or half-writing."""
    import monochrome

    def fake_proxy(isrc, quality_fmt):
        raise monochrome.QobuzProxyError("all proxies down", transport_failure=True)

    monkeypatch.setattr(monochrome, "_get_qobuz_stream_url", fake_proxy)
    monkeypatch.setattr(monochrome, "MONOCHROME_PROXY_RETRY_ROUNDS", 1)
    monkeypatch.setattr("qbdlx.resolve_qobuz_stream_url", lambda isrc, quality_fmt: None)

    output = tmp_path / "track.flac"
    with pytest.raises(RuntimeError, match="no Qobuz stream available"):
        monochrome.download_monochrome_track(
            "monochrome://tidal123?isrc=GBAYE9200070&quality=LOSSLESS",
            output,
        )
    assert not output.exists()


# ---------------------------------------------------------------------------
# Source default enabled
# ---------------------------------------------------------------------------

def test_monochrome_enabled_defaults_to_true_with_no_db_row(monkeypatch):
    """Fresh install has no DB row; monochrome_enabled() must return True."""
    import monochrome
    monkeypatch.setattr("settings.get_setting_bool",
                        lambda key, default=False, **kw: default)

    assert monochrome.monochrome_enabled() is True


def test_source_registry_has_monochrome_default_enabled_true():
    import search
    cfg = search.SOURCE_REGISTRY.get("monochrome", {})
    assert cfg.get("default_enabled") is True, (
        "Monochrome must default to enabled so fresh installs show results"
    )


# ---------------------------------------------------------------------------
# Legacy search tests (unchanged)
# ---------------------------------------------------------------------------

def test_monochrome_search_retries_with_punctuation_normalised(monkeypatch):
    import monochrome

    calls = []

    class FakeResponse:
        def __init__(self, items):
            self._items = items

        def raise_for_status(self):
            pass

        def json(self):
            return {"data": {"items": self._items}}

    def fake_get(url, params, headers, timeout, follow_redirects=False):
        calls.append(params["s"])
        if params["s"] == "Artist1 Artist2 trackName":
            return FakeResponse([
                {
                    "id": "123",
                    "title": "trackName",
                    "artist": {"name": "Artist1"},
                    "duration": 180,
                    "isrc": "GBABC1234567",
                    "mediaMetadata": {"tags": ["LOSSLESS"]},
                    "album": {"title": "Album", "cover": ""},
                }
            ])
        return FakeResponse([])

    monkeypatch.setattr(monochrome, "_hifi_api_url", lambda: "https://api.example.test")
    monkeypatch.setattr(monochrome.httpx, "get", fake_get)

    results = monochrome.search_monochrome("Artist1, Artist2 - trackName", limit=5)

    assert calls == ["Artist1, Artist2 - trackName", "Artist1 Artist2 trackName"]
    assert results
    assert results[0]["source"] == "monochrome"


def test_monochrome_search_searches_normalised_variant_even_when_exact_query_returns_items(monkeypatch):
    import monochrome

    calls = []

    class FakeResponse:
        def __init__(self, items):
            self._items = items

        def raise_for_status(self):
            pass

        def json(self):
            return {"data": {"items": self._items}}

    def fake_get(url, params, headers, timeout, follow_redirects=False):
        calls.append(params["s"])
        if params["s"] == "Artist Featured Track":
            return FakeResponse([
                {
                    "id": "456",
                    "title": "Track",
                    "artist": {"name": "Artist"},
                    "duration": 219,
                    "isrc": "USGF19942501",
                    "mediaMetadata": {"tags": ["HIRES_LOSSLESS"]},
                    "album": {"title": "Album", "cover": ""},
                }
            ])
        return FakeResponse([
            {
                "id": "999",
                "title": "Irrelevant Raw Hit",
                "artist": {"name": "Other"},
                "duration": 180,
                "isrc": "USGF19949999",
                "mediaMetadata": {"tags": ["LOSSLESS"]},
                "album": {"title": "Other Album", "cover": ""},
            }
        ])

    monkeypatch.setattr(monochrome, "_hifi_api_url", lambda: "https://api.example.test")
    monkeypatch.setattr(monochrome.httpx, "get", fake_get)

    results = monochrome.search_monochrome("Artist, Featured - Track", limit=5)

    assert calls == ["Artist, Featured - Track", "Artist Featured Track"]
    assert results
    assert results[0]["title"] == "Track"


def test_monochrome_search_continues_to_normalised_variant_after_exact_query_error(monkeypatch):
    import monochrome

    calls = []

    class FakeResponse:
        def __init__(self, items):
            self._items = items

        def raise_for_status(self):
            pass

        def json(self):
            return {"data": {"items": self._items}}

    class BrokenResponse:
        def raise_for_status(self):
            raise RuntimeError("503 Service Unavailable")

    def fake_get(url, params, headers, timeout, follow_redirects=False):
        calls.append(params["s"])
        if params["s"] == "ILLENIUM Emma Grace Brave Soul":
            return FakeResponse([
                {
                    "id": "mono-1",
                    "title": "Brave Soul",
                    "artist": {"name": "ILLENIUM & Emma Grace"},
                    "duration": 217,
                    "isrc": "USAT22100001",
                    "mediaMetadata": {"tags": ["LOSSLESS"]},
                    "album": {"title": "Fallen Embers", "cover": ""},
                }
            ])
        return BrokenResponse()

    monkeypatch.setattr(monochrome, "_hifi_api_url", lambda: "https://api.example.test")
    monkeypatch.setattr(monochrome.httpx, "get", fake_get)

    results = monochrome.search_monochrome("ILLENIUM, Emma Grace - Brave Soul", limit=5)

    assert calls == ["ILLENIUM, Emma Grace - Brave Soul", "ILLENIUM Emma Grace Brave Soul"]
    assert results
    assert results[0]["source"] == "monochrome"
    assert results[0]["title"] == "Brave Soul"


def test_monochrome_search_falls_back_to_next_hifi_api_endpoint(monkeypatch):
    import monochrome

    calls = []
    monochrome._hifi_api_url_cache = None

    class FakeResponse:
        def __init__(self, items):
            self._items = items

        def raise_for_status(self):
            pass

        def json(self):
            return {"data": {"items": self._items}}

    class BrokenResponse:
        def raise_for_status(self):
            raise RuntimeError("503 Service Unavailable")

    def fake_get(url, params, headers, timeout, follow_redirects=False):
        calls.append((url, params["s"]))
        if url.startswith("https://dead.example.test"):
            return BrokenResponse()
        return FakeResponse([
            {
                "id": "mono-2",
                "title": "Brave Soul",
                "artist": {"name": "ILLENIUM & Emma Grace"},
                "duration": 277,
                "isrc": "ZZOPM2106210",
                "mediaMetadata": {"tags": ["LOSSLESS"]},
                "album": {"title": "Fallen Embers", "cover": ""},
            }
        ])

    monkeypatch.setattr(
        monochrome,
        "_hifi_api_url",
        lambda: "https://dead.example.test,https://working.example.test",
    )
    monkeypatch.setattr(monochrome.httpx, "get", fake_get)

    results = monochrome.search_monochrome("ILLENIUM, Emma Grace - Brave Soul", limit=5)

    assert calls[0][0] == "https://dead.example.test/search"
    assert any(url == "https://working.example.test/search" for url, _query in calls)
    assert results
    assert results[0]["source"] == "monochrome"
    assert monochrome._hifi_api_url_cache == "https://working.example.test"


def test_monochrome_playlist_fetch_handles_top_level_playlist_payload(monkeypatch):
    import monochrome

    class FakeResponse:
        def json(self):
            return {
                "version": "2.3",
                "playlist": {
                    "uuid": "0dfc3b10-fbdb-4419-bf54-11b90051fa6c",
                    "title": "Chill Pop",
                    "numberOfTracks": 2,
                },
                "items": [
                    {
                        "item": {
                            "title": "Carrie Bradshaw",
                            "artist": {"name": "Kylie Cantrall"},
                        }
                    },
                    {
                        "item": {
                            "title": "Somebody New",
                            "artist": {"name": "Morgan St. Jean"},
                        }
                    },
                ],
            }

    def fake_hifi_api_get(path, params, timeout):
        assert path == "/playlist/"
        assert params["id"] == "0dfc3b10-fbdb-4419-bf54-11b90051fa6c"
        return FakeResponse()

    monkeypatch.setattr(monochrome, "_hifi_api_get", fake_hifi_api_get)

    tracks, name = monochrome.fetch_tidal_playlist_tracks("0dfc3b10-fbdb-4419-bf54-11b90051fa6c")

    assert name == "Chill Pop"
    assert tracks == [
        ("Kylie Cantrall", "Carrie Bradshaw"),
        ("Morgan St. Jean", "Somebody New"),
    ]


def test_monochrome_playlist_urls_detect_as_monochrome():
    from watched_playlists import detect_playlist_platform

    platform, playlist_id = detect_playlist_platform(
        "https://monochrome.tf/playlist/0dfc3b10-fbdb-4419-bf54-11b90051fa6c"
    )

    assert platform == "monochrome"
    assert playlist_id == "0dfc3b10-fbdb-4419-bf54-11b90051fa6c"
