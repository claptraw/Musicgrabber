"""
MusicGrabber - Source Health Checks

Living the pirate lifestyle means free services come and go. Monochrome's Qobuz
proxies in particular love to fall over (502 one second, 200 the next), and when
a source is down its results used to still show, rank high, and then fail
silently at preview/download time. Nobody enjoys a result that won't play.

This module checks whether each search source can actually deliver, hides the
ones that can't, and parks a failing source for a cooldown before re-checking.

Design notes:
- Each source gets the *cheapest* check that proves it works. Monochrome is
  gated on its download leg (Qobuz proxy), because the search leg being up is
  worthless if it can't stream. The direct-MP3 sites just need a live root. The
  big platforms need host reachability. Soulseek needs to be configured and
  slskd reachable.
- State is in-memory and ephemeral. A restart re-checks everything, which is
  fine: checks are cheap and services bounce constantly anyway.
- No source here imports the search registry at module load (search.py imports
  us), so any cross-reference is a lazy import inside a function.
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

from constants import (
    SOURCE_HEALTH_CHECK_INTERVAL,
    SOURCE_HEALTH_COOLDOWN,
    SERVICECHECK_TIMEOUT,
)
from settings import get_setting_bool

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0",
}

# source_id -> {"healthy": bool, "checked_at": float, "reason": str, "disabled_until": float}
_HEALTH: dict[str, dict] = {}
_LOCK = threading.RLock()


# ---------------------------------------------------------------------------
# Low-level reachability helpers
# ---------------------------------------------------------------------------

def _http_ok(url: str, method: str = "GET") -> tuple[bool, str]:
    """A 2xx/3xx (or any non-5xx) from the host means it's alive enough to query."""
    try:
        resp = httpx.request(
            method, url,
            headers=_HEADERS,
            timeout=SERVICECHECK_TIMEOUT,
            follow_redirects=True,
        )
        # 4xx still proves the host is up and serving; only 5xx / network death counts as down.
        if resp.status_code < 500:
            return True, ""
        return False, f"HTTP {resp.status_code}"
    except Exception as exc:
        return False, f"unreachable ({exc})"


# ---------------------------------------------------------------------------
# Per-source checks: each returns (healthy: bool, reason: str)
# ---------------------------------------------------------------------------

def _check_monochrome() -> tuple[bool, str]:
    """Gate on the Qobuz download leg, the bit that actually serves FLAC bytes."""
    from monochrome import download_leg_healthy
    return download_leg_healthy()


def _check_youtube() -> tuple[bool, str]:
    # If YouTube responds, yt-dlp can work; no need to run a real search.
    return _http_ok("https://www.youtube.com", method="HEAD")


def _check_soundcloud() -> tuple[bool, str]:
    return _http_ok("https://soundcloud.com", method="HEAD")


def _check_mp3phoenix() -> tuple[bool, str]:
    from mp3phoenix import _BASE_URL
    return _http_ok(_BASE_URL)


def _check_zvu4no() -> tuple[bool, str]:
    from zvu4no import _BASE_URL
    return _http_ok(_BASE_URL)


def _check_freemp3cloud() -> tuple[bool, str]:
    from freemp3cloud import _BASE_URL
    return _http_ok(_BASE_URL)


def _check_soulseek() -> tuple[bool, str]:
    """Soulseek is opt-in. If it's not enabled it's simply out of play (healthy);
    if it IS enabled, a token round-trip proves slskd is reachable and auth works."""
    from slskd import slskd_enabled, get_slskd_token
    if not slskd_enabled():
        return True, ""  # disabled by toggle, not unhealthy; handled by the source filter
    try:
        token = get_slskd_token()
    except Exception as exc:
        return False, f"slskd unreachable ({exc})"
    if token:
        return True, ""
    return False, "slskd unreachable or auth failed"


# source_id -> check callable. Sources absent here are assumed healthy.
_CHECKS = {
    "monochrome": _check_monochrome,
    "youtube": _check_youtube,
    "soundcloud": _check_soundcloud,
    "mp3phoenix": _check_mp3phoenix,
    "zvu4no": _check_zvu4no,
    "freemp3cloud": _check_freemp3cloud,
    "soulseek": _check_soulseek,
}


# ---------------------------------------------------------------------------
# Settings-driven tuneables
# ---------------------------------------------------------------------------

def _checks_enabled() -> bool:
    return get_setting_bool("source_health_checks_enabled", True)


def _check_interval() -> float:
    from settings import get_setting_int
    minutes = get_setting_int("source_health_check_interval_minutes", 0)
    return minutes * 60 if minutes > 0 else SOURCE_HEALTH_CHECK_INTERVAL


def _cooldown() -> float:
    from settings import get_setting_int
    minutes = get_setting_int("source_health_cooldown_minutes", 0)
    return minutes * 60 if minutes > 0 else SOURCE_HEALTH_COOLDOWN


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_source_available(source_id: str) -> bool:
    """Fast, no-network check: is this source currently allowed to return results?

    True unless we've recorded a failure whose cooldown hasn't expired. Checks
    being globally disabled means everything is always "available".
    """
    if not _checks_enabled():
        return True
    with _LOCK:
        entry = _HEALTH.get(source_id)
        if not entry:
            return True  # unknown == innocent until a check proves otherwise
        if entry["healthy"]:
            return True
        return time.time() >= entry.get("disabled_until", 0)


