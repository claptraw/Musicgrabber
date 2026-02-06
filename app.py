#!/usr/bin/env python3
"""
Music Grabber - A self-hosted music acquisition service
Searches YouTube, downloads best quality audio with optional conversion to FLAC, drops into Navidrome library
"""

import json
import os
import re
import sqlite3
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
import httpx

from constants import (
    VERSION, MUSIC_DIR, SINGLES_DIR, DB_PATH, COOKIES_FILE,
    TIMEOUT_YTDLP_INFO,
    TIMEOUT_YTDLP_PREVIEW,
    TIMEOUT_SLSKD_SEARCH,
    WATCHED_PLAYLIST_CHECK_HOURS,
)
from db import db_conn, init_db, start_stale_job_monitor, cleanup_stale_jobs
from settings import (
    get_setting, get_setting_bool, set_setting,
    SETTINGS_SCHEMA, SENSITIVE_SETTINGS, _get_typed_setting, _is_env_override,
)
from models import (
    SearchRequest, DownloadRequest, SpotifyPlaylistRequest,
    AsyncBulkImportRequest, WatchedPlaylistRequest, WatchedPlaylistUpdate,
    SettingsUpdate, SearchResult,
    TestSlskdRequest, TestNavidromeRequest, TestJellyfinRequest, TestYouTubeCookiesRequest,
)
from middleware import AuthMiddleware
from youtube import (
    _has_valid_cookie_entries, _cookie_lines_for_domain_check, _sync_cookies_file,
    _ytdlp_base_args, _is_ytdlp_403, search_youtube, parse_duration,
)
from slskd import slskd_enabled, search_slskd
from downloads import (
    process_download, process_playlist_download, process_slskd_download,
)
from bulk_import import clean_bulk_import_line, start_bulk_import_for_tracks, process_bulk_import_worker
from watched_playlists import (
    detect_playlist_platform, fetch_playlist_tracks, refresh_watched_playlist,
    start_scheduler, _fetch_spotify_playlist_embed,
)
from utils import hash_track, is_valid_youtube_id, spawn_daemon_thread, subsonic_auth_params

# =============================================================================
# Application Setup
# =============================================================================

app = FastAPI(title="Music Grabber", version=VERSION)
app.mount("/static", StaticFiles(directory="static"), name="static")

# Ensure directories exist
SINGLES_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# Initialise database and start background monitors
init_db()
start_stale_job_monitor()

# Sync cookies file from settings at startup
_sync_cookies_file()

# Register middleware
app.add_middleware(AuthMiddleware)


# =============================================================================
# Basic Routes
# =============================================================================

@app.get("/", response_class=HTMLResponse)
def root():
    """Serve the main UI"""
    return FileResponse("static/index.html")

def _is_volume_mounted() -> bool:
    """Check if MUSIC_DIR appears to be a mounted volume.

    Compares device IDs - if /music is on a different device than /,
    it's likely a mounted volume. This helps detect misconfigured setups
    where users forgot to mount their music directory.
    """
    try:
        root_stat = os.stat("/")
        music_stat = os.stat(MUSIC_DIR)
        # Different device ID means it's a mount point
        return root_stat.st_dev != music_stat.st_dev
    except OSError:
        # Can't stat, assume it's fine
        return True


@app.get("/api/config")
def get_config():
    """Expose server configuration and version for the UI"""
    api_key = get_setting("api_key", "")
    return {
        "version": VERSION,
        "default_convert_to_flac": get_setting_bool("default_convert_to_flac", True),
        "auth_required": bool(api_key),
        "volume_mounted": _is_volume_mounted()
    }


# =============================================================================
# Settings API
# =============================================================================

@app.get("/api/settings")
def get_settings():
    """Get all settings. Sensitive values are masked unless empty."""
    settings = {}
    env_overrides = []

    for key, schema in SETTINGS_SCHEMA.items():
        value = _get_typed_setting(key)
        is_sensitive = schema.get("sensitive", False)

        # Track which settings are locked by env vars
        if _is_env_override(key):
            env_overrides.append(key)

        # Mask sensitive values (show that something is set, but not what)
        if is_sensitive and value:
            settings[key] = "••••••••"
        else:
            settings[key] = value

    return {
        "settings": settings,
        "env_overrides": env_overrides,  # Frontend can disable these fields
        "sensitive_fields": list(SENSITIVE_SETTINGS)
    }


