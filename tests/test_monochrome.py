import os
from pathlib import Path
import subprocess
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


# The failover tests need several proxies to fail over between, but the shipped
# default list shrinks every time another community proxy gives up the ghost.
# Pinning our own trio here keeps these tests about the ordering and blacklisting
# logic, rather than about whichever hosts happened to be alive on release day.
_PROXY_A = "https://proxy-a.example.test"
_PROXY_B = "https://proxy-b.example.test"
_PROXY_C = "https://proxy-c.example.test"
_FAKE_PROXY_LIST = f"{_PROXY_A},{_PROXY_B},{_PROXY_C}"


def _patch_proxy_list(monkeypatch, value=_FAKE_PROXY_LIST):
    """Point the Qobuz proxy setting at our fake trio."""
    monkeypatch.setattr(
        "settings.get_setting",
        lambda key, default="", **kw: value if key == "monochrome_qobuz_proxy_url" else default,
    )


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

    from constants import MONOCHROME_QOBUZ_PROXY_URL
    urls = monochrome._qobuz_proxy_urls()
    # Whatever we ship as the default should all come back; asserting against the
    # constant means the next proxy funeral doesn't also break this test.
    assert urls == monochrome._split_endpoint_urls(MONOCHROME_QOBUZ_PROXY_URL)


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
    monkeypatch.setattr(monochrome, "_qobuz_proxy_failures", {_PROXY_A: time.time()})
    _patch_proxy_list(monkeypatch)

    urls = monochrome._qobuz_proxy_urls()
    assert urls[-1] == _PROXY_A


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


def test_prune_qobuz_proxy_failures_removes_expired_entries(monkeypatch):
    import monochrome
    failures = {
        "https://old.example.test": 100.0,
        "https://recent.example.test": 950.0,
    }
    monkeypatch.setattr(monochrome, "_qobuz_proxy_failures", failures)
    monkeypatch.setattr(monochrome, "_QOBUZ_FAILURE_TTL", 600)

    removed = monochrome._prune_qobuz_proxy_failures(now=1000.0)

    assert removed == 1
    assert failures == {"https://recent.example.test": 950.0}


# ---------------------------------------------------------------------------
# download_leg_healthy: gates Monochrome's overall source health. The qbdlx
# leg's reason used to be discarded on both the healthy and unhealthy paths
# (`ok, _reason = qbdlx_healthy()`); it's now threaded through so a live
# token-pool note actually reaches servicecheck and the UI's "unavailable"
# tooltip, instead of only ever saying "also unavailable".
# ---------------------------------------------------------------------------

def test_download_leg_healthy_true_when_proxies_up(monkeypatch):
    import monochrome
    monkeypatch.setattr(monochrome, "_probe_qobuz_proxies", lambda: True)

    ok, reason = monochrome.download_leg_healthy()

    assert ok is True
    assert reason == ""


def test_download_leg_healthy_true_via_qbdlx_when_proxies_down(monkeypatch):
    import monochrome
    monkeypatch.setattr(monochrome, "_probe_qobuz_proxies", lambda: False)
    monkeypatch.setattr("qbdlx.qbdlx_enabled", lambda: True)
    monkeypatch.setattr("qbdlx.download_leg_healthy",
                         lambda: (True, "3/28 shared tokens usable this cycle"))

    ok, reason = monochrome.download_leg_healthy()

    assert ok is True


def test_download_leg_healthy_false_reports_qbdlx_reason(monkeypatch):
    """The qbdlx reason must survive, not just collapse into 'also unavailable'."""
    import monochrome
    monkeypatch.setattr(monochrome, "_probe_qobuz_proxies", lambda: False)
    monkeypatch.setattr("qbdlx.qbdlx_enabled", lambda: True)
    monkeypatch.setattr(
        "qbdlx.download_leg_healthy",
        lambda: (False, "qbdlx could not resolve a stream (0/28 shared tokens usable this cycle)"),
    )
    monkeypatch.setattr("monochrome_browser.browser_fallback_enabled", lambda: False)

    ok, reason = monochrome.download_leg_healthy()

    assert ok is False
    assert "0/28 shared tokens usable this cycle" in reason


def test_download_leg_healthy_false_when_qbdlx_disabled(monkeypatch):
    import monochrome
    monkeypatch.setattr(monochrome, "_probe_qobuz_proxies", lambda: False)
    monkeypatch.setattr("qbdlx.qbdlx_enabled", lambda: False)
    monkeypatch.setattr("monochrome_browser.browser_fallback_enabled", lambda: False)

    ok, reason = monochrome.download_leg_healthy()

    assert ok is False
    assert "qbdlx" in reason.lower()


