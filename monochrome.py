"""
MusicGrabber - Monochrome Source

Two-leg approach: Tidal's hifi-api for track metadata/ISRC, then the Qobuz
proxy for the actual audio. End result: direct FLAC from Qobuz CDN, no DASH
segment nonsense required.

Search endpoint:  GET {HIFI_API}/search?s=query
Qobuz lookup:     GET {QOBUZ_PROXY}/api/get-music?q=ISRC&offset=0
Qobuz stream:     GET {QOBUZ_PROXY}/api/download-music?track_id=ID&quality=27
CDN audio:        https://streaming-qobuz-std.akamaized.net/... (direct FLAC, no auth)

source_url format: monochrome://tidal_id?isrc=ISRC&quality=HI_RES_LOSSLESS
"""

import hashlib
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qs

import httpx

from constants import (
    TIMEOUT_MONOCHROME_SEARCH,
    TIMEOUT_MONOCHROME_DOWNLOAD,
    MONOCHROME_HIFI_API_URL,
    MONOCHROME_QOBUZ_PROXY_URL,
    MONOCHROME_PROXY_RETRY_ROUNDS,
    MONOCHROME_PROXY_RETRY_WAIT,
)
from settings import get_setting_bool
from youtube import score_search_result_with_breakdown, parse_duration

# Quality map: Tidal tag → (quality string stored in source_url, Qobuz format ID)
_QUALITY_MAP = {
    "HIRES_LOSSLESS": ("HI_RES_LOSSLESS", 27),
    "LOSSLESS":       ("LOSSLESS",         7),
    "HIGH":           ("HIGH",             6),
}
_SOURCE_QUALITY_TO_QOBUZ_FORMAT = {source_quality: qobuz_fmt for source_quality, qobuz_fmt in _QUALITY_MAP.values()}

# Score bonuses per quality tier. Kept near Soulseek's stack so Monochrome can
# compete on overall ranking, but the HIRES-over-LOSSLESS delta is deliberately
# small (15). Anything more lets piano covers and tribute bands ride the HIRES
# bonus straight over the legitimate studio master.
_QUALITY_BONUS = {
    "HI_RES_LOSSLESS": 175,
    "LOSSLESS":        160,
    "HIGH":             30,
}

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0",
}

_KNOWN_PUBLIC_HIFI_API_URLS = {
    "https://us-west.monochrome.tf",
    "https://monochrome-api.samidy.com",
    "https://api.monochrome.tf",
    "https://eu-central.monochrome.tf",
}
_hifi_api_url_cache = None

_KNOWN_PUBLIC_QOBUZ_PROXY_URLS = {
    "https://qobuz.kennyy.com.br",
    "https://mono.scavengerfurs.net",
    "https://qdl-api.monochrome.tf",
}
_qobuz_proxy_url_cache: str | None = None

# Per-proxy failure tracking. A 4xx/5xx records a timestamp here; the proxy is
# deprioritised until _QOBUZ_FAILURE_TTL seconds have passed.
_qobuz_proxy_failures: dict[str, float] = {}
_QOBUZ_FAILURE_TTL = 1800  # 30 minutes

# Background health-probe state.
_qobuz_probe_last_run: float = 0.0
_QOBUZ_PROBE_INTERVAL = 3600  # probe all proxies once per hour
_PROBE_ISRC = "GBAYE9200070"  # Radiohead - Creep; reliably indexed on Qobuz


def monochrome_enabled() -> bool:
    return get_setting_bool("source_monochrome_enabled", True)


def _hifi_api_url() -> str:
    from settings import get_setting
    return get_setting("monochrome_hifi_api_url", MONOCHROME_HIFI_API_URL).rstrip("/")


def _split_endpoint_urls(value: str) -> list[str]:
    urls = []
    seen = set()
    for part in re.split(r"[\s,]+", value or ""):
        url = part.strip().rstrip("/")
        if not url or not re.match(r"https?://", url, re.I) or url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def _hifi_api_urls() -> list[str]:
    configured = _split_endpoint_urls(_hifi_api_url())
    defaults = _split_endpoint_urls(MONOCHROME_HIFI_API_URL)

    if not configured:
        candidates = defaults
    elif len(configured) == 1 and configured[0] in _KNOWN_PUBLIC_HIFI_API_URLS:
        candidates = configured + defaults
    else:
        candidates = configured

    if _hifi_api_url_cache and _hifi_api_url_cache in candidates:
        candidates = [_hifi_api_url_cache] + [url for url in candidates if url != _hifi_api_url_cache]

    deduped = []
    seen = set()
    for url in candidates:
        if url not in seen:
            seen.add(url)
            deduped.append(url)
    return deduped or defaults