@app.put("/api/settings")
def update_settings(updates: SettingsUpdate):
    """Update settings. Only non-None values are updated. Returns updated settings."""
    updated_keys = []

    for key, value in updates.model_dump(exclude_none=True).items():
        if key not in SETTINGS_SCHEMA:
            continue

        # Don't allow updating settings that are locked by env vars
        if _is_env_override(key):
            continue

        # Convert booleans to string for storage
        if isinstance(value, bool):
            value = "true" if value else "false"
        else:
            value = str(value)

        # Validate cookie format before saving
        if key == "youtube_cookies" and value.strip() and not _has_valid_cookie_entries(value):
            raise HTTPException(
                status_code=400,
                detail="Invalid cookies format. Paste Netscape-format cookies.txt content."
            )

        set_setting(key, value)
        updated_keys.append(key)

    # Sync cookies file if YouTube cookies were updated
    if "youtube_cookies" in updated_keys:
        _sync_cookies_file()

    return {
        "updated": updated_keys,
        "settings": get_settings()["settings"]
    }


# =============================================================================
# Settings Test Endpoints
# =============================================================================

@app.post("/api/settings/test/slskd")
def test_slskd_connection(request: TestSlskdRequest = None):
    """Test connection to slskd server. Uses form values if provided, otherwise saved settings."""
    url = (request.url if request and request.url else None) or _get_typed_setting("slskd_url")
    user = (request.username if request and request.username else None) or _get_typed_setting("slskd_user")
    password = (request.password if request and request.password else None) or _get_typed_setting("slskd_pass")

    if not url:
        return {"success": False, "message": "slskd URL not configured"}

    try:
        with httpx.Client(timeout=10) as client:
            auth_response = client.post(
                f"{url.rstrip('/')}/api/v0/session",
                json={"username": user, "password": password}
            )
            if auth_response.status_code == 200:
                return {"success": True, "message": "Connected to slskd successfully"}
            else:
                return {"success": False, "message": f"Authentication failed: {auth_response.status_code}"}
    except httpx.TimeoutException:
        return {"success": False, "message": "Connection timed out"}
    except Exception as e:
        return {"success": False, "message": f"Connection failed: {str(e)}"}


@app.post("/api/settings/test/navidrome")
def test_navidrome_connection(request: TestNavidromeRequest = None):
    """Test connection to Navidrome server. Uses form values if provided, otherwise saved settings."""
    url = (request.url if request and request.url else None) or _get_typed_setting("navidrome_url")
    user = (request.username if request and request.username else None) or _get_typed_setting("navidrome_user")
    password = (request.password if request and request.password else None) or _get_typed_setting("navidrome_pass")

    if not url:
        return {"success": False, "message": "Navidrome URL not configured"}

    try:
        params = subsonic_auth_params(user, password)

        with httpx.Client(timeout=10) as client:
            response = client.get(
                f"{url.rstrip('/')}/rest/ping",
                params=params
            )
            if response.status_code == 200:
                data = response.json()
                if data.get("subsonic-response", {}).get("status") == "ok":
                    return {"success": True, "message": "Connected to Navidrome successfully"}
                else:
                    return {"success": False, "message": "Authentication failed"}
            else:
                return {"success": False, "message": f"Connection failed: {response.status_code}"}
    except httpx.TimeoutException:
        return {"success": False, "message": "Connection timed out"}
    except Exception as e:
        return {"success": False, "message": f"Connection failed: {str(e)}"}


