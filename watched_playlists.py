"""
MusicGrabber - Watched Playlists

Platform detection, track fetching, playlist refresh, and background scheduler.
"""

import json
import pathlib
import random
import re
import sqlite3
import subprocess
import threading
import time
from datetime import datetime, timezone

from fastapi import HTTPException

from constants import (
    TIMEOUT_YTDLP_PLAYLIST, TIMEOUT_HTTP_SPOTIFY,
    SPOTIFY_EMBED_MAX_ATTEMPTS, SPOTIFY_EMBED_RETRY_BACKOFF,
    WATCHED_PLAYLIST_CHECK_HOURS, WATCHED_REFRESH_STALE_SECONDS,
    WATCHED_GONE_STRIKES_BEFORE_PAUSE,
    LISTENBRAINZ_API_URL, TIMEOUT_LISTENBRAINZ, TIMEOUT_LISTENBRAINZ_PLAYLIST,
    AUDIO_EXTENSIONS,
)
from db import db_conn
from bulk_import import start_bulk_import_for_tracks
from amazon import fetch_amazon_playlist
from apple import fetch_apple_music_playlist
from beatport import fetch_beatport_playlist
from tidal import fetch_tidal_playlist
from downloads import rebuild_watched_playlist_m3u
from settings import get_playlists_dir, get_setting
from spotify import fetch_spotify_playlist_via_browser
from utils import (
    extract_artist_title, hash_track, spawn_daemon_thread, sanitize_filename,
    check_duplicate,
)
from downloads import check_navidrome_duplicate
from youtube import _ytdlp_base_args

import httpx


def _normalise_match_text(text: str) -> str:
    """Normalise text for loose track/file matching."""
    t = (text or "").lower()
    t = t.replace("’", "'").replace("‘", "'").replace("`", "'")
    t = re.sub(r"\s*[\(\[].*?[\)\]]", "", t)
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _playlist_file_exists(playlist_name: str, artist: str, title: str, user_id: str | None = None) -> bool:
    """Check whether a track exists inside Playlists/<playlist_name>/."""
    playlists_dir = get_playlists_dir(user_id=user_id)
    if not playlists_dir:
        return False
    track_dir = playlists_dir / sanitize_filename(playlist_name)
    if not track_dir.exists():
        return False

    stem = f"{sanitize_filename(artist or 'Unknown Artist')} - {sanitize_filename(title or 'Unknown Title')}"
    for ext in AUDIO_EXTENSIONS:
        if (track_dir / f"{stem}{ext}").exists():
            return True

    # Fuzzy fallback so metadata wobble does not hide real files.
    artist_n = _normalise_match_text(artist)
    title_n = _normalise_match_text(title)
    if not artist_n or not title_n:
        return False
    for ext in AUDIO_EXTENSIONS:
        for p in track_dir.glob(f"*{ext}"):
            if " - " not in p.stem:
                continue
            f_artist, f_title = p.stem.split(" - ", 1)
            fa = _normalise_match_text(f_artist)
            ft = _normalise_match_text(f_title)
            artist_ok = fa and (artist_n in fa or fa in artist_n)
            title_ok = ft and (title_n in ft or ft in title_n)
            if artist_ok and title_ok:
                return True
    return False


def _has_local_track_file(playlist_name: str, use_playlists_dir: bool, artist: str, title: str, job_artist: str = "", job_title: str = "", user_id: str | None = None, resolved_path: str | None = None) -> bool:
    """Return True if we can resolve a local file for this watched track.

    Checks the stored resolved_path first (covers tracks found in album folders or
    other non-Singles locations), then MusicGrabber's own library, then falls back
    to Navidrome (real absolute paths only  -  synthetic paths mean real-path mode
    is off, which is a config problem, not a reason to re-download).
    """
    # Fastest check: if we recorded exactly where the file landed, trust it
    if resolved_path:
        rp = pathlib.Path(resolved_path)
        if rp.is_absolute() and rp.exists():
            return True

    pairs = []
    for a, t in ((job_artist, job_title), (artist, title)):
        a = (a or "").strip()
        t = (t or "").strip()
        if a and t and (a, t) not in pairs:
            pairs.append((a, t))

    for a, t in pairs:
        if check_duplicate(a, t, user_id=user_id):
            return True
        if use_playlists_dir and _playlist_file_exists(playlist_name, a, t, user_id=user_id):
            return True

    # Last resort: check Navidrome. Any non-None return means Navidrome confirmed
    # the track is in the library; absolute paths are real on-disk locations,
    # while the relative sentinel (Path(title)) means "exists but no real-path
    # mode". Both block re-downloads. Filtering by .is_absolute() here used to
    # treat the sentinel as "missing", causing every track to re-download when a
    # watched playlist was re-added.
    for a, t in pairs:
        nav_path = check_navidrome_duplicate(a, t, user_id=user_id)
        if nav_path is not None:
            return True

    return False


