"""Short-lived SeleniumBase bootstrap for an MP3Phoenix HTTP session."""

import json
import os
import shutil
import sys
import time


RESULT_PREFIX = "MUSICGRABBER_MP3PHOENIX="
BASE_URL = "https://mp3phoenix.net"


def _emit(payload: dict) -> None:
    print(f"{RESULT_PREFIX}{json.dumps(payload, separators=(',', ':'))}", flush=True)


def _wait_for_access(sb, timeout: int) -> None:
    deadline = time.monotonic() + timeout
    attempted_interaction = False
    while time.monotonic() < deadline:
        title = str(sb.cdp.evaluate("document.title") or "").lower()
        body = str(sb.cdp.evaluate("document.body ? document.body.innerText : ''") or "").lower()
        challenged = "just a moment" in title or "verify you are human" in body
        if not challenged and sb.cdp.evaluate("document.readyState") == "complete":
            return
        if not attempted_interaction and time.monotonic() > deadline - timeout + 5:
            attempted_interaction = True
            try:
                sb.solve_captcha()
            except Exception as exc:
                print(f"MP3Phoenix challenge interaction did not complete: {exc}", file=sys.stderr)
        sb.sleep(1)
    raise TimeoutError(f"MP3Phoenix browser access was not ready within {timeout}s")


def _session_details(sb) -> dict:
    """Export only the clearance material needed by the HTTP client."""
    cookies = []
    for cookie in sb.cdp.get_all_cookies():
        cookies.append({
            "name": cookie.name,
            "value": cookie.value,
            "domain": cookie.domain,
            "path": cookie.path or "/",
        })
    return {
        "cookies": cookies,
        "user_agent": str(sb.cdp.evaluate("navigator.userAgent") or ""),
    }


def main() -> None:
    try:
        timeout = max(20, int(os.environ.get("MP3PHOENIX_BROWSER_TIMEOUT", "75")))
    except ValueError:
        timeout = 75

    try:
        from seleniumbase import SB
    except Exception as exc:
        _emit({"success": False, "error": f"SeleniumBase unavailable: {exc}"})
        return

    try:
        options = {
            "uc": True,
            "test": True,
            "xvfb": True,
            "locale": "en",
            "chromium_arg": "no-sandbox,disable-dev-shm-usage",
        }
        if not shutil.which("google-chrome") and shutil.which("chromium"):
            options["use_chromium"] = True
            options["binary_location"] = shutil.which("chromium")

        with SB(**options) as sb:
            sb.activate_cdp_mode(BASE_URL)
            _wait_for_access(sb, timeout)
            request = json.loads(sys.stdin.readline())
            if request.get("action") != "session":
                raise ValueError("Unknown MP3Phoenix browser action")
            _emit({"success": True, "result": _session_details(sb)})
    except Exception as exc:
        _emit({"success": False, "error": f"MP3Phoenix browser failed: {exc}"})


if __name__ == "__main__":
    main()