@app.post("/api/settings/test/jellyfin")
def test_jellyfin_connection(request: TestJellyfinRequest = None):
    """Test connection to Jellyfin server. Uses form values if provided, otherwise saved settings."""
    url = (request.url if request and request.url else None) or _get_typed_setting("jellyfin_url")
    api_key = (request.api_key if request and request.api_key else None) or _get_typed_setting("jellyfin_api_key")

    if not url:
        return {"success": False, "message": "Jellyfin URL not configured"}
    if not api_key:
        return {"success": False, "message": "Jellyfin API key not configured"}

    try:
        with httpx.Client(timeout=10) as client:
            response = client.get(
                f"{url.rstrip('/')}/System/Info",
                headers={"X-Emby-Token": api_key}
            )
            if response.status_code == 200:
                data = response.json()
                server_name = data.get("ServerName", "Jellyfin")
                return {"success": True, "message": f"Connected to {server_name} successfully"}
            elif response.status_code == 401:
                return {"success": False, "message": "Invalid API key"}
            else:
                return {"success": False, "message": f"Connection failed: {response.status_code}"}
    except httpx.TimeoutException:
        return {"success": False, "message": "Connection timed out"}
    except Exception as e:
        return {"success": False, "message": f"Connection failed: {str(e)}"}


@app.post("/api/settings/test/youtube-cookies")
def test_youtube_cookies(request: TestYouTubeCookiesRequest = None):
    """Test YouTube cookies by fetching info for a known public video.
    Uses form value if provided, otherwise the saved cookies."""
    cookies_text = (request.cookies if request and request.cookies else None)
    if cookies_text is None:
        cookies_text = get_setting("youtube_cookies", "")

    if not cookies_text.strip():
        return {"success": False, "message": "No cookies provided"}

    # Basic format validation
    if not _has_valid_cookie_entries(cookies_text):
        return {"success": False, "message": "No cookie entries found (only comments or blank lines)"}

    lines = _cookie_lines_for_domain_check(cookies_text)
    has_youtube_cookie = any(".youtube.com" in l or ".google.com" in l for l in lines)
    if not has_youtube_cookie:
        return {"success": False, "message": "No YouTube or Google cookies found. Export cookies from youtube.com."}

    # Write to a temp file and test with yt-dlp
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write(cookies_text)
            tmp_path = f.name

        # Use a short, well-known public video (Rick Astley - official)
        test_cmd = [
            "yt-dlp",
            "--cookies", tmp_path,
            "--dump-json",
            "--no-warnings",
            "--no-download",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        ]

        result = subprocess.run(test_cmd, capture_output=True, text=True, timeout=TIMEOUT_YTDLP_INFO)

        if result.returncode == 0:
            try:
                info = json.loads(result.stdout)
                title = info.get("title", "Unknown")
                return {"success": True, "message": f"Cookies valid — fetched: {title}"}
            except json.JSONDecodeError:
                return {"success": True, "message": "Cookies appear valid (got a response)"}
        else:
            stderr = result.stderr
            if _is_ytdlp_403(stderr):
                return {"success": False, "message": "Cookies rejected by YouTube (403). They may be expired — try re-exporting."}
            return {"success": False, "message": f"yt-dlp failed: {stderr[:200]}"}

    except subprocess.TimeoutExpired:
        return {"success": False, "message": "Test timed out"}
    except Exception as e:
        return {"success": False, "message": f"Test failed: {str(e)}"}
    finally:
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)


@app.get("/api/settings/youtube-cookies/status")
def youtube_cookies_status():
    """Return non-sensitive status for the cookies file."""
    cookies_text = get_setting("youtube_cookies", "")
    has_setting = bool(cookies_text.strip())
    file_exists = COOKIES_FILE.exists()
    file_size = COOKIES_FILE.stat().st_size if file_exists else 0
    file_mtime = COOKIES_FILE.stat().st_mtime if file_exists else None
    return {
        "has_setting": has_setting,
        "file_exists": file_exists,
        "file_size": file_size,
        "file_mtime": file_mtime,
        "file_has_valid_entries": _has_valid_cookie_entries(cookies_text) if has_setting else False
    }


# =============================================================================
# Search API
# =============================================================================

@app.get("/api/preview/{video_id}")
def get_preview_url(video_id: str):
    """Get a streamable audio URL for preview playback"""
    try:
        if not is_valid_youtube_id(video_id):
            raise HTTPException(status_code=400, detail="Invalid YouTube video ID")
        cmd = [
            "yt-dlp",
            *_ytdlp_base_args(),
            "-f", "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best",
            "-g",  # Get URL only, don't download
            "--no-warnings",
            f"https://www.youtube.com/watch?v={video_id}"
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_YTDLP_PREVIEW)

        if result.returncode != 0:
            raise HTTPException(status_code=500, detail="Failed to get preview URL")

        audio_url = result.stdout.strip()

        if not audio_url:
            raise HTTPException(status_code=404, detail="No audio stream found")

        return {"url": audio_url, "video_id": video_id}

    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Preview request timed out")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/search")