def _remember_hifi_api_url(base: str) -> None:
    global _hifi_api_url_cache
    _hifi_api_url_cache = base


def _hifi_api_get(path: str, params: dict, timeout: int | float = TIMEOUT_MONOCHROME_SEARCH) -> httpx.Response:
    errors = []
    for base in _hifi_api_urls():
        try:
            resp = httpx.get(
                f"{base}{path}",
                params=params,
                headers=_HEADERS,
                timeout=timeout,
                follow_redirects=True,
            )
            resp.raise_for_status()
            _remember_hifi_api_url(base)
            return resp
        except Exception as exc:
            errors.append(f"{base}: {exc}")
    raise RuntimeError("; ".join(errors))


def _qobuz_proxy_urls() -> list[str]:
    _maybe_probe_qobuz_proxies_bg()

    from settings import get_setting
    configured = _split_endpoint_urls(
        get_setting("monochrome_qobuz_proxy_url", MONOCHROME_QOBUZ_PROXY_URL)
    )
    defaults = _split_endpoint_urls(MONOCHROME_QOBUZ_PROXY_URL)

    if not configured:
        candidates = defaults
    elif len(configured) == 1 and configured[0] in _KNOWN_PUBLIC_QOBUZ_PROXY_URLS:
        candidates = configured + [u for u in defaults if u != configured[0]]
    else:
        candidates = configured

    deduped: list[str] = []
    seen: set[str] = set()
    for url in candidates:
        if url not in seen:
            seen.add(url)
            deduped.append(url)
    if not deduped:
        deduped = defaults

    # Sort: last-known-good first, recently-failed last, unknown in between
    def _health_key(url: str) -> int:
        if url == _qobuz_proxy_url_cache:
            return 0
        if _qobuz_proxy_recently_failed(url):
            return 2
        return 1

    deduped.sort(key=_health_key)
    return deduped


def _remember_qobuz_proxy_url(base: str) -> None:
    global _qobuz_proxy_url_cache
    _qobuz_proxy_url_cache = base
    _qobuz_proxy_failures.pop(base, None)  # clear any stale failure mark


def _mark_qobuz_proxy_failed(url: str) -> None:
    global _qobuz_proxy_url_cache
    _qobuz_proxy_failures[url] = time.time()
    if _qobuz_proxy_url_cache == url:
        _qobuz_proxy_url_cache = None  # force re-selection next call


def _qobuz_proxy_recently_failed(url: str) -> bool:
    ts = _qobuz_proxy_failures.get(url)
    return ts is not None and (time.time() - ts) < _QOBUZ_FAILURE_TTL


def _probe_qobuz_proxies() -> bool:
    """Probe all configured Qobuz proxies and update health state.

    Returns True if at least one proxy is currently serving streams. Meant for a
    background thread, but also called synchronously by the source health check.
    """
    global _qobuz_probe_last_run
    _qobuz_probe_last_run = time.time()

    try:
        from settings import get_setting
        configured = _split_endpoint_urls(
            get_setting("monochrome_qobuz_proxy_url", MONOCHROME_QOBUZ_PROXY_URL)
        )
    except Exception:
        configured = []
    defaults = _split_endpoint_urls(MONOCHROME_QOBUZ_PROXY_URL)
    urls = list(dict.fromkeys(configured + defaults))  # configured first, deduped

    found_healthy = False
    for url in urls:
        try:
            resp = httpx.get(
                f"{url}/api/get-music",
                params={"q": _PROBE_ISRC, "offset": 0},
                headers=_HEADERS,
                timeout=8,
            )
            if resp.is_success:
                items = (((resp.json().get("data") or {}).get("tracks") or {}).get("items")) or []
                if items:
                    _remember_qobuz_proxy_url(url)
                    if not found_healthy:
                        print(f"Monochrome: Qobuz proxy healthy: {url}")
                    found_healthy = True
                    continue
            _mark_qobuz_proxy_failed(url)
            print(f"Monochrome: Qobuz proxy unhealthy ({resp.status_code}): {url}")
        except Exception as exc:
            # Connection errors: don't blacklist (might be transient network), just note
            print(f"Monochrome: Qobuz proxy unreachable: {url} ({exc})")

    if not found_healthy:
        print("Monochrome: all Qobuz proxies are currently unhealthy")
    return found_healthy


