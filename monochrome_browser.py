"""Browser-authenticated Monochrome unified-playback fallback."""

import atexit
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time
from urllib.parse import urljoin

import httpx

from constants import (
    MONOCHROME_BROWSER_AUTH_TIMEOUT,
    MONOCHROME_BROWSER_FALLBACK_ENABLED,
    MONOCHROME_WEB_URL,
    TIMEOUT_MONOCHROME_SEARCH,
)


_AUTH_SCRIPT = Path(__file__).with_name("monochrome_browser_auth.py")
_RESULT_PREFIX = "MUSICGRABBER_MONOCHROME_AUTH="
_config_cache: tuple[str, str] | None = None
_config_lock = threading.Lock()
_decryption_keys: dict[str, str] = {}
_decryption_lock = threading.Lock()
_broker_process: subprocess.Popen | None = None
_broker_lines: queue.Queue[str | None] | None = None
_broker_lock = threading.Lock()
_browser_health_lock = threading.Lock()
_browser_failures = 0
_browser_last_error = ""
_browser_last_failure_at = 0.0
_BROWSER_FAILURE_THRESHOLD = 2
_BROWSER_HEALTH_RETRY_SECONDS = 600


def browser_fallback_enabled() -> bool:
    from settings import get_setting_bool
    return get_setting_bool(
        "monochrome_browser_fallback_enabled", MONOCHROME_BROWSER_FALLBACK_ENABLED
    )


def _record_browser_success() -> None:
    global _browser_failures, _browser_last_error, _browser_last_failure_at
    with _browser_health_lock:
        _browser_failures = 0
        _browser_last_error = ""
        _browser_last_failure_at = 0.0


def _record_browser_failure(detail: str) -> None:
    global _browser_failures, _browser_last_error, _browser_last_failure_at
    with _browser_health_lock:
        _browser_failures += 1
        _browser_last_error = detail[:300]
        _browser_last_failure_at = time.monotonic()


def browser_fallback_health() -> tuple[bool, str]:
    """Report remembered browser health without launching Chrome for a probe."""
    if not browser_fallback_enabled():
        return False, "browser-authenticated fallback disabled"
    with _browser_health_lock:
        if _browser_failures >= _BROWSER_FAILURE_THRESHOLD:
            retry_in = _BROWSER_HEALTH_RETRY_SECONDS - (
                time.monotonic() - _browser_last_failure_at
            )
            if retry_in > 0:
                return False, (
                    f"{_browser_last_error or 'browser authentication repeatedly failed'}; "
                    f"retry available in {int(retry_in) + 1}s"
                )
            return True, "browser fallback retry window is open"
        if _browser_failures:
            return True, f"browser fallback had a recent failure ({_browser_last_error})"
    return True, "browser-authenticated playback available on demand"


def _broker_environment() -> dict[str, str]:
    """Return the small, non-secret environment the browser child requires."""
    browser_home = Path("/tmp") / f"musicgrabber-browser-{os.getuid()}"
    browser_home.mkdir(mode=0o700, parents=True, exist_ok=True)
    return {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(browser_home),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "MONOCHROME_WEB_URL": MONOCHROME_WEB_URL,
        "MONOCHROME_BROWSER_AUTH_TIMEOUT": str(MONOCHROME_BROWSER_AUTH_TIMEOUT),
    }


