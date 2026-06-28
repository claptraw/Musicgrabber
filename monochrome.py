"""
MusicGrabber - Monochrome Source

The resolution ladder, in order of preference:

  Search:   Deezer (clean ISRCs, typo-tolerant, machine-readable version labels)
            -> Tidal hifi-api top-up (catalogue gaps, Deezer outage)
            -> qbdlx direct Qobuz (everything else face-down)
  Download: Qobuz proxies (tier walk) -> qbdlx -> Deezer ISRC rescue
            -> Tidal stream via hifi-api -> fail honestly

Search endpoint:  GET {DEEZER}/search?q=query (ISRC arrives inline, free of charge)
Tidal search:     GET {HIFI_API}/search?s=query
Qobuz lookup:     GET {QOBUZ_PROXY}/api/get-music?q=ISRC&offset=0
Qobuz stream:     GET {QOBUZ_PROXY}/api/download-music?track_id=ID&quality=27
Tidal stream:     GET {HIFI_API}/track/?id=ID&quality=LOSSLESS (last-ditch leg)
CDN audio:        https://streaming-qobuz-std.akamaized.net/... (direct FLAC, no auth)

source_url format: monochrome://track_id?isrc=ISRC&quality=HI_RES_LOSSLESS&src=tidal
The `src` param records which leg found the track (tidal/deezer/qbdlx); only
tidal-sourced results may use the Tidal stream fallback, because for the other
legs the netloc is not a Tidal ID and resolving it as one could fetch a
completely different song. Nobody wants surprise polka.
"""

import base64
import hashlib
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
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
    DEEZER_API_URL,
    TIMEOUT_DEEZER,
)
from matching import compute_match_confidence
from settings import get_setting_bool
from youtube import score_search_result_with_breakdown, parse_duration, _parse_query_artist_title

# ISRC: two-letter country, three alphanumeric registrant, two-digit year,
# five-digit designation. Twelve characters, no punctuation, no exceptions,
# whatever Tidal's metadata department may believe.
_ISRC_RE = re.compile(r"^[A-Za-z]{2}[A-Za-z0-9]{3}\d{7}$")


def _isrc_valid(isrc: str) -> bool:
    return bool(_ISRC_RE.fullmatch((isrc or "").strip()))

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


def _deezer_search_tracks(query: str, limit: int) -> list[dict]:
    """Raw Deezer track search. Returns the data list, or raises on transport woes."""
    resp = httpx.get(
        f"{DEEZER_API_URL}/search",
        params={"q": query, "limit": max(1, min(limit, 50))},
        headers=_HEADERS,
        timeout=TIMEOUT_DEEZER,
        follow_redirects=True,
    )
    resp.raise_for_status()
    body = resp.json()
    if isinstance(body, dict) and body.get("error"):
        raise RuntimeError(f"Deezer API error: {body['error']}")
    return (body.get("data") or []) if isinstance(body, dict) else []


def _deezer_phrase(text: str) -> str:
    """Sanitise a field for a Deezer structured query phrase.

    Deezer wraps phrases in double quotes (artist:"x" track:"y"), so an embedded
    quote would slam the phrase shut early. We just swap them for spaces; nobody's
    artist name hinges on a literal double quote.
    """
    return (text or "").replace('"', " ").strip()


