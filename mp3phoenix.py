"""
MusicGrabber - mp3phoenix.net Source

Russian MP3 portal backed by VK audio CDN. Returns ~320 kbps MP3s.
No auth required; download tokens are time-limited VK hashes so search
and download must happen in the same session (which they always do here).

Search hits the AJAX endpoint and parses the HTML fragment response.
Download streams the MP3 directly via the getmp3/ URL embedded in results.
"""

import hashlib
import re
from pathlib import Path

import httpx

from constants import (
    TIMEOUT_MP3PHOENIX_SEARCH,
    TIMEOUT_MP3PHOENIX_DOWNLOAD,
)
from youtube import score_search_result_with_breakdown, parse_duration

_BASE_URL = "https://mp3phoenix.net"
_AJAX_URL = f"{_BASE_URL}/ajax/music/"
_DELIMITER = "<!|!>"

# Stolen from a perfectly ordinary Firefox  -  the site 403s bare Python UA strings
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0",
    "Referer": _BASE_URL + "/",
}

# Pre-compiled regexes for the HTML fragment  -  marginally faster than recompiling per call
_RE_ARTIST = re.compile(r'musicTheme-results-info__card_artist"[^>]*>\s*<b>(.*?)</b>', re.DOTALL)
_RE_TITLE  = re.compile(r'musicTheme-results-info__card_tracklink"[^>]*>(.*?)</a>', re.DOTALL)
_RE_DUR    = re.compile(r'<span class="dur">(.*?)</span>')
_RE_HREF   = re.compile(r'musicTheme-results-info__card_download link"[^>]*href="(//mp3phoenix\.net/getmp3/[^"]+)"')


def _encode_query(query: str) -> str:
    """Encode a search query for the AJAX endpoint.

    Plain '+' for spaces. The site doesn't use ' - ' as an artist/title
    separator  -  it just does a full-text search, and '%20-%20' actually
    produces worse results by confusing the ranking.
    """
    return query.replace(" ", "+")


def _duration_to_secs(dur: str) -> int:
    """Convert 'M:SS' string to total seconds, or 0 on parse failure."""
    try:
        parts = dur.strip().split(":")
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except (ValueError, AttributeError):
        pass
    return 0


def search_mp3phoenix(query: str, limit: int) -> list[dict]:
    """Search mp3phoenix.net and return normalised result dicts.

    Returns up to `limit` results sorted by quality_score descending.
    On any error, returns [] rather than raising  -  a missing source
    should degrade gracefully, not bring down the whole search.
    """
    try:
        encoded = _encode_query(query)

        resp = httpx.get(
            _AJAX_URL + encoded,
            headers=_HEADERS,
            timeout=TIMEOUT_MP3PHOENIX_SEARCH,
            follow_redirects=True,
        )
        resp.raise_for_status()

        parts = resp.text.split(_DELIMITER)
        if len(parts) < 3:
            print(f"mp3phoenix: unexpected response format (got {len(parts)} parts)")
            return []

        html = parts[2]

        artists = _RE_ARTIST.findall(html)
        titles  = _RE_TITLE.findall(html)
        durs    = _RE_DUR.findall(html)
        hrefs   = _RE_HREF.findall(html)

        results = []
        for artist, title, dur, href in zip(artists, titles, durs, hrefs):
            artist = artist.strip()
            title  = title.strip()
            dur    = dur.strip()

            duration_secs = _duration_to_secs(dur)
            # Score against the combined "Artist - Title" string so the scorer
            # sees the full text rather than just the bare track name. YouTube
            # results naturally include artist in the title; without this,
            # "Everytime" scores far lower than "Britney Spears - Everytime
            # (Official HD Video)" and phoenix results never surface.
            combined_title = f"{artist} - {title}" if artist else title
            quality_score, score_breakdown = score_search_result_with_breakdown(
                combined_title, artist, query,
                duration_seconds=duration_secs or None,
                view_count=None,
            )
            # 320 kbps MP3 is better than SoundCloud (64-128 kbps) and bare YouTube
            # rips, but it's still lossy  -  lossless should always win.
            quality_score += 30
            score_breakdown.append("source_quality=+30")

            # The full getmp3 URL is stored in source_url for download and preview.
            # video_id gets a short hash  -  the raw token contains slashes which
            # would wreck the /api/preview/{video_id} route if used directly.
            download_url = "https:" + href
            video_id = "px_" + hashlib.md5(href.encode()).hexdigest()[:12]

            results.append({
                "video_id": video_id,
                "title": title,
                "channel": artist,
                "duration": parse_duration(duration_secs) if duration_secs else dur,
                "thumbnail": "",
                "is_playlist": False,
                "video_count": None,
                "source": "mp3phoenix",
                "source_url": download_url,
                "quality": "320kbps",
                "quality_score": quality_score,
                "score_breakdown": score_breakdown,
                "slskd_username": None,
                "slskd_filename": None,
                "slskd_size": None,
            })

        results.sort(key=lambda x: x["quality_score"], reverse=True)
        # Return the full scored pool  -  the site caps at 40 results anyway.
        # search_source / search_all will apply the final [:limit] trim after
        # blacklist filtering, same as every other source.
        return results

    except Exception as e:
        print(f"mp3phoenix search error: {e}")
        return []


def download_mp3phoenix_track(download_url: str, output_path: Path) -> None:
    """Stream an mp3phoenix track directly to output_path.

    The download_url is the full https://mp3phoenix.net/getmp3/... URL
    captured at search time. Raises on any HTTP or I/O error.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with httpx.stream(
        "GET",
        download_url,
        headers=_HEADERS,
        timeout=TIMEOUT_MP3PHOENIX_DOWNLOAD,
        follow_redirects=True,
    ) as resp:
        resp.raise_for_status()
        expected_size = int(resp.headers.get("content-length", 0))
        with open(output_path, "wb") as f:
            for chunk in resp.iter_bytes(chunk_size=16384):
                f.write(chunk)

    actual_size = output_path.stat().st_size
    if actual_size == 0:
        output_path.unlink(missing_ok=True)
        raise RuntimeError("MP3Phoenix download produced an empty file")
    if expected_size > 0 and actual_size < expected_size:
        output_path.unlink(missing_ok=True)
        raise RuntimeError(f"MP3Phoenix download truncated: got {actual_size} of {expected_size} bytes")
