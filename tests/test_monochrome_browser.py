import json
import queue

import pytest

import monochrome_browser as browser


def setup_function():
    browser._broker_process = None
    browser._broker_lines = None
    browser._config_cache = None
    browser._decryption_keys.clear()
    browser._browser_failures = 0
    browser._browser_last_error = ""
    browser._browser_last_failure_at = 0.0


class _FakeStdin:
    def __init__(self, lines):
        self.lines = lines
        self.writes = []

    def write(self, value):
        self.writes.append(value)
        response = {"success": True, "playback": {"status": 200, "body": {}}}
        self.lines.put(f"{browser._RESULT_PREFIX}{json.dumps(response)}")

    def flush(self):
        pass

    def close(self):
        pass


class _FakeProcess:
    def __init__(self, lines):
        self.stdin = _FakeStdin(lines)

    def poll(self):
        return None

    def terminate(self):
        pass

    def wait(self, timeout=None):
        return 0


def test_broker_session_is_reused_between_tracks():
    lines = queue.Queue()
    process = _FakeProcess(lines)
    browser._broker_process = process
    browser._broker_lines = lines

    browser._broker_request({"url": "https://api.test/one", "headers": {}})
    browser._broker_request({"url": "https://api.test/two", "headers": {}})

    assert len(process.stdin.writes) == 2
    assert json.loads(process.stdin.writes[0])["url"].endswith("/one")
    assert json.loads(process.stdin.writes[1])["url"].endswith("/two")


def test_broker_subprocess_gets_no_application_secrets(monkeypatch):
    monkeypatch.setenv("API_KEY", "secret")
    monkeypatch.setenv("SLSKD_PASS", "also-secret")

    env = browser._broker_environment()

    assert "API_KEY" not in env
    assert "SLSKD_PASS" not in env
    assert env["MONOCHROME_WEB_URL"]
    assert env["MONOCHROME_BROWSER_AUTH_TIMEOUT"]


def test_broker_exit_has_useful_error():
    class ExitedProcess(_FakeProcess):
        def poll(self):
            return None

    lines = queue.Queue()
    lines.put(None)
    browser._broker_process = ExitedProcess(lines)
    browser._broker_lines = lines

    with pytest.raises(RuntimeError, match="exited unexpectedly"):
        browser._broker_request({"url": "https://api.test/track", "headers": {}})


def test_discover_config_from_public_bundle(monkeypatch):
    class Response:
        def __init__(self, url, text):
            self.url = url
            self.text = text

        def raise_for_status(self):
            return None

    responses = iter([
        Response("https://monochrome.tf/", '<script type="module" src="./assets/index-abc.js"></script>'),
        Response(
            "https://monochrome.tf/assets/index-abc.js",
            'DEFAULT_API_BASE_URL:"https://music.example.test",DEFAULT_API_TOKEN:"public-token"',
        ),
    ])
    monkeypatch.setattr(browser.httpx, "get", lambda *a, **kw: next(responses))
    assert browser._discover_unified_config() == ("https://music.example.test", "public-token")


def test_resolve_refreshes_config_and_browser_after_auth_rejection(monkeypatch):
    monkeypatch.setattr(browser, "browser_fallback_enabled", lambda: True)
    config_calls = []
    monkeypatch.setattr(
        browser,
        "_discover_unified_config",
        lambda force=False: config_calls.append(force) or (
            "https://api.test",
            "new-token" if force else "old-token",
        ),
    )
    playback_calls = []

    def fake_playback(api_base, api_token, params, restart=False, wait_for_lock=True):
        playback_calls.append((api_token, restart))
        if len(playback_calls) == 1:
            return 401, {"detail": "Invalid client token"}
        return 200, {
            "schema_version": "2.0",
            "playback": [{
                "kind": "audio",
                "delivery": "direct",
                "url": "https://cdn.test/track.flac",
            }],
        }

    monkeypatch.setattr(browser, "_browser_playback", fake_playback)
    assert browser.resolve_unified_stream_url(
        "GBAYE9200070", "LOSSLESS", artist="Radiohead", title="Creep"
    ) == "https://cdn.test/track.flac"
    assert config_calls == [False, True]
    assert playback_calls == [("old-token", False), ("new-token", True)]


def test_broker_warm_reflects_process_state():
    assert browser.broker_warm() is False
    lines = queue.Queue()
    browser._broker_process = _FakeProcess(lines)
    browser._broker_lines = lines
    assert browser.broker_warm() is True