def download_leg_healthy() -> tuple[bool, str]:
    """Health check for the source layer: can Monochrome actually stream a FLAC?

    The search leg (hifi-api) being up is worthless if the Qobuz download leg is
    dead, so we gate Monochrome's availability on the leg that serves bytes. Runs
    a live proxy sweep and reports the verdict for servicecheck.py.
    """
    try:
        healthy = _probe_qobuz_proxies()
    except Exception as exc:
        return False, f"Qobuz proxy probe error: {exc}"
    if healthy:
        return True, ""

    # Proxies are all down, but the qbdlx direct-Qobuz fallback might still be
    # able to serve bytes. If it can, Monochrome is still deliverable, so don't
    # park it.
    try:
        from qbdlx import qbdlx_enabled, download_leg_healthy as qbdlx_healthy
        if qbdlx_enabled():
            ok, _reason = qbdlx_healthy()
            if ok:
                print("Monochrome: proxies down but qbdlx direct-Qobuz fallback is healthy")
                return True, ""
    except Exception as exc:
        print(f"Monochrome: qbdlx health probe errored: {exc}")

    return False, "all Qobuz proxies down (qbdlx fallback also unavailable)"


def _maybe_probe_qobuz_proxies_bg() -> None:
    """Kick off a background proxy probe if one hasn't run recently."""
    if time.time() - _qobuz_probe_last_run < _QOBUZ_PROBE_INTERVAL:
        return
    threading.Thread(target=_probe_qobuz_proxies, daemon=True, name="mono-qobuz-probe").start()


def _cover_url(cover_uuid: str) -> str:
    """Convert Tidal cover UUID to a resources.tidal.com thumbnail URL."""
    if not cover_uuid:
        return ""
    return f"https://resources.tidal.com/images/{cover_uuid.replace('-', '/')}/320x320.jpg"


# When several copies of a track sit at the same quality tier, prefer the
# canonical studio version. Tidal exposes two signals that make this far easier
# than guessing from album titles:
#
#   item.version     — non-empty for live/remix/demo/rehearsal/karaoke/etc.;
#                      empty (or just a remaster note) on the canonical track.
#   item.popularity  — Tidal's own popularity ranking. Canonical masters tend
#                      to dominate. We use it as a small tiebreaker.
#
# Album titles are still a useful fallback (soundtracks, karaoke comps, tribute
# albums often have clean version fields but obvious album names).

# Track-version patterns. First match wins. A bare "remastered" tag is NOT
# penalised: Tidal almost never carries the un-remastered original master, so
# the remaster IS the canonical version.
#
# The third tuple element is a "waiver" pattern: if the user's own query
# contains it, the penalty is dropped. That way someone searching for
# "spawn soundtrack" or "live at wembley" or "instrumental version" isn't
# punished for getting exactly what they asked for.
_VERSION_PENALTIES = [
    (re.compile(r"\b(karaoke|tribute|piano cover|originally performed)\b", re.I), -120, "version_karaoke_or_tribute",
     re.compile(r"\b(karaoke|tribute)\b", re.I)),
    (re.compile(r"\b(live|unplugged|rehearsal|boombox|in concert|live aid)\b", re.I), -60, "version_live",
     re.compile(r"\b(live|unplugged|concert)\b", re.I)),
    (re.compile(r"\b(demo|outtake|alternate|early|rough mix|work tape|monitor mix)\b", re.I), -45, "version_demo_or_alt",
     re.compile(r"\b(demo|outtake|alternate)\b", re.I)),
    (re.compile(r"\b(instrumental|a cappella|backing track)\b", re.I),  -45, "version_instrumental",
     re.compile(r"\b(instrumental|a cappella|backing)\b", re.I)),
    (re.compile(r"\b(remix|extended|edit|mix|dub|radio|single version)\b", re.I), -30, "version_remix_or_edit",
     re.compile(r"\b(remix|edit|mix|dub|extended)\b", re.I)),
    (re.compile(r"\b(muppet|orchestral|piano version|acoustic version)\b", re.I), -50, "version_arrangement",
     re.compile(r"\b(muppet|orchestral|acoustic|piano)\b", re.I)),
]