def detect_playlist_platform(url: str) -> tuple[str, str]:
    """Detect platform and extract ID from playlist URL

    Returns (platform, id) or raises HTTPException if invalid
    """
    # Spotify liked songs (requires sp_dc cookie)
    if re.match(r'https?://open\.spotify\.com/collection/tracks', url):
        return "spotify_likes", "collection/tracks"

    # Spotify playlist
    spotify_playlist = re.match(r'https?://open\.spotify\.com/playlist/([a-zA-Z0-9]+)', url)
    if spotify_playlist:
        return "spotify", spotify_playlist.group(1)

    # Spotify album
    spotify_album = re.match(r'https?://open\.spotify\.com/album/([a-zA-Z0-9]+)', url)
    if spotify_album:
        return "spotify", spotify_album.group(1)

    # YouTube / YouTube Music  -  /playlist?list=... or /watch?v=...&list=... (Mixes, Radio, etc.)
    youtube_list = re.search(r'https?://(www\.|music\.)?youtube\.com/(?:playlist|watch)\?[^"]*list=([a-zA-Z0-9_-]+)', url)
    if youtube_list:
        return "youtube", youtube_list.group(2)

    # Apple Music playlist or album (public catalog, any storefront)
    apple_playlist = re.match(r'https?://music\.apple\.com/[a-z]{2}/(playlist|album)/', url, re.IGNORECASE)
    if apple_playlist:
        return "apple", url  # Full URL needed  -  storefront is part of the path

    # Apple Music private library playlist
    apple_library = re.match(r'https?://music\.apple\.com/library/(playlist|album)/', url, re.IGNORECASE)
    if apple_library:
        return "apple", url

    # Amazon Music playlist (user or curated, any regional TLD)
    amazon_playlist = re.match(r'https?://music\.amazon\.[a-z.]+/(user-playlists|playlists)/\S+', url)
    if amazon_playlist:
        return "amazon", url  # Full URL needed  -  no extractable ID

    # ListenBrainz individual playlist URL
    lb_playlist = re.match(r'https?://listenbrainz\.org/playlist/([0-9a-f-]{36})', url, re.IGNORECASE)
    if lb_playlist:
        return "listenbrainz", lb_playlist.group(1)

    # ListenBrainz user profile URL  -  triggers "Created for You" fan-out
    lb_user_url = re.match(r'https?://listenbrainz\.org/user/([a-zA-Z0-9_-]+)', url, re.IGNORECASE)
    if lb_user_url:
        return "listenbrainz_user", lb_user_url.group(1)

    # Bare ListenBrainz username (no protocol, no dots  -  just alphanumeric/underscore/hyphen)
    if re.match(r'^[a-zA-Z0-9_-]+$', url) and '.' not in url:
        return "listenbrainz_user", url

    # SoundCloud sets (user playlists) and likes
    soundcloud_sets = re.match(r'https?://soundcloud\.com/[^/]+/sets/[^/?]+', url, re.IGNORECASE)
    if soundcloud_sets:
        return "soundcloud", url

    soundcloud_likes = re.match(r'https?://soundcloud\.com/[^/]+/likes', url, re.IGNORECASE)
    if soundcloud_likes:
        return "soundcloud", url

    # Monochrome playlist shares are Tidal playlist UUIDs behind hifi-api, but
    # the public URL uses Monochrome's own host.
    monochrome_playlist = re.match(
        r'https?://(?:www\.)?(?:monochrome\.tf|monochrome\.samidy\.com)/playlist/([0-9a-f-]{36})',
        url,
        re.IGNORECASE,
    )
    if monochrome_playlist:
        return "monochrome", monochrome_playlist.group(1)

    # Tidal playlist (both tidal.com/browse/playlist/UUID and tidal.com/playlist/UUID)
    tidal_playlist = re.match(r'https?://tidal\.com/(?:browse/)?playlist/([0-9a-f-]{36})', url, re.IGNORECASE)
    if tidal_playlist:
        return "tidal", tidal_playlist.group(1)

    # Beatport Top 100, genre charts, and editorial/user charts
    beatport = re.match(
        r'https?://(?:www\.)?beatport\.com/(top-100|genre/[^/]+/\d+/top-100|chart/[^/]+/\d+)',
        url, re.IGNORECASE
    )
    if beatport:
        return "beatport", url

    raise HTTPException(
        status_code=400,
        detail="Invalid playlist URL. Supported: Spotify playlists/albums/liked songs, YouTube/YouTube Music playlists, Apple Music playlists/albums, Amazon Music playlists, ListenBrainz playlists or usernames, SoundCloud sets/likes, Tidal/Monochrome playlists, Beatport charts/Top 100."
    )


