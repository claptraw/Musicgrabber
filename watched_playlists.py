"""
MusicGrabber - Watched Playlists

Platform detection, track fetching, playlist refresh, and background scheduler.
"""

import json
import random
import re
import sqlite3
import subprocess
import time

from fastapi import HTTPException

from constants import (
    TIMEOUT_YTDLP_PLAYLIST, TIMEOUT_HTTP_SPOTIFY, TIMEOUT_MONOCHROME_API,
    MONOCHROME_API_URL, WATCHED_PLAYLIST_CHECK_HOURS, WATCHED_REFRESH_STALE_SECONDS,
    LISTENBRAINZ_API_URL, TIMEOUT_LISTENBRAINZ, TIMEOUT_LISTENBRAINZ_PLAYLIST,
    AUDIO_EXTENSIONS,
)
from db import db_conn
from bulk_import import start_bulk_import_for_tracks
from amazon import fetch_amazon_playlist
from downloads import rebuild_watched_playlist_m3u
from settings import get_playlists_dir
from spotify import fetch_spotify_playlist_via_browser
from utils import extract_artist_title, hash_track, spawn_daemon_thread, sanitize_filename, check_duplicate
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


def _playlist_file_exists(playlist_name: str, artist: str, title: str) -> bool:
    """Check whether a track exists inside Playlists/<playlist_name>/."""
    playlists_dir = get_playlists_dir()
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


def _has_local_track_file(playlist_name: str, use_playlists_dir: bool, artist: str, title: str, job_artist: str = "", job_title: str = "") -> bool:
    """Return True if we can resolve a local file for this watched track.

    Checks MusicGrabber's own library first, then falls back to Navidrome (real
    absolute paths only  -  synthetic paths mean real-path mode is off, which is
    a config problem, not a reason to re-download).
    """
    pairs = []
    for a, t in ((job_artist, job_title), (artist, title)):
        a = (a or "").strip()
        t = (t or "").strip()
        if a and t and (a, t) not in pairs:
            pairs.append((a, t))

    for a, t in pairs:
        if check_duplicate(a, t):
            return True
        if use_playlists_dir and _playlist_file_exists(playlist_name, a, t):
            return True

    # Last resort: check Navidrome. Accepts absolute paths only  -  synthetic
    # relative paths ("Artist/Album/Track.mp3") are not a reliable signal that
    # the file actually exists on MusicGrabber's filesystem.
    for a, t in pairs:
        nav_path = check_navidrome_duplicate(a, t)
        if nav_path and nav_path.is_absolute():
            return True

    return False


def detect_playlist_platform(url: str) -> tuple[str, str]:
    """Detect platform and extract ID from playlist URL

    Returns (platform, id) or raises HTTPException if invalid
    """
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

    # Amazon Music playlist (user or curated, any regional TLD)
    amazon_playlist = re.match(r'https?://music\.amazon\.[a-z.]+/(user-playlists|playlists)/\S+', url)
    if amazon_playlist:
        return "amazon", url  # Full URL needed  -  no extractable ID

    # Tidal public playlist
    tidal_playlist = re.match(r'https?://(?:www\.)?tidal\.com/(?:browse/)?playlist/([0-9a-f-]{36})', url, re.IGNORECASE)
    if tidal_playlist:
        return "tidal", tidal_playlist.group(1)

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

    raise HTTPException(
        status_code=400,
        detail="Invalid playlist URL. Supported: Spotify playlists/albums, YouTube/YouTube Music playlists, Amazon Music playlists, Tidal public playlists, ListenBrainz playlists or usernames."
    )


