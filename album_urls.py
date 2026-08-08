"""
MusicGrabber - Streaming album URL resolution

Detects whether a pasted URL points at a whole ALBUM, as opposed to a
playlist or a single track, on the services MusicGrabber already knows how to
scrape, then turns that into a MusicBrainz match ready for the album download
pipeline (or a confirmation prompt, if the match isn't confident enough).

Spotify and Apple Music are the only services handled here, and both borrow
their existing scrapers rather than talking to the services directly:
  - Spotify's playlist/album embed parser lives in watched_playlists.py
    (it already treats a Spotify /album/ URL as a "playlist" of tracks for
    watching purposes; we just ask it what's on the album).
  - Apple Music's playlist/album fetcher lives in apple.py and already
    supports /album/ URLs natively.

YouTube, Amazon, Beatport and Monochrome are deliberately NOT handled: none
of them has an "album" URL shape distinct from a playlist/track today (or, in
YouTube's case, no scraper that would tell us), so there is no contract here
to half-build. Add them properly, with their own tests, if that ever changes.
"""

import re

from apple import fetch_apple_music_playlist
from matching import parse_query
from metadata import match_album_to_musicbrainz


_SPOTIFY_ALBUM_RE = re.compile(
    r'^https?://open\.spotify\.com/album/([A-Za-z0-9]+)(?:[/?#].*)?$'
)
# Apple Music album URLs are /<storefront>/album/<slug>/<numeric id>, e.g.
# https://music.apple.com/gb/album/ok-computer/1097864980. A trailing
# "?i=<track id>" deep-links one track within the album page; the URL still
# names the whole album, so it stays recognised.
_APPLE_ALBUM_RE = re.compile(
    r'^https?://music\.apple\.com/[a-z]{2}/album/[^/?#]+/(\d+)(?:[/?#].*)?$',
    re.IGNORECASE,
)


def _first_track_artist(tracks: list[str]) -> str | None:
    """Best-guess album artist: the artist half of the first scraped track.

    Good enough for the overwhelming majority of albums, which have a single
    artist. A genuine Various Artists compilation just comes back with
    whichever act happens to sit first in the tracklist; that's fine because
    match_album_to_musicbrainz already treats a "Various Artists" credit as
    legitimate rather than something to gate on, and the album title carries
    most of the matching weight anyway.
    """
    if not tracks:
        return None
    artist, _ = parse_query(tracks[0])
    return artist or None


def parse_album_url(url: str) -> dict | None:
    """Identify a Spotify or Apple Music ALBUM url and scrape its artist/album.

    Returns {"service": "spotify"|"apple", "id": ..., "artist": ..., "album": ...}
    when the url matches a known album shape. artist/album are filled in on a
    best-effort basis: a scrape failure (dead link, private album, service
    hiccup) leaves them as None but the url is still "recognised", since that
    judgement is a pure pattern match and does not depend on the network call
    succeeding.

    Returns None when the url is not a recognised album url at all, including
    a Spotify or Apple Music *playlist* or *track* url; those are a different
    job (Bulk Import's ordinary playlist path already handles them).
    """
    url = (url or "").strip()
    if not url:
        return None

    spotify_match = _SPOTIFY_ALBUM_RE.match(url)
    if spotify_match:
        return _parse_spotify_album(url, spotify_match.group(1))

    apple_match = _APPLE_ALBUM_RE.match(url)
    if apple_match:
        return _parse_apple_album(url, apple_match.group(1))

    return None


def _parse_spotify_album(url: str, spotify_id: str) -> dict:
    # Lazy import: watched_playlists.py pulls in the whole download/db stack,
    # and this module has no other reason to load any of that at import time.
    from watched_playlists import _fetch_spotify_playlist_embed

    artist, album = None, None
    try:
        result = _fetch_spotify_playlist_embed(url)
        album = result.get("playlist_name") or None
        artist = _first_track_artist(result.get("tracks") or [])
    except Exception as exc:
        print(f"Spotify album scrape failed for {url}: {exc}")

    return {"service": "spotify", "id": spotify_id, "artist": artist, "album": album}


def _parse_apple_album(url: str, apple_id: str) -> dict:
    artist, album = None, None
    try:
        result = fetch_apple_music_playlist(url)
        album = result.get("playlist_name") or None
        artist = _first_track_artist(result.get("tracks") or [])
    except Exception as exc:
        print(f"Apple Music album scrape failed for {url}: {exc}")

    return {"service": "apple", "id": apple_id, "artist": artist, "album": album}


def resolve_album_url(url: str) -> dict:
    """Resolve a streaming album url all the way to a MusicBrainz match.

    Combines parse_album_url with metadata.match_album_to_musicbrainz so a
    caller can go straight from a pasted url to either the album download
    pipeline (confident match) or a confirmation prompt (not confident).

    Returns:
        {
            "recognised": bool,   # False: not a Spotify/Apple album url at all
            "service": "spotify" | "apple" | None,
            "artist": str | None,  # as scraped from the streaming service
            "album": str | None,
            "match": dict | None,  # best MusicBrainz release-group candidate
            "confidence": float,
            "confident": bool,     # True -> caller may proceed to the album pipeline
            "candidates": list,    # offer these when not confident
        }

    Raises MusicBrainzUnavailable (from metadata.py) when MusicBrainz itself
    cannot be reached, so the caller can show a Retry rather than a false
    "no match found".
    """
    parsed = parse_album_url(url)
    if parsed is None:
        return {
            "recognised": False, "service": None, "artist": None, "album": None,
            "match": None, "confidence": 0.0, "confident": False, "candidates": [],
        }

    artist, album = parsed.get("artist"), parsed.get("album")
    if not album:
        # Recognised the url shape but couldn't scrape anything off it; no
        # album title means nothing sensible to match against.
        return {
            "recognised": True, "service": parsed["service"],
            "artist": artist, "album": album,
            "match": None, "confidence": 0.0, "confident": False, "candidates": [],
        }

    match_result = match_album_to_musicbrainz(artist or "", album)
    return {
        "recognised": True,
        "service": parsed["service"],
        "artist": artist,
        "album": album,
        **match_result,
    }