def _extract_sp_dc(cookies_text: str) -> str | None:
    """Extract the sp_dc session cookie value from a Netscape-format cookie string."""
    for raw_line in (cookies_text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 7 and parts[5] == "sp_dc":
            return parts[6].strip()
    return None


def _flag_spotify_cookies_expired(user_id: str | None) -> None:
    """Mark Spotify cookies as expired in settings so the UI can warn the user."""
    from settings import set_setting, set_user_setting
    if user_id:
        set_user_setting(user_id, "spotify_cookies_expired", "true")
    else:
        set_setting("spotify_cookies_expired", "true")


def _fetch_spotify_playlist_embed(url: str, sp_dc: str | None = None, user_id: str | None = None) -> dict:
    """Fetch Spotify playlist tracks via embed endpoint.
    This is the fast path that works for playlists with <100 tracks.

    sp_dc is the Spotify session cookie. When provided it enables access to
    private playlists. Without it only public playlists are accessible.
    """
    # Extract ID and type from URL
    playlist_match = re.match(r'https?://open\.spotify\.com/playlist/([a-zA-Z0-9]+)', url)
    album_match = re.match(r'https?://open\.spotify\.com/album/([a-zA-Z0-9]+)', url)

    if playlist_match:
        spotify_id = playlist_match.group(1)
        spotify_type = "playlist"
    elif album_match:
        spotify_id = album_match.group(1)
        spotify_type = "album"
    else:
        raise HTTPException(status_code=400, detail="Invalid Spotify URL. Expected playlist or album URL.")

    # Build headers and cookies for the request.
    # sp_dc is the session credential that unlocks private content.
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    cookies = {"sp_dc": sp_dc} if sp_dc else {}

    # Fetch the embed page. Spotify's public playlist API is gone, so we scrape the
    # embed HTML which includes a predictable JSON-in-HTML "title"/"subtitle" pattern.
    # If this breaks, inspect the embed HTML for renamed fields or a new data blob.
    # Spotify's embed edge has a habit of throwing the odd transient 502/503/504
    # (gateway timeouts under load) or dropping the connection. One blip
    # shouldn't sink the whole fetch, so we retry a few times with a short
    # linear backoff. 401/403/404 are NOT transient (private/missing playlist),
    # so a definitive answer breaks out of the loop and is handled below.
    embed_url = f"https://open.spotify.com/embed/{spotify_type}/{spotify_id}"
    response = None
    last_error = None
    for attempt in range(SPOTIFY_EMBED_MAX_ATTEMPTS):
        try:
            with httpx.Client(timeout=TIMEOUT_HTTP_SPOTIFY, follow_redirects=True) as client:
                response = client.get(embed_url, headers=headers, cookies=cookies)
        except httpx.RequestError as e:
            response = None
            last_error = e
        else:
            if response.status_code not in (502, 503, 504):
                break  # definitive answer (200/401/403/404/...), stop retrying
            last_error = httpx.HTTPStatusError(
                f"transient {response.status_code} from Spotify embed",
                request=response.request, response=response,
            )
        if attempt < SPOTIFY_EMBED_MAX_ATTEMPTS - 1:
            time.sleep(SPOTIFY_EMBED_RETRY_BACKOFF * (attempt + 1))

    try:
        if response is None:
            # Every attempt hit a connection-level error; surface the last one.
            raise HTTPException(status_code=502, detail=f"Failed to connect to Spotify: {last_error}")
        if response.status_code in (401, 403):
            # Embed refused. Could be a private playlist or stale cookies.
            # The headless browser handles both cases more gracefully
            # when sp_dc is available, so try that before giving up.
            if sp_dc:
                print(
                    f"Spotify embed returned {response.status_code} for "
                    f"{spotify_type} {spotify_id}, trying headless browser..."
                )
                try:
                    return fetch_spotify_playlist_via_browser(
                        spotify_id, spotify_type, sp_dc=sp_dc, user_id=user_id,
                    )
                except HTTPException as browser_exc:
                    if browser_exc.detail == "spotify_cookies_expired":
                        _flag_spotify_cookies_expired(user_id)
                    raise
            raise HTTPException(status_code=403, detail=f"{spotify_type.title()} not found or is private")
        response.raise_for_status()
    except HTTPException:
        raise
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(status_code=404, detail=f"{spotify_type.title()} not found or is private")
        raise HTTPException(status_code=502, detail=f"Failed to fetch {spotify_type}: {e}")
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Failed to connect to Spotify: {e}")

    html_content = response.text
    expected_total = None
    playlist_name = f"Spotify {spotify_type.title()}"
    tracks = []

    # Primary: parse the __NEXT_DATA__ JSON blob Spotify embeds in the page.
    # Previously we scraped this with regexes, but Spotify added nested arrays
    # (e.g. "contentRatings":{"labels":[]}) which broke the non-greedy trackList
    # regex. Proper JSON parsing handles any nesting depth.
    next_data_match = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.+?)</script>',
        html_content,
        re.DOTALL,
    )
    if next_data_match:
        try:
            next_data = json.loads(next_data_match.group(1))
            entity = next_data["props"]["pageProps"]["state"]["data"]["entity"]
            playlist_name = entity.get("title") or entity.get("name") or playlist_name
            for track in entity.get("trackList", []):
                raw_title = track.get("title", "").strip()
                raw_artist = track.get("subtitle", "").strip()
                entity_type = track.get("entityType", "track")
                if not raw_title:
                    continue
                # Subtitle separator is sometimes a non-breaking space — normalise it
                raw_artist = raw_artist.replace(" ", " ")
                if raw_artist.lower() == "music video" or entity_type == "music_video":
                    from utils import extract_artist_title
                    artist, title = extract_artist_title(raw_title, channel="")
                    if artist and title and artist.lower() not in ("unknown artist", ""):
                        tracks.append(f"{artist} - {title}")
                    continue
                if raw_artist:
                    tracks.append(f"{raw_artist} - {raw_title}")
        except (KeyError, TypeError, json.JSONDecodeError):
            tracks = []

    # Fallback: old regex approach, kept in case Spotify ever drops __NEXT_DATA__
    if not tracks:
        print("Spotify __NEXT_DATA__ parse failed, falling back to regex scrape")
        tracklist_match = re.search(r'"trackList":\[(.+?)\](?=,"|\})', html_content, re.DOTALL)
        if tracklist_match:
            tracklist_content = tracklist_match.group(1)
            tl_titles = re.findall(r'"title":"([^"]*)"', tracklist_content)
            tl_subtitles = re.findall(r'"subtitle":"([^"]*)"', tracklist_content)
            tl_entity_types = re.findall(r'"entityType":"([^"]*)"', tracklist_content)
            for i, (raw_title, raw_artist) in enumerate(zip(tl_titles, tl_subtitles)):
                entity_type = tl_entity_types[i] if i < len(tl_entity_types) else "track"
                try:
                    raw_title = json.loads(f'"{raw_title}"')
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass
                try:
                    raw_artist = json.loads(f'"{raw_artist}"')
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass
                if not raw_title:
                    continue
                if raw_artist.lower() == "music video" or entity_type == "music_video":
                    from utils import extract_artist_title
                    artist, title = extract_artist_title(raw_title, channel="")
                    if artist and title and artist.lower() not in ("unknown artist", ""):
                        tracks.append(f"{artist} - {title}")
                    continue
                if raw_artist:
                    tracks.append(f"{raw_artist} - {raw_title}")

    if not tracks:
        # Embed parsed cleanly but yielded zero tracks. The browser path works
        # for public playlists without sp_dc, so always try it before declaring
        # the playlist empty. Private playlists still need sp_dc and will
        # surface the proper "cookies expired" error if they're missing.
        print(
            f"Spotify embed returned no tracks for {spotify_type} {spotify_id}, "
            "trying headless browser..."
        )
        try:
            return fetch_spotify_playlist_via_browser(
                spotify_id, spotify_type, expected_total=expected_total,
                sp_dc=sp_dc, user_id=user_id,
            )
        except HTTPException as browser_exc:
            if browser_exc.detail == "spotify_cookies_expired":
                _flag_spotify_cookies_expired(user_id)
            # If the browser also couldn't find any tracks, give the user the
            # original "page structure may have changed" message instead of
            # the browser-specific error  -  it's the more accurate diagnosis.
            if browser_exc.status_code == 422:
                raise HTTPException(
                    status_code=422,
                    detail=f"Could not extract tracks from {spotify_type}. It may be empty or Spotify's page structure may have changed."
                )
            raise

    # If at the embed limit, attempt the headless browser to get the full playlist.
    # The browser path works for public playlists without sp_dc; only private
    # playlists actually need the cookie.
    if len(tracks) >= 95:
        print(
            f"Spotify embed returned {len(tracks)} tracks (at limit), trying headless browser"
            + (" with sp_dc..." if sp_dc else " without sp_dc...")
        )
        browser_error = None
        try:
            browser_result = fetch_spotify_playlist_via_browser(
                spotify_id, spotify_type, expected_total=expected_total, sp_dc=sp_dc,
                user_id=user_id,
            )
            if browser_result["count"] > len(tracks):
                print(f"Headless browser returned {browser_result['count']} tracks (embed had {len(tracks)})")
                if expected_total and browser_result["count"] < expected_total:
                    browser_result = dict(browser_result)
                    browser_result["warning"] = (
                        f"Spotify reports {expected_total} items, browser extracted {browser_result['count']}. "
                        "Some tracks may still be missing."
                    )
                return browser_result
            # Browser confirmed same count as embed - playlist is probably complete
            print(f"Headless browser confirmed {browser_result['count']} tracks, playlist looks complete")
        except HTTPException as e:
            browser_error = e.detail
            print(f"Headless browser failed ({e.detail}), using embed results")
        except Exception as e:
            browser_error = str(e)
            print(f"Headless browser error: {e}, using embed results")

        if browser_error:
            expected_note = f" (Spotify reports {expected_total})" if expected_total else ""
            return {
                "tracks": tracks,
                "playlist_name": playlist_name,
                "count": len(tracks),
                "warning": (
                    f"Only the first {len(tracks)} tracks were fetched{expected_note}. "
                    f"Headless browser failed: {browser_error}. "
                    "Check that shm_size: '2gb' is set in docker-compose.yml."
                ),
            }

    return {
        "tracks": tracks,
        "playlist_name": playlist_name,
        "count": len(tracks)
    }


