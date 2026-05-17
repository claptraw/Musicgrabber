"""
MusicGrabber - Tidal playlist fetching

Tidal's main web app is a client-side SPA with no useful SSR data.
The embed player (embed.tidal.com/playlists/UUID) is a different story:
it server-renders the full track list as custom <list-item> web components.
We fetch that, scrape the slots, and call it done.
"""

import re
import urllib.error
import urllib.request

from fastapi import HTTPException

from constants import TIMEOUT_HTTP_SPOTIFY

_TIMEOUT = TIMEOUT_HTTP_SPOTIFY
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-GB,en;q=0.9",
}
_EMBED_URL = "https://embed.tidal.com/playlists/{uuid}"


def fetch_tidal_playlist(uuid: str) -> dict:
    """Fetch tracks from a Tidal playlist via the embed player.

    Returns dict with: tracks (list of "Artist - Title"), playlist_name, count.
    """
    url = _EMBED_URL.format(uuid=uuid)
    print(f"[Tidal] Fetching embed: {url}")
    html = _fetch_html(url)
    playlist_name = _extract_playlist_name(html)
    tracks = _extract_tracks(html)

    if not tracks:
        raise HTTPException(
            status_code=422,
            detail=(
                "No tracks found in that Tidal playlist. "
                "It may be empty, private, or the embed structure has changed."
            ),
        )

    print(f"[Tidal] Found {len(tracks)} tracks from '{playlist_name}'")
    return {"tracks": tracks, "playlist_name": playlist_name, "count": len(tracks)}


def _fetch_html(url: str) -> str:
    req = urllib.request.Request(url, headers=_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return resp.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Tidal embed returned HTTP {exc.code}. The playlist may be private or deleted.",
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch Tidal embed page: {exc}")


def _extract_playlist_name(html: str) -> str:
    # <h1 class="media-album" title="Album: Indie Party Hits">
    match = re.search(r'<h1[^>]+class="media-album"[^>]+title="Album:\s*([^"]+)"', html)
    if match:
        return match.group(1).strip()

    # Fallback: og:title or <title>
    og = re.search(r'property="og:title"\s+content="([^"]+)"', html)
    if og:
        return og.group(1).strip()

    return "Tidal Playlist"


def _extract_tracks(html: str) -> list[str]:
    # Each track is wrapped in a <list-item product-type="track"> block
    blocks = re.findall(
        r'<list-item[^>]*product-type="track"[^>]*>(.*?)</list-item>',
        html,
        re.DOTALL,
    )

    tracks = []
    for block in blocks:
        title_m = re.search(r'slot="title">([^<]+)<', block)
        # Artist may be a plain text node or wrapped in an <a> tag
        artist_m = re.search(r'slot="artist">(?:<[^>]+>)?([^<]+)<', block)
        if title_m and artist_m:
            title = title_m.group(1).strip()
            artist = artist_m.group(1).strip()
            if artist and title:
                tracks.append(f"{artist} - {title}")

    return tracks
