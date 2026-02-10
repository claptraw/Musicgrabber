"""
MusicGrabber - Search Source Registry

Extensible source architecture. YouTube and SoundCloud both use yt-dlp
with different search prefixes; adding a new source is one function and
one registry entry.
"""

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

from constants import (
    TIMEOUT_YTDLP_SEARCH,
    SOUNDCLOUD_SEARCH_MULTIPLIER, SOUNDCLOUD_SEARCH_MIN_FETCH,
)
from db import get_blacklisted_video_ids, get_blacklisted_uploaders
from youtube import search_youtube, score_search_result, parse_duration

# Penalty large enough to push blacklisted uploaders to the bottom of results
# without hiding them entirely — the user might still want to see them
_BLACKLIST_UPLOADER_PENALTY = 500


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
            # SoundCloud sets are 'playlist' type — skip them for single-track search
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
# Source registry — add new sources here
# ---------------------------------------------------------------------------

SOURCE_REGISTRY = {
    "youtube": {
        "label": "YouTube",
        "badge": "YT",
        "colour": "#ff0000",
        "search_fn": search_youtube,
        "has_preview": True,
    },
    "soundcloud": {
        "label": "SoundCloud",
        "badge": "SC",
        "colour": "#ff5500",
        "search_fn": search_soundcloud,
        "has_preview": True,
    },
}


def _apply_blacklist_filter(results: list[dict], source: str | None = None) -> list[dict]:
    """Remove blacklisted videos and penalise blacklisted uploaders.

    Loads the blacklist once per call (not per result) — the lists are small
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


def search_source(source: str, query: str, limit: int) -> list[dict]:
    """Search a single registered source."""
    if source not in SOURCE_REGISTRY:
        raise ValueError(f"Unknown search source: {source}")
    results = SOURCE_REGISTRY[source]["search_fn"](query, limit)
    results = _apply_blacklist_filter(results, source=source)
    results.sort(key=lambda x: x["quality_score"], reverse=True)
    return results[:limit]


def search_all(query: str, limit: int) -> list[dict]:
    """Search every registered source in parallel, merge by quality score."""
    futures = {}
    with ThreadPoolExecutor(max_workers=len(SOURCE_REGISTRY)) as pool:
        for name, cfg in SOURCE_REGISTRY.items():
            futures[pool.submit(cfg["search_fn"], query, limit)] = name

    all_results = []
    for future in as_completed(futures):
        source_name = futures[future]
        try:
            all_results.extend(future.result(timeout=TIMEOUT_YTDLP_SEARCH + 5))
        except Exception as e:
            print(f"search_all: {source_name} failed: {e}")

    all_results = _apply_blacklist_filter(all_results)
    all_results.sort(key=lambda x: x["quality_score"], reverse=True)
    return all_results[:limit]


def get_available_sources() -> list[dict]:
    """Return source metadata for the frontend source selector."""
    return [
        {"id": name, "label": cfg["label"], "badge": cfg["badge"], "colour": cfg["colour"]}
        for name, cfg in SOURCE_REGISTRY.items()
    ]