def _start_broker_locked() -> tuple[subprocess.Popen, queue.Queue[str | None]]:
    global _broker_process, _broker_lines
    process = subprocess.Popen(
        [sys.executable, str(_AUTH_SCRIPT)],
        env=_broker_environment(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        close_fds=True,
    )
    lines: queue.Queue[str | None] = queue.Queue()

    def _read_output() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            lines.put(line.rstrip("\n"))
        lines.put(None)

    threading.Thread(
        target=_read_output,
        name="monochrome-browser-output",
        daemon=True,
    ).start()
    _broker_process = process
    _broker_lines = lines
    return process, lines


def _stop_broker_locked() -> None:
    global _broker_process, _broker_lines
    process = _broker_process
    _broker_process = None
    _broker_lines = None
    if not process:
        return
    if process.stdin:
        try:
            process.stdin.close()
        except (BrokenPipeError, OSError):
            pass
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def stop_browser_broker() -> None:
    with _broker_lock:
        _stop_broker_locked()


atexit.register(stop_browser_broker)


def warm_browser_broker() -> bool:
    """Start the browser session up front so nothing else pays the cold start.

    Chrome's launch plus the Turnstile handshake takes the better part of
    fifteen seconds: perfectly reasonable in the background at boot, thoroughly
    rude in front of somebody who has just hovered a play button. Only worth
    doing when Monochrome is actually an enabled source and the browser leg is
    allowed; warming a browser for a source nobody searches is an expensive way
    to heat a container.
    """
    from monochrome import monochrome_enabled
    if not monochrome_enabled() or not browser_fallback_enabled():
        return False
    with _broker_lock:
        if _broker_process is not None and _broker_process.poll() is None:
            return True
        try:
            _start_broker_locked()
        except Exception as exc:
            # Best-effort on purpose: a failed warm-up shouldn't prejudice the
            # circuit breaker against the first real request, which is the one
            # anybody actually cares about.
            print(f"Monochrome: could not warm the browser session: {exc}")
            return False
    print("Monochrome: browser session warming in the background")
    return True


def broker_warm() -> bool:
    """Whether the persistent browser session is already up and answering.

    Gates previews: a non-blocking request only avoids queueing behind an
    in-flight one, it does nothing about cold-starting Chrome from scratch,
    which is the exact slow, unhinged thing previews were kept away from in
    the first place. So a preview only asks once a download has already
    warmed the session up.
    """
    if not _broker_lock.acquire(blocking=False):
        return True  # something else is using it right now, so it's up
    try:
        return _broker_process is not None and _broker_process.poll() is None
    finally:
        _broker_lock.release()


class BrokerBusy(RuntimeError):
    """Raised for a non-blocking request when the browser is mid-request already.

    Not a fault, just contention, so callers should treat it as "try again
    later" rather than feeding it to the failure/circuit-breaker bookkeeping.
    """


def _broker_request(playback_request: dict, *, restart: bool = False, wait_for_lock: bool = True) -> dict:
    timeout = MONOCHROME_BROWSER_AUTH_TIMEOUT + 20
    if not _broker_lock.acquire(blocking=wait_for_lock):
        raise BrokerBusy("Monochrome browser session is busy with another request")
    try:
        diagnostics: list[str] = []
        try:
            if restart:
                _stop_broker_locked()
            process = _broker_process
            lines = _broker_lines
            if not process or process.poll() is not None or lines is None:
                process, lines = _start_broker_locked()
            assert process.stdin is not None
            process.stdin.write(json.dumps(playback_request, separators=(",", ":")) + "\n")
            process.stdin.flush()
            deadline = time.monotonic() + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"timed out after {timeout}s")
                try:
                    line = lines.get(timeout=remaining)
                except queue.Empty as exc:
                    raise TimeoutError(f"timed out after {timeout}s") from exc
                if line is None:
                    raise RuntimeError("browser broker exited unexpectedly")
                if line.startswith(_RESULT_PREFIX):
                    payload = json.loads(line[len(_RESULT_PREFIX):])
                    if not payload.get("success"):
                        raise RuntimeError(str(payload.get("error") or "unknown browser error"))
                    return payload
                if line.strip():
                    diagnostics.append(line.strip())
                    diagnostics = diagnostics[-8:]
        except Exception as exc:
            detail = str(exc)
            if diagnostics:
                detail = f"{detail} ({diagnostics[-1]})"
            _stop_broker_locked()
            _record_browser_failure(detail)
            raise RuntimeError(f"Monochrome browser authentication failed: {detail}") from exc
    finally:
        _broker_lock.release()


def _discover_unified_config(force: bool = False) -> tuple[str, str]:
    """Read the public API base/token from the deployed Monochrome client.

    These values are part of the public browser bundle and have changed before,
    so baking today's copy into MusicGrabber would be an expiry date disguised
    as source code.
    """
    global _config_cache
    if _config_cache and not force:
        return _config_cache
    with _config_lock:
        if _config_cache and not force:
            return _config_cache
        page = httpx.get(
            MONOCHROME_WEB_URL,
            timeout=TIMEOUT_MONOCHROME_SEARCH,
            follow_redirects=True,
        )
        page.raise_for_status()
        match = re.search(r'<script[^>]+src=["\']([^"\']*assets/index-[^"\']+\.js)', page.text)
        if not match:
            raise RuntimeError("Monochrome client bundle was not found")
        bundle = httpx.get(
            urljoin(str(page.url), match.group(1)),
            timeout=TIMEOUT_MONOCHROME_SEARCH,
            follow_redirects=True,
        )
        bundle.raise_for_status()
        api_match = re.search(r'DEFAULT_API_BASE_URL:"(https?://[^"\\]+)"', bundle.text)
        token_match = re.search(r'DEFAULT_API_TOKEN:"([^"\\]+)"', bundle.text)
        if not api_match or not token_match:
            raise RuntimeError("Monochrome unified-playback configuration was not found")
        _config_cache = (api_match.group(1).rstrip("/"), token_match.group(1))
        return _config_cache


