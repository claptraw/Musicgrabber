"""
MusicGrabber - Search Source Registry

Extensible source architecture. YouTube, SoundCloud, and MP3Phoenix are supported.
Adding a new source is one function and one registry entry.
"""

import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

from constants import (
    TIMEOUT_YTDLP_SEARCH,
    TIMEOUT_SLSKD_SEARCH,
    SOUNDCLOUD_SEARCH_MULTIPLIER, SOUNDCLOUD_SEARCH_MIN_FETCH,
    SEARCH_MAX_PER_SOURCE,
    SEARCH_MAX_PER_SOURCE_YOUTUBE, SEARCH_MAX_PER_SOURCE_MP3PHOENIX,
    SEARCH_MAX_PER_SOURCE_SOUNDCLOUD, SEARCH_MAX_PER_SOURCE_ZVU4NO,
    SEARCH_MAX_PER_SOURCE_FREEMP3CLOUD,
    SEARCH_MAX_PER_SOURCE_SOULSEEK, SEARCH_MAX_PER_SOURCE_MONOCHROME,
)
from db import get_blacklisted_video_ids, get_blacklisted_uploaders
from metadata import fetch_mb_expected_duration, search_artist_mbid, lookup_musicbrainz
from settings import get_setting, get_setting_bool
from monochrome import search_monochrome, monochrome_enabled
from mp3phoenix import search_mp3phoenix
from slskd import slskd_enabled, search_slskd
from zvu4no import search_zvu4no
from freemp3cloud import search_freemp3cloud
import servicecheck
from youtube import (
    search_youtube, score_search_result_with_breakdown, format_score_breakdown, parse_duration,
    _normalise_search_text, _parse_query_artist_title, _query_has_variation,
    _artist_match_strength,
)

# Penalty large enough to push blacklisted uploaders to the bottom of results
# without hiding them entirely  -  the user might still want to see them
_BLACKLIST_UPLOADER_PENALTY = 500


# ---------------------------------------------------------------------------
# SoundCloud search
# ---------------------------------------------------------------------------

def search_monochrome_source(query: str, limit: int = 10) -> list[dict]:
    """Wrap search_monochrome with the enabled-check so the registry stays consistent."""
    if not monochrome_enabled():
        return []
    return search_monochrome(query, limit)


def search_soulseek(query: str, limit: int = 10) -> list[dict]:
    """Search Soulseek via slskd and return normal search-result dictionaries."""
    if not slskd_enabled():
        return []

    results = []
    for item in search_slskd(query, timeout_secs=TIMEOUT_SLSKD_SEARCH)[:limit]:
        duration_raw = item.get("duration", "0")
        try:
            duration = parse_duration(int(duration_raw))
        except (TypeError, ValueError):
            duration = str(duration_raw or "")
        result = dict(item)
        result["video_id"] = item.get("id") or f"slskd_{len(results)}"
        result["duration"] = duration
        result["thumbnail"] = ""
        result["source_url"] = f"soulseek://{item.get('slskd_username', '')}/{item.get('slskd_filename', '')}"
        result["slskd_size"] = item.get("slskd_size") or item.get("size")
        results.append(result)
    return results

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
            quality_score, score_breakdown = score_search_result_with_breakdown(
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
                "score_breakdown": score_breakdown,
                "slskd_username": None,
                "slskd_filename": None,
                "slskd_size": None,
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
    "zvu4no": {
        "label": "zvu4no",
        "badge": "ZV",
        "colour": "#7a6aee",
        "search_fn": search_zvu4no,
        "has_preview": True,
    },
    "freemp3cloud": {
        "label": "FreeMp3Cloud",
        "badge": "FMC",
        "colour": "#3a2fd6",
        "search_fn": search_freemp3cloud,
        "has_preview": True,
    },
    "soulseek": {
        "label": "Soulseek",
        "badge": "SLK",
        "colour": "#7c3aed",
        "search_fn": search_soulseek,
        "has_preview": False,
        "default_enabled": False,
    },
    "monochrome": {
        "label": "Monochrome",
        "badge": "MONO",
        "colour": "#0f766e",
        "search_fn": search_monochrome_source,
        "has_preview": True,
        "default_enabled": True,
    },
}