def _parse_listenbrainz_jspf_tracks(jspf_playlist: dict) -> list[tuple[str, str]]:
    """Extract (artist, title) pairs from a JSPF playlist dict.

    JSPF uses 'creator' for artist and 'title' for track name. Tracks with
    either field missing are skipped -- a song with no name is no song at all.
    """
    tracks = []
    for track in jspf_playlist.get("track", []):
        artist = (track.get("creator") or "").strip()
        title = (track.get("title") or "").strip()
        if artist and title:
            tracks.append((artist, title))
    return tracks


def fetch_listenbrainz_createdfor(username: str) -> list[dict]:
    """Fetch all 'Created for You' playlists for a ListenBrainz user.

    Returns a list of dicts, each with:
        playlist_url  -- stable JSPF URL for the individual playlist
        playlist_uuid -- UUID extracted from the URL
        name          -- playlist title from LB
        tracks        -- list of (artist, title) tuples

    Raises HTTPException on error.
    """
    url = f"{LISTENBRAINZ_API_URL}/1/user/{username}/playlists/createdfor"
    try:
        with httpx.Client(timeout=TIMEOUT_LISTENBRAINZ) as client:
            resp = client.get(url, headers={"Accept": "application/json"})
            if resp.status_code == 404:
                raise HTTPException(status_code=404, detail=f"ListenBrainz user '{username}' not found")
            resp.raise_for_status()
    except HTTPException:
        raise
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Failed to connect to ListenBrainz: {e}")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"ListenBrainz API error: {e.response.status_code}")

    data = resp.json()
    raw_playlists = data.get("playlists", [])

    if not raw_playlists:
        raise HTTPException(
            status_code=422,
            detail=f"No 'Created for You' playlists found for '{username}'. "
                   "ListenBrainz generates these weekly  -  check back after your account has some listening history."
        )

    results = []
    for entry in raw_playlists:
        playlist = entry.get("playlist", {})
        name = playlist.get("title", "ListenBrainz Playlist").strip()
        # The identifier is a URL like https://listenbrainz.org/playlist/UUID/
        identifier = playlist.get("identifier", "")
        uuid_match = re.search(r'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})', identifier, re.IGNORECASE)
        if not uuid_match:
            print(f"ListenBrainz: skipping playlist '{name}'  -  no UUID in identifier '{identifier}'")
            continue
        playlist_uuid = uuid_match.group(1)
        # Store the canonical per-playlist URL (without trailing slash for consistency)
        playlist_url = f"https://listenbrainz.org/playlist/{playlist_uuid}"
        # The listing endpoint always returns track:[] — tracks only exist on the
        # per-playlist JSPF endpoint, so we have to fetch each one individually.
        try:
            tracks, _ = _fetch_listenbrainz_playlist(playlist_uuid)
        except HTTPException as e:
            print(f"ListenBrainz: skipping playlist '{name}' ({playlist_uuid}): {e.detail}")
            continue
        results.append({
            "playlist_url": playlist_url,
            "playlist_uuid": playlist_uuid,
            "name": name,
            "tracks": tracks,
        })

    return results


def _fetch_listenbrainz_playlist(playlist_uuid: str) -> tuple[list[tuple[str, str]], str]:
    """Fetch a single ListenBrainz playlist by UUID via the JSPF API.

    Used during the regular refresh cycle for individual LB playlists.
    Returns (list of (artist, title) tuples, playlist_name).
    """
    url = f"{LISTENBRAINZ_API_URL}/1/playlist/{playlist_uuid}"
    try:
        with httpx.Client(timeout=TIMEOUT_LISTENBRAINZ_PLAYLIST) as client:
            resp = client.get(url, headers={"Accept": "application/json"})
            if resp.status_code == 404:
                raise HTTPException(status_code=404, detail="ListenBrainz playlist not found (may have been rotated)")
            resp.raise_for_status()
    except HTTPException:
        raise
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Failed to connect to ListenBrainz: {e}")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"ListenBrainz API error: {e.response.status_code}")

    data = resp.json()
    playlist = data.get("playlist", {})
    name = playlist.get("title", "ListenBrainz Playlist").strip()
    tracks = _parse_listenbrainz_jspf_tracks(playlist)

    if not tracks:
        raise HTTPException(status_code=422, detail="No tracks found in ListenBrainz playlist")

    print(f"Fetched {len(tracks)} tracks from ListenBrainz playlist '{name}'")
    return tracks, name


def _fetch_soundcloud_playlist(url: str) -> tuple[list[tuple[str, str]], str]:
    """Fetch tracks from a SoundCloud set or likes URL via yt-dlp.

    Unlike YouTube, SoundCloud's flat-playlist mode returns stub entries with
    no title or uploader. --dump-single-json fetches the full playlist object
    including complete track metadata in one shot.
    """
    cmd = [
        "yt-dlp",
        "--dump-single-json",
        "--no-warnings",
        url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_YTDLP_PLAYLIST)
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Timeout fetching SoundCloud playlist")

    # yt-dlp may exit non-zero if individual tracks are geo-restricted, but
    # still return valid playlist JSON on stdout.  Parse first, fail later.
    try:
        data = json.loads(result.stdout.strip())
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=502, detail="Failed to fetch SoundCloud playlist")

    playlist_name = data.get("title") or "SoundCloud Playlist"
    entries = data.get("entries") or []

    tracks = []
    seen: set[tuple[str, str]] = set()

    for entry in entries:
        if not entry or not entry.get("id"):
            continue
        title = entry.get("title") or "Unknown"
        channel = entry.get("uploader") or entry.get("channel") or "Unknown"
        artist, clean_title = extract_artist_title(title, channel)
        key = (artist.lower(), clean_title.lower())
        if key not in seen:
            seen.add(key)
            tracks.append((artist, clean_title))

    if not tracks:
        raise HTTPException(status_code=422, detail="No tracks found in SoundCloud playlist")

    return tracks, playlist_name