def test_download_leg_healthy_true_via_browser_without_launching_it(monkeypatch):
    import monochrome
    monkeypatch.setattr(monochrome, "_probe_qobuz_proxies", lambda: False)
    monkeypatch.setattr("qbdlx.qbdlx_enabled", lambda: False)
    monkeypatch.setattr(
        "monochrome_browser.browser_fallback_health",
        lambda: (True, "browser-authenticated playback available on demand"),
    )

    ok, reason = monochrome.download_leg_healthy()

    assert ok is True
    assert "browser-authenticated" in reason


def test_download_leg_healthy_remembers_repeated_browser_failure(monkeypatch):
    import monochrome
    monkeypatch.setattr(monochrome, "_probe_qobuz_proxies", lambda: False)
    monkeypatch.setattr("qbdlx.qbdlx_enabled", lambda: False)
    monkeypatch.setattr(
        "monochrome_browser.browser_fallback_health",
        lambda: (False, "Chrome failed twice"),
    )

    ok, reason = monochrome.download_leg_healthy()

    assert ok is False
    assert "Chrome failed twice" in reason


# ---------------------------------------------------------------------------
# _get_qobuz_stream_url fallback chain
# ---------------------------------------------------------------------------

def test_get_qobuz_stream_url_uses_first_healthy_proxy(monkeypatch):
    import monochrome

    calls = []
    monkeypatch.setattr(monochrome, "_qobuz_proxy_url_cache", None)
    monkeypatch.setattr(monochrome, "_qobuz_proxy_failures", {})
    monkeypatch.setattr(monochrome, "_qobuz_probe_last_run", time.time())
    _patch_proxy_list(monkeypatch)

    def fake_get(url, params, headers, timeout):
        calls.append(url)
        if "api/get-music" in url:
            return _FakeHTTPResponse(_make_qobuz_search_response())
        return _FakeHTTPResponse(_make_qobuz_download_response())

    monkeypatch.setattr(monochrome.httpx, "get", fake_get)

    cdn_url = monochrome._get_qobuz_stream_url("GBAYE9200070", 6)

    assert cdn_url == "https://streaming-qobuz-std.akamaized.net/test.flac"
    # Should have stopped at the first proxy and left the others alone
    assert all(u.startswith(_PROXY_A) for u in calls)


def test_get_qobuz_stream_url_falls_back_on_http_error(monkeypatch):
    import monochrome

    failures = {}
    monkeypatch.setattr(monochrome, "_qobuz_proxy_url_cache", None)
    monkeypatch.setattr(monochrome, "_qobuz_proxy_failures", failures)
    monkeypatch.setattr(monochrome, "_qobuz_probe_last_run", time.time())
    _patch_proxy_list(monkeypatch)

    calls = []

    def fake_get(url, params, headers, timeout):
        calls.append(url)
        if url.startswith(_PROXY_A):
            return _FakeHTTPResponse({}, status_code=400)
        if "api/get-music" in url:
            return _FakeHTTPResponse(_make_qobuz_search_response())
        return _FakeHTTPResponse(_make_qobuz_download_response())

    monkeypatch.setattr(monochrome.httpx, "get", fake_get)

    cdn_url = monochrome._get_qobuz_stream_url("GBAYE9200070", 6)

    assert cdn_url == "https://streaming-qobuz-std.akamaized.net/test.flac"
    # The 400 got the first proxy blacklisted
    assert _PROXY_A in failures
    # The second one picked up the slack
    assert monochrome._qobuz_proxy_url_cache == _PROXY_B


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


def test_get_qobuz_stream_url_timeout_blacklists_proxy(monkeypatch):
    """A timing-out proxy charges 15s per visit, so it must be parked on sight."""
    import monochrome

    failures = {}
    monkeypatch.setattr(monochrome, "_qobuz_proxy_url_cache", None)
    monkeypatch.setattr(monochrome, "_qobuz_proxy_failures", failures)
    monkeypatch.setattr(monochrome, "_qobuz_probe_last_run", time.time())
    _patch_proxy_list(monkeypatch)

    def fake_get(url, params, headers, timeout):
        if url.startswith(_PROXY_A):
            raise httpx.ReadTimeout("glacial proxy")
        if "api/get-music" in url:
            return _FakeHTTPResponse(_make_qobuz_search_response())
        return _FakeHTTPResponse(_make_qobuz_download_response())

    monkeypatch.setattr(monochrome.httpx, "get", fake_get)

    cdn_url = monochrome._get_qobuz_stream_url("GBAYE9200070", 6)

    assert cdn_url  # the next proxy along served it
    assert _PROXY_A in failures