SEARCH_MAX_PER_SOURCE_BY_SOURCE = {
    "youtube": SEARCH_MAX_PER_SOURCE_YOUTUBE,
    "mp3phoenix": SEARCH_MAX_PER_SOURCE_MP3PHOENIX,
    "soundcloud": SEARCH_MAX_PER_SOURCE_SOUNDCLOUD,
    "zvu4no": SEARCH_MAX_PER_SOURCE_ZVU4NO,
    "freemp3cloud": SEARCH_MAX_PER_SOURCE_FREEMP3CLOUD,
    "soulseek": SEARCH_MAX_PER_SOURCE_SOULSEEK,
    "monochrome": SEARCH_MAX_PER_SOURCE_MONOCHROME,
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


def _mb_album_lookup(query: str) -> dict | None:
    """Find the MusicBrainz album a track belongs to, if we can figure it out.

    Parses "Artist - Title" from the query, then does a proper recording search
    via MusicBrainz (artist + title) and picks the best-scored release using the
    same release-group heuristics as post-download tagging. Much more reliable
    than fuzzy-matching a track title against an artist's album discography.

    Returns {artist_name, artist_mbid, album_title, release_mbid} or None.
    """
    if _query_has_variation(query):
        return None
    artist, title = _parse_query_artist_title(query)
    if not artist or not title:
        return None

    mb = lookup_musicbrainz(artist, title)
    if not mb or not mb.get("album") or not mb.get("release_mbid"):
        return None

    # Get the artist MBID for the frontend album browser
    artists = search_artist_mbid(artist)
    artist_mbid = artists[0]["mbid"] if artists else None
    artist_name = artists[0]["name"] if artists else (mb.get("artist") or artist)

    return {
        "artist_name": artist_name,
        "artist_mbid": artist_mbid,
        "album_title": mb["album"],
        "release_mbid": mb["release_mbid"],
    }


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
            r.setdefault("score_breakdown", []).append("mb_search_duration=+40")
        elif delta_ratio <= 0.05:
            r["quality_score"] += 20
            r.setdefault("score_breakdown", []).append("mb_search_duration=+20")
        elif delta_ratio <= 0.10:
            pass
        elif delta_ratio <= 0.25:
            r["quality_score"] -= 30
            r.setdefault("score_breakdown", []).append("mb_search_duration=-30")
        else:
            r["quality_score"] -= 60
            r.setdefault("score_breakdown", []).append("mb_search_duration=-60")


def log_ranked_results(context: str, query: str, results: list[dict], top_n: int = 3) -> None:
    """Print the top scored candidates with their main score reasons."""
    if not results:
        print(f"{context}: no candidates for '{query}'")
        return
    print(f"{context}: top {min(top_n, len(results))} candidates for '{query}'")
    for idx, r in enumerate(results[:top_n], start=1):
        title = (r.get("title") or "").strip() or "Unknown"
        channel = (r.get("channel") or "").strip() or "Unknown"
        source = r.get("source") or "unknown"
        score = r.get("quality_score")
        breakdown = format_score_breakdown(r.get("score_breakdown") or [])
        print(f"  {idx}. [{source}] {channel} - {title} (score {score}) :: {breakdown}")


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


def _enabled_sources(include_soulseek: bool = False) -> dict:
    """Return the subset of SOURCE_REGISTRY that is currently enabled in settings."""
    return {
        name: cfg for name, cfg in SOURCE_REGISTRY.items()
        if (include_soulseek or name != "soulseek")
        and get_setting_bool(f"source_{name}_enabled", cfg.get("default_enabled", True))
    }


def search_source(source: str, query: str, limit: int) -> list[dict]:
    """Search a single registered source."""
    if source not in SOURCE_REGISTRY:
        raise ValueError(f"Unknown search source: {source}")
    cfg = SOURCE_REGISTRY[source]
    if not get_setting_bool(f"source_{source}_enabled", cfg.get("default_enabled", True)):
        return []

    # Fire MB duration lookup in parallel with the source search so it doesn't
    # add any latency  -  both finish before we sort and return.
    with ThreadPoolExecutor(max_workers=2) as pool:
        search_future = pool.submit(cfg["search_fn"], query, limit)
        mb_future = pool.submit(_mb_duration_lookup, query)
        results = search_future.result()
        expected_dur = mb_future.result()

    results = _apply_blacklist_filter(results, source=source)
    if expected_dur:
        _apply_mb_duration_scores(results, expected_dur)
    results.sort(key=lambda x: x["quality_score"], reverse=True)
    return results[:limit]


def search_all(query: str, limit: int, sources: list[str] | None = None, include_soulseek: bool = False) -> tuple[list[dict], dict | None]:
    """Search enabled sources in parallel, merge by quality score.

    If *sources* is provided (list of source IDs), only those sources are used,
    provided they are also enabled in settings. Falls back to all enabled sources
    if the filtered set is empty (e.g. source disabled globally but playlist prefers it).

    Returns (results, album_suggestion) where album_suggestion is a dict with
    artist_name, artist_mbid, album_title, release_mbid, or None if the query
    didn't resolve to a known album.
    """
    active = _enabled_sources(include_soulseek=include_soulseek)
    if sources:
        # Intersect requested sources with enabled ones; fall back to all if none survive
        filtered = {k: v for k, v in active.items() if k in sources}
        active = filtered if filtered else active
    # Lazily refresh stale health checks before deciding which multi-source
    # results are safe to show.
    servicecheck.check_sources(set(active))
    # Hide sources currently parked in a failure cooldown so their results don't
    # show up only to fall over at play or download time. Single-source explicit
    # search deliberately skips this; if the user asks for it, they get it.
    active = {name: cfg for name, cfg in active.items() if servicecheck.is_source_available(name)}
    futures = {}
    with ThreadPoolExecutor(max_workers=len(active) + 2) as pool:
        for name, cfg in active.items():
            futures[pool.submit(cfg["search_fn"], query, limit)] = name
        # MB lookups run alongside the source searches at no extra cost
        mb_future = pool.submit(_mb_duration_lookup, query)
        mb_album_future = pool.submit(_mb_album_lookup, query)

    all_results = []
    for future in as_completed(futures):
        source_name = futures[future]
        try:
            source_results = future.result(timeout=TIMEOUT_YTDLP_SEARCH + 5)
            # Cap per-source contribution so one prolific source can't drown out the rest.
            # Each source gets its best N results; scoring decides the final order.
            per_source_cap = SEARCH_MAX_PER_SOURCE_BY_SOURCE.get(source_name, SEARCH_MAX_PER_SOURCE)
            all_results.extend(source_results[:per_source_cap])
        except Exception as e:
            print(f"search_all: {source_name} failed: {e}")

    try:
        expected_dur = mb_future.result(timeout=1)
    except Exception:
        expected_dur = None

    try:
        album_suggestion = mb_album_future.result(timeout=2)
    except Exception:
        album_suggestion = None

    all_results = _apply_blacklist_filter(all_results)
    if expected_dur:
        _apply_mb_duration_scores(all_results, expected_dur)
    all_results.sort(key=lambda x: x["quality_score"], reverse=True)
    return all_results[:limit], album_suggestion


def get_available_sources() -> list[dict]:
    """Return source metadata for the frontend source selector."""
    return [
        {
            "id": name,
            "label": cfg["label"],
            "badge": cfg["badge"],
            "colour": cfg["colour"],
            "enabled": get_setting_bool(f"source_{name}_enabled", cfg.get("default_enabled", True)),
            "has_preview": bool(cfg.get("has_preview", True)),
        }
        for name, cfg in SOURCE_REGISTRY.items()
    ]
