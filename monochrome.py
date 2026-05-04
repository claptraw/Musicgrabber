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
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qs

import httpx

from constants import (
    TIMEOUT_MONOCHROME_SEARCH,
    TIMEOUT_MONOCHROME_DOWNLOAD,
    MONOCHROME_HIFI_API_URL,
    MONOCHROME_QOBUZ_PROXY_URL,
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

# Score bonuses per quality tier — matched to Soulseek's hi-res/lossless stack
# (Soulseek: 150 quality + 35 trust + 25 lossless = 210 for hi-res FLAC, 160 for FLAC)
_QUALITY_BONUS = {
    "HI_RES_LOSSLESS": 210,
    "LOSSLESS":        160,
    "HIGH":             30,
}

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0",
}


def monochrome_enabled() -> bool:
    return get_setting_bool("source_monochrome_enabled", False)


def _hifi_api_url() -> str:
    from settings import get_setting
    return get_setting("monochrome_hifi_api_url", MONOCHROME_HIFI_API_URL).rstrip("/")


def _qobuz_proxy_url() -> str:
    from settings import get_setting
    return get_setting("monochrome_qobuz_proxy_url", MONOCHROME_QOBUZ_PROXY_URL).rstrip("/")


def _cover_url(cover_uuid: str) -> str:
    """Convert Tidal cover UUID to a resources.tidal.com thumbnail URL."""
    if not cover_uuid:
        return ""
    return f"https://resources.tidal.com/images/{cover_uuid.replace('-', '/')}/320x320.jpg"


def _best_quality(tags: list[str]) -> tuple[str, int]:
    """Return (quality_string, qobuz_fmt) for the best available quality tier."""
    for tag in ("HIRES_LOSSLESS", "LOSSLESS", "HIGH"):
        if tag in tags:
            return _QUALITY_MAP[tag]
    return ("HIGH", 6)


def search_monochrome(query: str, limit: int) -> list[dict]:
    """Search Tidal via hifi-api and return normalised result dicts."""
    try:
        base = _hifi_api_url()
        resp = httpx.get(
            f"{base}/search",
            params={"s": query, "limit": limit * 3},
            headers=_HEADERS,
            timeout=TIMEOUT_MONOCHROME_SEARCH,
        )
        resp.raise_for_status()

        items = resp.json().get("data", {}).get("items", [])
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
            cover_id  = (item.get("album") or {}).get("cover", "")

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
            )
            quality_score += bonus
            score_breakdown.append(f"source_quality=+{bonus}")

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


def _get_qobuz_stream_url(isrc: str, quality_fmt: int) -> str:
    """Look up ISRC on the Qobuz proxy, then get a time-limited CDN stream URL."""
    base = _qobuz_proxy_url()

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
        raise RuntimeError(f"Qobuz proxy: no results for ISRC {isrc!r}")

    # Take the first result; Tidal ISRCs map to a unique recording
    qobuz_id = items[0].get("id")
    if not qobuz_id:
        raise RuntimeError("Qobuz proxy: result missing track ID")

    resp2 = httpx.get(
        f"{base}/api/download-music",
        params={"track_id": qobuz_id, "quality": quality_fmt},
        headers=_HEADERS,
        timeout=TIMEOUT_MONOCHROME_SEARCH,
    )
    resp2.raise_for_status()

    body2 = resp2.json()
    if not body2.get("success"):
        raise RuntimeError(f"Qobuz proxy: download-music failed: {body2}")

    url = (body2.get("data") or {}).get("url", "")
    if not url:
        raise RuntimeError("Qobuz proxy: no URL in response")

    return url


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
    cdn_url = ""
    last_error: Exception | None = None
    for tier in candidates:
        fmt = _SOURCE_QUALITY_TO_QOBUZ_FORMAT.get(tier, 7)
        try:
            cdn_url = _get_qobuz_stream_url(isrc, fmt)
            if tier != quality:
                print(f"Monochrome: requested {quality} unavailable, fell back to {tier} for ISRC {isrc}")
            break
        except Exception as exc:
            last_error = exc
            continue
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
    24-bit/192kHz at users who only wanted to hear a few seconds.
    """
    if not isrc:
        raise RuntimeError("Monochrome preview requires an ISRC")
    return _get_qobuz_stream_url(isrc, 7)


def fetch_tidal_playlist_tracks(playlist_uuid: str) -> tuple[list[tuple[str, str]], str]:
    """Fetch a Tidal playlist's track list via hifi-api.

    Returns ([(artist, title), ...], playlist_name).
    """
    base = _hifi_api_url()
    resp = httpx.get(
        f"{base}/playlist",
        params={"id": playlist_uuid},
        headers=_HEADERS,
        timeout=30,
    )
    resp.raise_for_status()

    data  = resp.json().get("data", {})
    name  = data.get("title") or "Tidal Playlist"
    items = (data.get("tracks") or {}).get("items", [])

    tracks = []
    for item in items:
        track_title = item.get("title", "").strip()
        artist_name = (item.get("artist") or {}).get("name", "").strip()
        if track_title and artist_name:
            tracks.append((artist_name, track_title))

    return tracks, name