def test_get_qobuz_stream_url_fails_fast_when_all_proxies_parked(monkeypatch):
    """Every proxy in cooldown: no HTTP calls, instant no-retry error."""
    import monochrome

    now = time.time()
    failures = {
        "https://qobuz.kennyy.com.br": now,
        "https://mono.scavengerfurs.net": now,
        "https://qdl-api.monochrome.tf": now,
    }
    monkeypatch.setattr(monochrome, "_qobuz_proxy_url_cache", None)
    monkeypatch.setattr(monochrome, "_qobuz_proxy_failures", failures)
    monkeypatch.setattr(monochrome, "_qobuz_probe_last_run", now)
    monkeypatch.setattr("settings.get_setting", lambda key, default="", **kw: default)

    def fake_get(url, params, headers, timeout):
        raise AssertionError("no HTTP call should be made when every proxy is parked")

    monkeypatch.setattr(monochrome.httpx, "get", fake_get)

    with pytest.raises(monochrome.QobuzProxyError, match="failure cooldown") as excinfo:
        monochrome._get_qobuz_stream_url("GBAYE9200070", 6)

    # False = "retrying won't help"; the retry rounds must not grind on this
    assert excinfo.value.transport_failure is False


def test_get_qobuz_stream_url_clean_no_isrc_answer_is_not_transport_failure(monkeypatch):
    """A live proxy saying 'never heard of it' means the track is missing, not the infra."""
    import monochrome

    monkeypatch.setattr(monochrome, "_qobuz_proxy_url_cache", None)
    monkeypatch.setattr(monochrome, "_qobuz_proxy_failures", {})
    monkeypatch.setattr(monochrome, "_qobuz_probe_last_run", time.time())
    monkeypatch.setattr("settings.get_setting", lambda key, default="", **kw: default)

    def fake_get(url, params, headers, timeout):
        # Proxy is up and answers, just with an empty catalogue result
        return _FakeHTTPResponse({"success": True, "data": {"tracks": {"items": []}}})

    monkeypatch.setattr(monochrome.httpx, "get", fake_get)

    with pytest.raises(monochrome.QobuzProxyError, match="all instances failed") as excinfo:
        monochrome._get_qobuz_stream_url("GBAYE9200070", 6)

    assert excinfo.value.transport_failure is False


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


def test_monochrome_preview_never_falls_back_to_lossy_qbdlx_format(monkeypatch):
    import monochrome

    def fake_proxy(isrc, quality_fmt):
        raise monochrome.QobuzProxyError("all proxies down", transport_failure=True)

    calls = []

    def fake_qbdlx(isrc, quality_fmt):
        calls.append((isrc, quality_fmt))
        return None

    monkeypatch.setattr(monochrome, "_get_qobuz_stream_url", fake_proxy)
    monkeypatch.setattr("qbdlx.resolve_qobuz_stream_url", fake_qbdlx)

    with pytest.raises(RuntimeError, match="no stream available"):
        monochrome.get_monochrome_preview_url("GBAYE9200070")

    assert calls == [("GBAYE9200070", 7)]


def test_monochrome_preview_rescues_isrc_with_artist_and_title(monkeypatch):
    import monochrome
    proxy_calls = []

    def fake_proxy(isrc, quality_fmt):
        proxy_calls.append((isrc, quality_fmt))
        if isrc == "GBNEW2500001":
            return "https://cdn.test/rescued.flac"
        raise monochrome.QobuzProxyError("not found", transport_failure=False)

    rescue_calls = []
    def fake_rescue(artist, title, old_isrc):
        rescue_calls.append((artist, title, old_isrc))
        return "GBNEW2500001"

    monkeypatch.setattr(monochrome, "_get_qobuz_stream_url", fake_proxy)
    monkeypatch.setattr("qbdlx.resolve_qobuz_stream_url", lambda isrc, quality_fmt: None)
    monkeypatch.setattr(monochrome, "_deezer_isrc_rescue", fake_rescue)

    url = monochrome.get_monochrome_preview_url(
        "monochrome://123?isrc=GBOLD2500001&quality=LOSSLESS&src=deezer",
        artist_hint="Artist",
        title_hint="Track",
    )

    assert url == "https://cdn.test/rescued.flac"
    assert rescue_calls == [("Artist", "Track", "GBOLD2500001")]
    assert proxy_calls == [("GBOLD2500001", 7), ("GBNEW2500001", 7)]


