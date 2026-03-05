"""
MusicGrabber - Search Source Registry

Extensible source architecture. YouTube and SoundCloud use yt-dlp with
different search prefixes; Monochrome hits the Tidal API directly for
proper lossless results. Adding a new source is one function and one
registry entry.
"""

import json
import hashlib
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

import base64

import httpx

from constants import (
    TIMEOUT_YTDLP_SEARCH,
    SOUNDCLOUD_SEARCH_MULTIPLIER, SOUNDCLOUD_SEARCH_MIN_FETCH,
    MONOCHROME_API_URL, MONOCHROME_COVER_BASE, TIMEOUT_MONOCHROME_API,
    SEARCH_MAX_PER_SOURCE,
)
from db import get_blacklisted_video_ids, get_blacklisted_uploaders
from metadata import fetch_mb_expected_duration
from settings import get_setting, get_setting_bool
from mp3phoenix import search_mp3phoenix
from youtube import search_youtube, score_search_result, parse_duration, _normalise_search_text, _parse_query_artist_title, _query_has_variation

# Penalty large enough to push blacklisted uploaders to the bottom of results
# without hiding them entirely  -  the user might still want to see them
_BLACKLIST_UPLOADER_PENALTY = 500
_MONOCHROME_URL_RE = re.compile(r"^https?://(?:www\.)?monochrome\.tf/", re.IGNORECASE)


# ---------------------------------------------------------------------------
# SoundCloud search
# ---------------------------------------------------------------------------

def parse_soundcloud_search_results(stdout: str, query: str | None = None) -> list[dict]:
    """Parse yt-dlp JSON output from an scsearch query."""
    results = []
    for line in stdout.strip().split("\n"):
        if not line:
            continue
        try:
            data = json.loads(line)
            # SoundCloud sets are 'playlist' type  -  skip them for single-track search
            is_playlist = data.get("_type") == "playlist"
            if is_playlist:
                continue

            title = data.get("title", "Unknown")
            # SoundCloud uses 'uploader' rather than 'channel'
            channel = data.get("uploader", data.get("channel", "Unknown"))
            duration_secs = data.get("duration") or 0
            views = data.get("view_count")
            quality_score = score_search_result(
                title, channel, query,
                duration_seconds=duration_secs or None,
                view_count=views,
            )

            results.append({
                "video_id": data.get("id", ""),
                "title": title,
                "channel": channel,
                "duration": parse_duration(duration_secs) if duration_secs else "",
                "thumbnail": data.get("thumbnail", ""),
                "is_playlist": False,
                "video_count": None,
                "source": "soundcloud",
                "source_url": data.get("webpage_url", data.get("url", "")),
                "quality": None,
                "quality_score": quality_score,
                "slskd_username": None,
                "slskd_filename": None,
            })
        except json.JSONDecodeError:
            continue
    return results


def search_soundcloud(query: str, limit: int) -> list[dict]:
    """Search SoundCloud via yt-dlp and return normalised results."""
    try:
        fetch_limit = max(limit * SOUNDCLOUD_SEARCH_MULTIPLIER, SOUNDCLOUD_SEARCH_MIN_FETCH)

        cmd = [
            "yt-dlp",
            "--dump-json",
            "--flat-playlist",
            "--no-warnings",
            f"scsearch{fetch_limit}:{query}",
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_YTDLP_SEARCH)

        if result.returncode != 0:
            return []

        results = parse_soundcloud_search_results(result.stdout, query=query)
        results.sort(key=lambda x: x["quality_score"], reverse=True)
        return results[:limit]

    except Exception as e:
        print(f"SoundCloud search error: {e}")
        return []


# ---------------------------------------------------------------------------
# Monochrome  -  full search via the Monochrome/Tidal API, plus URL resolution
# ---------------------------------------------------------------------------