def test_preview_skips_browser_leg_when_broker_is_cold(monkeypatch):
    # No process running yet: a preview must not be the thing that cold-starts
    # Chrome, so it should bail before ever touching _browser_playback.
    monkeypatch.setattr(browser, "browser_fallback_enabled", lambda: True)
    calls = []
    monkeypatch.setattr(browser, "_browser_playback", lambda *a, **kw: calls.append(1) or (200, {}))

    result = browser.resolve_unified_stream_url(
        "GBAYE9200070", "LOSSLESS", artist="Radiohead", title="Creep", wait_for_lock=False
    )

    assert result is None
    assert calls == []


def test_preview_does_not_retry_or_restart_on_auth_rejection(monkeypatch):
    monkeypatch.setattr(browser, "browser_fallback_enabled", lambda: True)
    monkeypatch.setattr(browser, "_discover_unified_config", lambda force=False: ("https://api.test", "token"))
    lines = queue.Queue()
    browser._broker_process = _FakeProcess(lines)
    browser._broker_lines = lines
    calls = []

    def fake_playback(api_base, api_token, params, restart=False, wait_for_lock=True):
        calls.append(restart)
        return 401, {"detail": "expired"}

    monkeypatch.setattr(browser, "_browser_playback", fake_playback)

    result = browser.resolve_unified_stream_url(
        "GBAYE9200070", "LOSSLESS", artist="Radiohead", title="Creep", wait_for_lock=False
    )

    assert result is None
    assert calls == [False]  # one shot only; never restart=True off the back of a hover


def test_preview_gives_up_immediately_when_broker_is_busy(monkeypatch):
    monkeypatch.setattr(browser, "browser_fallback_enabled", lambda: True)
    monkeypatch.setattr(browser, "_discover_unified_config", lambda force=False: ("https://api.test", "token"))
    lines = queue.Queue()
    browser._broker_process = _FakeProcess(lines)
    browser._broker_lines = lines
    browser._broker_lock.acquire()  # simulate a download already using the session
    try:
        result = browser.resolve_unified_stream_url(
            "GBAYE9200070", "LOSSLESS", artist="Radiohead", title="Creep", wait_for_lock=False
        )
    finally:
        browser._broker_lock.release()

    assert result is None


def test_repeated_browser_failures_are_remembered(monkeypatch):
    monkeypatch.setattr(browser, "browser_fallback_enabled", lambda: True)
    browser._record_browser_failure("Chrome failed once")
    assert browser.browser_fallback_health()[0] is True
    browser._record_browser_failure("Chrome failed twice")
    healthy, reason = browser.browser_fallback_health()
    assert healthy is False
    assert "twice" in reason
    browser._record_browser_success()
    assert browser.browser_fallback_health()[0] is True


def test_browser_health_opens_a_retry_window(monkeypatch):
    monkeypatch.setattr(browser, "browser_fallback_enabled", lambda: True)
    browser._browser_failures = browser._BROWSER_FAILURE_THRESHOLD
    browser._browser_last_failure_at = 100.0
    monkeypatch.setattr(browser.time, "monotonic", lambda: 100.0 + browser._BROWSER_HEALTH_RETRY_SECONDS + 1)

    healthy, reason = browser.browser_fallback_health()

    assert healthy is True
    assert "retry window" in reason


def test_manifest_and_unsupported_encryption_are_rejected():
    assert browser._downloadable_audio({
        "playback": [
            {"kind": "manifest", "delivery": "dash", "url": "https://cdn.test/a.mpd"},
            {
                "kind": "audio",
                "delivery": "direct",
                "url": "https://cdn.test/a.mp4",
                "encryption": {"scheme": "unknown"},
            },
        ]
    }) == ("", None)


def test_cenc_key_must_be_exactly_16_bytes():
    def resource(key):
        return {"playback": [{
            "kind": "audio",
            "delivery": "direct",
            "url": "https://cdn.test/lossless.mp4",
            "encryption": {
                "scheme": "cenc-aes-ctr",
                "iv_source": "cenc-senc",
                "key": {"encoding": "hex", "value": key},
            },
        }]}

    assert browser._downloadable_audio(resource("a1" * 16)) == (
        "https://cdn.test/lossless.mp4", "a1" * 16
    )
    assert browser._downloadable_audio(resource("a1" * 32)) == ("", None)