def test_monochrome_preview_uses_tidal_lossless_as_final_leg(monkeypatch):
    import monochrome

    monkeypatch.setattr(
        monochrome,
        "_get_qobuz_stream_url",
        lambda isrc, quality_fmt: (_ for _ in ()).throw(
            monochrome.QobuzProxyError("not found", transport_failure=False)
        ),
    )
    monkeypatch.setattr("qbdlx.resolve_qobuz_stream_url", lambda isrc, quality_fmt: None)
    monkeypatch.setattr(monochrome, "_deezer_isrc_rescue", lambda artist, title, isrc: "")
    tidal_calls = []

    def fake_tidal(tidal_id, quality):
        tidal_calls.append((tidal_id, quality))
        return "https://tidal-cdn.test/lossless.flac"

    monkeypatch.setattr(monochrome, "_tidal_stream_url", fake_tidal)

    url = monochrome.get_monochrome_preview_url(
        "monochrome://18420572?isrc=GBZZZ9900001&quality=HI_RES_LOSSLESS&src=tidal"
    )

    assert url == "https://tidal-cdn.test/lossless.flac"
    assert tidal_calls == [("18420572", "LOSSLESS")]


def test_monochrome_preview_only_sweeps_proxies_once(monkeypatch):
    import monochrome
    calls = []

    def dead_proxy(isrc, quality_fmt):
        calls.append((isrc, quality_fmt))
        raise monochrome.QobuzProxyError("down", transport_failure=True)

    monkeypatch.setattr(monochrome, "MONOCHROME_PROXY_RETRY_ROUNDS", 5)
    monkeypatch.setattr(monochrome, "_get_qobuz_stream_url", dead_proxy)
    monkeypatch.setattr("qbdlx.resolve_qobuz_stream_url", lambda isrc, quality_fmt: None)

    with pytest.raises(RuntimeError, match="no stream available"):
        monochrome.get_monochrome_preview_url("GBAYE9200070")

    assert calls == [("GBAYE9200070", 7)]


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
    with pytest.raises(RuntimeError, match="no stream available"):
        monochrome.download_monochrome_track(
            "monochrome://tidal123?isrc=GBAYE9200070&quality=LOSSLESS",
            output,
        )
    assert not output.exists()


def test_browser_fallback_is_tried_before_qbdlx(monkeypatch):
    """Browser leg costs no shared token, so it now goes ahead of qbdlx,
    which is the leg actually running short (see qbdlx.py's token pool)."""
    import monochrome

    monkeypatch.setattr(
        monochrome,
        "_get_qobuz_stream_url",
        lambda *a, **k: (_ for _ in ()).throw(
            monochrome.QobuzProxyError("proxy down", transport_failure=True)
        ),
    )
    monkeypatch.setattr(monochrome, "MONOCHROME_PROXY_RETRY_ROUNDS", 1)
    monkeypatch.setattr(
        "monochrome_browser.resolve_unified_stream_url",
        lambda *a, **k: "https://monochrome.test/browser.flac",
    )

    def qbdlx_must_not_run(isrc, quality_fmt):
        raise AssertionError("qbdlx must not run when the browser leg already succeeded")

    monkeypatch.setattr("qbdlx.resolve_qobuz_stream_url", qbdlx_must_not_run)

    url = monochrome._resolve_monochrome_stream_url(
        "monochrome://123?isrc=GBAYE9200070&quality=LOSSLESS",
        artist_hint="Radiohead",
        title_hint="Creep",
    )

    assert url == "https://monochrome.test/browser.flac"


def test_qbdlx_still_runs_when_browser_leg_has_nothing(monkeypatch):
    """Browser is tried first, but qbdlx remains the fallback when it comes up empty."""
    import monochrome

    monkeypatch.setattr(
        monochrome,
        "_get_qobuz_stream_url",
        lambda *a, **k: (_ for _ in ()).throw(
            monochrome.QobuzProxyError("proxy down", transport_failure=True)
        ),
    )
    monkeypatch.setattr(monochrome, "MONOCHROME_PROXY_RETRY_ROUNDS", 1)
    monkeypatch.setattr("monochrome_browser.resolve_unified_stream_url", lambda *a, **k: None)
    monkeypatch.setattr(
        "qbdlx.resolve_qobuz_stream_url",
        lambda isrc, quality_fmt: "https://qobuz.test/fast.flac",
    )

    url = monochrome._resolve_monochrome_stream_url(
        "monochrome://123?isrc=GBAYE9200070&quality=LOSSLESS",
        artist_hint="Radiohead",
        title_hint="Creep",
    )

    assert url == "https://qobuz.test/fast.flac"


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