def fetch_playlist_tracks(url: str, platform: str, user_id: str | None = None) -> tuple[list[tuple[str, str]], str, str | None]:
    """Fetch tracks from a playlist URL

    Returns (list of (artist, title) tuples, playlist_name, warning_or_None)
    """
    if platform == "spotify_likes":
        spotify_cookies_text = get_setting("spotify_cookies", "", user_id=user_id)
        sp_dc = _extract_sp_dc(spotify_cookies_text) if spotify_cookies_text.strip() else None
        if not sp_dc:
            raise HTTPException(
                status_code=401,
                detail="Spotify liked songs requires an sp_dc cookie. Add your Spotify cookies in Settings."
            )
        result = fetch_spotify_playlist_via_browser(
            "tracks", "collection", sp_dc=sp_dc, user_id=user_id
        )
        tracks = []
        for track_str in result["tracks"]:
            if " - " in track_str:
                artist, title = track_str.split(" - ", 1)
                tracks.append((artist.strip(), title.strip()))
            else:
                tracks.append(("Unknown", track_str.strip()))
        return tracks, result["playlist_name"], result.get("warning")

    if platform == "spotify":
        spotify_cookies_text = get_setting("spotify_cookies", "", user_id=user_id)
        sp_dc = _extract_sp_dc(spotify_cookies_text) if spotify_cookies_text.strip() else None
        result = _fetch_spotify_playlist_embed(url, sp_dc=sp_dc, user_id=user_id)

        # Parse "Artist - Title" format back to tuples
        tracks = []
        for track_str in result["tracks"]:
            if " - " in track_str:
                artist, title = track_str.split(" - ", 1)
                tracks.append((artist.strip(), title.strip()))
            else:
                tracks.append(("Unknown", track_str.strip()))

        return tracks, result["playlist_name"], result.get("warning")

    elif platform == "youtube":
        # Use yt-dlp to get playlist info
        m = re.search(r'list=([a-zA-Z0-9_-]+)', url)
        if not m:
            raise HTTPException(status_code=400, detail="Invalid YouTube playlist URL: no list= parameter found")
        playlist_id = m.group(1)

        # Mix/Radio playlists (RD prefix) only work when seeded with the original watch URL  - 
        # YouTube refuses the bare /playlist?list=RD... form. Pass the URL as-is in that case.
        if playlist_id.startswith("RD"):
            ytdlp_url = url
        else:
            ytdlp_url = f"https://www.youtube.com/playlist?list={playlist_id}"

        info_cmd = [
            "yt-dlp",
            *_ytdlp_base_args(),
            "--dump-json",
            "--flat-playlist",
            "--no-warnings",
            ytdlp_url
        ]

        try:
            result = subprocess.run(info_cmd, capture_output=True, text=True, timeout=TIMEOUT_YTDLP_PLAYLIST)
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=504, detail="Timeout fetching YouTube playlist")

        if result.returncode != 0:
            # A bad/missing/private playlist is the caller's mistake (404), not an
            # upstream gateway failure (502). yt-dlp tells us which in stderr: a
            # dud or malformed list id makes YouTube's API answer "400 Bad
            # Request", a deleted/private one says so outright. Anything else
            # (timeouts, transient network) stays a 502.
            stderr_lc = (result.stderr or "").lower()
            client_error_signs = (
                "does not exist", "unavailable", "private", "has been removed",
                "this playlist does not exist or is private",
                "http error 400", "bad request", "unable to download api page",
                "not a valid url", "incomplete youtube id",
            )
            if any(s in stderr_lc for s in client_error_signs):
                raise HTTPException(status_code=404, detail="YouTube playlist not found (it may have been deleted, made private, or the URL is wrong)")
            raise HTTPException(status_code=502, detail="Failed to fetch YouTube playlist")

        tracks = []
        playlist_name = "YouTube Playlist"

        for line in result.stdout.strip().split('\n'):
            if not line:
                continue
            try:
                data = json.loads(line)
                # First entry often has playlist title
                if data.get("playlist_title") and playlist_name == "YouTube Playlist":
                    playlist_name = data["playlist_title"]

                if data.get("id"):
                    title = data.get("title", "Unknown")
                    channel = data.get("channel", data.get("uploader", "Unknown"))
                    artist, clean_title_val = extract_artist_title(title, channel)
                    tracks.append((artist, clean_title_val))
            except json.JSONDecodeError:
                continue

        if not tracks:
            raise HTTPException(status_code=422, detail="No tracks found in YouTube playlist")

        return tracks, playlist_name, None

    elif platform == "apple":
        music_user_token = get_setting("apple_music_user_token", "", user_id=user_id) or None
        result = fetch_apple_music_playlist(url, music_user_token=music_user_token)

        tracks = []
        for track_str in result["tracks"]:
            if " - " in track_str:
                artist, title = track_str.split(" - ", 1)
                tracks.append((artist.strip(), title.strip()))
            else:
                tracks.append(("Unknown", track_str.strip()))

        return tracks, result["playlist_name"], None

    elif platform == "amazon":
        result = fetch_amazon_playlist(url)

        tracks = []
        for track_str in result["tracks"]:
            if " - " in track_str:
                artist, title = track_str.split(" - ", 1)
                tracks.append((artist.strip(), title.strip()))
            else:
                tracks.append(("Unknown", track_str.strip()))

        return tracks, result["playlist_name"], None

    elif platform == "listenbrainz":
        # Single LB playlist by UUID  -  regular refresh path
        m = re.search(r'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})', url, re.IGNORECASE)
        if not m:
            raise HTTPException(status_code=400, detail="Invalid ListenBrainz playlist URL: no UUID found")
        tracks, name = _fetch_listenbrainz_playlist(m.group(1))
        return tracks, name, None

    elif platform == "listenbrainz_user":
        # Username URLs are handled at the add-watched level (fan-out to multiple playlists).
        # If we ever reach here at refresh time something has gone wrong.
        raise HTTPException(
            status_code=400,
            detail="ListenBrainz user URLs are only valid when adding a watched playlist. Individual playlist URLs are stored for refresh."
        )

    elif platform == "soundcloud":
        tracks, name = _fetch_soundcloud_playlist(url)
        return tracks, name, None

    elif platform == "tidal":
        m = re.search(r'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})', url, re.IGNORECASE)
        if not m:
            raise HTTPException(status_code=400, detail="Invalid Tidal playlist URL: no UUID found")
        result = fetch_tidal_playlist(m.group(1))
        tracks = []
        for track_str in result["tracks"]:
            if " - " in track_str:
                artist, title = track_str.split(" - ", 1)
                tracks.append((artist.strip(), title.strip()))
            else:
                tracks.append(("Unknown", track_str.strip()))
        return tracks, result["playlist_name"], None

    elif platform == "monochrome":
        m = re.search(r'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})', url, re.IGNORECASE)
        if not m:
            raise HTTPException(status_code=400, detail="Invalid Monochrome playlist URL: no UUID found")
        from monochrome import fetch_tidal_playlist_tracks
        tracks, name = fetch_tidal_playlist_tracks(m.group(1))
        if not tracks:
            raise HTTPException(status_code=422, detail="No tracks found in Monochrome playlist")
        return tracks, name, None

    elif platform == "beatport":
        result = fetch_beatport_playlist(url)
        tracks = []
        for track_str in result["tracks"]:
            if " - " in track_str:
                artist, title = track_str.split(" - ", 1)
                tracks.append((artist.strip(), title.strip()))
            else:
                tracks.append(("Unknown", track_str.strip()))
        return tracks, result["playlist_name"], None

    raise HTTPException(status_code=400, detail=f"Unsupported platform: {platform}")