def _fetch_spotify_playlist_embed(url: str) -> dict:
    """Fetch Spotify playlist tracks via embed endpoint.
    This is the fast path that works for playlists with <100 tracks.
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

    # Fetch the embed page. Spotify's public playlist API is gone, so we scrape the
    # embed HTML which includes a predictable JSON-in-HTML "title"/"subtitle" pattern.
    # If this breaks, inspect the embed HTML for renamed fields or a new data blob.
    try:
        with httpx.Client(timeout=TIMEOUT_HTTP_SPOTIFY, follow_redirects=True) as client:
            response = client.get(
                f"https://open.spotify.com/embed/{spotify_type}/{spotify_id}",
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                }
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(status_code=404, detail=f"{spotify_type.title()} not found or is private")
        raise HTTPException(status_code=502, detail=f"Failed to fetch {spotify_type}: {e}")
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Failed to connect to Spotify: {e}")

    html_content = response.text
    expected_total = None
    total_match = re.search(r'"totalCount":\s*(\d+)', html_content)
    if total_match:
        try:
            expected_total = int(total_match.group(1))
        except ValueError:
            expected_total = None

    # Extract name
    playlist_name = f"Spotify {spotify_type.title()}"
    title_matches = re.findall(r'"title":"([^"]+)"', html_content)
    if title_matches:
        playlist_name = title_matches[0]

    # Extract tracks using title/subtitle pattern
    tracks = []
    titles = re.findall(r'"title":"([^"]+)"', html_content)
    subtitles = re.findall(r'"subtitle":"([^"]+)"', html_content)

    if len(titles) > 1 and len(subtitles) > 1:
        track_titles = titles[1:]  # Skip playlist name
        track_artists = subtitles[1:]  # Skip "Spotify"

        for title, artist in zip(track_titles, track_artists):
            try:
                title = json.loads(f'"{title}"')
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass
            try:
                artist = json.loads(f'"{artist}"')
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass
            tracks.append(f"{artist} - {title}")

    if not tracks:
        raise HTTPException(
            status_code=422,
            detail=f"Could not extract tracks from {spotify_type}. It may be empty or Spotify's page structure may have changed."
        )

    # If near the embed limit, try headless browser for full list
    if len(tracks) >= 95:
        print(f"Spotify embed returned {len(tracks)} tracks (near limit), trying headless browser...")
        browser_error = None
        try:
            browser_result = fetch_spotify_playlist_via_browser(
                spotify_id, spotify_type, expected_total=expected_total
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
        except HTTPException as e:
            browser_error = e.detail
            print(f"Headless browser failed ({e.detail}), using embed results")
        except Exception as e:
            browser_error = str(e)
            print(f"Headless browser error: {e}, using embed results")

        expected_note = f" (Spotify reports {expected_total})" if expected_total else ""
        warning = (
            f"Playlist truncated at {len(tracks)} tracks{expected_note} - headless browser failed"
            + (f": {browser_error}" if browser_error else "")
            + ". Check that shm_size: '2gb' is set in docker-compose.yml."
        )
        return {
            "tracks": tracks,
            "playlist_name": playlist_name,
            "count": len(tracks),
            "warning": warning,
        }

    return {
        "tracks": tracks,
        "playlist_name": playlist_name,
        "count": len(tracks)
    }


def _fetch_tidal_playlist(playlist_uuid: str) -> dict:
    """Fetch Tidal playlist tracks via the Monochrome API.

    Monochrome exposes a public /playlist/ endpoint that returns the full
    track list in one shot  -  no pagination, no headless browser required.
    Each item has artist.name and title at the top level. Simple.
    """
    api_url = f"{MONOCHROME_API_URL}/playlist/?id={playlist_uuid}"
    try:
        with httpx.Client(timeout=TIMEOUT_MONOCHROME_API) as client:
            response = client.get(api_url)
            response.raise_for_status()
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(status_code=404, detail="Tidal playlist not found or is private")
        raise HTTPException(status_code=502, detail=f"Monochrome API error: {e.response.status_code}")
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Failed to connect to Monochrome API: {e}")

    data = response.json()
    playlist_info = data.get("playlist", {})
    playlist_name = playlist_info.get("title", "Tidal Playlist")
    items = data.get("items", [])

    tracks = []
    for item in items:
        track = item.get("item", {})
        if item.get("type") != "track":
            continue
        title = track.get("title", "").strip()
        # Prefer the primary artist; fall back to the first in the artists list
        artist_obj = track.get("artist") or (track.get("artists") or [{}])[0]
        artist = artist_obj.get("name", "").strip()
        if title and artist:
            tracks.append(f"{artist} - {title}")

    if not tracks:
        raise HTTPException(
            status_code=422,
            detail="No tracks found in Tidal playlist. It may be empty or private."
        )

    print(f"Fetched {len(tracks)} tracks from Tidal playlist '{playlist_name}' via Monochrome API")
    return {"tracks": tracks, "playlist_name": playlist_name, "count": len(tracks)}


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


def fetch_playlist_tracks(url: str, platform: str) -> tuple[list[tuple[str, str]], str]:
    """Fetch tracks from a playlist URL

    Returns (list of (artist, title) tuples, playlist_name)
    """
    if platform == "spotify":
        result = _fetch_spotify_playlist_embed(url)

        # Parse "Artist - Title" format back to tuples
        tracks = []
        for track_str in result["tracks"]:
            if " - " in track_str:
                artist, title = track_str.split(" - ", 1)
                tracks.append((artist.strip(), title.strip()))
            else:
                tracks.append(("Unknown", track_str.strip()))

        return tracks, result["playlist_name"]

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

        return tracks, playlist_name

    elif platform == "amazon":
        result = fetch_amazon_playlist(url)

        tracks = []
        for track_str in result["tracks"]:
            if " - " in track_str:
                artist, title = track_str.split(" - ", 1)
                tracks.append((artist.strip(), title.strip()))
            else:
                tracks.append(("Unknown", track_str.strip()))

        return tracks, result["playlist_name"]

    elif platform == "tidal":
        m = re.search(r'([0-9a-f-]{36})', url, re.IGNORECASE)
        if not m:
            raise HTTPException(status_code=400, detail="Invalid Tidal playlist URL: no playlist UUID found")
        result = _fetch_tidal_playlist(m.group(1))

        tracks = []
        for track_str in result["tracks"]:
            if " - " in track_str:
                artist, title = track_str.split(" - ", 1)
                tracks.append((artist.strip(), title.strip()))
            else:
                tracks.append(("Unknown", track_str.strip()))

        return tracks, result["playlist_name"]

    elif platform == "listenbrainz":
        # Single LB playlist by UUID  -  regular refresh path
        m = re.search(r'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})', url, re.IGNORECASE)
        if not m:
            raise HTTPException(status_code=400, detail="Invalid ListenBrainz playlist URL: no UUID found")
        return _fetch_listenbrainz_playlist(m.group(1))

    elif platform == "listenbrainz_user":
        # Username URLs are handled at the add-watched level (fan-out to multiple playlists).
        # If we ever reach here at refresh time something has gone wrong.
        raise HTTPException(
            status_code=400,
            detail="ListenBrainz user URLs are only valid when adding a watched playlist. Individual playlist URLs are stored for refresh."
        )

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
            conn.execute(
                """UPDATE watched_playlists
                   SET refresh_state = 'idle',
                       refresh_stage = 'done',
                       refresh_error = NULL,
                       refresh_import_id = ?,
                       refresh_completed_at = datetime('now')
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
            tracks, _ = fetch_playlist_tracks(playlist["url"], playlist["platform"])

            # Build a set of hashes for what the upstream playlist currently contains
            current_hashes = {hash_track(artist, title) for artist, title in tracks}

            # Load existing track state (including job status and removal flag)
            set_refresh_stage("diffing")
            track_rows = conn.execute(
                """SELECT wpt.track_hash, wpt.downloaded_at, wpt.job_id, wpt.removed_at,
                          wpt.artist, wpt.title, j.status as job_status,
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

            for artist, title in tracks:
                track_hash = hash_track(artist, title)
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
            for artist, title, track_hash in new_tracks:
                conn.execute("""
                    INSERT INTO watched_playlist_tracks
                    (playlist_id, track_hash, artist, title)
                    VALUES (?, ?, ?, ?)
                """, (playlist_id, track_hash, artist, title))

            tracks_to_import = [(artist, title) for artist, title, _ in new_tracks + missing_tracks]
            use_playlists_dir = bool(playlist.get("use_playlists_dir", False))
            import_id = None
            if tracks_to_import:
                set_refresh_stage("queueing")
                import_id = start_bulk_import_for_tracks(
                    tracks_to_import,
                    bool(playlist["convert_to_flac"]),
                    watch_playlist_id=playlist_id,
                    use_playlists_dir=use_playlists_dir,
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
            conn.execute(
                "UPDATE watched_playlists SET last_checked = datetime('now') WHERE id = ?",
                (playlist_id,)
            )
            conn.commit()
            finish_refresh_error(err_msg)
            return {
                "playlist_id": playlist_id,
                "name": playlist["name"],
                "error": e.detail,
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


def watched_playlist_scheduler():
    """Background thread that periodically checks watched playlists"""
    global _scheduler_running
    _scheduler_running = True

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

    if _scheduler_running:
        return

    spawn_daemon_thread(watched_playlist_scheduler)