def test_hifi_search_leg_stops_at_its_wall_clock_budget(monkeypatch):
    import monochrome
    clock = {"now": 100.0}
    calls = []

    monkeypatch.setattr(monochrome, "MONOCHROME_HIFI_SEARCH_BUDGET", 5.0)
    monkeypatch.setattr(monochrome, "_hifi_api_urls", lambda: ["https://one.test", "https://two.test"])
    monkeypatch.setattr(monochrome, "_monochrome_search_queries", lambda query: [query, f"{query} alt"])
    monkeypatch.setattr(monochrome.time, "monotonic", lambda: clock["now"])

    def slow_failure(url, params, headers, timeout, follow_redirects):
        calls.append((url, timeout))
        clock["now"] += timeout
        raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr(monochrome.httpx, "get", slow_failure)

    assert monochrome._hifi_search_leg("Artist - Track", 5) == []
    assert calls == [("https://one.test/search", 5.0)]


# ---------------------------------------------------------------------------
# qbdlx direct-Qobuz search fallback (when the hifi-api search leg is down)
# ---------------------------------------------------------------------------

def _make_qobuz_catalog_item(track_id=8767428, isrc="USQX91300809", title="Get Lucky",
                             artist="Daft Punk", album="Random Access Memories",
                             streamable=True, version=""):
    """A minimal Qobuz catalog/search track item, shaped like the real API."""
    return {
        "id": track_id,
        "isrc": isrc,
        "title": title,
        "performer": {"name": artist},
        "album": {"title": album, "image": {"large": "https://img.test/large.jpg"}},
        "duration": 247,
        "version": version,
        "streamable": streamable,
    }


def test_qbdlx_search_fallback_maps_items(monkeypatch):
    import monochrome
    monkeypatch.setattr("qbdlx.search_qobuz_catalog",
                        lambda q, limit: [_make_qobuz_catalog_item()])

    results = monochrome._qbdlx_search_fallback("daft punk get lucky", 5)

    assert len(results) == 1
    r = results[0]
    assert r["source"] == "monochrome"
    assert r["quality"] == "LOSSLESS"          # free tokens cap here, labelled honestly
    assert r["channel"] == "Daft Punk"
    assert r["title"] == "Get Lucky"
    assert r["thumbnail"] == "https://img.test/large.jpg"
    assert r["source_url"].startswith("monochrome://8767428?")
    assert "isrc=USQX91300809" in r["source_url"]
    assert "quality=LOSSLESS" in r["source_url"]
    assert any("qbdlx-direct" in b for b in r["score_breakdown"])


def test_qbdlx_search_fallback_skips_no_isrc_and_unstreamable(monkeypatch):
    import monochrome
    items = [
        _make_qobuz_catalog_item(track_id=1, isrc="", title="No ISRC"),
        _make_qobuz_catalog_item(track_id=2, streamable=False, title="Not streamable"),
        _make_qobuz_catalog_item(track_id=3, isrc="GBABC1234567", title="Good"),
    ]
    monkeypatch.setattr("qbdlx.search_qobuz_catalog", lambda q, limit: items)

    results = monochrome._qbdlx_search_fallback("x", 5)

    assert [r["title"] for r in results] == ["Good"]


def test_qbdlx_search_fallback_dedupes_by_isrc(monkeypatch):
    import monochrome
    items = [
        _make_qobuz_catalog_item(track_id=10, isrc="USQX91300809", title="Get Lucky"),
        _make_qobuz_catalog_item(track_id=11, isrc="USQX91300809", title="Get Lucky (dupe)"),
    ]
    monkeypatch.setattr("qbdlx.search_qobuz_catalog", lambda q, limit: items)

    results = monochrome._qbdlx_search_fallback("x", 5)

    assert len(results) == 1


def test_search_monochrome_falls_back_to_qbdlx_when_hifi_api_down(monkeypatch):
    import monochrome
    monkeypatch.setattr(monochrome, "_hifi_api_urls", lambda: ["https://dead.example.test"])

    def boom(*a, **k):
        raise RuntimeError("hifi-api dead")
    monkeypatch.setattr(monochrome.httpx, "get", boom)
    monkeypatch.setattr("qbdlx.search_qobuz_catalog",
                        lambda q, limit: [_make_qobuz_catalog_item()])

    results = monochrome.search_monochrome("daft punk get lucky", 5)

    assert results
    assert results[0]["title"] == "Get Lucky"
    assert any("qbdlx-direct" in b for b in results[0]["score_breakdown"])