_ALBUM_PENALTIES = [
    (re.compile(r"\b(karaoke|tribute|piano covers?)\b", re.I), -80, "album_karaoke_or_tribute",
     re.compile(r"\b(karaoke|tribute)\b", re.I)),
    (re.compile(r"\b(soundtrack|original score|o\.?s\.?t\.?)\b", re.I), -50, "album_soundtrack",
     re.compile(r"\b(soundtrack|score|ost|o\.s\.t)\b", re.I)),
    (re.compile(r"\b(live|unplugged|in concert|at wembley|at reading|rehearsals?)\b", re.I), -40, "album_live",
     re.compile(r"\b(live|unplugged|concert)\b", re.I)),
    (re.compile(r"\b(greatest hits|best of|anthology|essentials?|the hits|compilation|disco night|pop classics|hits collection)\b", re.I), -35, "album_compilation",
     re.compile(r"\b(greatest hits|best of|anthology|compilation|hits)\b", re.I)),
]


def _apply_penalty_set(text: str, query_lower: str, rules: list) -> tuple[int, str | None]:
    if not text:
        return 0, None
    for pattern, penalty, reason, waiver in rules:
        if pattern.search(text):
            if waiver.search(query_lower):
                return 0, None
            return penalty, f"{reason}={penalty}"
    return 0, None


def _version_penalty(version: str, query: str) -> tuple[int, str | None]:
    """Return (penalty, reason) from the Tidal track `version` field, query-aware."""
    if not version:
        return 0, None
    if re.fullmatch(r"\s*(?:\d{4}\s+)?remaster(?:ed)?(?:\s+\d{4})?\s*", version, re.I):
        return 0, None
    return _apply_penalty_set(version, query.lower(), _VERSION_PENALTIES)


def _album_edition_penalty(album_title: str, query: str) -> tuple[int, str | None]:
    """Return (penalty, reason) for the Tidal album title, query-aware."""
    return _apply_penalty_set(album_title, query.lower(), _ALBUM_PENALTIES)


