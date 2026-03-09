"""
MusicGrabber - Apple Music Playlist Fetching

Apple Music server-renders the full track list into a `serialized-server-data`
JSON blob on the page, so we can scrape it with a plain HTTP request.
No browser required -- take that, Apple.

Supports public playlists and albums. Private libraries and user playlists
(anything requiring sign-in) will 403 or return no tracks.
"""

import json
import re
import urllib.error
import urllib.request

from fastapi import HTTPException

from constants import TIMEOUT_HTTP_SPOTIFY

# Reuse the Spotify timeout -- same class of HTTP fetch
_TIMEOUT = TIMEOUT_HTTP_SPOTIFY

# Pretend to be a browser; Apple 403s bare Python user-agents
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-GB,en;q=0.9",
}


def fetch_apple_music_playlist(url: str) -> dict:
    """Fetch tracks from a public Apple Music playlist or album URL.

    Returns dict with: tracks (list of "Artist - Title"), playlist_name, count
    """
    print(f"Fetching Apple Music playlist: {url}")

    req = urllib.request.Request(url, headers=_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Apple Music returned HTTP {e.code}. The playlist may be private or region-locked."
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch Apple Music page: {e}")

    # Playlist name: og:title is the most reliable, strip the " on Apple Music" suffix
    og_title = re.search(r'property="og:title"\s+content="([^"]+)"', html)
    playlist_name = og_title.group(1).strip() if og_title else "Apple Music Playlist"
    playlist_name = re.sub(r'\s+on Apple Music$', '', playlist_name, flags=re.IGNORECASE).strip()

    # Tracks live in the server-rendered JSON blob
    blob_match = re.search(
        r'id="serialized-server-data"[^>]*>(.*?)</script>',
        html, re.DOTALL
    )
    if not blob_match:
        raise HTTPException(
            status_code=502,
            detail="Could not find track data in Apple Music page. The page structure may have changed."
        )

    try:
        data = json.loads(blob_match.group(1))
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=502, detail=f"Failed to parse Apple Music page data: {e}")

    tracks = _extract_tracks(data)

    if not tracks:
        raise HTTPException(
            status_code=422,
            detail="No tracks found in Apple Music playlist. It may be empty, private, or region-locked."
        )

    print(f"Successfully extracted {len(tracks)} tracks from Apple Music playlist '{playlist_name}'")
    return {
        "tracks": tracks,
        "playlist_name": playlist_name,
        "count": len(tracks),
    }


def _extract_tracks(obj, _seen=None, _depth=0) -> list[str]:
    """Recursively walk the server data and collect unique Artist - Title strings."""
    if _seen is None:
        _seen = []
    if _depth > 20:
        return _seen

    if isinstance(obj, dict):
        # Track objects have both 'artistName' and 'title' (not 'name')
        artist = obj.get("artistName")
        title = obj.get("title")
        if isinstance(artist, str) and isinstance(title, str):
            artist = artist.strip()
            title = title.strip()
            if artist and title:
                entry = f"{artist} - {title}"
                if entry not in _seen:
                    _seen.append(entry)
        for v in obj.values():
            _extract_tracks(v, _seen, _depth + 1)
    elif isinstance(obj, list):
        for item in obj:
            _extract_tracks(item, _seen, _depth + 1)

    return _seen