def _deezer_search_candidates(query: str, limit: int) -> list[dict]:
    """Gather Deezer track candidates: freetext, plus structured field queries.

    Deezer's freetext relevance can bury an exact track under fuzzier noise (a
    real casualty: 'BUNT. - LIEBE' vanishing under a heap of 'Immer Liebe'),
    whereas an artist:"x" track:"y" query pins it at the top. So when the query
    splits cleanly into "Artist - Title" we fire the structured query in the
    proper order first. The transposed ordering (Title - Artist) is only worth
    trying when the proper order draws a blank, since reversing the fields
    otherwise just drags in funny matches; it's there to rescue a query the user
    typed back-to-front, not to second-guess a good one. Results are de-duplicated
    by Deezer id; the caller's ISRC dedup mops up the rest.
    """
    raw_items: list[dict] = []
    seen_ids: set = set()

    def _absorb(items: list[dict]) -> None:
        for item in items:
            did = item.get("id")
            if did is not None and did in seen_ids:
                continue
            if did is not None:
                seen_ids.add(did)
            raw_items.append(item)

    def _search(q: str) -> list[dict]:
        try:
            return _deezer_search_tracks(q, limit)
        except Exception as exc:
            print(f"Monochrome: Deezer search variant {q!r} failed: {exc}")
            return []

    _absorb(_search(query))  # freetext primary

    artist, title = _parse_query_artist_title(query)
    if artist and title:
        forward = _search(f'artist:"{_deezer_phrase(artist)}" track:"{_deezer_phrase(title)}"')
        _absorb(forward)
        # Only reverse the fields if the proper order found nothing of its own.
        if not forward:
            _absorb(_search(f'artist:"{_deezer_phrase(title)}" track:"{_deezer_phrase(artist)}"'))

    return raw_items


def _qobuz_isrc_lookup(isrc: str) -> tuple[list[dict], bool]:
    """Ask the Qobuz proxies whether an ISRC exists in the catalogue.

    Returns (matching_items, transport_failure). A clean "Qobuz has never heard
    of it" is ([], False); ([], True) means every proxy fell over before
    answering, so absence proves nothing.
    """
    transport = True
    for base in _qobuz_proxy_urls():
        # Don't burn a 15s timeout on a proxy we already know is face-down. The
        # background probe re-checks and clears the mark when it recovers, so this
        # self-heals; meanwhile a whole page of ISRC lookups fails fast instead of
        # waiting ~15s per dead proxy per candidate (which is how a Monochrome
        # search ballooned to ~47s and got dropped by the search deadline).
        if _qobuz_proxy_recently_failed(base):
            continue
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
            # The proxy falls back to fulltext search when the query doesn't hit
            # the ISRC index, so only items that actually carry this ISRC count.
            matches = [t for t in items if (t.get("isrc") or "").upper() == isrc.upper()]
            _remember_qobuz_proxy_url(base)
            return matches, False
        except Exception:
            _mark_qobuz_proxy_failed(base)
            continue
    # Either every proxy errored, or they're all parked in the failure cooldown.
    # Can't verify, so report transport failure: callers keep the candidate as
    # unverified rather than wrongly dropping a track that may well exist.
    return [], transport