def search(request: SearchRequest):
    """Search YouTube for music (fast, no slskd delay)"""
    try:
        yt_results = search_youtube(request.query, request.limit)

        final_results = []
        for item in yt_results[:request.limit]:
            final_results.append(SearchResult(
                video_id=item["video_id"],
                title=item["title"],
                artist=None,
                channel=item["channel"],
                duration=item["duration"],
                thumbnail=item["thumbnail"],
                is_playlist=item["is_playlist"],
                video_count=item["video_count"],
                source=item["source"],
                quality=item["quality"],
                quality_score=item["quality_score"],
                slskd_username=item["slskd_username"],
                slskd_filename=item["slskd_filename"],
            ))

        return {"results": final_results, "slskd_enabled": slskd_enabled()}

    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Search timed out")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/search/slskd")
def search_slskd_endpoint(request: SearchRequest):
    """Search Soulseek via slskd (slower, called separately)"""
    if not slskd_enabled():
        return {"results": [], "slskd_enabled": False}

    try:
        print(f"Searching slskd for: {request.query}")
        slskd_results = search_slskd(request.query, timeout_secs=TIMEOUT_SLSKD_SEARCH)
        print(f"slskd returned {len(slskd_results)} results")

        final_results = []
        for r in slskd_results[:request.limit]:
            final_results.append(SearchResult(
                video_id=r["id"],
                title=r["title"],
                artist=r["artist"],
                channel=r["channel"],
                duration=parse_duration(int(r["duration"])) if r["duration"].isdigit() else r["duration"],
                thumbnail="",
                is_playlist=False,
                video_count=None,
                source="soulseek",
                quality=r["quality"],
                quality_score=r["quality_score"],
                slskd_username=r["slskd_username"],
                slskd_filename=r["slskd_filename"],
            ))

        return {"results": final_results, "slskd_enabled": True}

    except Exception as e:
        print(f"slskd search error: {e}")
        return {"results": [], "slskd_enabled": True, "error": str(e)}


# =============================================================================
# Download API
# =============================================================================

@app.post("/api/download")
def download(request: DownloadRequest):
    """Queue a download job"""
    job_id = str(uuid.uuid4())[:8]

    # Extract artist/title if not provided
    artist = request.artist
    title = request.title

    # Create job record
    with db_conn() as conn:
        # Determine source type
        source = "youtube"
        if request.source == "soulseek" and request.slskd_username and request.slskd_filename:
            source = "soulseek"

        if source == "youtube":
            if not request.video_id or not is_valid_youtube_id(request.video_id):
                raise HTTPException(status_code=400, detail="Invalid YouTube video ID")

        # Build source URL for tracking
        if source == "soulseek":
            source_url = f"soulseek://{request.slskd_username}/{request.slskd_filename}" if request.slskd_username else None
        elif request.download_type == "playlist":
            source_url = f"https://www.youtube.com/playlist?list={request.video_id}" if request.video_id else None
        else:
            source_url = f"https://www.youtube.com/watch?v={request.video_id}" if request.video_id else None

        if request.download_type == "playlist":
            conn.execute(
                """INSERT INTO jobs (id, video_id, title, status, download_type, playlist_name, source, convert_to_flac, source_url)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (job_id, request.video_id, title, "queued", "playlist", title, "youtube", int(request.convert_to_flac), source_url)
            )
        else:
            conn.execute(
                """INSERT INTO jobs (id, video_id, title, artist, status, download_type, source, slskd_username, slskd_filename, convert_to_flac, source_url)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (job_id, request.video_id, title, artist or "", "queued", "single", source,
                 request.slskd_username, request.slskd_filename, int(request.convert_to_flac), source_url)
            )
        conn.commit()

    # Queue the download based on source
    if request.download_type == "playlist":
        spawn_daemon_thread(process_playlist_download, job_id, request.video_id, title, request.convert_to_flac)
    elif source == "soulseek":
        spawn_daemon_thread(
            process_slskd_download,
            job_id,
            request.slskd_username,
            request.slskd_filename,
            artist or "",
            title,
            request.convert_to_flac
        )
    else:
        spawn_daemon_thread(process_download, job_id, request.video_id, request.convert_to_flac)

    return {"job_id": job_id, "status": "queued"}