def check_source(source_id: str, force: bool = False) -> dict:
    """Run the source's health check if stale or forced; return its health entry.

    Sources with no registered check are always healthy. On failure the source is
    parked for the cooldown so we don't hammer a dead service every search.
    """
    check_fn = _CHECKS.get(source_id)
    if check_fn is None:
        return {"healthy": True, "checked_at": time.time(), "reason": "", "disabled_until": 0}

    now = time.time()
    with _LOCK:
        entry = _HEALTH.get(source_id)
        cooldown_expired = entry and not entry.get("healthy") and now >= entry.get("disabled_until", 0)
        if entry and not force and not cooldown_expired and (now - entry.get("checked_at", 0)) < _check_interval():
            return entry

    try:
        healthy, reason = check_fn()
    except Exception as exc:
        healthy, reason = False, f"check error: {exc}"

    entry = {
        "healthy": healthy,
        "checked_at": time.time(),
        "reason": "" if healthy else reason,
        "disabled_until": 0 if healthy else time.time() + _cooldown(),
    }
    with _LOCK:
        _HEALTH[source_id] = entry
    if not healthy:
        print(f"servicecheck: {source_id} unavailable: {reason} (parked ~{int(_cooldown() // 60)}m)")
    return entry


def check_sources(source_ids: list[str] | tuple[str, ...] | set[str], force: bool = False) -> dict[str, dict]:
    """Check a subset of sources in parallel, usually the active search sources."""
    ids = [sid for sid in source_ids if sid in _CHECKS]
    if not ids or not _checks_enabled():
        return {}
    with ThreadPoolExecutor(max_workers=len(ids)) as pool:
        futures = {pool.submit(check_source, sid, force): sid for sid in ids}
        results = {}
        for fut in futures:
            sid = futures[fut]
            try:
                results[sid] = fut.result(timeout=SERVICECHECK_TIMEOUT + 5)
            except Exception as exc:
                results[sid] = {"healthy": False, "checked_at": time.time(), "reason": f"probe crashed: {exc}", "disabled_until": time.time() + _cooldown()}
                with _LOCK:
                    _HEALTH[sid] = results[sid]
        return results


def check_all_sources(force: bool = False) -> dict[str, dict]:
    """Check every registered source in parallel. Used at startup and on demand."""
    return check_sources(set(_CHECKS), force=force)


def mark_unhealthy(source_id: str, reason: str) -> None:
    """Let the download path report a real-world failure straight into health state.

    When a download dies because a whole source is offline, we don't want to wait
    for the next scheduled probe: park it now so the very next search hides it.
    """
    if source_id not in _CHECKS:
        return
    entry = {
        "healthy": False,
        "checked_at": time.time(),
        "reason": reason,
        "disabled_until": time.time() + _cooldown(),
    }
    with _LOCK:
        _HEALTH[source_id] = entry
    print(f"servicecheck: {source_id} marked unhealthy from download failure: {reason}")


def unavailable_sources() -> list[dict]:
    """Sources currently parked in cooldown, for the API / search toast.

    Returns [{id, label, reason, retry_at}] where retry_at is a unix timestamp.
    """
    if not _checks_enabled():
        return []
    try:
        from search import SOURCE_REGISTRY
        labels = {sid: cfg.get("label", sid) for sid, cfg in SOURCE_REGISTRY.items()}
    except Exception:
        labels = {}
    now = time.time()
    out = []
    with _LOCK:
        for sid, entry in _HEALTH.items():
            if not entry["healthy"] and now < entry.get("disabled_until", 0):
                out.append({
                    "id": sid,
                    "label": labels.get(sid, sid),
                    "reason": entry.get("reason", ""),
                    "retry_at": entry.get("disabled_until", 0),
                })
    return out


def health_snapshot() -> list[dict]:
    """Full current health state for the /api/sources/health endpoint."""
    try:
        from search import SOURCE_REGISTRY
        labels = {sid: cfg.get("label", sid) for sid, cfg in SOURCE_REGISTRY.items()}
    except Exception:
        labels = {}
    with _LOCK:
        snap = []
        for sid in _CHECKS:
            entry = _HEALTH.get(sid)
            snap.append({
                "id": sid,
                "label": labels.get(sid, sid),
                "healthy": entry["healthy"] if entry else None,
                "reason": (entry or {}).get("reason", ""),
                "checked_at": (entry or {}).get("checked_at", 0),
                "retry_at": (entry or {}).get("disabled_until", 0),
                "available": is_source_available(sid),
            })
    return snap


def start_health_checks() -> None:
    """Kick off an initial check of all sources in the background at startup."""
    if not _checks_enabled():
        return
    threading.Thread(
        target=lambda: check_all_sources(force=True),
        daemon=True,
        name="source-health-startup",
    ).start()