def _popularity_bonus(popularity: int | None) -> tuple[int, str | None]:
    """Small tiebreaker. Maps Tidal popularity (0 to 100) to a 0 to +10 bump."""
    if not popularity:
        return 0, None
    bonus = min(10, max(0, popularity // 10))
    if not bonus:
        return 0, None
    return bonus, f"tidal_popularity=+{bonus}"


def _best_quality(tags: list[str]) -> tuple[str, int]:
    """Return (quality_string, qobuz_fmt) for the best available quality tier."""
    for tag in ("HIRES_LOSSLESS", "LOSSLESS", "HIGH"):
        if tag in tags:
            return _QUALITY_MAP[tag]
    return ("HIGH", 6)


def _normalise_query_for_hifi_api(query: str) -> str:
    """Return a Monochrome/Tidal search query with punctuation softened.

    The hifi-api search endpoint is much less forgiving of separator punctuation
    than the other providers. Bulk imports naturally produce queries such as
    "Artist1, Artist2 - Track", which can return zero results even though
    "Artist1 Artist2 Track" succeeds.
    """
    normalised = re.sub(r"[\W_]+", " ", query or "", flags=re.UNICODE)
    return re.sub(r"\s+", " ", normalised).strip()


def _monochrome_search_queries(query: str) -> list[str]:
    """Return hifi-api query variants, preserving the user's exact query first."""
    queries = []
    original = (query or "").strip()
    if original:
        queries.append(original)

    normalised = _normalise_query_for_hifi_api(original)
    if normalised and normalised.lower() != original.lower():
        queries.append(normalised)

    return queries


def search_monochrome(query: str, limit: int) -> list[dict]:
    """Search Tidal via hifi-api and return normalised result dicts."""
    try:
        items = []
        seen_raw_ids = set()
        search_errors = []

        for base in _hifi_api_urls():
            endpoint_had_success = False
            for hifi_query in _monochrome_search_queries(query):
                try:
                    resp = httpx.get(
                        f"{base}/search",
                        params={"s": hifi_query, "limit": limit * 3},
                        headers=_HEADERS,
                        timeout=TIMEOUT_MONOCHROME_SEARCH,
                        follow_redirects=True,
                    )
                    resp.raise_for_status()
                    endpoint_had_success = True
                    _remember_hifi_api_url(base)
                except Exception as exc:
                    search_errors.append(f"{base} {hifi_query!r}: {exc}")
                    continue

                for item in resp.json().get("data", {}).get("items", []):
                    tidal_id = item.get("id")
                    if not tidal_id or tidal_id in seen_raw_ids:
                        continue
                    seen_raw_ids.add(tidal_id)
                    items.append(item)

            if items or endpoint_had_success:
                break

        if not items and search_errors:
            print(f"Monochrome search error: {'; '.join(search_errors)}")

        results = []
        seen_ids = set()

        for item in items:
            tidal_id = item.get("id")
            if not tidal_id or tidal_id in seen_ids:
                continue
            seen_ids.add(tidal_id)

            title     = item.get("title", "")
            artist    = (item.get("artist") or {}).get("name", "")
            duration  = item.get("duration") or 0
            isrc      = item.get("isrc", "")
            tags      = (item.get("mediaMetadata") or {}).get("tags", [])
            album     = (item.get("album") or {}).get("title", "")
            cover_id  = (item.get("album") or {}).get("cover", "")
            version   = item.get("version") or ""
            popularity = item.get("popularity") or 0

            if not (title and artist):
                continue
            # No ISRC means Qobuz can't find it, which means we can't download or even
            # preview it. Pretending otherwise just leads to broken results and angry users.
            if not isrc:
                continue

            quality_str, _ = _best_quality(tags)
            bonus = _QUALITY_BONUS.get(quality_str, 30)

            combined = f"{artist} - {title}"
            quality_score, score_breakdown = score_search_result_with_breakdown(
                combined, artist, query,
                duration_seconds=duration or None,
                view_count=None,
                album=album,
            )
            quality_score += bonus
            score_breakdown.append(f"source_quality=+{bonus}")

            for delta, reason in (
                _version_penalty(version, query),
                _album_edition_penalty(album, query),
                _popularity_bonus(popularity),
            ):
                if delta:
                    quality_score += delta
                    score_breakdown.append(reason)

            params = urlencode({"isrc": isrc, "quality": quality_str})
            source_url = f"monochrome://{tidal_id}?{params}"
            video_id = f"mono_{hashlib.md5(source_url.encode()).hexdigest()[:12]}"

            results.append({
                "video_id": video_id,
                "title": title,
                "channel": artist,
                "duration": parse_duration(duration) if duration else "",
                "thumbnail": _cover_url(cover_id),
                "is_playlist": False,
                "video_count": None,
                "source": "monochrome",
                "source_url": source_url,
                "quality": quality_str,
                "quality_score": quality_score,
                "score_breakdown": score_breakdown,
                "slskd_username": None,
                "slskd_filename": None,
                "slskd_size": None,
            })

        results.sort(key=lambda x: x["quality_score"], reverse=True)
        return results[:limit]

    except Exception as e:
        print(f"Monochrome search error: {e}")
        return []


class QobuzProxyError(RuntimeError):
    """Raised when no proxy could serve a stream URL.

    `transport_failure` is True when at least one proxy died at the transport/HTTP
    level (connection refused, no route to host, 5xx, 4xx) rather than cleanly
    reporting "no track for this ISRC". That distinction tells the caller whether
    it's worth waiting and retrying (flaky infra) or pointless (track genuinely
    missing), so we don't burn retry rounds on tracks Qobuz simply doesn't have.
    """
    def __init__(self, message: str, transport_failure: bool):
        super().__init__(message)
        self.transport_failure = transport_failure


def _get_qobuz_stream_url(isrc: str, quality_fmt: int) -> str:
    """Look up ISRC on the Qobuz proxy, then get a time-limited CDN stream URL.

    Tries each configured proxy in turn; the first one that returns a usable
    CDN URL wins and is remembered for future calls this process lifetime.
    """
    errors = []
    had_transport_failure = False
    for base in _qobuz_proxy_urls():
        try:
            resp = httpx.get(
                f"{base}/api/get-music",
                params={"q": isrc, "offset": 0},
                headers=_HEADERS,
                timeout=TIMEOUT_MONOCHROME_SEARCH,
            )
            resp.raise_for_status()

            body = resp.json()
            items = (((body.get("data") or {}).get("tracks") or {}).get("items")) or []
            if not items:
                errors.append(f"{base}: no results for ISRC {isrc!r}")
                continue

            qobuz_id = items[0].get("id")
            if not qobuz_id:
                errors.append(f"{base}: result missing track ID")
                continue

            resp2 = httpx.get(
                f"{base}/api/download-music",
                params={"track_id": qobuz_id, "quality": quality_fmt},
                headers=_HEADERS,
                timeout=TIMEOUT_MONOCHROME_SEARCH,
            )
            resp2.raise_for_status()

            body2 = resp2.json()
            if not body2.get("success"):
                errors.append(f"{base}: download-music failed: {body2}")
                continue

            url = (body2.get("data") or {}).get("url", "")
            if not url:
                errors.append(f"{base}: no URL in response")
                continue

            _remember_qobuz_proxy_url(base)
            return url
        except httpx.HTTPStatusError as exc:
            _mark_qobuz_proxy_failed(base)
            errors.append(f"{base}: HTTP {exc.response.status_code}")
            had_transport_failure = True
            continue
        except Exception as exc:
            errors.append(f"{base}: {exc}")
            had_transport_failure = True
            continue

    raise QobuzProxyError(
        f"Qobuz proxy: all instances failed for ISRC {isrc!r} quality {quality_fmt}: "
        f"{'; '.join(errors)}",
        transport_failure=had_transport_failure,
    )


def download_monochrome_track(source_url: str, output_path: Path) -> None:
    """Resolve a monochrome:// source URL and stream the FLAC to output_path.

    The output_path will have whatever extension the caller gave it (typically .mp3
    since we reuse _process_direct_mp3_download). That's fine — ffmpeg detects the
    actual container format regardless of extension.
    """
    parsed = urlparse(source_url)
    tidal_id = parsed.netloc
    params = parse_qs(parsed.query)
    isrc    = (params.get("isrc") or [""])[0]
    quality = (params.get("quality") or ["LOSSLESS"])[0]

    if not isrc:
        raise RuntimeError(f"Monochrome: no ISRC in source_url {source_url!r}")

    # Step down through quality tiers if the requested one is unavailable on Qobuz.
    # We start at the requested tier and walk downward; HI_RES → LOSSLESS → HIGH.
    # If every tier fails, the caller's fallback machinery picks another source.
    tier_order = ["HI_RES_LOSSLESS", "LOSSLESS", "HIGH"]
    if quality in tier_order:
        candidates = tier_order[tier_order.index(quality):]
    else:
        candidates = tier_order

    def _resolve_cdn_url() -> tuple[str, Exception | None, bool]:
        """One sweep down the quality tiers. Returns (url, last_error, transport_failure)."""
        last_err: Exception | None = None
        transport = False
        for tier in candidates:
            fmt = _SOURCE_QUALITY_TO_QOBUZ_FORMAT.get(tier, 7)
            try:
                url = _get_qobuz_stream_url(isrc, fmt)
                if tier != quality:
                    print(f"Monochrome: requested {quality} unavailable, fell back to {tier} for ISRC {isrc}")
                return url, None, False
            except QobuzProxyError as exc:
                last_err = exc
                transport = transport or exc.transport_failure
                continue
            except Exception as exc:
                last_err = exc
                transport = True
                continue
        return "", last_err, transport

    # The proxies are flaky, so sweep all tiers, and if every proxy died at the
    # transport level (not a clean "track missing"), wait a beat and sweep again
    # a few times before giving up. A genuinely-missing track fails fast instead.
    cdn_url = ""
    last_error: Exception | None = None
    rounds = max(1, MONOCHROME_PROXY_RETRY_ROUNDS)
    for attempt in range(1, rounds + 1):
        cdn_url, last_error, transport_failure = _resolve_cdn_url()
        if cdn_url or not transport_failure:
            break
        if attempt < rounds:
            print(
                f"Monochrome: all proxies unreachable for ISRC {isrc} "
                f"(round {attempt}/{rounds}), retrying in {MONOCHROME_PROXY_RETRY_WAIT}s"
            )
            time.sleep(MONOCHROME_PROXY_RETRY_WAIT)

    # Proxies all face-down? Sign the official Qobuz API ourselves with a shared
    # qbdlx token. No proxy middleman, so this survives when the whole proxy list
    # is dead. It tops out at 16/44.1 lossless, but a real FLAC beats a failure.
    if not cdn_url:
        from qbdlx import resolve_qobuz_stream_url
        for tier in candidates:
            fmt = _SOURCE_QUALITY_TO_QOBUZ_FORMAT.get(tier, 7)
            try:
                fallback_url = resolve_qobuz_stream_url(isrc, fmt)
            except Exception as exc:
                print(f"Monochrome: qbdlx fallback errored for ISRC {isrc}: {exc}")
                fallback_url = None
            if fallback_url:
                print(f"Monochrome: proxies down, served ISRC {isrc} via qbdlx direct Qobuz")
                cdn_url = fallback_url
                break

    if not cdn_url:
        raise RuntimeError(
            f"Monochrome: no Qobuz stream available for ISRC {isrc} at any quality tier "
            f"(last error: {last_error})"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with httpx.stream(
        "GET",
        cdn_url,
        headers=_HEADERS,
        timeout=TIMEOUT_MONOCHROME_DOWNLOAD,
        follow_redirects=True,
    ) as resp:
        resp.raise_for_status()
        expected_size = int(resp.headers.get("content-length", 0))
        with open(output_path, "wb") as f:
            for chunk in resp.iter_bytes(chunk_size=65536):
                f.write(chunk)

    actual_size = output_path.stat().st_size
    if actual_size == 0:
        output_path.unlink(missing_ok=True)
        raise RuntimeError(f"Monochrome: download of tidal/{tidal_id} produced an empty file")
    if expected_size > 0 and actual_size < expected_size:
        output_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"Monochrome: download truncated for tidal/{tidal_id}: "
            f"got {actual_size} of {expected_size} bytes"
        )


def get_monochrome_preview_url(isrc: str) -> str:
    """Return a direct CDN URL suitable for browser audio preview.

    The Tidal hifi-api /track endpoint has been returning "Upstream API error",
    so we go via the Qobuz proxy instead. The Akamai-hosted FLAC plays back fine
    in modern browsers, and we ask for LOSSLESS (16-bit) so we don't push
    24-bit/192kHz at users who only wanted to hear a few seconds. If every
    proxy is down but the qbdlx direct-Qobuz fallback is healthy, use that path
    too; source health may keep Monochrome visible based on qbdlx availability.
    """
    if not isrc:
        raise RuntimeError("Monochrome preview requires an ISRC")
    try:
        return _get_qobuz_stream_url(isrc, 7)
    except Exception as proxy_exc:
        try:
            from qbdlx import resolve_qobuz_stream_url
            fallback_errors = []
            fallback_url = None
            for fmt in (7, 6):
                try:
                    fallback_url = resolve_qobuz_stream_url(isrc, fmt)
                except Exception as exc:
                    fallback_errors.append(f"format {fmt}: {exc}")
                    fallback_url = None
                if fallback_url:
                    break
        except Exception as fallback_exc:
            raise RuntimeError(
                f"Monochrome preview unavailable via proxy ({proxy_exc}) "
                f"or qbdlx fallback ({fallback_exc})"
            ) from proxy_exc
        if fallback_url:
            print(f"Monochrome: preview served ISRC {isrc} via qbdlx direct Qobuz")
            return fallback_url
        if fallback_errors:
            raise RuntimeError(
                f"Monochrome preview unavailable via proxy ({proxy_exc}) "
                f"or qbdlx fallback ({'; '.join(fallback_errors)})"
            ) from proxy_exc
        raise proxy_exc


def fetch_tidal_playlist_tracks(playlist_uuid: str) -> tuple[list[tuple[str, str]], str]:
    """Fetch a Tidal playlist's track list via hifi-api.

    Returns ([(artist, title), ...], playlist_name).
    """
    def _playlist_payload(offset: int = 0) -> dict:
        resp = _hifi_api_get(
            "/playlist/",
            params={"id": playlist_uuid, "offset": offset},
            timeout=30,
        )
        body = resp.json()
        return body.get("data") or body

    data = _playlist_payload()
    playlist = data.get("playlist") or data
    name = playlist.get("title") or "Monochrome Playlist"
    items = data.get("items") or (data.get("tracks") or {}).get("items", [])
    total = playlist.get("numberOfTracks") or len(items)

    offset = len(items)
    while offset < total:
        page = _playlist_payload(offset)
        page_items = page.get("items") or (page.get("tracks") or {}).get("items", [])
        if not page_items:
            break
        items.extend(page_items)
        offset += len(page_items)

    tracks = []
    for item in items:
        item = item.get("item") if isinstance(item, dict) and "item" in item else item
        if not isinstance(item, dict):
            continue
        track_title = item.get("title", "").strip()
        artist_name = (item.get("artist") or {}).get("name", "").strip()
        if track_title and artist_name:
            tracks.append((artist_name, track_title))

    return tracks, name