# ---------------------------------------------------------------------------
# Deezer ISRC leg (v2.9.4): search, rescue, and the Tidal stream fallback
# ---------------------------------------------------------------------------

def _make_deezer_item(track_id=62847142, isrc="GB28K1100036", title="Titanium (feat. Sia)",
                      artist="David Guetta", album="Nothing but the Beat",
                      duration=245, rank=956365, title_version=""):
    """A minimal Deezer /search track item, shaped like the real API."""
    return {
        "id": track_id,
        "isrc": isrc,
        "title": title,
        "title_version": title_version,
        "artist": {"name": artist},
        "album": {"title": album, "cover_big": "https://img.test/cover.jpg"},
        "duration": duration,
        "rank": rank,
    }


def test_isrc_valid():
    import monochrome
    assert monochrome._isrc_valid("GB28K1100036")
    assert monochrome._isrc_valid("usum71703861")     # case-insensitive
    assert not monochrome._isrc_valid("QT&JC2622262") # Tidal's finest ampersand
    assert not monochrome._isrc_valid("QTJC2622262")  # eleven characters
    assert not monochrome._isrc_valid("")
    assert not monochrome._isrc_valid("GB28K110003X") # designation must be digits


def test_parse_tidal_track_payload_shapes():
    import base64
    import json as jsonlib
    import monochrome

    assert monochrome._parse_tidal_track_payload(
        {"data": {"OriginalTrackUrl": "http://cdn.test/a.flac"}}
    ) == "http://cdn.test/a.flac"
    assert monochrome._parse_tidal_track_payload(
        {"data": {"urls": ["http://cdn.test/b.flac"]}}
    ) == "http://cdn.test/b.flac"
    bts = base64.b64encode(jsonlib.dumps({"urls": ["http://cdn.test/c.flac"]}).encode()).decode()
    assert monochrome._parse_tidal_track_payload(
        {"data": {"manifest": bts, "manifestMimeType": "application/vnd.tidal.bts"}}
    ) == "http://cdn.test/c.flac"
    # DASH means Widevine means no; and error payloads yield nothing.
    assert monochrome._parse_tidal_track_payload(
        {"data": {"manifest": bts, "manifestMimeType": "application/dash+xml"}}
    ) == ""
    assert monochrome._parse_tidal_track_payload({"detail": "Upstream API error"}) == ""


def test_deezer_search_leg_verifies_against_qobuz(monkeypatch):
    import monochrome
    monkeypatch.setattr(monochrome, "_deezer_search_tracks",
                        lambda q, limit: [
                            _make_deezer_item(),
                            _make_deezer_item(track_id=2, isrc="GB28K1100170", title="Titanium (hi-res edition)"),
                            _make_deezer_item(track_id=3, isrc="GB28K1100999", title="Titanium (not on Qobuz)"),
                        ])

    def fake_lookup(isrc):
        if isrc == "GB28K1100036":
            return [{"id": 1, "isrc": isrc, "hires": False}], False
        if isrc == "GB28K1100170":
            return [{"id": 2, "isrc": isrc, "hires": True}], False
        return [], False  # clean miss: Qobuz has never heard of it
    monkeypatch.setattr(monochrome, "_qobuz_isrc_lookup", fake_lookup)

    results = monochrome._deezer_search_leg("david guetta titanium", 5)

    assert len(results) == 2  # the clean miss was dropped
    qualities = {r["quality"] for r in results}
    assert qualities == {"LOSSLESS", "HI_RES_LOSSLESS"}
    assert all("src=deezer" in r["source_url"] for r in results)
    assert all(r["source"] == "monochrome" for r in results)
    assert all(any("via=deezer-isrc" in b for b in r["score_breakdown"]) for r in results)


def test_deezer_search_leg_keeps_unverified_when_proxies_down(monkeypatch):
    import monochrome
    monkeypatch.setattr(monochrome, "_deezer_search_tracks",
                        lambda q, limit: [_make_deezer_item()])
    monkeypatch.setattr(monochrome, "_qobuz_isrc_lookup", lambda isrc: ([], True))

    results = monochrome._deezer_search_leg("david guetta titanium", 5)

    assert len(results) == 1
    assert results[0]["quality"] == "LOSSLESS"
    assert any("qobuz_unverified" in b for b in results[0]["score_breakdown"])