def _downloadable_audio(body: dict) -> tuple[str, str | None]:
    """Return a direct resource and an optional CENC key."""
    for resource in body.get("playback") or []:
        if not isinstance(resource, dict):
            continue
        url = resource.get("url")
        if not (
            isinstance(url, str)
            and url.startswith(("http://", "https://"))
            and str(resource.get("kind") or "").lower() == "audio"
            and str(resource.get("delivery") or "").lower() == "direct"
        ):
            continue
        encryption = resource.get("encryption")
        if not encryption:
            return url, None
        if not isinstance(encryption, dict) or encryption.get("scheme") != "cenc-aes-ctr":
            continue
        key = encryption.get("key")
        if not isinstance(key, dict) or key.get("encoding") != "hex":
            continue
        value = str(key.get("value") or "")
        if re.fullmatch(r"[0-9a-fA-F]{32}", value):
            return url, value.lower()
    return "", None


def pop_decryption_key(url: str) -> str | None:
    """Consume the in-memory key attached to a just-resolved resource URL."""
    with _decryption_lock:
        return _decryption_keys.pop(url, None)


def _browser_playback(
    api_base: str,
    api_token: str,
    params: dict,
    *,
    restart: bool = False,
    wait_for_lock: bool = True,
) -> tuple[int, dict]:
    request_url = str(httpx.URL(f"{api_base}/api/v2/track/", params=params))
    payload = _broker_request({
        "url": request_url,
        "headers": {
            "Accept": "application/json",
            "Authorization": f"Bearer {api_token}",
        },
    }, restart=restart, wait_for_lock=wait_for_lock)
    playback = payload.get("playback") or {}
    body = playback.get("body") if isinstance(playback, dict) else None
    if not isinstance(body, dict):
        body = {}
    return int(playback.get("status") or 0), body


def resolve_unified_stream_url(
    isrc: str,
    quality: str,
    artist: str = "",
    title: str = "",
    *,
    wait_for_lock: bool = True,
) -> str | None:
    """Resolve one directly-downloadable resource through Monochrome's web flow.

    wait_for_lock=False is for previews: the browser session handles one
    request at a time, so a hover asks once and gives up immediately if a
    download already has it, rather than queueing behind however long that
    download takes.
    """
    if not browser_fallback_enabled() or not isrc or not title:
        return None
    browser_ok, _ = browser_fallback_health()
    if not browser_ok:
        return None
    if not wait_for_lock and not broker_warm():
        return None

    params = {
        "track": title,
        "isrc": isrc.upper(),
        "quality": quality or "LOSSLESS",
        "intent": "download",
    }
    if artist:
        params["artist"] = artist

    status = 0
    body: dict = {}
    # Previews get one shot, no restart: a 401 there just means "skip this leg
    # for now", not "relaunch Chrome for a hover". Downloads still get the
    # second, restart-backed attempt.
    attempts = 2 if wait_for_lock else 1
    try:
        for attempt in range(attempts):
            try:
                api_base, api_token = _discover_unified_config(force=attempt > 0)
            except Exception as exc:
                _record_browser_failure(str(exc))
                raise
            status, body = _browser_playback(
                api_base,
                api_token,
                params,
                restart=attempt > 0,
                wait_for_lock=wait_for_lock,
            )
            if status not in (401, 403, 428):
                break
    except BrokerBusy:
        return None
    if not wait_for_lock and status in (401, 403, 428):
        # A preview declined the restart-and-retry recovery flight on purpose;
        # that's not evidence the browser leg is actually broken, so leave the
        # verdict (and the circuit breaker) to a download's full attempt.
        return None
    if status in (404, 429, 502):
        _record_browser_success()
        return None
    if status != 200:
        detail = str(body.get("detail") or "unknown error")[:300]
        _record_browser_failure(f"HTTP {status}: {detail}")
        raise RuntimeError(f"Monochrome unified playback returned HTTP {status}: {detail}")
    _record_browser_success()
    major = str(body.get("schema_version") or "").split(".")[0]
    if major not in ("1", "2"):
        raise RuntimeError(
            f"Monochrome unified playback returned unsupported schema {body.get('schema_version')!r}"
        )
    url, decryption_key = _downloadable_audio(body)
    if url and decryption_key:
        with _decryption_lock:
            while len(_decryption_keys) >= 32:
                _decryption_keys.pop(next(iter(_decryption_keys)))
            _decryption_keys[url] = decryption_key
    return url or None