def refresh_watched_playlist(playlist_id: str) -> dict:
    """Fetch playlist and queue any new tracks for download

    Returns dict with refresh results
    """
    with db_conn() as conn:
        conn.row_factory = sqlite3.Row

        playlist = conn.execute(
            "SELECT * FROM watched_playlists WHERE id = ?", (playlist_id,)
        ).fetchone()

        if not playlist:
            return {"error": "Playlist not found", "playlist_id": playlist_id}

        playlist = dict(playlist)
        sync_mode = playlist.get("sync_mode", "append")
        user_id = playlist.get("user_id")

        # Acquire an atomic per-playlist refresh lock.
        # If a stale "running" state is older than WATCHED_REFRESH_STALE_SECONDS,
        # this update will take over and start a fresh run.
        lock_cursor = conn.execute(
            """UPDATE watched_playlists
               SET refresh_state = 'running',
                   refresh_stage = 'starting',
                   refresh_started_at = datetime('now'),
                   refresh_completed_at = NULL,
                   refresh_error = NULL,
                   refresh_import_id = NULL
               WHERE id = ?
               AND (
                   refresh_state IS NULL
                   OR refresh_state != 'running'
                   OR refresh_started_at IS NULL
                   OR refresh_started_at < datetime('now', '-' || ? || ' seconds')
               )""",
            (playlist_id, str(WATCHED_REFRESH_STALE_SECONDS))
        )
        conn.commit()

        if lock_cursor.rowcount == 0:
            running_state = conn.execute(
                "SELECT refresh_stage, refresh_started_at FROM watched_playlists WHERE id = ?",
                (playlist_id,)
            ).fetchone()
            return {
                "playlist_id": playlist_id,
                "name": playlist["name"],
                "already_running": True,
                "message": "Refresh already in progress",
                "refresh_stage": running_state["refresh_stage"] if running_state else None,
                "refresh_started_at": running_state["refresh_started_at"] if running_state else None,
            }

        def set_refresh_stage(stage: str) -> None:
            conn.execute(
                """UPDATE watched_playlists
                   SET refresh_state = 'running',
                       refresh_stage = ?,
                       refresh_error = NULL
                   WHERE id = ?""",
                (stage, playlist_id)
            )
            conn.commit()

        def finish_refresh_success(import_id: str | None) -> None:
            # A clean fetch means the playlist is alive and well, so wipe any
            # accumulated "gone" strikes and clear a stale auto-pause note.
            conn.execute(
                """UPDATE watched_playlists
                   SET refresh_state = 'idle',
                       refresh_stage = 'done',
                       refresh_error = NULL,
                       refresh_import_id = ?,
                       refresh_completed_at = datetime('now'),
                       gone_strikes = 0,
                       auto_paused = 0,
                       pause_reason = NULL
                   WHERE id = ?""",
                (import_id, playlist_id)
            )
            conn.commit()

        def finish_refresh_error(error_msg: str) -> None:
            conn.execute(
                """UPDATE watched_playlists
                   SET refresh_state = 'error',
                       refresh_stage = 'failed',
                       refresh_error = ?,
                       refresh_import_id = NULL,
                       refresh_completed_at = datetime('now')
                   WHERE id = ?""",
                ((error_msg or "Refresh failed")[:800], playlist_id)
            )
            conn.commit()

        try:
            # Fetch current tracks
            set_refresh_stage("fetching")
            # ListenBrainz "Created for You" playlists rotate every Monday — check the date stamp in
            # the playlist name ("week of YYYY-MM-DD") and proactively re-resolve if it's ≥6 days old.
            # Also re-resolves reactively on 404 in case the name date wasn't parseable.

            # Self-heal: playlists added before lb_username was introduced have NULL stored.
            # Extract the username from the name ("Weekly Exploration for USERNAME, week of ...") and
            # persist it so the re-resolution logic can do its job.
            if playlist["platform"] == "listenbrainz" and not playlist.get("lb_username"):
                _name_for_heal = playlist.get("name") or ""
                _heal_m = re.search(r'.+ for ([^,]+), week of \d{4}-\d{2}-\d{2}', _name_for_heal)
                if _heal_m:
                    _healed_username = _heal_m.group(1).strip()
                    conn.execute("UPDATE watched_playlists SET lb_username = ? WHERE id = ?",
                                 (_healed_username, playlist_id))
                    conn.commit()
                    playlist["lb_username"] = _healed_username
                    print(f"[lb] Self-healed lb_username='{_healed_username}' for '{_name_for_heal}'")

            if playlist["platform"] == "listenbrainz" and playlist.get("lb_username"):
                playlist_name = playlist["name"] or ""

                def _lb_needs_reresolution() -> bool:
                    m = re.search(r'week of (\d{4}-\d{2}-\d{2})', playlist_name)
                    if not m:
                        return False
                    try:
                        week_date = datetime.strptime(m.group(1), "%Y-%m-%d").replace(tzinfo=timezone.utc)
                        age_days = (datetime.now(timezone.utc) - week_date).days
                        return age_days >= 6
                    except ValueError:
                        return False

                def _lb_playlist_week_date(name: str) -> datetime:
                    m = re.search(r'week of (\d{4}-\d{2}-\d{2})', name or "")
                    if not m:
                        return datetime.min.replace(tzinfo=timezone.utc)
                    try:
                        return datetime.strptime(m.group(1), "%Y-%m-%d").replace(tzinfo=timezone.utc)
                    except ValueError:
                        return datetime.min.replace(tzinfo=timezone.utc)

                def _lb_reresolution_fetch(prefer_latest: bool = False) -> list:
                    """Re-query createdfor API, update stored URL, return tracks."""
                    print(f"ListenBrainz playlist '{playlist_name}' appears stale — re-resolving via createdfor API")
                    lb_playlists = fetch_listenbrainz_createdfor(playlist["lb_username"])
                    name_prefix = playlist_name.split(", week of")[0]
                    same_family = [p for p in lb_playlists if p["name"].startswith(name_prefix)]
                    if prefer_latest and same_family:
                        matched = max(same_family, key=lambda p: _lb_playlist_week_date(p["name"]))
                    else:
                        # For 404 self-healing, exact match is fine. For stale
                        # weekly playlists, exact match is the old playlist and
                        # must not win over the newer playlist with the same prefix.
                        matched = next((p for p in lb_playlists if p["name"] == playlist_name), None)
                        if not matched and same_family:
                            matched = max(same_family, key=lambda p: _lb_playlist_week_date(p["name"]))
                    if not matched:
                        raise HTTPException(
                            status_code=404,
                            detail=f"ListenBrainz playlist '{playlist_name}' not found in current createdfor list for '{playlist['lb_username']}'"
                        )
                    new_url = matched["playlist_url"]
                    if new_url != playlist["url"]:
                        conn.execute("UPDATE watched_playlists SET url = ?, name = ? WHERE id = ?",
                                     (new_url, matched["name"], playlist_id))
                        conn.commit()
                        playlist["url"] = new_url
                        playlist["name"] = matched["name"]
                        print(f"Updated ListenBrainz URL for '{matched['name']}' to {new_url}")
                    return matched["tracks"]

                if _lb_needs_reresolution():
                    tracks = _lb_reresolution_fetch(prefer_latest=True)
                else:
                    try:
                        tracks, _, _ = fetch_playlist_tracks(playlist["url"], playlist["platform"], user_id=user_id)
                    except HTTPException as e:
                        if e.status_code != 404:
                            raise
                        tracks = _lb_reresolution_fetch(prefer_latest=False)
            else:
                tracks, _, _ = fetch_playlist_tracks(playlist["url"], playlist["platform"], user_id=user_id)

            # Build a set of hashes for what the upstream playlist currently contains
            current_hashes = {hash_track(artist, title) for artist, title in tracks}

            # Load existing track state (including job status and removal flag)
            set_refresh_stage("diffing")
            track_rows = conn.execute(
                """SELECT wpt.track_hash, wpt.downloaded_at, wpt.job_id, wpt.removed_at,
                          wpt.artist, wpt.title, wpt.resolved_path, j.status as job_status,
                          j.artist as job_artist, j.title as job_title
                   FROM watched_playlist_tracks wpt
                   LEFT JOIN jobs j ON wpt.job_id = j.id
                   WHERE wpt.playlist_id = ?""",
                (playlist_id,)
            ).fetchall()
            tracked = {row["track_hash"]: row for row in track_rows}

            new_tracks = []
            missing_tracks = []
            removed_count = 0

            for position, (artist, title) in enumerate(tracks):
                track_hash = hash_track(artist, title)
                # Always keep positions current so M3U reflects upstream order
                conn.execute(
                    "UPDATE watched_playlist_tracks SET position = ? WHERE playlist_id = ? AND track_hash = ?",
                    (position, playlist_id, track_hash)
                )
                existing = tracked.get(track_hash)
                if not existing:
                    new_tracks.append((artist, title, track_hash))
                    continue

                # Track has reappeared after being removed upstream  -  clear the removal flag
                if existing["removed_at"] and sync_mode == "mirror":
                    conn.execute(
                        "UPDATE watched_playlist_tracks SET removed_at = NULL WHERE playlist_id = ? AND track_hash = ?",
                        (playlist_id, track_hash)
                    )

                if existing["downloaded_at"]:
                    # File was deleted manually after being marked downloaded.
                    # If we cannot resolve it locally anymore, treat it as missing and re-queue.
                    if not _has_local_track_file(
                        playlist["name"],
                        bool(playlist.get("use_playlists_dir", False)),
                        existing["artist"] or artist,
                        existing["title"] or title,
                        existing["job_artist"] or "",
                        existing["job_title"] or "",
                        user_id=user_id,
                        resolved_path=existing["resolved_path"],
                    ):
                        conn.execute(
                            "UPDATE watched_playlist_tracks SET downloaded_at = NULL WHERE playlist_id = ? AND track_hash = ?",
                            (playlist_id, track_hash)
                        )
                        missing_tracks.append((artist, title, track_hash))
                        continue
                    continue

                job_status = existing["job_status"]
                if job_status == "completed":
                    conn.execute(
                        "UPDATE watched_playlist_tracks SET downloaded_at = datetime('now') WHERE playlist_id = ? AND track_hash = ?",
                        (playlist_id, track_hash)
                    )
                    continue

                if job_status in ("queued", "downloading"):
                    continue

                # Job failed (or never ran), but check if the file landed on disk anyway
                # (e.g. a manual download, or a previous sync via a different playlist).
                if _has_local_track_file(
                    playlist["name"],
                    bool(playlist.get("use_playlists_dir", False)),
                    existing["artist"] or artist,
                    existing["title"] or title,
                    existing["job_artist"] or "",
                    existing["job_title"] or "",
                    user_id=user_id,
                    resolved_path=existing["resolved_path"],
                ):
                    conn.execute(
                        "UPDATE watched_playlist_tracks SET downloaded_at = datetime('now') WHERE playlist_id = ? AND track_hash = ?",
                        (playlist_id, track_hash)
                    )
                    continue

                missing_tracks.append((artist, title, track_hash))

            # In mirror mode: mark any previously tracked tracks that are no longer in the upstream
            if sync_mode == "mirror":
                for track_hash, row in tracked.items():
                    if track_hash not in current_hashes and not row["removed_at"]:
                        conn.execute(
                            "UPDATE watched_playlist_tracks SET removed_at = datetime('now') WHERE playlist_id = ? AND track_hash = ?",
                            (playlist_id, track_hash)
                        )
                        removed_count += 1

                if removed_count:
                    print(
                        f"Watched playlist '{playlist['name']}' (mirror): "
                        f"{removed_count} track(s) removed from upstream, marked in DB"
                    )

            # Insert any new tracks so they are tracked before download
            # Build a position lookup from the current upstream order
            track_positions = {hash_track(a, t): i for i, (a, t) in enumerate(tracks)}
            for artist, title, track_hash in new_tracks:
                conn.execute("""
                    INSERT INTO watched_playlist_tracks
                    (playlist_id, track_hash, artist, title, position)
                    VALUES (?, ?, ?, ?, ?)
                """, (playlist_id, track_hash, artist, title, track_positions.get(track_hash)))

            tracks_to_import = [(artist, title) for artist, title, _ in new_tracks + missing_tracks]
            use_playlists_dir = bool(playlist.get("use_playlists_dir", False))
            custom_subdir = (playlist.get("custom_subdir") or "").strip() or None
            import_id = None
            if tracks_to_import:
                set_refresh_stage("queueing")
                import_id = start_bulk_import_for_tracks(
                    tracks_to_import,
                    bool(playlist["convert_to_flac"]),
                    watch_playlist_id=playlist_id,
                    use_playlists_dir=use_playlists_dir,
                    user_id=user_id,
                    preferred_sources=playlist.get("preferred_sources") or "all",
                    priority_source=playlist.get("priority_source"),
                    custom_subdir=custom_subdir,
                )

            # Update playlist metadata
            set_refresh_stage("finalizing")
            conn.execute("""
                UPDATE watched_playlists
                SET last_checked = datetime('now'), last_track_count = ?
                WHERE id = ?
            """, (len(tracks), playlist_id))

            conn.commit()

            # Rebuild M3U from all tracks downloaded so far (new ones are still queued,
            # so they'll appear next refresh once marked downloaded)
            if playlist.get("make_m3u"):
                set_refresh_stage("rebuilding_m3u")
                rebuild_watched_playlist_m3u(
                    playlist_id, playlist["name"],
                    use_playlists_dir=use_playlists_dir,
                    sync_mode=sync_mode,
                    user_id=user_id,
                    custom_subdir=custom_subdir,
                )

            queued_count = len(tracks_to_import)
            if queued_count:
                print(
                    f"Watched playlist '{playlist['name']}': {len(new_tracks)} new tracks, "
                    f"{len(missing_tracks)} missing tracks, {queued_count} queued"
                )

            finish_refresh_success(import_id)
            return {
                "playlist_id": playlist_id,
                "name": playlist["name"],
                "total_tracks": len(tracks),
                "new_tracks": len(new_tracks),
                "missing_tracks": len(missing_tracks),
                "removed_tracks": removed_count,
                "queued": queued_count,
                "import_id": import_id,
                "refresh_state": "idle",
                "refresh_stage": "done",
                "jobs": []
            }

        except HTTPException as e:
            err_msg = str(e.detail)
            # Spotify cookie expiry deserves a human-readable message and a notification
            if err_msg == "spotify_cookies_expired":
                err_msg = "Spotify cookies have expired. Update them in Settings to resume this playlist."
                try:
                    from notifications import send_notification
                    send_notification(
                        "error", playlist["name"], status="failed",
                        error=err_msg, playlist_name=playlist["name"],
                        user_id=user_id,
                    )
                except Exception:
                    pass
            conn.execute(
                "UPDATE watched_playlists SET last_checked = datetime('now') WHERE id = ?",
                (playlist_id,)
            )
            conn.commit()

            # A 404 means the upstream playlist came back "not found": it's been
            # deleted, made private, or the login token for that platform expired.
            # We can't tell which from one fetch, so we count consecutive strikes
            # and only auto-pause once it's clearly not a one-off blip. Pausing
            # (not deleting) leaves the user free to fix the source and Resume.
            auto_paused = False
            if e.status_code == 404:
                new_strikes = (playlist.get("gone_strikes") or 0) + 1
                if new_strikes >= WATCHED_GONE_STRIKES_BEFORE_PAUSE:
                    auto_paused = True
                    pause_note = (
                        f"Auto-paused after {new_strikes} consecutive 'not found' checks, "
                        "so this playlist looks deleted or made private upstream (or the login "
                        "token for that platform has expired). Check the source and your cookies, "
                        "then Resume to retry."
                    )
                    conn.execute(
                        """UPDATE watched_playlists
                           SET enabled = 0, auto_paused = 1, pause_reason = ?, gone_strikes = ?
                           WHERE id = ?""",
                        (pause_note, new_strikes, playlist_id)
                    )
                    conn.commit()
                    err_msg = pause_note
                    try:
                        from notifications import send_notification
                        send_notification(
                            "error", playlist["name"], status="failed",
                            error=pause_note, playlist_name=playlist["name"],
                            user_id=user_id,
                        )
                    except Exception:
                        pass
                    print(f"Watched playlist '{playlist['name']}' auto-paused: {pause_note}")
                else:
                    conn.execute(
                        "UPDATE watched_playlists SET gone_strikes = ? WHERE id = ?",
                        (new_strikes, playlist_id)
                    )
                    conn.commit()
                    print(
                        f"Watched playlist '{playlist['name']}' returned 'not found' "
                        f"(strike {new_strikes}/{WATCHED_GONE_STRIKES_BEFORE_PAUSE})"
                    )

            finish_refresh_error(err_msg)
            return {
                "playlist_id": playlist_id,
                "name": playlist["name"],
                "error": err_msg,
                "auto_paused": auto_paused,
                "refresh_state": "error",
                "refresh_stage": "failed",
            }
        except Exception as e:
            err_msg = str(e)
            conn.execute(
                "UPDATE watched_playlists SET last_checked = datetime('now') WHERE id = ?",
                (playlist_id,)
            )
            conn.commit()
            finish_refresh_error(err_msg)
            return {
                "playlist_id": playlist_id,
                "name": playlist["name"],
                "error": err_msg,
                "refresh_state": "error",
                "refresh_stage": "failed",
            }