def _monochrome_cover_url(cover_uuid: str) -> str:
    """Turn a Tidal cover UUID into a CDN thumbnail URL.

    UUIDs come as 'ccc50c5e-b347-4faa-9524-924dc8f071fc' and need
    splitting into path segments for the Tidal image CDN.
    """
    if not cover_uuid:
        return ""
    return f"{MONOCHROME_COVER_BASE}/{cover_uuid.replace('-', '/')}/320x320.jpg"


def _score_monochrome_result(item: dict, query: str | None = None) -> int:
    """Score a Monochrome/Tidal search result.

    Lossless gets a genuine quality bonus  -  unlike YouTube where 'FLAC'
    is just a lossy-to-lossless transcode, this is the real deal.
    """
    title = item.get("title", "")
    artist_name = (item.get("artist") or {}).get("name", "")
    album_title = (item.get("album") or {}).get("title", "")
    audio_quality = item.get("audioQuality", "")
    popularity = item.get("popularity") or 0
    duration = item.get("duration") or 0

    # Start with the standard relevance scoring  -  pass album title so cover/tribute
    # albums (e.g. "Piano Covers of Taylor Swift") get penalised correctly.
    score = score_search_result(
        title, artist_name, query,
        duration_seconds=duration or None,
        view_count=None,
        album=album_title,
    )

    # Quality bonus  -  the whole point of Monochrome.  50-point gap over MP3Phoenix
    # (320 kbps = +30) ensures lossless always wins on equal relevance.  Still loses
    # to a stacked official YouTube result (Topic + official audio = ~70 pts).
    quality_bonuses = {
        "HI_RES_LOSSLESS": 100,
        "LOSSLESS": 80,
        "HIGH": 30,
    }
    score += quality_bonuses.get(audio_quality, 0)

    # Title variant penalty: if the query has no parenthetical suffix but the
    # Tidal result does (e.g. "Hey Man Nice Shot (½ oz)" vs "hey man nice shot"),
    # it's a non-standard variant.  Benign remaster/edition tags get a light touch;
    # opaque suffixes get enough of a penalty to neutralise the quality bonus.
    if query:
        _, expected_title = _parse_query_artist_title(query)
        if expected_title and not re.search(r'[\(\[]', expected_title):
            if re.search(r'[\(\[]', title):
                paren_content = " ".join(re.findall(r'[\(\[]([^\)\]]*)[\)\]]', title.lower()))
                _benign_variant_re = r'\b(remaster(?:ed)?|expanded|deluxe|edition|feat(?:uring)?|ft|bonus|single|stereo|mono|explicit)\b'
                if not re.search(_benign_variant_re, paren_content):
                    score -= 110  # Neutralise even HI_RES_LOSSLESS for unknown variants

    # Artist mismatch penalty: if the query specifies an artist and the Tidal
    # result is by a completely different artist, the quality bonus must not
    # override a correct-artist YouTube result.  "Venjent - Who Are Ya" should
    # not match "Wolf Parade - Who Are Ya" just because Tidal has it lossless.
    if query:
        expected_artist, _ = _parse_query_artist_title(query)
        if expected_artist:
            expected_norm = _normalise_search_text(expected_artist)
            result_norm = _normalise_search_text(artist_name)
            # Neither contains the other  -  completely different artist.
            # Subtract enough to neutralise even Hi-Res lossless bonus.
            if expected_norm and result_norm and \
                    expected_norm not in result_norm and result_norm not in expected_norm:
                score -= 150

    # Popularity tiebreaker (0–15 points, log-ish scale)
    score += min(popularity // 10, 15)

    return score


def _search_monochrome_api(query: str, limit: int) -> list[dict]:
    """Search the Monochrome API for tracks matching a free-text query."""
    try:
        resp = httpx.get(
            f"{MONOCHROME_API_URL}/search/",
            params={"s": query},
            timeout=TIMEOUT_MONOCHROME_API,
        )
        resp.raise_for_status()
        data = resp.json().get("data") or {}
        items = data.get("items") or []
    except Exception as e:
        print(f"Monochrome API search error: {e}")
        return []

    results = []
    for item in items:
        if not item.get("streamReady"):
            continue

        track_id = str(item.get("id", ""))
        title = item.get("title", "Unknown")
        artist_obj = item.get("artist") or {}
        artist_name = artist_obj.get("name", "Unknown")
        album_obj = item.get("album") or {}
        duration_secs = item.get("duration") or 0
        audio_quality = item.get("audioQuality", "")

        results.append({
            "video_id": track_id,
            "title": title,
            "channel": artist_name,
            "duration": parse_duration(duration_secs) if duration_secs else "",
            "thumbnail": _monochrome_cover_url(album_obj.get("cover", "")),
            "is_playlist": False,
            "video_count": None,
            "source": "monochrome",
            "source_url": f"https://monochrome.tf/track/{track_id}",
            "quality": audio_quality if audio_quality else None,
            "quality_score": _score_monochrome_result(item, query),
            "slskd_username": None,
            "slskd_filename": None,
            # Extra Monochrome metadata  -  available for richer tagging at download time
            "monochrome_album": album_obj.get("title"),
            "monochrome_album_cover": album_obj.get("cover"),
            "monochrome_isrc": item.get("isrc"),
            "monochrome_explicit": item.get("explicit", False),
        })

    results.sort(key=lambda x: x["quality_score"], reverse=True)
    return results[:limit]


def _resolve_monochrome_url(query: str, limit: int) -> list[dict]:
    """Resolve a pasted monochrome.tf URL via yt-dlp (legacy path)."""
    try:
        cmd = [
            "yt-dlp",
            "--dump-json",
            "--flat-playlist",
            "--no-warnings",
            query,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_YTDLP_SEARCH)
        if result.returncode != 0:
            return []

        results = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            try:
                data = json.loads(line)
                if data.get("_type") == "playlist":
                    continue

                title = data.get("title", "Unknown")
                channel = data.get("uploader", data.get("channel", "Monochrome"))
                duration_secs = data.get("duration") or 0
                source_url = data.get("webpage_url") or data.get("url") or query
                video_id = data.get("id") or hashlib.md5(source_url.encode()).hexdigest()[:16]

                results.append({
                    "video_id": str(video_id),
                    "title": title,
                    "channel": channel,
                    "duration": parse_duration(duration_secs) if duration_secs else "",
                    "thumbnail": data.get("thumbnail", ""),
                    "is_playlist": False,
                    "video_count": None,
                    "source": "monochrome",
                    "source_url": source_url,
                    "quality": None,
                    "quality_score": score_search_result(
                        title, channel, query,
                        duration_seconds=duration_secs or None,
                        view_count=data.get("view_count"),
                    ),
                    "slskd_username": None,
                    "slskd_filename": None,
                })
            except json.JSONDecodeError:
                continue

        results.sort(key=lambda x: x["quality_score"], reverse=True)
        return results[:limit]
    except Exception as e:
        print(f"Monochrome URL resolve error: {e}")
        return []


def search_monochrome(query: str, limit: int) -> list[dict]:
    """Search Monochrome for tracks, or resolve a pasted URL."""
    query = (query or "").strip()
    if not query:
        return []

    # Pasted URL  -  resolve via yt-dlp (handles edge cases the API can't)
    if _MONOCHROME_URL_RE.match(query):
        return _resolve_monochrome_url(query, limit)

    # Free-text search via the Monochrome API
    return _search_monochrome_api(query, limit)


def get_monochrome_stream_url(track_id: str, quality: str = "LOSSLESS") -> dict:
    """Fetch the stream manifest for a Monochrome/Tidal track.

    Returns a dict with 'url' (direct CDN link), 'mime_type', 'codec',
    'bit_depth', and 'sample_rate'. Raises on failure.
    """
    resp = httpx.get(
        f"{MONOCHROME_API_URL}/track/",
        params={"id": track_id, "quality": quality},
        timeout=TIMEOUT_MONOCHROME_API,
    )
    resp.raise_for_status()
    data = resp.json().get("data") or {}
    if not data.get("manifest"):
        raise ValueError(f"No manifest returned for track {track_id}")

    manifest = json.loads(base64.b64decode(data["manifest"]))
    urls = manifest.get("urls") or []
    if not urls:
        raise ValueError(f"Empty URL list in manifest for track {track_id}")

    return {
        "url": urls[0],
        "mime_type": manifest.get("mimeType", "audio/flac"),
        "codec": manifest.get("codecs", "flac"),
        "encryption": manifest.get("encryptionType", "NONE"),
        "bit_depth": data.get("bitDepth"),
        "sample_rate": data.get("sampleRate"),
        "audio_quality": data.get("audioQuality", quality),
    }


def get_monochrome_track_info(track_id: str) -> dict | None:
    """Fetch full track metadata from the Monochrome API.

    Returns the raw API response data dict, or None on failure.
    """
    try:
        resp = httpx.get(
            f"{MONOCHROME_API_URL}/info/",
            params={"id": track_id},
            timeout=TIMEOUT_MONOCHROME_API,
        )
        resp.raise_for_status()
        return resp.json().get("data")
    except Exception as e:
        print(f"Monochrome track info error: {e}")
        return None


# ---------------------------------------------------------------------------
# Source registry  -  add new sources here
# ---------------------------------------------------------------------------

SOURCE_REGISTRY = {
    "youtube": {
        "label": "YouTube",
        "badge": "YT",
        "colour": "#ff0000",
        "search_fn": search_youtube,
        "has_preview": True,
    },
    "mp3phoenix": {
        "label": "MP3Phoenix",
        "badge": "PX",
        "colour": "#e05c00",
        "search_fn": search_mp3phoenix,
        "has_preview": True,
    },
    "soundcloud": {
        "label": "SoundCloud",
        "badge": "SC",
        "colour": "#ff5500",
        "search_fn": search_soundcloud,
        "has_preview": True,
    },
    "monochrome": {
        "label": "Monochrome",
        "badge": "MO",
        "colour": "#111111",
        "search_fn": search_monochrome,
        "has_preview": True,
    },
}


def _mb_duration_lookup(query: str) -> float | None:
    """Return the MusicBrainz canonical duration for an artist/title query, or None.

    Only fires when the query contains a ' - ' separator AND doesn't request a
    specific variation (remix, live, etc.). Silent on any failure.
    """
    if _query_has_variation(query):
        return None
    artist, title = _parse_query_artist_title(query)
    if not artist or not title:
        return None
    return fetch_mb_expected_duration(artist, title)


def _apply_mb_duration_scores(results: list[dict], expected_duration_secs: float) -> None:
    """Mutate quality_score on each result based on delta from MB expected duration.

    Operates in-place  -  call after blacklist filtering, before final sort.
    """
    for r in results:
        dur_str = r.get("duration", "")
        if not dur_str:
            continue
        # duration field is stored as "M:SS" or "H:MM:SS" string
        parts = dur_str.split(":")
        try:
            if len(parts) == 2:
                secs = int(parts[0]) * 60 + int(parts[1])
            elif len(parts) == 3:
                secs = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            else:
                continue
        except (ValueError, IndexError):
            continue
        if secs <= 0:
            continue
        delta_ratio = abs(secs - expected_duration_secs) / expected_duration_secs
        if delta_ratio <= 0.02:
            r["quality_score"] += 40
        elif delta_ratio <= 0.05:
            r["quality_score"] += 20
        elif delta_ratio <= 0.10:
            pass
        elif delta_ratio <= 0.25:
            r["quality_score"] -= 30
        else:
            r["quality_score"] -= 60


def _apply_blacklist_filter(results: list[dict], source: str | None = None) -> list[dict]:
    """Remove blacklisted videos and penalise blacklisted uploaders.

    Loads the blacklist once per call (not per result)  -  the lists are small
    so this is cheap and avoids hammering the DB.
    """
    blocked_ids = get_blacklisted_video_ids()
    # Collect blocked uploaders for all relevant sources in one pass
    sources_to_check = {source} if source else {r.get("source", "youtube") for r in results}
    blocked_uploaders: dict[str, set[str]] = {}
    for s in sources_to_check:
        blocked_uploaders[s] = get_blacklisted_uploaders(s)

    filtered = []
    for r in results:
        if r.get("video_id") in blocked_ids:
            continue
        r_source = r.get("source", "youtube")
        channel = (r.get("channel") or "").lower()
        if channel and channel in blocked_uploaders.get(r_source, set()):
            r["quality_score"] = r.get("quality_score", 0) - _BLACKLIST_UPLOADER_PENALTY
        filtered.append(r)
    return filtered


def _enabled_sources() -> dict:
    """Return the subset of SOURCE_REGISTRY that is currently enabled in settings."""
    return {
        name: cfg for name, cfg in SOURCE_REGISTRY.items()
        if get_setting_bool(f"source_{name}_enabled", True)
    }


def search_source(source: str, query: str, limit: int) -> list[dict]:
    """Search a single registered source."""
    if source not in SOURCE_REGISTRY:
        raise ValueError(f"Unknown search source: {source}")
    if not get_setting_bool(f"source_{source}_enabled", True):
        return []

    # Fire MB duration lookup in parallel with the source search so it doesn't
    # add any latency  -  both finish before we sort and return.
    with ThreadPoolExecutor(max_workers=2) as pool:
        search_future = pool.submit(SOURCE_REGISTRY[source]["search_fn"], query, limit)
        mb_future = pool.submit(_mb_duration_lookup, query)
        results = search_future.result()
        expected_dur = mb_future.result()

    results = _apply_blacklist_filter(results, source=source)
    if expected_dur:
        _apply_mb_duration_scores(results, expected_dur)
    results.sort(key=lambda x: x["quality_score"], reverse=True)
    return results[:limit]


def search_all(query: str, limit: int) -> list[dict]:
    """Search every enabled source in parallel, merge by quality score."""
    active = _enabled_sources()
    futures = {}
    with ThreadPoolExecutor(max_workers=len(active) + 1) as pool:
        for name, cfg in active.items():
            futures[pool.submit(cfg["search_fn"], query, limit)] = name
        # MB lookup runs alongside the source searches at no extra cost
        mb_future = pool.submit(_mb_duration_lookup, query)

    all_results = []
    for future in as_completed(futures):
        source_name = futures[future]
        try:
            source_results = future.result(timeout=TIMEOUT_YTDLP_SEARCH + 5)
            # Cap per-source contribution so one prolific source can't drown out the rest.
            # Each source gets its best N results; scoring decides the final order.
            all_results.extend(source_results[:SEARCH_MAX_PER_SOURCE])
        except Exception as e:
            print(f"search_all: {source_name} failed: {e}")

    try:
        expected_dur = mb_future.result(timeout=1)
    except Exception:
        expected_dur = None

    all_results = _apply_blacklist_filter(all_results)
    if expected_dur:
        _apply_mb_duration_scores(all_results, expected_dur)
    all_results.sort(key=lambda x: x["quality_score"], reverse=True)
    return all_results[:limit]


def get_available_sources() -> list[dict]:
    """Return source metadata for the frontend source selector."""
    return [
        {
            "id": name,
            "label": cfg["label"],
            "badge": cfg["badge"],
            "colour": cfg["colour"],
            "enabled": bool(get_setting(f"source_{name}_enabled", True)),
        }
        for name, cfg in SOURCE_REGISTRY.items()
    ]