def test_deezer_search_leg_skips_junk_isrcs(monkeypatch):
    import monochrome
    monkeypatch.setattr(monochrome, "_deezer_search_tracks",
                        lambda q, limit: [
                            _make_deezer_item(isrc="QT&JC2622262", title="Junk ISRC"),
                            _make_deezer_item(isrc=""),
                        ])
    monkeypatch.setattr(monochrome, "_qobuz_isrc_lookup",
                        lambda isrc: ([{"id": 1, "isrc": isrc, "hires": False}], False))

    assert monochrome._deezer_search_leg("whatever", 5) == []


def test_deezer_isrc_rescue_prefers_studio_version(monkeypatch):
    import monochrome
    monkeypatch.setattr(monochrome, "_deezer_search_tracks",
                        lambda q, limit: [
                            _make_deezer_item(isrc="GBCEE0300050", title="Titanium (Live At Wembley)",
                                              title_version="(Live At Wembley)"),
                            _make_deezer_item(isrc="GB28K1100036", title="Titanium (feat. Sia)"),
                        ])

    rescued = monochrome._deezer_isrc_rescue("David Guetta", "Titanium", "QT&JC2622262")

    assert rescued == "GB28K1100036"


def test_deezer_isrc_rescue_returns_empty_on_weak_match(monkeypatch):
    import monochrome
    monkeypatch.setattr(monochrome, "_deezer_search_tracks",
                        lambda q, limit: [
                            _make_deezer_item(isrc="FRXXX9900001", title="Entirely Different Song",
                                              artist="Someone Else"),
                        ])

    assert monochrome._deezer_isrc_rescue("David Guetta", "Titanium", "") == ""


def test_deezer_isrc_rescue_no_hints_no_network(monkeypatch):
    import monochrome

    def boom(*a, **k):
        raise AssertionError("should not search Deezer without hints")
    monkeypatch.setattr(monochrome, "_deezer_search_tracks", boom)

    assert monochrome._deezer_isrc_rescue("", "", "QT&JC2622262") == ""


def test_search_monochrome_skips_hifi_when_deezer_delivers(monkeypatch):
    import monochrome
    fake_results = [
        {"relevance_score": 100 - i, "source_url": f"monochrome://x{i}?isrc=GBABC123456{i}&src=deezer"}
        for i in range(5)
    ]
    monkeypatch.setattr(monochrome, "_deezer_search_leg", lambda q, limit: fake_results)

    def boom(*a, **k):
        raise AssertionError("hifi leg should not run when Deezer fills the limit")
    monkeypatch.setattr(monochrome, "_hifi_search_leg", boom)

    results = monochrome.search_monochrome("query", 5)
    assert len(results) == 5


def test_search_monochrome_tops_up_from_hifi_and_dedupes(monkeypatch):
    import monochrome
    deezer = [{"relevance_score": 90, "source_url": "monochrome://1?isrc=GB28K1100036&src=deezer"}]
    hifi = [
        {"relevance_score": 80, "source_url": "monochrome://2?isrc=GB28K1100036&src=tidal"},  # dupe
        {"relevance_score": 70, "source_url": "monochrome://3?isrc=USUM71703861&src=tidal"},
    ]
    monkeypatch.setattr(monochrome, "_deezer_search_leg", lambda q, limit: deezer)
    monkeypatch.setattr(monochrome, "_hifi_search_leg", lambda q, limit: hifi)

    results = monochrome.search_monochrome("query", 5)

    assert len(results) == 2
    assert results[0]["source_url"].startswith("monochrome://1")
    assert results[1]["source_url"].startswith("monochrome://3")


class _FakeStreamResponse:
    """Pretend httpx.stream context manager serving a small FLAC-ish blob."""
    def __init__(self, payload=b"FLACDATA" * 16):
        self._payload = payload
        self.headers = {"content-length": str(len(payload))}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        pass

    def iter_bytes(self, chunk_size=65536):
        yield self._payload


def test_download_rescues_malformed_isrc_via_deezer(monkeypatch, tmp_path):
    import monochrome

    rescue_calls = []
    def fake_rescue(artist, title, bad_isrc):
        rescue_calls.append((artist, title, bad_isrc))
        return "GB28K1100036"
    monkeypatch.setattr(monochrome, "_deezer_isrc_rescue", fake_rescue)

    stream_calls = []
    def fake_stream_url(isrc, fmt):
        stream_calls.append(isrc)
        return "https://cdn.test/track.flac"
    monkeypatch.setattr(monochrome, "_get_qobuz_stream_url", fake_stream_url)
    monkeypatch.setattr(monochrome.httpx, "stream",
                        lambda *a, **k: _FakeStreamResponse())

    output = tmp_path / "track.flac"
    monochrome.download_monochrome_track(
        "monochrome://12345?isrc=QT%26JC2622262&quality=LOSSLESS&src=tidal",
        output,
        artist_hint="David Guetta", title_hint="Titanium",
    )

    assert rescue_calls == [("David Guetta", "Titanium", "QT&JC2622262")]
    assert stream_calls and stream_calls[0] == "GB28K1100036"
    assert output.exists() and output.stat().st_size > 0