# =============================================================================
# Background Scheduler for Watched Playlists
# =============================================================================

_scheduler_running = False
_scheduler_lock = threading.Lock()


def watched_playlist_scheduler():
    """Background thread that periodically checks watched playlists"""
    print(f"Watched playlist scheduler started (checking every {WATCHED_PLAYLIST_CHECK_HOURS} hours)")

    # Brief delay to let the app fully initialise, then check immediately
    time.sleep(10)
    print("Scheduler: Running initial check for overdue playlists...")

    while _scheduler_running:
        try:
            # Run the check
            print("Scheduler: Checking watched playlists...")
            with db_conn() as conn:
                conn.row_factory = sqlite3.Row

                playlists = conn.execute("""
                    SELECT id, name FROM watched_playlists
                    WHERE enabled = 1
                    AND (last_checked IS NULL
                         OR datetime(last_checked, '+' || refresh_interval_hours || ' hours') < datetime('now'))
                """).fetchall()

            if playlists:
                print(f"Scheduler: Found {len(playlists)} playlists due for refresh")
                total_new = 0
                for playlist in playlists:
                    result = refresh_watched_playlist(playlist["id"])
                    total_new += result.get("new_tracks", 0)
                print(f"Scheduler: Checked {len(playlists)} playlists, {total_new} new tracks found")
            else:
                print("Scheduler: No playlists due for refresh")

        except Exception as e:
            print(f"Scheduler error: {e}")

        # Sleep until next check interval
        base_sleep_seconds = WATCHED_PLAYLIST_CHECK_HOURS * 3600
        jitter = random.uniform(0.95, 1.05)
        sleep_seconds = max(60, int(base_sleep_seconds * jitter))
        elapsed = 0
        while elapsed < sleep_seconds and _scheduler_running:
            time.sleep(60)  # Check every minute if we should stop
            elapsed += 60


def start_scheduler():
    """Start the background scheduler if not already running"""
    global _scheduler_running

    if WATCHED_PLAYLIST_CHECK_HOURS <= 0:
        print("Watched playlist scheduler disabled (WATCHED_PLAYLIST_CHECK_HOURS=0)")
        return

    with _scheduler_lock:
        if _scheduler_running:
            return
        _scheduler_running = True

    spawn_daemon_thread(watched_playlist_scheduler)