# =============================================================================
# Job Management API
# =============================================================================

def _ensure_utc_suffix(timestamp: str | None) -> str | None:
    """Ensure timestamp has UTC indicator for proper JS parsing.

    SQLite's CURRENT_TIMESTAMP and datetime('now') return UTC but without
    timezone suffix. JavaScript's Date() treats such strings as local time.
    Appending 'Z' tells JS to interpret as UTC.
    """
    if not timestamp:
        return timestamp
    # Already has timezone info
    if timestamp.endswith('Z') or '+' in timestamp[-6:]:
        return timestamp
    # SQLite format uses space, ISO uses T
    return timestamp.replace(' ', 'T') + 'Z'


@app.get("/api/jobs")
def get_jobs(limit: int = 20):
    """Get recent jobs"""
    with db_conn() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?",
            (limit,)
        )
        jobs = []
        for row in cursor.fetchall():
            job = dict(row)
            job['created_at'] = _ensure_utc_suffix(job.get('created_at'))
            job['completed_at'] = _ensure_utc_suffix(job.get('completed_at'))
            jobs.append(job)
    return {"jobs": jobs}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    """Get a specific job"""
    with db_conn() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        row = cursor.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Job not found")

    job = dict(row)
    job['created_at'] = _ensure_utc_suffix(job.get('created_at'))
    job['completed_at'] = _ensure_utc_suffix(job.get('completed_at'))
    return job


@app.post("/api/jobs/{job_id}/retry")
def retry_job(job_id: str):
    """Retry a failed job"""
    with db_conn() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        row = cursor.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Job not found")

        job = dict(row)

        # Only allow retrying failed jobs
        if job["status"] != "failed":
            raise HTTPException(status_code=400, detail="Only failed jobs can be retried")

        # Reset job status
        conn.execute(
            "UPDATE jobs SET status = ?, error = NULL, completed_at = NULL WHERE id = ?",
            ("queued", job_id)
        )
        conn.commit()

    # Re-queue the job based on source type
    convert_to_flac = bool(job.get("convert_to_flac", 1))

    if job["download_type"] == "playlist":
        spawn_daemon_thread(process_playlist_download, job_id, job["video_id"], job["playlist_name"], convert_to_flac)
    elif job.get("source") == "soulseek" and job.get("slskd_username") and job.get("slskd_filename"):
        spawn_daemon_thread(
            process_slskd_download,
            job_id,
            job["slskd_username"],
            job["slskd_filename"],
            job.get("artist", ""),
            job.get("title", ""),
            convert_to_flac
        )
    else:
        spawn_daemon_thread(process_download, job_id, job["video_id"], convert_to_flac)

    return {"job_id": job_id, "status": "queued"}


@app.delete("/api/jobs/cleanup")
def cleanup_jobs(status: Optional[str] = None):
    """Delete completed, failed, or stale jobs"""
    # First, mark any stale jobs as failed so they get cleaned up
    cleanup_stale_jobs()

    with db_conn() as conn:
        if status == "completed":
            cursor = conn.execute("DELETE FROM jobs WHERE status IN ('completed', 'completed_with_errors')")
        elif status == "failed":
            cursor = conn.execute("DELETE FROM jobs WHERE status = 'failed'")
        elif status == "stale":
            cursor = conn.execute("DELETE FROM jobs WHERE status IN ('downloading', 'queued')")
        else:
            cursor = conn.execute("DELETE FROM jobs WHERE status IN ('completed', 'completed_with_errors', 'failed')")

        deleted_count = cursor.rowcount
        conn.commit()

    return {"deleted": deleted_count}


# =============================================================================
# Bulk Import API
# =============================================================================

