"""
MusicGrabber - qbdlx Qobuz fallback

Monochrome's normal download leg goes through third-party Qobuz proxies, which
are gloriously flaky. This module is the last resort for when every proxy is
face-down: it talks to the *official* Qobuz API directly, signing requests with
a shared free-account token (the same pool the qbdlx web UI at
qbdlx.launchpd.cloud hands out under its "Free account" tab).

The flow mirrors classic qobuz-dl:
  1. GET a pool of {token, app_id, app_secret, country} from the shared webhook.
  2. catalog/search?query=ISRC  ->  track_id
  3. track/getFileUrl (signed)  ->  a real streaming-qobuz-std.akamaized.net URL

Signature scheme (verified live):
  request_sig = MD5("trackgetFileUrl" + "format_id"+fmt + "intent"+"stream"
                    + "track_id"+id + ts + app_secret)
  headers: X-App-Id, X-User-Auth-Token

Important: the free shared tokens resolve to 16-bit/44.1kHz lossless FLAC
(format_id 6), NOT 24-bit hi-res, no matter which format you ask for. That's
fine for a fallback. A genuine FLAC beats a download that face-plants.

No proxy dependency here, which is the whole point.
"""

import hashlib
import threading
import time
from pathlib import Path

import httpx

from constants import (
    QBDLX_FALLBACK_ENABLED,
    QBDLX_SHARED_TOKENS_URL,
    QBDLX_QOBUZ_API_BASE,
    QBDLX_TOKEN_CACHE_TTL,
    TIMEOUT_MONOCHROME_SEARCH,
)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0",
}

# Cached token pool: list of {token, app_id, app_secret, country}. The pool
# rotates upstream, so we re-fetch it every QBDLX_TOKEN_CACHE_TTL seconds.
_token_cache: list[dict] = []
_token_cache_at: float = 0.0
_token_lock = threading.Lock()


def qbdlx_enabled() -> bool:
    """Whether the qbdlx fallback is allowed to run.

    Env var locks it; otherwise the DB setting wins, defaulting to the env-driven
    constant (True out of the box).
    """
    from settings import get_setting_bool
    return get_setting_bool("monochrome_qbdlx_fallback_enabled", QBDLX_FALLBACK_ENABLED)


def _fetch_shared_tokens(force: bool = False) -> list[dict]:
    """Return the shared token pool, cached for QBDLX_TOKEN_CACHE_TTL seconds.

    Returns whatever we last had on a fetch failure rather than blowing up; a
    stale token still beats no token, and the caller treats an empty list as
    "fallback unavailable" anyway.
    """
    global _token_cache, _token_cache_at
    with _token_lock:
        fresh = _token_cache and (time.time() - _token_cache_at) < QBDLX_TOKEN_CACHE_TTL
        if fresh and not force:
            return _token_cache

        try:
            resp = httpx.get(
                QBDLX_SHARED_TOKENS_URL,
                headers=_HEADERS,
                timeout=TIMEOUT_MONOCHROME_SEARCH,
                follow_redirects=True,
            )
            resp.raise_for_status()
            data = resp.json()
            tokens = [
                t for t in (data if isinstance(data, list) else [])
                if t.get("token") and t.get("app_id") and t.get("app_secret")
            ]
            if tokens:
                _token_cache = tokens
                _token_cache_at = time.time()
                return tokens
            print("qbdlx: shared token pool came back empty")
        except Exception as exc:
            print(f"qbdlx: failed to fetch shared tokens: {exc}")

        return _token_cache  # last known good, possibly empty


def _signed_call(token: dict, path: str, params: dict, signed_concat: str | None = None) -> dict | None:
    """Make a (optionally signed) Qobuz API call with this token. None on failure."""
    p = dict(params)
    p["app_id"] = token["app_id"]
    if signed_concat is not None:
        ts = int(time.time())
        sig = hashlib.md5((signed_concat + str(ts) + token["app_secret"]).encode()).hexdigest()
        p["request_ts"] = ts
        p["request_sig"] = sig
    try:
        resp = httpx.get(
            f"{QBDLX_QOBUZ_API_BASE.rstrip('/')}/{path}",
            params=p,
            headers={
                **_HEADERS,
                "X-App-Id": str(token["app_id"]),
                "X-User-Auth-Token": token["token"],
            },
            timeout=TIMEOUT_MONOCHROME_SEARCH,
            follow_redirects=True,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        print(f"qbdlx: Qobuz {path} failed ({token.get('country', '?')}): {exc}")
        return None


def _resolve_track_id(token: dict, isrc: str) -> int | None:
    """Resolve an ISRC to a Qobuz track id via catalog/search."""
    body = _signed_call(token, "catalog/search", {"query": isrc, "limit": 5})
    if not body:
        return None
    items = ((body.get("tracks") or {}).get("items")) or []
    # Prefer an exact ISRC match; fall back to the top result if Qobuz doesn't
    # echo the ISRC back on the item.
    for item in items:
        if (item.get("isrc") or "").upper() == isrc.upper():
            return item.get("id")
    return items[0].get("id") if items else None


def resolve_qobuz_track_id(isrc: str) -> int | None:
    """Resolve an ISRC to a Qobuz track id using the shared qbdlx token pool."""
    if not qbdlx_enabled():
        return None
    if not isrc:
        return None

    tokens = _fetch_shared_tokens()
    for token in tokens:
        track_id = _resolve_track_id(token, isrc)
        if track_id:
            return track_id
    return None


def resolve_qobuz_stream_url(isrc: str, quality_fmt: int) -> str | None:
    """Resolve an ISRC to a direct Qobuz CDN FLAC URL via the shared tokens.

    Tries each token in the pool until one yields a stream URL. Returns None when
    the fallback is disabled, the pool is empty/unreachable, or no token can
    resolve the track (in which case the caller keeps whatever error it had).
    """
    if not qbdlx_enabled():
        return None
    if not isrc:
        return None

    tokens = _fetch_shared_tokens()
    if not tokens:
        print("qbdlx: no shared tokens available, cannot fall back")
        return None

    for token in tokens:
        track_id = _resolve_track_id(token, isrc)
        if not track_id:
            continue
        concat = f"trackgetFileUrlformat_id{quality_fmt}intentstreamtrack_id{track_id}"
        body = _signed_call(
            token,
            "track/getFileUrl",
            {"track_id": track_id, "format_id": quality_fmt, "intent": "stream"},
            signed_concat=concat,
        )
        if not body:
            continue
        url = body.get("url") or ""
        if url:
            served_fmt = body.get("format_id")
            print(
                f"qbdlx: resolved ISRC {isrc} via direct Qobuz "
                f"({token.get('country', '?')} token, format_id {served_fmt})"
            )
            return url

    print(f"qbdlx: no token could resolve a stream for ISRC {isrc}")
    return None


def download_leg_healthy() -> tuple[bool, str]:
    """Can the qbdlx fallback actually serve a FLAC right now?

    Used by servicecheck so Monochrome can be considered healthy when the proxies
    are all down but the direct-Qobuz fallback still works. Probes the same known
    ISRC the proxy health check uses (Radiohead - Creep).
    """
    if not qbdlx_enabled():
        return False, "qbdlx fallback disabled"
    url = resolve_qobuz_stream_url("GBAYE9200070", 6)
    if url:
        return True, ""
    return False, "qbdlx could not resolve a stream"
