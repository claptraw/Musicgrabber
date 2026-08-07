"""Long-lived SeleniumBase broker for Monochrome playback resolution.

The parent sends one JSON request per stdin line and receives one sentinel-
prefixed JSON response. Chrome and its Turnstile-authorised web session stay
alive between tracks; a browser crash remains isolated from the download worker.
"""

import json
import os
import shutil
import sys
import time


RESULT_PREFIX = "MUSICGRABBER_MONOCHROME_AUTH="
JWT_STORAGE_KEY = "unified-playback-turnstile-jwt"


def _emit(payload: dict) -> None:
    print(f"{RESULT_PREFIX}{json.dumps(payload, separators=(',', ':'))}", flush=True)


def _wait_for_jwt(sb, timeout: int) -> str:
    deadline = time.monotonic() + timeout
    attempted_interaction = False
    while time.monotonic() < deadline:
        token = sb.cdp.evaluate(
            f"window.localStorage.getItem({json.dumps(JWT_STORAGE_KEY)})"
        )
        if isinstance(token, str) and token.count(".") == 2:
            return token

        # Managed Turnstile normally completes without a gesture. If it asks
        # for one, SeleniumBase handles the same visible widget a visitor sees.
        if not attempted_interaction and time.monotonic() > deadline - timeout + 5:
            attempted_interaction = True
            try:
                sb.solve_captcha()
            except Exception as exc:
                print(
                    f"Monochrome Turnstile interaction did not complete: {exc}",
                    file=sys.stderr,
                )
        sb.sleep(1)
    raise TimeoutError(f"Turnstile JWT not issued within {timeout}s")


def _resolve_playback(sb, playback_request: dict, token: str) -> dict:
    headers = dict(playback_request.get("headers") or {})
    headers["X-Turnstile-JWT"] = token
    request = {"url": str(playback_request.get("url") or ""), "headers": headers}
    if not request["url"].startswith(("http://", "https://")):
        raise ValueError("Playback request URL is invalid")

    fetch_script = """(async () => {
        try {
            const request = %s;
            const response = await fetch(request.url, {
                method: "GET",
                headers: request.headers
            });
            let body = null;
            try { body = await response.json(); }
            catch (_) { body = {detail: await response.text()}; }
            return {status: response.status, body: body};
        } catch (error) {
            return {status: 0, body: {detail: String(error)}};
        }
    })()""" % json.dumps(request, separators=(",", ":"))
    return sb.cdp.loop.run_until_complete(
        sb.cdp.page.evaluate(fetch_script, await_promise=True)
    )


def main() -> None:
    web_url = (os.environ.get("MONOCHROME_WEB_URL") or "https://monochrome.tf").rstrip("/")
    try:
        timeout = max(20, int(os.environ.get("MONOCHROME_BROWSER_AUTH_TIMEOUT", "75")))
    except ValueError:
        timeout = 75

    try:
        from seleniumbase import SB
    except Exception as exc:
        _emit({"success": False, "error": f"SeleniumBase unavailable: {exc}"})
        return

    try:
        browser_options = {
            "uc": True,
            "test": True,
            "xvfb": True,
            "locale": "en",
            "chromium_arg": "no-sandbox,disable-dev-shm-usage",
        }
        if not shutil.which("google-chrome") and shutil.which("chromium"):
            browser_options["use_chromium"] = True
            browser_options["binary_location"] = shutil.which("chromium")

        with SB(**browser_options) as sb:
            sb.activate_cdp_mode(web_url)
            for line in sys.stdin:
                try:
                    request = json.loads(line)
                    if not isinstance(request, dict):
                        raise ValueError("Playback request must be a JSON object")
                    token = _wait_for_jwt(sb, timeout)
                    playback = _resolve_playback(sb, request, token)
                    _emit({"success": True, "playback": playback})
                except Exception as exc:
                    _emit({"success": False, "error": str(exc)})
    except Exception as exc:
        _emit({"success": False, "error": f"Monochrome browser authentication failed: {exc}"})


if __name__ == "__main__":
    main()