@app.post("/api/bulk-import-async")
def bulk_import_async(request: AsyncBulkImportRequest):
    """Start an async bulk import job"""
    lines = request.songs.strip().split('\n')
    import_id = str(uuid.uuid4())[:8]

    # Parse and validate all lines first
    tracks_to_import = []
    for line_num, line in enumerate(lines, 1):
        line = clean_bulk_import_line(line)

        if not line:
            continue

        if len(line) > 200:
            continue

        # Try to parse "Artist - Song" format
        match = re.match(r'^(.+?)\s*[-–—]\s*(.+)$', line)
        if not match:
            continue

        artist, song = match.groups()
        artist = artist.strip()
        song = song.strip()

        if not artist or not song:
            continue

        tracks_to_import.append({
            "line_num": line_num,
            "artist": artist,
            "song": song
        })

    if not tracks_to_import:
        raise HTTPException(status_code=400, detail="No valid tracks found in input")

    # Create bulk import record
    with db_conn() as conn:
        conn.execute(
            """INSERT INTO bulk_imports
               (id, status, total_tracks, create_playlist, playlist_name, convert_to_flac)
               VALUES (?, 'pending', ?, ?, ?, ?)""",
            (import_id, len(tracks_to_import), int(request.create_playlist),
             request.playlist_name, int(request.convert_to_flac))
        )

        # Insert all tracks
        for track in tracks_to_import:
            conn.execute(
                "INSERT INTO bulk_import_tracks (import_id, line_num, artist, song, status) VALUES (?, ?, ?, ?, 'pending')",
                (import_id, track["line_num"], track["artist"], track["song"])
            )

        conn.commit()

    # Start background worker for this import
    spawn_daemon_thread(process_bulk_import_worker, import_id)

    return {
        "import_id": import_id,
        "total_tracks": len(tracks_to_import),
        "status": "pending"
    }


@app.get("/api/bulk-import/{import_id}/status")
def get_bulk_import_status(import_id: str):
    """Get status of a bulk import job"""
    with db_conn() as conn:
        conn.row_factory = sqlite3.Row

        cursor = conn.execute("SELECT * FROM bulk_imports WHERE id = ?", (import_id,))
        import_row = cursor.fetchone()

        if not import_row:
            raise HTTPException(status_code=404, detail="Import not found")

        import_row = dict(import_row)

        # Get recent track statuses for display
        cursor = conn.execute(
            """SELECT artist, song, status, error FROM bulk_import_tracks
               WHERE import_id = ?
               ORDER BY
                   CASE status
                       WHEN 'queued' THEN 0
                       WHEN 'failed' THEN 0
                       WHEN 'searching' THEN 1
                       ELSE 2
                   END,
                   line_num DESC
               LIMIT 10""",
            (import_id,)
        )
        recent_tracks = [dict(row) for row in cursor.fetchall()]

        # Count download statuses by joining bulk_import_tracks with jobs
        cursor = conn.execute(
            """SELECT
                   SUM(CASE WHEN j.status = 'completed' THEN 1 ELSE 0 END) as completed,
                   SUM(CASE WHEN j.status = 'failed' THEN 1 ELSE 0 END) as download_failed,
                   SUM(CASE WHEN j.status IN ('queued', 'downloading') THEN 1 ELSE 0 END) as still_queued
               FROM bulk_import_tracks t
               JOIN jobs j ON t.job_id = j.id
               WHERE t.import_id = ?""",
            (import_id,)
        )
        row = cursor.fetchone()
        completed_count = row[0] or 0
        download_failed_count = row[1] or 0
        still_queued_count = row[2] or 0

    total_failed = import_row["failed"] + download_failed_count
    search_done = import_row["status"] in ("completed", "error")
    all_done = search_done and still_queued_count == 0

    return {
        "import_id": import_id,
        "status": import_row["status"],
        "total_tracks": import_row["total_tracks"],
        "searched": import_row["searched"],
        "queued": still_queued_count,
        "completed": completed_count,
        "failed": total_failed,
        "skipped": import_row["skipped"],
        "rate_limited": import_row["rate_limited_until"] is not None,
        "error": import_row["error"],
        "recent_tracks": recent_tracks,
        "complete": all_done
    }