def test_download_decrypts_browser_cenc_resource_without_persisting_key(monkeypatch, tmp_path):
    import monochrome

    monkeypatch.setattr(
        monochrome, "_resolve_monochrome_stream_url", lambda *a, **k: "https://cdn.test/encrypted.mp4"
    )
    monkeypatch.setattr("monochrome_browser.pop_decryption_key", lambda url: "a1" * 16)
    monkeypatch.setattr(
        monochrome.httpx, "stream", lambda *a, **k: _FakeStreamResponse(b"encrypted-media")
    )
    calls = []

    def fake_ffmpeg(args, **kwargs):
        calls.append(args)
        Path(args[-1]).write_bytes(b"fLaCclean-audio")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(monochrome.subprocess, "run", fake_ffmpeg)
    output = tmp_path / "track.flac"

    monochrome.download_monochrome_track("monochrome://123?isrc=TEST", output)

    assert output.read_bytes().startswith(b"fLaC")
    assert not (tmp_path / "track.flac.encrypted.mp4").exists()
    assert calls and calls[0][calls[0].index("-decryption_key") + 1] == "a1" * 16


def test_download_falls_back_to_tidal_stream_for_tidal_results(monkeypatch, tmp_path):
    import monochrome

    def clean_miss(isrc, fmt):
        raise monochrome.QobuzProxyError("no results", transport_failure=False)
    monkeypatch.setattr(monochrome, "_get_qobuz_stream_url", clean_miss)
    monkeypatch.setattr(monochrome, "MONOCHROME_PROXY_RETRY_ROUNDS", 1)
    monkeypatch.setattr("qbdlx.resolve_qobuz_stream_url", lambda isrc, fmt: None)
    monkeypatch.setattr(monochrome, "_deezer_isrc_rescue", lambda a, t, b: "")

    tidal_calls = []
    def fake_tidal(tidal_id, quality):
        tidal_calls.append((tidal_id, quality))
        return "https://tidal-cdn.test/track.flac"
    monkeypatch.setattr(monochrome, "_tidal_stream_url", fake_tidal)
    monkeypatch.setattr(monochrome.httpx, "stream",
                        lambda *a, **k: _FakeStreamResponse())

    output = tmp_path / "track.flac"
    monochrome.download_monochrome_track(
        "monochrome://18420572?isrc=GBZZZ9900001&quality=LOSSLESS&src=tidal",
        output,
    )

    assert tidal_calls == [("18420572", "LOSSLESS")]
    assert output.exists()


def test_download_never_uses_tidal_stream_for_non_tidal_results(monkeypatch, tmp_path):
    import monochrome

    def clean_miss(isrc, fmt):
        raise monochrome.QobuzProxyError("no results", transport_failure=False)
    monkeypatch.setattr(monochrome, "_get_qobuz_stream_url", clean_miss)
    monkeypatch.setattr(monochrome, "MONOCHROME_PROXY_RETRY_ROUNDS", 1)
    monkeypatch.setattr("qbdlx.resolve_qobuz_stream_url", lambda isrc, fmt: None)
    monkeypatch.setattr(monochrome, "_deezer_isrc_rescue", lambda a, t, b: "")

    def boom(tidal_id, quality):
        raise AssertionError("Tidal stream must not run for qbdlx-sourced results")
    monkeypatch.setattr(monochrome, "_tidal_stream_url", boom)

    output = tmp_path / "track.flac"
    # netloc is a Qobuz id here; treating it as a Tidal id risks surprise polka
    with pytest.raises(RuntimeError, match="no stream available"):
        monochrome.download_monochrome_track(
            "monochrome://8767428?isrc=GBZZZ9900001&quality=LOSSLESS&src=qbdlx",
            output,
        )
    assert not output.exists()


def test_monochrome_playlist_urls_detect_as_monochrome():
    from watched_playlists import detect_playlist_platform

    platform, playlist_id = detect_playlist_platform(
        "https://monochrome.tf/playlist/0dfc3b10-fbdb-4419-bf54-11b90051fa6c"
    )

    assert platform == "monochrome"
    assert playlist_id == "0dfc3b10-fbdb-4419-bf54-11b90051fa6c"