def _deezer_rank_bonus(rank: int | None) -> tuple[int, str | None]:
    """Deezer rank (0 to ~1M) squeezed into the same 0 to +10 tiebreaker as Tidal."""
    if not rank:
        return 0, None
    bonus = min(10, max(0, int(rank) // 100_000))
    if not bonus:
        return 0, None
    return bonus, f"deezer_rank=+{bonus}"


def _build_monochrome_result(track_id, isrc: str, quality: str, src: str,
                             title: str, artist: str, duration,
                             cover: str, quality_score: int,
                             score_breakdown: list[str]) -> dict:
    """Stamp out the normalised result dict every Monochrome leg emits.

    The download path keys off the ISRC baked into the monochrome:// URL, so the
    netloc (track_id) is purely informational. Keeps the source_url / video_id
    recipe in one place so the legs cannot drift apart.
    """
    params = urlencode({"isrc": isrc, "quality": quality, "src": src})
    source_url = f"monochrome://{track_id}?{params}"
    video_id = f"mono_{hashlib.md5(source_url.encode()).hexdigest()[:12]}"
    return {
        "video_id": video_id,
        "title": title,
        "channel": artist,
        "duration": parse_duration(duration) if duration else "",
        "thumbnail": cover,
        "is_playlist": False,
        "video_count": None,
        "source": "monochrome",
        "source_url": source_url,
        "quality": quality,
        "quality_score": quality_score,
        "score_breakdown": score_breakdown,
        "slskd_username": None,
        "slskd_filename": None,
        "slskd_size": None,
    }


def resolve_by_isrc(isrc: str, artist: str = "", title: str = "") -> dict | None:
    """Resolve a known ISRC straight to a downloadable Monochrome result.

    The precise first attempt for a track whose studio recording we have already
    identified (e.g. from a MusicBrainz album fetch). Returns a normalised result
    dict on a hit, or None when Qobuz cleanly has nothing for this ISRC, which is
    the caller's signal to fall back to a free-text search.

    Deliberately conservative on transport failure: if every proxy is face-down
    we return None rather than gambling on a maybe-dead ISRC. The fallback search
    is safer than blindly queueing a recording we could not confirm exists. This
    differs on purpose from _deezer_search_leg's optimistic "label it and hope"
    behaviour; here we have a clean fallback waiting, so we use it.
    """
    isrc = (isrc or "").strip().upper()
    if not _isrc_valid(isrc):
        return None

    matches, transport_failure = _qobuz_isrc_lookup(isrc)
    if not matches:
        # Clean miss or proxies all down; either way, let the caller fall back.
        return None

    quality = "HI_RES_LOSSLESS" if any(t.get("hires") for t in matches) else "LOSSLESS"

    # Prefer the catalogue's own metadata; fall back to the caller's hints when a
    # field is missing (the download keys off the ISRC, so this is cosmetic).
    item = matches[0]
    item_artist = ((item.get("performer") or {}).get("name") or "").strip()
    item_title = (item.get("title") or "").strip()
    cover = (((item.get("album") or {}).get("image") or {}).get("large", "") or
             ((item.get("album") or {}).get("image") or {}).get("thumbnail", "")) or ""
    duration = item.get("duration") or 0

    bonus = _QUALITY_BONUS[quality]
    # Flat high score: this is a direct, identity-pinned pick, not a contender in
    # a ranked free-text list, so it sits above anything the search legs produce.
    quality_score = 1000 + bonus
    score_breakdown = ["via=isrc-direct", f"source_quality=+{bonus}"]

    return _build_monochrome_result(
        track_id=item.get("id", ""),
        isrc=isrc,
        quality=quality,
        src="deezer",
        title=item_title or title,
        artist=item_artist or artist,
        duration=duration,
        cover=cover,
        quality_score=quality_score,
        score_breakdown=score_breakdown,
    )


def _deezer_search_leg(query: str, limit: int) -> list[dict]:
    """Primary Monochrome search leg: Deezer finds the track, Qobuz confirms it.

    Deezer's catalogue search is far more forgiving of typos than the hifi-api,
    every result carries a clean ISRC, and `title_version` flags live/karaoke
    versions explicitly instead of making us guess from punctuation. Each
    candidate ISRC is then verified against Qobuz so we only show tracks we can
    actually download, labelled with the quality Qobuz really has.
    """
    try:
        raw_items = _deezer_search_candidates(query, limit * 3)
    except Exception as exc:
        print(f"Monochrome: Deezer search leg errored: {exc}")
        return []
    if not raw_items:
        return []

    candidates = []
    seen_isrcs = set()
    for item in raw_items:
        isrc = (item.get("isrc") or "").strip().upper()
        title = (item.get("title") or "").strip()
        artist = ((item.get("artist") or {}).get("name") or "").strip()
        if not (_isrc_valid(isrc) and title and artist):
            continue
        if isrc in seen_isrcs:
            continue
        seen_isrcs.add(isrc)

        album = ((item.get("album") or {}).get("title") or "")
        cover = ((item.get("album") or {}).get("cover_big") or
                 (item.get("album") or {}).get("cover") or "")
        duration = item.get("duration") or 0
        version = item.get("title_version") or ""

        combined = f"{artist} - {title}"
        quality_score, score_breakdown = score_search_result_with_breakdown(
            combined, artist, query,
            duration_seconds=duration or None,
            view_count=None,
            album=album,
        )
        for delta, reason in (
            _version_penalty(version, query),
            _album_edition_penalty(album, query),
            _deezer_rank_bonus(item.get("rank")),
        ):
            if delta:
                quality_score += delta
                score_breakdown.append(reason)

        candidates.append({
            "deezer_id": item.get("id", ""),
            "isrc": isrc,
            "title": title,
            "artist": artist,
            "album": album,
            "cover": cover,
            "duration": duration,
            "quality_score": quality_score,
            "score_breakdown": score_breakdown,
        })

    if not candidates:
        return []

    # Only Qobuz-verify the contenders; no point burning proxy calls on the
    # page-two also-rans.
    candidates.sort(key=lambda c: c["quality_score"], reverse=True)
    candidates = candidates[:limit]

    with ThreadPoolExecutor(max_workers=min(4, len(candidates))) as pool:
        lookups = list(pool.map(lambda c: _qobuz_isrc_lookup(c["isrc"]), candidates))

    results = []
    for cand, (qobuz_items, transport_failure) in zip(candidates, lookups):
        if qobuz_items:
            quality_str = "HI_RES_LOSSLESS" if any(t.get("hires") for t in qobuz_items) else "LOSSLESS"
        elif transport_failure:
            # Proxies all face-down; can't verify, but qbdlx may still deliver
            # at download time. Label conservatively rather than dropping.
            quality_str = "LOSSLESS"
            cand["score_breakdown"].append("qobuz_unverified (proxies down)")
        else:
            # Qobuz answered and has nothing for this ISRC: not downloadable.
            continue

        bonus = _QUALITY_BONUS[quality_str]
        quality_score = cand["quality_score"] + bonus
        breakdown = cand["score_breakdown"] + [f"source_quality=+{bonus}", "via=deezer-isrc"]

        results.append(_build_monochrome_result(
            track_id=cand["deezer_id"],
            isrc=cand["isrc"],
            quality=quality_str,
            src="deezer",
            title=cand["title"],
            artist=cand["artist"],
            duration=cand["duration"],
            cover=cand["cover"],
            quality_score=quality_score,
            score_breakdown=breakdown,
        ))

    results.sort(key=lambda x: x["quality_score"], reverse=True)
    return results


def _deezer_isrc_rescue(artist: str, title: str, bad_isrc: str) -> str:
    """Find the canonical studio ISRC for artist/title via Deezer.

    Used when the ISRC we were handed (usually by Tidal's metadata) is either
    malformed or unknown to Qobuz. Returns "" rather than guessing: a confident
    miss beats a wrong track.
    """
    artist = (artist or "").strip()
    title = (title or "").strip()
    if not (artist or title):
        return ""
    query = f"{artist} {title}".strip()
    try:
        raw_items = _deezer_search_tracks(query, 10)
    except Exception as exc:
        print(f"Monochrome: Deezer ISRC rescue search failed: {exc}")
        return ""

    best_isrc, best_conf = "", 0.0
    for item in raw_items:
        isrc = (item.get("isrc") or "").strip().upper()
        if not _isrc_valid(isrc) or isrc == (bad_isrc or "").upper():
            continue
        # The whole point is escaping live/karaoke/remix variants, so any
        # penalised version label disqualifies the candidate outright.
        version = item.get("title_version") or ""
        penalty, _reason = _version_penalty(version, query)
        if penalty:
            continue
        confidence, _bd = compute_match_confidence(
            artist or None,
            title or None,
            item.get("title") or "",
            ((item.get("artist") or {}).get("name")) or None,
        )
        if confidence > best_conf:
            best_conf, best_isrc = confidence, isrc
    if best_conf >= 0.6:
        return best_isrc
    return ""


def _qbdlx_search_fallback(query: str, limit: int) -> list[dict]:
    """Search Qobuz directly via qbdlx when the hifi-api search leg is dead.

    The hifi-api (Tidal gateway) is the flaky single point of failure for
    Monochrome search. qbdlx already talks to the real Qobuz API for downloads,
    so we reuse its token pool to search the catalogue too. Results carry an
    ISRC, so the existing monochrome:// download path works unchanged; quality
    is honestly LOSSLESS (16/44.1) since the free shared tokens cap there, even
    when Qobuz reports a hi-res master exists.
    """
    try:
        from qbdlx import search_qobuz_catalog
        raw_items = search_qobuz_catalog(query, limit * 2)
    except Exception as exc:
        print(f"Monochrome: qbdlx search fallback errored: {exc}")
        return []
    if not raw_items:
        return []

    results = []
    seen_isrcs = set()
    for item in raw_items:
        if not item.get("streamable", True):
            continue
        isrc = (item.get("isrc") or "").strip()
        title = (item.get("title") or "").strip()
        artist = ((item.get("performer") or {}).get("name") or "").strip()
        # No ISRC means the download leg can't resolve it on Qobuz; skip it,
        # same rule the hifi-api path applies.
        if not (isrc and title and artist):
            continue
        if isrc in seen_isrcs:
            continue
        seen_isrcs.add(isrc)

        album = ((item.get("album") or {}).get("title") or "")
        cover = ((item.get("album") or {}).get("image") or {}).get("large", "") or \
                ((item.get("album") or {}).get("image") or {}).get("thumbnail", "")
        duration = item.get("duration") or 0
        version = item.get("version") or ""

        combined = f"{artist} - {title}"
        quality_score, score_breakdown = score_search_result_with_breakdown(
            combined, artist, query,
            duration_seconds=duration or None,
            view_count=None,
            album=album,
        )
        bonus = _QUALITY_BONUS["LOSSLESS"]
        quality_score += bonus
        score_breakdown.append(f"source_quality=+{bonus}")
        score_breakdown.append("via=qbdlx-direct (hifi-api down)")

        for delta, reason in (
            _version_penalty(version, query),
            _album_edition_penalty(album, query),
        ):
            if delta:
                quality_score += delta
                score_breakdown.append(reason)

        # The item id slot carries the Qobuz track id here; the download path keys
        # off the ISRC, so the netloc is purely informational. The src marker
        # stops the Tidal stream fallback treating a Qobuz id as a Tidal one.
        results.append(_build_monochrome_result(
            track_id=item.get("id", ""),
            isrc=isrc,
            quality="LOSSLESS",
            src="qbdlx",
            title=title,
            artist=artist,
            duration=duration,
            cover=cover,
            quality_score=quality_score,
            score_breakdown=score_breakdown,
        ))

    results.sort(key=lambda x: x["quality_score"], reverse=True)
    if results:
        print(f"Monochrome: hifi-api search empty, served {len(results)} result(s) via qbdlx direct Qobuz")
    return results[:limit]


def _result_isrc(result: dict) -> str:
    """Pull the ISRC back out of a result's monochrome:// source_url."""
    try:
        params = parse_qs(urlparse(result.get("source_url", "")).query)
        return ((params.get("isrc") or [""])[0]).upper()
    except Exception:
        return ""


_DEEZER_CONFIDENT_MATCH_FLOOR = 0.8


def _has_confident_match(results: list[dict], query: str) -> bool:
    """True if any result is a confident artist/title match for the query.

    Used to decide whether a full Deezer page is good enough to skip the Tidal
    leg. Needs a clean 'Artist - Title' split to judge against; without one we
    can't fairly score, so we keep the old "a full page is fine" behaviour.
    """
    artist, title = _parse_query_artist_title(query)
    if not (artist and title):
        return True
    for r in results:
        confidence, _bd = compute_match_confidence(
            artist or None,
            title or None,
            r.get("title") or "",
            r.get("channel") or None,
            query=query,
        )
        if confidence >= _DEEZER_CONFIDENT_MATCH_FLOOR:
            return True
    return False


def search_monochrome(query: str, limit: int) -> list[dict]:
    """Search the Monochrome ladder and return normalised result dicts.

    Deezer is the primary leg (clean ISRCs, Qobuz-verified, typo-tolerant).
    The Tidal hifi-api tops up when Deezer comes back light (catalogue gaps,
    or Deezer itself having a moment), and qbdlx direct Qobuz is the final
    safety net when both metadata legs are face-down.
    """
    deezer_results = _deezer_search_leg(query, limit)
    # A full page of Deezer hits is only worth trusting if one of them actually
    # matches what was asked for. Deezer loves to return a tidy ten fuzzy
    # near-misses, and that used to short-circuit the Tidal leg, the very source
    # that might hold the track Deezer's ranking buried. So: full page AND a
    # confident match before we call it a day.
    if len(deezer_results) >= limit and _has_confident_match(deezer_results, query):
        return deezer_results[:limit]

    hifi_results = _hifi_search_leg(query, limit)
    if hifi_results:
        seen = {_result_isrc(r) for r in deezer_results}
        merged = deezer_results + [r for r in hifi_results if _result_isrc(r) not in seen]
        merged.sort(key=lambda x: x["quality_score"], reverse=True)
        return merged[:limit]
    if deezer_results:
        return deezer_results[:limit]
    return _qbdlx_search_fallback(query, limit)


def _hifi_search_leg(query: str, limit: int) -> list[dict]:
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

            params = urlencode({"isrc": isrc, "quality": quality_str, "src": "tidal"})
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
        # The caller (search_monochrome) decides what to fall back to;
        # this leg just reports honestly that it came up empty.
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
            # The proxy degrades to fulltext search when the query misses the
            # ISRC index, so insist on an exact ISRC match; and when several
            # editions carry the same ISRC, prefer the hi-res master.
            exact = [t for t in items if (t.get("isrc") or "").upper() == isrc.upper()]
            if exact:
                items = exact
                if quality_fmt == 27:
                    items = sorted(items, key=lambda t: bool(t.get("hires")), reverse=True)
            elif items:
                items = []
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


def _parse_tidal_track_payload(body) -> str:
    """Extract a direct (non-DRM) stream URL from a hifi-api /track response.

    The instances drift between response shapes as they update, so accept the
    known variants: a bare URL field, a urls list, or a base64 BTS manifest.
    A DASH/Widevine manifest yields "" because we don't do DRM circumvention;
    LOSSLESS and below come through as plain BTS with direct URLs.
    """
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict):
        return ""
    for key in ("OriginalTrackUrl", "originalTrackUrl", "url"):
        url = data.get(key)
        if isinstance(url, str) and url.startswith("http"):
            return url
    urls = data.get("urls")
    if isinstance(urls, list) and urls and isinstance(urls[0], str) and urls[0].startswith("http"):
        return urls[0]
    manifest = data.get("manifest")
    mime = (data.get("manifestMimeType") or "").lower()
    if isinstance(manifest, str) and "dash" not in mime:
        try:
            decoded = json.loads(base64.b64decode(manifest))
            decoded_urls = decoded.get("urls") or []
            if decoded_urls and isinstance(decoded_urls[0], str) and decoded_urls[0].startswith("http"):
                return decoded_urls[0]
        except Exception:
            pass
    return ""


def _tidal_stream_url(tidal_id: str, quality: str) -> str:
    """Resolve a direct stream URL from the Tidal hifi-api /track endpoint.

    Last-ditch leg for tracks Qobuz simply doesn't stock (or whose ISRC is
    beyond rescue) but which Tidal happily streams; this is exactly how the
    Monochrome web player serves them. Caps at LOSSLESS (16/44.1 FLAC), since
    Tidal's hi-res is Widevine-wrapped and we are not in that business.
    """
    last_err: Exception | None = None
    for base in _hifi_api_urls():
        try:
            resp = httpx.get(
                f"{base}/track/",
                params={"id": tidal_id, "quality": quality},
                headers=_HEADERS,
                timeout=TIMEOUT_MONOCHROME_SEARCH,
                follow_redirects=True,
            )
            resp.raise_for_status()
            url = _parse_tidal_track_payload(resp.json())
            if url:
                _remember_hifi_api_url(base)
                return url
            last_err = RuntimeError(f"{base}: no direct URL in /track payload")
        except Exception as exc:
            last_err = exc
            continue
    raise RuntimeError(f"Tidal stream unavailable for id {tidal_id} at {quality}: {last_err}")


def download_monochrome_track(source_url: str, output_path: Path,
                              artist_hint: str = "", title_hint: str = "") -> None:
    """Resolve a monochrome:// source URL and stream the FLAC to output_path.

    The output_path will have whatever extension the caller gave it (typically .mp3
    since we reuse _process_direct_mp3_download). That's fine; ffmpeg detects the
    actual container format regardless of extension.

    artist_hint/title_hint come from the job row and power the Deezer ISRC
    rescue when the stored ISRC turns out to be junk or unknown to Qobuz.
    """
    parsed = urlparse(source_url)
    tidal_id = parsed.netloc
    params = parse_qs(parsed.query)
    isrc    = (params.get("isrc") or [""])[0]
    quality = (params.get("quality") or ["LOSSLESS"])[0]
    src_leg = (params.get("src") or [""])[0]

    if not isrc:
        raise RuntimeError(f"Monochrome: no ISRC in source_url {source_url!r}")

    # Tidal occasionally ships ISRCs that fail the most basic format check
    # (ampersands, really?). Qobuz will never resolve those, so ask Deezer for
    # the real one before wasting retry rounds on a lost cause.
    rescued = False
    if not _isrc_valid(isrc):
        print(f"Monochrome: ISRC {isrc!r} is malformed; asking Deezer for the real one")
        rescue_isrc = _deezer_isrc_rescue(artist_hint, title_hint, isrc)
        if rescue_isrc:
            print(f"Monochrome: Deezer rescue swapped ISRC {isrc!r} -> {rescue_isrc}")
            isrc = rescue_isrc
            rescued = True

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
    def _resolve_via_qbdlx() -> str:
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
                return fallback_url
        return ""

    if not cdn_url:
        cdn_url = _resolve_via_qbdlx()

    # Qobuz genuinely has nothing under this ISRC. Before giving up on Qobuz,
    # ask Deezer whether the ISRC we were handed is simply wrong for the
    # recording (Tidal metadata strikes again) and retry with the real one.
    if not cdn_url and not rescued:
        rescue_isrc = _deezer_isrc_rescue(artist_hint, title_hint, isrc)
        if rescue_isrc and rescue_isrc != isrc.upper():
            print(f"Monochrome: Qobuz had nothing for ISRC {isrc}; retrying with Deezer's {rescue_isrc}")
            isrc = rescue_isrc
            cdn_url, last_error, _ = _resolve_cdn_url()
            if not cdn_url:
                cdn_url = _resolve_via_qbdlx()

    # Final leg: the track may live on Tidal but not Qobuz at all, in which
    # case the hifi-api can stream it directly (this is how the Monochrome web
    # player serves such tracks). Only for tidal-sourced results, where the
    # netloc really is a Tidal ID; hi-res is DRM-locked there, so LOSSLESS is
    # the honest ceiling.
    if not cdn_url and src_leg == "tidal" and tidal_id.isdigit():
        for tier in [t for t in candidates if t in ("LOSSLESS", "HIGH")]:
            try:
                cdn_url = _tidal_stream_url(tidal_id, tier)
                print(f"Monochrome: Qobuz exhausted, streaming tidal/{tidal_id} at {tier} via hifi-api")
                break
            except Exception as exc:
                last_error = exc

    if not cdn_url:
        raise RuntimeError(
            f"Monochrome: no stream available for ISRC {isrc} at any quality tier "
            f"on any leg (Qobuz proxies, qbdlx, Deezer rescue, Tidal stream) "
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