@app.get("/api/bulk-imports")
def list_bulk_imports(limit: int = 10):
    """List recent bulk imports"""
    with db_conn() as conn:
        conn.row_factory = sqlite3.Row

        cursor = conn.execute(
            "SELECT * FROM bulk_imports ORDER BY created_at DESC LIMIT ?",
            (limit,)
        )
        imports = [dict(row) for row in cursor.fetchall()]

    return {"imports": imports}


# =============================================================================
# Spotify Playlist API
# =============================================================================

@app.post("/api/spotify-playlist")
def fetch_spotify_playlist(request: SpotifyPlaylistRequest):
    """Fetch track list from a public Spotify playlist or album URL"""
    return _fetch_spotify_playlist_embed(request.url)


# =============================================================================
# Watched Playlists API
# =============================================================================

@app.post("/api/watched-playlists")
def add_watched_playlist(request: WatchedPlaylistRequest):
    """Add a new playlist to watch for new tracks"""
    platform, playlist_ext_id = detect_playlist_platform(request.url)

    with db_conn() as conn:
        # Check for duplicate
        conn.row_factory = sqlite3.Row

        existing = conn.execute(
            "SELECT id FROM watched_playlists WHERE url = ?", (request.url,)
        ).fetchone()

        if existing:
            raise HTTPException(status_code=409, detail="This playlist is already being watched")

        # Fetch playlist to get name and initial tracks
        try:
            tracks, playlist_name = fetch_playlist_tracks(request.url, platform)
        except HTTPException:
            raise

        # Create playlist record
        playlist_id = str(uuid.uuid4())[:8]

        conn.execute("""
            INSERT INTO watched_playlists
            (id, url, name, platform, refresh_interval_hours, convert_to_flac, last_track_count)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (playlist_id, request.url, playlist_name, platform,
              request.refresh_interval_hours, int(request.convert_to_flac), len(tracks)))

        # Insert all current tracks as "seen"
        for artist, title in tracks:
            track_hash = hash_track(artist, title)
            conn.execute("""
                INSERT OR IGNORE INTO watched_playlist_tracks
                (playlist_id, track_hash, artist, title)
                VALUES (?, ?, ?, ?)
            """, (playlist_id, track_hash, artist, title))

        conn.commit()

    import_id = None
    if tracks:
        import_id = start_bulk_import_for_tracks(
            tracks,
            request.convert_to_flac,
            watch_playlist_id=playlist_id
        )

    return {
        "id": playlist_id,
        "name": playlist_name,
        "platform": platform,
        "track_count": len(tracks),
        "refresh_interval_hours": request.refresh_interval_hours,
        "import_id": import_id,
        "message": f"Now watching '{playlist_name}' with {len(tracks)} tracks queued for download"
    }


@app.get("/api/watched-playlists")
def list_watched_playlists():
    """List all watched playlists"""
    with db_conn() as conn:
        conn.row_factory = sqlite3.Row

        playlists = conn.execute("""
            SELECT
                wp.*,
                (SELECT COUNT(*) FROM watched_playlist_tracks wpt WHERE wpt.playlist_id = wp.id) as tracked_count,
                (SELECT COUNT(*) FROM watched_playlist_tracks wpt WHERE wpt.playlist_id = wp.id AND wpt.downloaded_at IS NOT NULL) as downloaded_count
            FROM watched_playlists wp
            ORDER BY wp.created_at DESC
        """).fetchall()

    return {
        "playlists": [dict(p) for p in playlists]
    }


@app.get("/api/watched-playlists/{playlist_id}")
def get_watched_playlist(playlist_id: str):
    """Get details of a watched playlist including track history"""
    with db_conn() as conn:
        conn.row_factory = sqlite3.Row

        playlist = conn.execute(
            "SELECT * FROM watched_playlists WHERE id = ?", (playlist_id,)
        ).fetchone()

        if not playlist:
            raise HTTPException(status_code=404, detail="Watched playlist not found")

        tracks = conn.execute("""
            SELECT * FROM watched_playlist_tracks
            WHERE playlist_id = ?
            ORDER BY first_seen DESC
        """, (playlist_id,)).fetchall()

    return {
        "playlist": dict(playlist),
        "tracks": [dict(t) for t in tracks]
    }


@app.put("/api/watched-playlists/{playlist_id}")
def update_watched_playlist(playlist_id: str, request: WatchedPlaylistUpdate):
    """Update watched playlist settings"""
    with db_conn() as conn:
        conn.row_factory = sqlite3.Row

        playlist = conn.execute(
            "SELECT * FROM watched_playlists WHERE id = ?", (playlist_id,)
        ).fetchone()

        if not playlist:
            raise HTTPException(status_code=404, detail="Watched playlist not found")

        # Build update query
        updates = []
        params = []

        if request.refresh_interval_hours is not None:
            updates.append("refresh_interval_hours = ?")
            params.append(request.refresh_interval_hours)

        if request.enabled is not None:
            updates.append("enabled = ?")
            params.append(int(request.enabled))

        if request.convert_to_flac is not None:
            updates.append("convert_to_flac = ?")
            params.append(int(request.convert_to_flac))

        if updates:
            params.append(playlist_id)
            conn.execute(
                f"UPDATE watched_playlists SET {', '.join(updates)} WHERE id = ?",
                params
            )
            conn.commit()

        # Fetch updated record
        updated = conn.execute(
            "SELECT * FROM watched_playlists WHERE id = ?", (playlist_id,)
        ).fetchone()

    return {"playlist": dict(updated)}


@app.delete("/api/watched-playlists/{playlist_id}")
def delete_watched_playlist(playlist_id: str):
    """Remove a watched playlist and its track history"""
    with db_conn() as conn:
        conn.row_factory = sqlite3.Row

        playlist = conn.execute(
            "SELECT * FROM watched_playlists WHERE id = ?", (playlist_id,)
        ).fetchone()

        if not playlist:
            raise HTTPException(status_code=404, detail="Watched playlist not found")

        # Delete tracks first (FK constraint)
        conn.execute("DELETE FROM watched_playlist_tracks WHERE playlist_id = ?", (playlist_id,))
        conn.execute("DELETE FROM watched_playlists WHERE id = ?", (playlist_id,))
        conn.commit()

    return {"message": f"Deleted watched playlist '{playlist['name']}'"}


@app.post("/api/watched-playlists/{playlist_id}/refresh")
def refresh_single_playlist(playlist_id: str):
    """Force an immediate refresh of a specific watched playlist"""
    with db_conn() as conn:
        conn.row_factory = sqlite3.Row

        playlist = conn.execute(
            "SELECT * FROM watched_playlists WHERE id = ?", (playlist_id,)
        ).fetchone()

        if not playlist:
            raise HTTPException(status_code=404, detail="Watched playlist not found")

    result = refresh_watched_playlist(playlist_id)
    return result


@app.post("/api/watched-playlists/check-all")
def check_all_watched_playlists():
    """Check all playlists due for refresh (called by cron)"""
    with db_conn() as conn:
        conn.row_factory = sqlite3.Row

        playlists = conn.execute("""
            SELECT id, name FROM watched_playlists
            WHERE enabled = 1
            AND (last_checked IS NULL
                 OR datetime(last_checked, '+' || refresh_interval_hours || ' hours') < datetime('now'))
        """).fetchall()

    if not playlists:
        return {"checked": 0, "message": "No playlists due for refresh", "results": []}

    results = []
    for playlist in playlists:
        result = refresh_watched_playlist(playlist["id"])
        results.append(result)

    total_new = sum(r.get("new_tracks", 0) for r in results)
    total_queued = sum(r.get("queued", 0) for r in results)

    return {
        "checked": len(results),
        "total_new_tracks": total_new,
        "total_queued": total_queued,
        "results": results
    }


@app.get("/api/watched-playlists/schedule")
def get_watched_schedule():
    """Get the current watched playlist check schedule"""
    return {
        "check_interval_hours": WATCHED_PLAYLIST_CHECK_HOURS,
        "enabled": WATCHED_PLAYLIST_CHECK_HOURS > 0
    }


# =============================================================================
# Start Background Scheduler
# =============================================================================

start_scheduler()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
