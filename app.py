#!/usr/bin/env python3
"""
Music Grabber - A self-hosted music acquisition service
Searches YouTube, downloads best quality audio with optional conversion to FLAC, drops into Navidrome library
"""

import hashlib
import json
import os
import re
import smtplib
import subprocess
import sqlite3
import threading
import time
import random
import uuid
from email.mime.text import MIMEText
from datetime import datetime
from pathlib import Path
from typing import Optional
from collections import defaultdict
from fastapi import FastAPI, BackgroundTasks, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel
from mutagen.flac import FLAC
import httpx

# =============================================================================
# Application Constants
# =============================================================================

VERSION = "1.7.1"

# Timeout values (in seconds)
TIMEOUT_YTDLP_INFO = 30          # Getting video/playlist info
TIMEOUT_YTDLP_SEARCH = 30        # Search queries
TIMEOUT_YTDLP_DOWNLOAD = 300     # Downloading a track (5 minutes)
TIMEOUT_YTDLP_PREVIEW = 15       # Getting preview URL
TIMEOUT_YTDLP_PLAYLIST = 60      # Getting playlist contents
TIMEOUT_FFMPEG_CONVERT = 120     # Converting audio formats
TIMEOUT_HTTP_REQUEST = 10        # MusicBrainz, LRClib, Navidrome API calls
TIMEOUT_HTTP_SPOTIFY = 30        # Spotify embed fetch
TIMEOUT_SLSKD_SEARCH = 12        # Soulseek search polling
TIMEOUT_SLSKD_DOWNLOAD = 600     # Soulseek download (10 minutes)
TIMEOUT_SLSKD_API = 30           # slskd API calls
TIMEOUT_SPOTIFY_BROWSER = 180    # Headless browser for large playlists (3 minutes)
STALE_JOB_TIMEOUT = 900          # Mark downloading/queued jobs as failed after 15 minutes
STALE_JOB_CHECK_INTERVAL = 120   # Check for stale jobs every 2 minutes

# Bulk import settings
BULK_IMPORT_SEARCH_DELAY = 1.0           # Seconds between YouTube searches
BULK_IMPORT_BACKOFF_DELAYS = [30, 60, 120, 300]  # Rate limit backoff sequence
BULK_IMPORT_BACKOFF_RESET_AFTER = 5      # Consecutive successes before reducing backoff

# Playlist creation
PLAYLIST_WAIT_MAX = 3600         # Max seconds to wait for downloads to complete (1 hour)
PLAYLIST_WAIT_INTERVAL = 10      # Seconds between completion checks

# Search and results
YOUTUBE_SEARCH_MULTIPLIER = 3    # Fetch N times more results than requested for scoring
YOUTUBE_SEARCH_MIN_FETCH = 30    # Minimum results to fetch for scoring
SLSKD_MAX_RESULTS = 20           # Max Soulseek results to return
SLSKD_MIN_QUALITY_SCORE = 50     # Minimum quality score to include result

# File handling
MAX_FILENAME_LENGTH = 200        # Maximum characters in sanitised filenames
COOKIES_FILE = Path("/data/cookies.txt")  # yt-dlp cookies file path

# YouTube 403 retry
YTDLP_403_MAX_RETRIES = 2       # Retry attempts on 403/Forbidden errors
YTDLP_403_RETRY_DELAY = 3       # Seconds between retries

# YouTube bot/backoff handling
BOT_BACKOFF_MIN_SECONDS = 5
BOT_BACKOFF_MAX_SECONDS = 20

# Rate limiting
RATE_LIMIT_REQUESTS = 60         # Max requests per IP per window
RATE_LIMIT_WINDOW = 60           # Window size in seconds

app = FastAPI(title="Music Grabber", version=VERSION)
app.mount("/static", StaticFiles(directory="static"), name="static")

# Configuration from environment - these are structural paths that must exist at startup
MUSIC_DIR = Path(os.getenv("MUSIC_DIR", "/music"))
SINGLES_DIR = MUSIC_DIR / "Singles"
DB_PATH = Path(os.getenv("DB_PATH", "/data/music_grabber.db"))

# Other settings that don't change at runtime (not in UI)
SLSKD_REQUIRE_FREE_SLOT = os.getenv("SLSKD_REQUIRE_FREE_SLOT", "true").lower() == "true"
SLSKD_MAX_RETRIES = int(os.getenv("SLSKD_MAX_RETRIES", "5"))
WATCHED_PLAYLIST_CHECK_HOURS = int(os.getenv("WATCHED_PLAYLIST_CHECK_HOURS", "24"))

# Legacy constants - kept for backwards compatibility during migration
# These will be replaced by get_setting() calls throughout the codebase
# TODO: Remove these after full migration
DEFAULT_CONVERT_TO_FLAC = os.getenv("DEFAULT_CONVERT_TO_FLAC", "true").lower() == "true"

# slskd auth token cache
_slskd_token = None
_slskd_token_expires = 0

# YouTube bot/backoff state
_bot_backoff_until = 0.0
_bot_backoff_lock = threading.Lock()

# Ensure directories exist
SINGLES_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# Initialise DB
def get_db() -> sqlite3.Connection:
    """Create a SQLite connection with basic concurrency settings"""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    """Initialise SQLite database for job tracking"""
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            video_id TEXT,
            title TEXT,
            artist TEXT,
            status TEXT DEFAULT 'queued',
            error TEXT,
            download_type TEXT DEFAULT 'single',
            playlist_name TEXT,
            total_tracks INTEGER,
            completed_tracks INTEGER DEFAULT 0,
            failed_tracks INTEGER DEFAULT 0,
            skipped_tracks INTEGER DEFAULT 0,
            m3u_path TEXT,
            source TEXT DEFAULT 'youtube',
            slskd_username TEXT,
            slskd_filename TEXT,
            convert_to_flac INTEGER DEFAULT 1,
            source_url TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP
        )
    """)

    # Add columns if they don't exist (for existing databases)
    try:
        conn.execute("ALTER TABLE jobs ADD COLUMN source TEXT DEFAULT 'youtube'")
    except sqlite3.OperationalError:
        pass  # Column already exists
    try:
        conn.execute("ALTER TABLE jobs ADD COLUMN slskd_username TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE jobs ADD COLUMN slskd_filename TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE jobs ADD COLUMN convert_to_flac INTEGER DEFAULT 1")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE jobs ADD COLUMN source_url TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE jobs ADD COLUMN failed_tracks INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE jobs ADD COLUMN skipped_tracks INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass

    # Bulk imports table - tracks the overall import job
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bulk_imports (
            id TEXT PRIMARY KEY,
            status TEXT DEFAULT 'pending',
            total_tracks INTEGER DEFAULT 0,
            searched INTEGER DEFAULT 0,
            queued INTEGER DEFAULT 0,
            failed INTEGER DEFAULT 0,
            skipped INTEGER DEFAULT 0,
            create_playlist INTEGER DEFAULT 0,
            playlist_name TEXT,
            convert_to_flac INTEGER DEFAULT 1,
            watch_playlist_id TEXT,
            rate_limited_until TIMESTAMP,
            error TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP
        )
    """)

    # Individual tracks within a bulk import
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bulk_import_tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            import_id TEXT NOT NULL,
            line_num INTEGER,
            artist TEXT,
            song TEXT,
            status TEXT DEFAULT 'pending',
            job_id TEXT,
            video_id TEXT,
            error TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (import_id) REFERENCES bulk_imports(id)
        )
    """)

    # Index for faster lookups
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bulk_import_tracks_import_id ON bulk_import_tracks(import_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bulk_import_tracks_status ON bulk_import_tracks(status)")

    try:
        conn.execute("ALTER TABLE bulk_imports ADD COLUMN watch_playlist_id TEXT")
    except sqlite3.OperationalError:
        pass

    # Watched playlists - playlists to monitor for new tracks
    conn.execute("""
        CREATE TABLE IF NOT EXISTS watched_playlists (
            id TEXT PRIMARY KEY,
            url TEXT NOT NULL UNIQUE,
            name TEXT,
            platform TEXT NOT NULL,
            refresh_interval_hours INTEGER DEFAULT 24,
            last_checked TIMESTAMP,
            last_track_count INTEGER DEFAULT 0,
            enabled INTEGER DEFAULT 1,
            convert_to_flac INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Tracks seen in watched playlists (for detecting new additions)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS watched_playlist_tracks (
            playlist_id TEXT NOT NULL,
            track_hash TEXT NOT NULL,
            artist TEXT,
            title TEXT,
            first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            downloaded_at TIMESTAMP,
            job_id TEXT,
            PRIMARY KEY (playlist_id, track_hash),
            FOREIGN KEY (playlist_id) REFERENCES watched_playlists(id) ON DELETE CASCADE
        )
    """)

    conn.execute("CREATE INDEX IF NOT EXISTS idx_watched_tracks_playlist ON watched_playlist_tracks(playlist_id)")

    # Settings table - stores configuration that can be edited via UI
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()

init_db()


def cleanup_stale_jobs():
    """Mark any downloading/queued jobs older than STALE_JOB_TIMEOUT as failed.
    Handles cases where the background task crashed or the container restarted."""
    conn = get_db()
    try:
        cutoff = datetime.now().timestamp() - STALE_JOB_TIMEOUT
        cursor = conn.execute(
            """UPDATE jobs SET status = 'failed', error = 'Timed out (no progress)',
               completed_at = ?
               WHERE status IN ('downloading', 'queued')
               AND created_at < ?""",
            (datetime.now().isoformat(), datetime.fromtimestamp(cutoff).isoformat())
        )
        if cursor.rowcount > 0:
            print(f"Cleaned up {cursor.rowcount} stale job(s)")
        conn.commit()
    finally:
        conn.close()


def _stale_job_monitor():
    """Background thread that periodically checks for stale jobs."""
    while True:
        time.sleep(STALE_JOB_CHECK_INTERVAL)
        try:
            cleanup_stale_jobs()
        except Exception as e:
            print(f"Stale job monitor error: {e}")


# Run once at startup to catch jobs orphaned by a restart
cleanup_stale_jobs()

# Start periodic monitor
_stale_monitor_thread = threading.Thread(target=_stale_job_monitor, daemon=True)
_stale_monitor_thread.start()


# =============================================================================
# Settings Helper - Environment variables override DB values
# =============================================================================

def get_setting(key: str, default: str = "") -> str:
    """Get a setting value. Environment variable takes precedence over DB value."""
    # Check environment variable first (uppercase, with underscores)
    env_key = key.upper().replace(".", "_")
    env_value = os.getenv(env_key)
    if env_value is not None:
        return env_value

    # Fall back to database
    try:
        conn = get_db()
        cursor = conn.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cursor.fetchone()
        conn.close()
        if row and row[0] is not None:
            return row[0]
    except Exception:
        pass

    return default


def get_setting_bool(key: str, default: bool = False) -> bool:
    """Get a boolean setting value."""
    value = get_setting(key, str(default).lower())
    return value.lower() in ("true", "1", "yes", "on")


def get_setting_int(key: str, default: int = 0) -> int:
    """Get an integer setting value."""
    value = get_setting(key, str(default))
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def set_setting(key: str, value: str) -> None:
    """Set a setting value in the database."""
    conn = get_db()
    conn.execute("""
        INSERT INTO settings (key, value, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET value = ?, updated_at = CURRENT_TIMESTAMP
    """, (key, value, value))
    conn.commit()
    conn.close()


def get_all_settings() -> dict:
    """Get all settings from the database."""
    conn = get_db()
    cursor = conn.execute("SELECT key, value FROM settings")
    settings = {row[0]: row[1] for row in cursor.fetchall()}
    conn.close()
    return settings


# =============================================================================
# YouTube / yt-dlp Helpers
# =============================================================================

def _has_valid_cookie_entries(cookies_text: str) -> bool:
    """Check for at least one Netscape-format cookie entry (tabs-separated)."""
    for raw_line in cookies_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # Netscape format can prefix HttpOnly entries with "#HttpOnly_"
        if line.startswith("#HttpOnly_"):
            if line.count("\t") >= 6:
                return True
            continue
        # Skip comments
        if line.startswith("#"):
            continue
        if line.count("\t") >= 6:
            return True
    return False


def _cookie_lines_for_domain_check(cookies_text: str) -> list[str]:
    """Return cookie lines (including HttpOnly-prefixed entries) for domain checks."""
    lines = []
    for raw_line in cookies_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#HttpOnly_"):
            lines.append(line)
            continue
        if line.startswith("#"):
            continue
        lines.append(line)
    return lines


def _sync_cookies_file():
    """Write YouTube cookies from settings to the cookies file on disk.
    Called when settings are saved and at startup."""
    cookies = get_setting("youtube_cookies", "")
    if cookies.strip():
        if not _has_valid_cookie_entries(cookies):
            # Avoid writing invalid cookie data that can break yt-dlp
            if COOKIES_FILE.exists():
                COOKIES_FILE.unlink()
            return
        COOKIES_FILE.parent.mkdir(parents=True, exist_ok=True)
        COOKIES_FILE.write_text(cookies)
    elif COOKIES_FILE.exists():
        COOKIES_FILE.unlink()


def _android_client_args() -> list[str]:
    """Return yt-dlp args to force the Android player client."""
    return ["--extractor-args", "youtube:player_client=android"]


def _ytdlp_base_args():
    """Return common yt-dlp arguments (cookies, extractor args).
    These should be prepended after 'yt-dlp' in every command."""
    args = []
    if COOKIES_FILE.exists() and COOKIES_FILE.stat().st_size > 0:
        args.extend(["--cookies", str(COOKIES_FILE)])
    args.extend(_android_client_args())
    return args


def _is_ytdlp_403(stderr: str) -> bool:
    """Check if yt-dlp stderr indicates a YouTube 403/bot-block error."""
    lower = stderr.lower()
    return "403" in lower or "forbidden" in lower or "sign in to confirm" in lower


def _strip_cookies_args(cmd: list[str]) -> list[str]:
    """Return a command list with any --cookies args removed."""
    cleaned = []
    skip_next = False
    for arg in cmd:
        if skip_next:
            skip_next = False
            continue
        if arg == "--cookies":
            skip_next = True
            continue
        cleaned.append(arg)
    return cleaned


def _should_retry_without_cookies(stderr: str) -> bool:
    """Decide if a download failure likely stems from cookie/auth issues."""
    lower = stderr.lower()
    return _is_ytdlp_403(stderr) or "downloaded file is empty" in lower or "http error 403" in lower


def _get_bot_backoff_window() -> tuple[float, float]:
    """Return (min,max) seconds for bot backoff, enforcing sane bounds."""
    min_seconds = float(get_setting_int("youtube_bot_backoff_min", BOT_BACKOFF_MIN_SECONDS))
    max_seconds = float(get_setting_int("youtube_bot_backoff_max", BOT_BACKOFF_MAX_SECONDS))
    if min_seconds < 0:
        min_seconds = 0.0
    if max_seconds < 0:
        max_seconds = 0.0
    if max_seconds < min_seconds:
        min_seconds, max_seconds = max_seconds, min_seconds
    return min_seconds, max_seconds


def _note_bot_block() -> None:
    """Record bot-block and extend the global backoff window."""
    now = time.time()
    min_seconds, max_seconds = _get_bot_backoff_window()
    sleep_for = random.uniform(min_seconds, max_seconds) if max_seconds > 0 else 0
    with _bot_backoff_lock:
        global _bot_backoff_until
        _bot_backoff_until = max(_bot_backoff_until, now + sleep_for)


def _sleep_if_botted() -> None:
    """Sleep if a recent bot-block was detected to reduce request pressure."""
    with _bot_backoff_lock:
        wait_for = _bot_backoff_until - time.time()
    if wait_for > 0:
        time.sleep(wait_for)


# Sync cookies file from settings at startup
_sync_cookies_file()


# =============================================================================
# API Authentication & Rate Limiting Middleware
# =============================================================================

# In-memory rate limiting store: {ip: [(timestamp, count), ...]}
_rate_limit_store: dict[str, list[float]] = defaultdict(list)
_rate_limit_lock = threading.Lock()

# Paths that don't require authentication (static files, health checks)
AUTH_EXEMPT_PATHS = {"/", "/static", "/api/config"}


def _get_client_ip(request: Request) -> str:
    """Get client IP, respecting X-Forwarded-For for reverse proxies."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _check_rate_limit(ip: str) -> tuple[bool, int]:
    """Check if IP is within rate limit. Returns (allowed, remaining)."""
    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW

    with _rate_limit_lock:
        # Clean old entries
        _rate_limit_store[ip] = [t for t in _rate_limit_store[ip] if t > window_start]

        current_count = len(_rate_limit_store[ip])
        if current_count >= RATE_LIMIT_REQUESTS:
            return False, 0

        _rate_limit_store[ip].append(now)
        return True, RATE_LIMIT_REQUESTS - current_count - 1


class AuthMiddleware(BaseHTTPMiddleware):
    """Middleware for API key authentication and rate limiting."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Skip auth for exempt paths
        if path == "/" or path.startswith("/static"):
            return await call_next(request)

        # Get configured API key
        api_key = get_setting("api_key", "")

        # If API key is configured, enforce authentication
        if api_key:
            # Config endpoint is always accessible (needed for frontend to know auth is required)
            if path != "/api/config":
                request_key = request.headers.get("x-api-key", "")
                if request_key != api_key:
                    return JSONResponse(
                        status_code=401,
                        content={"detail": "Invalid or missing API key"},
                        headers={"WWW-Authenticate": "API-Key"}
                    )

        # Rate limiting (applied to all API requests)
        if path.startswith("/api"):
            client_ip = _get_client_ip(request)
            allowed, remaining = _check_rate_limit(client_ip)

            if not allowed:
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Rate limit exceeded. Try again later."},
                    headers={
                        "Retry-After": str(RATE_LIMIT_WINDOW),
                        "X-RateLimit-Limit": str(RATE_LIMIT_REQUESTS),
                        "X-RateLimit-Remaining": "0",
                        "X-RateLimit-Reset": str(int(time.time() + RATE_LIMIT_WINDOW))
                    }
                )

            response = await call_next(request)
            response.headers["X-RateLimit-Limit"] = str(RATE_LIMIT_REQUESTS)
            response.headers["X-RateLimit-Remaining"] = str(remaining)
            return response

        return await call_next(request)


# Register the middleware
app.add_middleware(AuthMiddleware)


# Classes
class SearchRequest(BaseModel):
    query: str
    limit: int = 15

class DownloadRequest(BaseModel):
    video_id: str
    title: str
    artist: Optional[str] = None
    download_type: str = "single"  # "single" or "playlist"
    convert_to_flac: bool = DEFAULT_CONVERT_TO_FLAC  # Whether to convert to FLAC or keep original format
    # Soulseek-specific fields
    source: str = "youtube"  # "youtube" or "soulseek"
    slskd_username: Optional[str] = None
    slskd_filename: Optional[str] = None

class BulkImportRequest(BaseModel):
    songs: str  # Multi-line text with "Artist - Song" format
    create_playlist: bool = False
    playlist_name: Optional[str] = None
    convert_to_flac: bool = DEFAULT_CONVERT_TO_FLAC  # Whether to convert to FLAC or keep original format

class SpotifyPlaylistRequest(BaseModel):
    url: str  # Spotify playlist URL

class AsyncBulkImportRequest(BaseModel):
    songs: str  # Multi-line text with "Artist - Song" format
    create_playlist: bool = False
    playlist_name: Optional[str] = None
    convert_to_flac: bool = DEFAULT_CONVERT_TO_FLAC

class WatchedPlaylistRequest(BaseModel):
    url: str  # Spotify or YouTube playlist URL
    refresh_interval_hours: int = 24
    convert_to_flac: bool = DEFAULT_CONVERT_TO_FLAC

class WatchedPlaylistUpdate(BaseModel):
    refresh_interval_hours: Optional[int] = None
    enabled: Optional[bool] = None
    convert_to_flac: Optional[bool] = None

class SettingsUpdate(BaseModel):
    """Settings that can be updated via the UI"""
    # General
    music_dir: Optional[str] = None
    enable_musicbrainz: Optional[bool] = None
    enable_lyrics: Optional[bool] = None
    default_convert_to_flac: Optional[bool] = None
    # Soulseek/slskd
    slskd_url: Optional[str] = None
    slskd_user: Optional[str] = None
    slskd_pass: Optional[str] = None
    slskd_downloads_path: Optional[str] = None
    # Navidrome
    navidrome_url: Optional[str] = None
    navidrome_user: Optional[str] = None
    navidrome_pass: Optional[str] = None
    # Jellyfin
    jellyfin_url: Optional[str] = None
    jellyfin_api_key: Optional[str] = None
    # Notifications
    notify_on: Optional[str] = None
    telegram_webhook_url: Optional[str] = None
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    smtp_user: Optional[str] = None
    smtp_pass: Optional[str] = None
    smtp_from: Optional[str] = None
    smtp_to: Optional[str] = None
    smtp_tls: Optional[bool] = None
    # YouTube
    youtube_cookies: Optional[str] = None
    # Security
    api_key: Optional[str] = None

class SearchResult(BaseModel):
    video_id: str
    title: str
    artist: Optional[str] = None
    channel: str
    duration: str
    thumbnail: str
    is_playlist: bool = False
    video_count: Optional[int] = None
    # New fields for multi-source support
    source: str = "youtube"  # "youtube" or "soulseek"
    quality: Optional[str] = None  # e.g., "FLAC", "MP3 320", None for YouTube
    quality_score: int = 40  # For sorting (higher = better)
    slskd_username: Optional[str] = None
    slskd_filename: Optional[str] = None

def parse_duration(seconds: float) -> str:
    """Convert seconds to MM:SS or HH:MM:SS format"""
    seconds = int(seconds)
    if seconds < 3600:
        return f"{seconds // 60}:{seconds % 60:02d}"
    return f"{seconds // 3600}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"

def score_search_result(title: str, channel: str) -> int:
    """Score a search result to prioritise official content over live versions

    Higher score = better match
    Lower score = worse match (live, cover, remix, etc.)
    """
    title_lower = title.lower()
    channel_lower = channel.lower()
    score = 100  # Start with base score

    # Penalties for live performances
    if re.search(r'\b(live|concert|tour|performance|unplugged)\b', title_lower):
        score -= 50

    # Penalties for covers, remixes, instrumentals
    if re.search(r'\b(cover|remix|instrumental|karaoke|acoustic version)\b', title_lower):
        score -= 40

    # Penalties for lyric videos (usually lower quality)
    if re.search(r'\b(lyric|lyrics)\b', title_lower):
        score -= 20

    # Penalties for fan uploads or unofficial - no cell phone video, thanks
    if re.search(r'\b(fan|unofficial|tribute)\b', title_lower):
        score -= 30

    # Bonuses for official content
    if re.search(r'\b(official|vevo)\b', title_lower):
        score += 30

    if re.search(r'\b(official|vevo)\b', channel_lower):
        score += 40

    # Bonus for "Topic" channels (often official audio)
    if channel_lower.endswith(" - topic"):
        score += 35

    # Bonus for "official music video" or "official video"
    if re.search(r'official\s*(music)?\s*video', title_lower):
        score += 25

    # Bonus for official audio
    if re.search(r'official\s*audio', title_lower):
        score += 20

    # Bonus when channel name appears in title (often "Artist - Title")
    if channel_lower and channel_lower in title_lower:
        score += 10

    # Penalty for reaction videos, compilations
    if re.search(r'\b(reaction|react|compilation|mashup|vs)\b', title_lower):
        score -= 60

    # Penalty for extended versions (often DJ mixes)
    if re.search(r'\b(extended|extended mix|extended version)\b', title_lower):
        score -= 15

    # Penalties for non-song results or modified audio
    if re.search(r'\b(full album|album|mix|playlist|soundtrack)\b', title_lower):
        score -= 40
    if re.search(r'\b(nightcore|sped up|slowed|8d|reverb|bass boosted)\b', title_lower):
        score -= 45
    if re.search(r'\b(cover|remix|instrumental|karaoke|acoustic version|live session)\b', title_lower):
        score -= 20

    return score


def sanitize_filename(name: str) -> str:
    """Remove/replace characters that are problematic in filenames"""
    # Remove or replace problematic characters
    name = re.sub(r'[<>:"/\\|?*]', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name[:MAX_FILENAME_LENGTH]


def clean_title(title: str) -> str:
    """Clean up YouTube title by removing common suffixes and annotations"""
    # Remove common video type annotations
    title = re.sub(r'\s*\(Official.*?\)', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s*\[Official.*?\]', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s*Official\s*(Music\s*)?Video', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s*\(.*?Music\s*Video.*?\)', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s*\[.*?Music\s*Video.*?\]', '', title, flags=re.IGNORECASE)

    # Remove lyrics annotations
    title = re.sub(r'\s*\(Lyrics?\)', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s*\[Lyrics?\]', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s*\(.*?Lyric.*?\)', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s*\[.*?Lyric.*?\]', '', title, flags=re.IGNORECASE)

    # Remove audio/video quality annotations
    title = re.sub(r'\s*\(Audio\)', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s*\[Audio\]', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s*\(HD\)', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s*\[HD\]', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s*\(HQ\)', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s*\[HQ\]', '', title, flags=re.IGNORECASE)

    # Remove remaster/remastered annotations
    title = re.sub(r'\s*\(.*?Remaster.*?\)', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s*\[.*?Remaster.*?\]', '', title, flags=re.IGNORECASE)

    return title.strip()


def normalise_track_for_hash(artist: str, title: str) -> str:
    """Normalise artist/title for consistent hashing across playlist checks"""
    text = f"{artist}|{title}".lower()
    # Remove feat./ft./featuring and everything after
    text = re.sub(r'\s*(feat\.?|ft\.?|featuring)\s+.*?\|', '|', text)
    text = re.sub(r'\s*(feat\.?|ft\.?|featuring)\s+.*$', '', text)
    # Remove common suffixes in parens/brackets
    text = re.sub(r'\s*[\(\[].*?[\)\]]', '', text)
    # Remove punctuation except pipe separator
    text = re.sub(r'[^\w\s|]', '', text)
    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def hash_track(artist: str, title: str) -> str:
    """Generate hash for track identification in watched playlists"""
    normalised = normalise_track_for_hash(artist, title)
    return hashlib.sha256(normalised.encode()).hexdigest()[:16]


def extract_artist_title(full_title: str, channel: str) -> tuple[str, str]:
    """Try to extract artist and title from YouTube video title"""
    # Common patterns: "Artist - Title", "Artist — Title", "Artist | Title"
    patterns = [
        r'^(.+?)\s*[-–—]\s*(.+)$',
        r'^(.+?)\s*\|\s*(.+)$',
    ]

    for pattern in patterns:
        match = re.match(pattern, full_title)
        if match:
            artist, title = match.groups()
            return artist.strip(), clean_title(title)

    # Fallback: use channel as artist, full title as title
    # Remove common channel suffixes like "VEVO", "Official"
    artist = re.sub(r'\s*(VEVO|Official|Music)$', '', channel, flags=re.IGNORECASE)
    return artist.strip(), clean_title(full_title)


def lookup_musicbrainz(artist: str, title: str) -> Optional[dict]:
    """Look up track metadata from MusicBrainz"""
    if not get_setting_bool("enable_musicbrainz", True):
        return None

    try:
        # Search for recording
        headers = {"User-Agent": f"MusicGrabber/{VERSION} (https://github.com/yourrepo)"}

        search_url = "https://musicbrainz.org/ws/2/recording/"
        params = {
            "query": f'artist:"{artist}" AND recording:"{title}"',
            "fmt": "json",
            "limit": 1
        }

        with httpx.Client(timeout=TIMEOUT_HTTP_REQUEST) as client:
            response = client.get(search_url, params=params, headers=headers)

        if response.status_code != 200:
            return None

        data = response.json()

        if not data.get("recordings"):
            return None

        recording = data["recordings"][0]

        # Extract metadata
        metadata = {
            "title": recording.get("title"),
            "artist": recording["artist-credit"][0]["name"] if recording.get("artist-credit") else None,
        }

        # Get release information for album and date
        if recording.get("releases"):
            release = recording["releases"][0]
            metadata["album"] = release.get("title")
            metadata["date"] = release.get("date")

            # Extract year from date
            if metadata.get("date"):
                year_match = re.match(r'(\d{4})', metadata["date"])
                if year_match:
                    metadata["year"] = year_match.group(1)

        return metadata

    except Exception:
        # If MusicBrainz lookup fails, just continue without it
        return None

def fetch_lyrics(artist: str, title: str) -> Optional[str]:
    """Fetch synced lyrics from LRClib API"""
    if not get_setting_bool("enable_lyrics", True):
        return None

    try:
        headers = {"User-Agent": f"MusicGrabber/{VERSION} (https://gitlab.com/g33kphr33k/musicgrabber)"}

        with httpx.Client(timeout=TIMEOUT_HTTP_REQUEST) as client:
            # Try the get endpoint first (exact match)
            params = {
                "artist_name": artist,
                "track_name": title
            }

            response = client.get(
                "https://lrclib.net/api/get",
                params=params,
                headers=headers
            )

            if response.status_code == 200:
                data = response.json()
                # Prefer synced lyrics, fall back to plain
                if data.get("syncedLyrics"):
                    return data["syncedLyrics"]
                elif data.get("plainLyrics"):
                    return data["plainLyrics"]

            # If exact match fails, try search
            search_params = {"q": f"{artist} {title}"}
            search_response = client.get(
                "https://lrclib.net/api/search",
                params=search_params,
                headers=headers
            )

            if search_response.status_code == 200:
                results = search_response.json()
                if results:
                    # Return first match with synced lyrics, or first with plain
                    for result in results:
                        if result.get("syncedLyrics"):
                            return result["syncedLyrics"]
                    for result in results:
                        if result.get("plainLyrics"):
                            return result["plainLyrics"]

        return None

    except Exception as e:
        # If lyrics lookup fails, log and continue without
        print(f"Lyrics lookup failed for {artist} - {title}: {e}")
        return None

def save_lyrics_file(flac_path: Path, lyrics: str):
    """Save lyrics as .lrc file alongside the FLAC"""
    lrc_path = flac_path.with_suffix(".lrc")
    lrc_path.write_text(lyrics, encoding="utf-8")
    set_file_permissions(lrc_path)


def set_file_permissions(file_path: Path):
    """Set file permissions to 777 for NAS/SMB compatibility"""
    try:
        os.chmod(file_path, 0o777)
    except OSError:
        pass  # Silently ignore permission errors (may not have rights)


# =============================================================================
# Spotify Playlist Fetching via Headless Browser
#
# You cannot fetch full playlist contents from Spotify without API credentials,
# but Spotify has nuked that after the archive theft. The workaround is to use a
# headless browser to load the playlist page and scroll to load all tracks. Else,
# you only get the first 100 tracks. 

def fetch_spotify_playlist_via_browser(spotify_id: str, spotify_type: str) -> dict:
    """Fetch playlist/album tracks using a headless browser

    This method works without API credentials by loading the Spotify page
    and scrolling to load all tracks (Spotify lazy-loads them).

    Runs Playwright in a completely separate subprocess to avoid any
    interference from uvicorn's event loop.

    Returns dict with: tracks (list of "Artist - Title"), playlist_name, count
    """
    import tempfile

    url = f"https://open.spotify.com/{spotify_type}/{spotify_id}"
    print(f"Fetching Spotify {spotify_type} via headless browser: {url}")

    # Write the script to a temp file to avoid shell escaping issues
    # Use double quotes for selectors to avoid escaping issues
    # Spotify uses "tracklist-row" (not "track-row") for playlist track elements
    selector = '[data-testid="tracklist-row"]'
    script_content = f"""
import json
import time
from playwright.sync_api import sync_playwright

url = "https://open.spotify.com/{spotify_type}/{spotify_id}"
tracks = []
playlist_name = "Spotify {spotify_type.title()}"
SELECTOR = '{selector}'

try:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={{"width": 1280, "height": 800}}
        )
        page = context.new_page()

        page.goto(url, timeout=60000)
        time.sleep(3)

        # Accept cookie consent if present - this can block page rendering
        # Try multiple selectors as Spotify's cookie banner varies
        cookie_selectors = [
            "button:has-text('Accept cookies')",
            "button:has-text('Accept Cookies')",
            "button:has-text('ACCEPT COOKIES')",
            "[data-testid='cookie-policy-manage-dialog-accept-button']",
            "button.onetrust-close-btn-handler"
        ]
        for selector in cookie_selectors:
            try:
                btn = page.query_selector(selector)
                if btn:
                    print(f"DEBUG: Found cookie button with selector: {{selector}}", file=__import__("sys").stderr)
                    btn.click()
                    time.sleep(2)
                    break
            except Exception as e:
                print(f"DEBUG: Cookie selector {{selector}} failed: {{e}}", file=__import__("sys").stderr)
                pass

        import sys as _sys

        # Wait for track list to load
        page.wait_for_selector(SELECTOR, timeout=30000)

        try:
            # Get playlist name from the page - try specific selectors first
            title_elem = page.query_selector('[data-testid="playlist-page"] h1')
            if not title_elem:
                title_elem = page.query_selector('[data-testid="entityTitle"] h1')
            if not title_elem:
                title_elem = page.query_selector('h1')
            if title_elem:
                name = title_elem.inner_text().strip()
                if name and name != "Your Library":
                    playlist_name = name
        except:
            pass

        # Spotify uses virtualized scrolling - tracks get unloaded as you scroll
        # We need to extract tracks incrementally while scrolling
        seen_tracks = set()
        stale_count = 0
        last_seen_count = 0

        def extract_visible_tracks():
            extracted = []
            for row in page.query_selector_all(SELECTOR):
                try:
                    text = row.inner_text().strip()
                    parts = text.split(chr(10))
                    parts = [p.strip() for p in parts if p.strip()]

                    # Only extract tracks that have a track number (actual playlist tracks)
                    # This filters out "Recommended" tracks at the bottom which don't have numbers
                    if not parts or not parts[0].isdigit():
                        continue

                    # Skip the track number
                    parts = parts[1:]

                    # Skip "E" for Explicit marker
                    if parts and parts[0] == "E":
                        parts = parts[1:]

                    if len(parts) >= 2:
                        track_name = parts[0].strip()
                        artist = parts[1].strip()
                        # Handle case where "E" slipped through as artist
                        if artist == "E" and len(parts) >= 3:
                            artist = parts[2].strip()
                        if track_name and artist and artist != "E":
                            track_str = artist + " - " + track_name
                            if track_str not in seen_tracks:
                                seen_tracks.add(track_str)
                                extracted.append(track_str)
                except:
                    continue
            return extracted

        # First extraction before scrolling
        extract_visible_tracks()

        while stale_count < 20:
            # Scroll the last visible track row into view to trigger loading more
            rows = page.query_selector_all(SELECTOR)
            if rows:
                rows[-1].scroll_into_view_if_needed()
            time.sleep(0.3)

            # Extract any new visible tracks
            extract_visible_tracks()

            if len(seen_tracks) == last_seen_count:
                stale_count += 1
            else:
                stale_count = 0
                last_seen_count = len(seen_tracks)
        tracks = list(seen_tracks)

        browser.close()

    print(json.dumps({{"success": True, "tracks": tracks, "playlist_name": playlist_name, "count": len(tracks)}}))

except Exception as e:
    print(json.dumps({{"success": False, "error": str(e)}}))
"""

    # Write script to temp file and execute it
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write(script_content)
        script_path = f.name

    print(f"Running browser script: {script_path}")

    try:
        result = subprocess.run(
            ["python3", script_path],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SPOTIFY_BROWSER
        )
        print(f"Script return code: {result.returncode}")
        print(f"Script stdout: {result.stdout[:500] if result.stdout else 'empty'}")
        print(f"Script stderr: {result.stderr[:500] if result.stderr else 'empty'}")
    finally:
        # Clean up temp file
        try:
            os.unlink(script_path)
        except:
            pass

    if result.returncode != 0:
        error_msg = result.stderr or "Unknown error"
        raise HTTPException(
            status_code=502,
            detail=f"Failed to fetch Spotify {spotify_type} via browser: {error_msg}"
        )

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=502,
            detail=f"Invalid response from browser subprocess: {result.stdout[:200]}"
        )

    if not data.get("success"):
        raise HTTPException(
            status_code=502,
            detail=f"Failed to fetch Spotify {spotify_type} via browser: {data.get('error', 'Unknown error')}"
        )

    tracks = data["tracks"]
    playlist_name = data["playlist_name"]

    print(f"Successfully extracted {len(tracks)} tracks via browser")

    if not tracks:
        raise HTTPException(
            status_code=422,
            detail=f"Could not extract tracks from {spotify_type}. The page structure may have changed."
        )

    return {
        "tracks": tracks,
        "playlist_name": playlist_name,
        "count": len(tracks)
    }

# =============================================================================
# Soulseek/slskd Integration

def slskd_enabled() -> bool:
    """Check if slskd integration is configured"""
    url = get_setting("slskd_url")
    user = get_setting("slskd_user")
    password = get_setting("slskd_pass")
    return bool(url and user and password)


def get_slskd_token() -> Optional[str]:
    """Get a valid slskd auth token, refreshing if needed"""
    global _slskd_token, _slskd_token_expires

    if not slskd_enabled():
        return None

    # Return cached token if still valid (with 60s buffer)
    if _slskd_token and time.time() < _slskd_token_expires - 60:
        return _slskd_token

    url = get_setting("slskd_url")
    user = get_setting("slskd_user")
    password = get_setting("slskd_pass")

    try:
        with httpx.Client(timeout=TIMEOUT_HTTP_REQUEST) as client:
            response = client.post(
                f"{url}/api/v0/session",
                json={"username": user, "password": password}
            )
            if response.status_code == 200:
                data = response.json()
                _slskd_token = data["token"]
                _slskd_token_expires = data["expires"]
                return _slskd_token
    except Exception as e:
        print(f"slskd auth failed: {e}")

    return None


def parse_slskd_quality(file_info: dict) -> tuple[str, int]:
    """
    Parse quality info from slskd file response.
    Returns (quality_label, sort_score) where higher score = better quality.
    """
    filename = file_info.get("filename", "").lower()
    bit_depth = file_info.get("bitDepth", 0)
    sample_rate = file_info.get("sampleRate", 0)
    bit_rate = file_info.get("bitRate", 0)

    # Determine format from filename extension
    if filename.endswith(".flac"):
        if bit_depth >= 24:
            return (f"FLAC {bit_depth}bit/{sample_rate//1000}kHz", 150)
        return ("FLAC", 100)
    elif filename.endswith(".wav"):
        return ("WAV", 95)
    elif filename.endswith(".mp3"):
        if bit_rate >= 320:
            return ("MP3 320", 80)
        elif bit_rate >= 256:
            return ("MP3 256", 70)
        elif bit_rate >= 192:
            return ("MP3 192", 60)
        else:
            return (f"MP3 {bit_rate}", 50)
    elif filename.endswith(".m4a") or filename.endswith(".aac"):
        if bit_rate >= 256:
            return ("AAC 256", 75)
        return (f"AAC {bit_rate}", 65)
    elif filename.endswith(".ogg") or filename.endswith(".opus"):
        return ("OGG/Opus", 70)
    else:
        return ("Unknown", 30)


def normalize_slskd_path(path: str) -> str:
    """Normalise slskd paths for matching"""
    return path.replace("\\", "/").strip()


def get_slskd_local_path(download_info: dict) -> Optional[str]:
    """Try to pull a local file path from slskd download info"""
    for key in ("localPath", "localFilename", "downloadedFilePath", "downloadPath", "path", "fullPath"):
        value = download_info.get(key)
        if value:
            return value
    return None


def should_retry_slskd_error(error_message: str) -> bool:
    """Decide whether to retry based on slskd error text"""
    msg = error_message.lower()
    retry_markers = [
        "aborted",
        "rejected",
        "cancelled",
        "failed",
        "timed out",
        "timeout",
        "queued",
    ]
    return any(marker in msg for marker in retry_markers)


def extract_track_info_from_path(filepath: str) -> tuple[str, str]:
    """
    Extract artist and title from a Soulseek file path.
    Tries common patterns like 'Artist/Album/## - Title.ext'
    """
    # Get just the filename
    filename = filepath.split("\\")[-1]
    # Remove extension
    name = re.sub(r'\.[^.]+$', '', filename)
    # Remove track number prefix like "01 - " or "01. "
    name = re.sub(r'^\d+[\s.\-]+', '', name)

    # Try to extract artist from path
    parts = filepath.replace("\\", "/").split("/")
    artist = "Unknown"

    # Look for artist in path (usually 2-3 levels up from file)
    for i, part in enumerate(parts):
        # Skip common folder names
        if part.lower() in ["music", "main", "albums", "singles", "anthologies", "instrumental", "@@*"]:
            continue
        if part.startswith("@@"):
            continue
        if re.match(r'^\[\d{4}\]', part):  # Album folder like [1976] Arrival
            continue
        if re.match(r'^cd\d*$', part.lower()):  # CD1, CD2, etc.
            continue
        # First real folder name is likely the artist
        if i > 0 and not part.startswith("["):
            artist = part
            break

    return artist, name


def search_slskd(query: str, timeout_secs: int = TIMEOUT_SLSKD_SEARCH) -> list[dict]:
    """
    Search slskd and return normalized results.
    Returns list of dicts with: id, title, artist, quality, score, source, slskd_* fields
    """
    token = get_slskd_token()
    if not token:
        return []

    slskd_url = get_setting("slskd_url")
    results = []

    try:
        headers = {"Authorization": f"Bearer {token}"}

        with httpx.Client(timeout=TIMEOUT_SLSKD_API) as client:
            # Start search
            search_response = client.post(
                f"{slskd_url}/api/v0/searches",
                headers=headers,
                json={"searchText": query}
            )

            if search_response.status_code != 200:
                print(f"slskd search failed: {search_response.status_code}")
                return []

            search_data = search_response.json()
            search_id = search_data["id"]
            print(f"slskd: Search started, ID: {search_id}")

            # Poll for results - wait for completion or timeout
            # Don't break early on file count as responses may not be ready
            start_time = time.time()
            last_file_count = 0
            final_status = None
            while time.time() - start_time < timeout_secs:
                time.sleep(1)

                status_response = client.get(
                    f"{slskd_url}/api/v0/searches/{search_id}",
                    headers=headers
                )

                if status_response.status_code == 200:
                    final_status = status_response.json()
                    file_count = final_status.get("fileCount", 0)
                    if file_count != last_file_count:
                        print(f"slskd: Polling... {file_count} files, {final_status.get('responseCount', 0)} responses")
                        last_file_count = file_count
                    if final_status.get("isComplete"):
                        print(f"slskd: Search complete. {file_count} files total")
                        break

            if final_status:
                print(f"slskd: Final status - {final_status.get('fileCount', 0)} files, {final_status.get('responseCount', 0)} responses")

            # Small delay to allow responses to be fully indexed
            time.sleep(1)

            # Get responses with a short retry window in case indexing lags
            responses = []
            responses_deadline = time.time() + min(5, timeout_secs)
            while time.time() < responses_deadline:
                responses_response = client.get(
                    f"{slskd_url}/api/v0/searches/{search_id}/responses",
                    headers=headers
                )
                if responses_response.status_code == 200:
                    responses = responses_response.json()
                    if responses:
                        break
                time.sleep(0.5)

            print(f"slskd: Got {len(responses)} user responses")

            # Process results - pick best file from each user
            seen_tracks = set()
            skipped_locked = 0
            skipped_quality = 0
            skipped_no_slot = 0

            for response in responses:
                username = response.get("username", "")
                has_free_slot = response.get("hasFreeUploadSlot", False)
                upload_speed = response.get("uploadSpeed", 0)

                if SLSKD_REQUIRE_FREE_SLOT and not has_free_slot:
                    skipped_no_slot += 1
                    continue

                files = response.get("files") or response.get("fileInfos") or response.get("fileInfo") or []
                for file_info in files:
                    if file_info.get("isLocked", False):
                        skipped_locked += 1
                        continue

                    filepath = file_info.get("filename", "")
                    quality_label, quality_score = parse_slskd_quality(file_info)

                    # Skip low quality
                    if quality_score < SLSKD_MIN_QUALITY_SCORE:
                        skipped_quality += 1
                        continue

                    artist, title = extract_track_info_from_path(filepath)

                    # Dedupe by artist+title+quality
                    track_key = f"{artist.lower()}|{title.lower()}|{quality_label}"
                    if track_key in seen_tracks:
                        continue
                    seen_tracks.add(track_key)

                    # Boost score for free slots and fast uploaders
                    adjusted_score = quality_score
                    if has_free_slot:
                        adjusted_score += 10
                    if upload_speed > 1000000:  # > 1MB/s
                        adjusted_score += 5

                    results.append({
                        "id": f"slskd_{uuid.uuid4().hex[:8]}",
                        "title": title,
                        "artist": artist,
                        "channel": username,  # Show username as "channel"
                        "quality": quality_label,
                        "quality_score": adjusted_score,
                        "source": "soulseek",
                        "duration": str(file_info.get("length", 0)),
                        "size": file_info.get("size", 0),
                        "slskd_username": username,
                        "slskd_filename": filepath,
                    })

            print(
                "slskd: Skipped "
                f"{skipped_locked} locked, {skipped_quality} low quality, "
                f"{skipped_no_slot} no free slot, kept {len(results)}"
            )

            # Clean up search
            try:
                client.delete(f"{slskd_url}/api/v0/searches/{search_id}", headers=headers)
            except Exception:
                pass

    except Exception as e:
        print(f"slskd search error: {e}")

    # Sort by quality score (descending)
    results.sort(key=lambda x: x["quality_score"], reverse=True)

    return results[:SLSKD_MAX_RESULTS]


def download_from_slskd(username: str, filename: str, dest_dir: Path, timeout_secs: int = TIMEOUT_SLSKD_DOWNLOAD) -> Optional[Path]:
    """
    Download a file from Soulseek via slskd.
    Returns the path to the downloaded file, or None on failure.

    Uses slskd_downloads_path setting if set; otherwise falls back to common download locations.
    slskd typically organises downloads as: {downloads_path}/{username}/{filename}
    """
    token = get_slskd_token()
    if not token:
        raise Exception("slskd authentication failed")

    slskd_url = get_setting("slskd_url")
    slskd_downloads_path = get_setting("slskd_downloads_path")

    headers = {"Authorization": f"Bearer {token}"}

    # Extract just the filename from the full path
    target_norm = normalize_slskd_path(filename)
    source_filename = Path(target_norm).name

    slskd_download_dirs = []
    if slskd_downloads_path:
        slskd_download_dirs.append(Path(slskd_downloads_path))
    slskd_download_dirs.extend([
        Path("/slskd/downloads"),
        Path("/app/downloads"),
        Path("/downloads"),
    ])
    seen_dirs = set()
    slskd_download_dirs = [
        d for d in slskd_download_dirs
        if not (str(d) in seen_dirs or seen_dirs.add(str(d)))
    ]

    try:
        with httpx.Client(timeout=TIMEOUT_SLSKD_API) as client:
            # Enqueue the download
            enqueue_response = client.post(
                f"{slskd_url}/api/v0/transfers/downloads/{username}",
                headers=headers,
                json=[{"filename": filename}]
            )

            if enqueue_response.status_code not in [200, 201]:
                raise Exception(f"Failed to enqueue download: {enqueue_response.status_code}")

            print(f"slskd: Enqueued download of '{source_filename}' from {username}")

            # Poll for download completion
            start_time = time.time()
            download_complete = False
            downloaded_path = None
            abort_count = 0
            max_abort_requeues = 3  # Re-queue up to 3 times on abort before giving up
            last_state = ""

            while time.time() - start_time < timeout_secs:
                time.sleep(5)

                # Get download status for this user
                status_response = client.get(
                    f"{slskd_url}/api/v0/transfers/downloads/{username}",
                    headers=headers
                )

                if status_response.status_code != 200:
                    continue

                downloads_data = status_response.json()

                # slskd returns { "directories": [...], "files": [...] } structure
                # Each directory has "files" array with the actual transfer info
                files_to_check = []

                if isinstance(downloads_data, dict):
                    # New API format: { directories: [...] }
                    for directory in downloads_data.get("directories", []):
                        files_to_check.extend(directory.get("files", []))
                elif isinstance(downloads_data, list):
                    # Old API format: direct list of files
                    files_to_check = downloads_data

                # Find our file in the downloads
                file_found = False
                for dl in files_to_check:
                    dl_filename = dl.get("filename", "")
                    dl_norm = normalize_slskd_path(dl_filename)
                    dl_base = Path(dl_norm).name
                    if dl_norm == target_norm or dl_base == source_filename or dl_norm.endswith(f"/{source_filename}"):
                        file_found = True
                        state = dl.get("state", "")
                        progress = dl.get("percentComplete", 0)

                        # Only log state changes to reduce noise
                        if state != last_state:
                            print(f"slskd: Download state: {state} ({progress}%)")
                            last_state = state

                        state_lower = state.lower()

                        # Terminal failure states - these won't recover
                        if any(s in state_lower for s in ("failed", "cancelled", "rejected", "errored")):
                            raise Exception(f"Download failed: {state}")

                        # Aborted is often transient - try re-queuing
                        if "aborted" in state_lower:
                            abort_count += 1
                            if abort_count > max_abort_requeues:
                                raise Exception(f"Download aborted {abort_count} times, giving up")

                            print(f"slskd: Download aborted, re-queuing (attempt {abort_count}/{max_abort_requeues})...")
                            time.sleep(2)  # Brief pause before re-queue

                            # Re-enqueue the download
                            requeue_response = client.post(
                                f"{slskd_url}/api/v0/transfers/downloads/{username}",
                                headers=headers,
                                json=[{"filename": filename}]
                            )
                            if requeue_response.status_code not in [200, 201]:
                                print(f"slskd: Re-queue failed with status {requeue_response.status_code}")
                            else:
                                print(f"slskd: Re-queued successfully")

                            last_state = ""  # Reset to log new state
                            break  # Continue polling

                        # Success states
                        if state_lower.startswith("completed") or state_lower == "succeeded":
                            # Make sure it's actually completed successfully, not "CompletedWithError"
                            if "error" not in state_lower:
                                download_complete = True
                                downloaded_path = get_slskd_local_path(dl) or dl_filename
                            else:
                                raise Exception(f"Download completed with error: {state}")
                            break

                if download_complete:
                    break

                # If file disappeared from the queue entirely, it might have been
                # removed or the user went offline - try re-queuing once
                if not file_found and last_state and "queue" not in last_state.lower():
                    print(f"slskd: File no longer in transfer queue, attempting re-queue...")
                    requeue_response = client.post(
                        f"{slskd_url}/api/v0/transfers/downloads/{username}",
                        headers=headers,
                        json=[{"filename": filename}]
                    )
                    if requeue_response.status_code in [200, 201]:
                        print(f"slskd: Re-queued successfully")
                    last_state = ""

            if not download_complete:
                raise Exception(f"Download timed out after {timeout_secs}s")

            # File should now be in slskd's downloads folder
            # slskd typically organises as: {downloads_path}/{username}/{filename}
            candidate_paths = []
            if downloaded_path:
                normalized_path = normalize_slskd_path(downloaded_path)
                dl_path = Path(normalized_path)
                # Only allow absolute paths if they're within a known download directory
                if dl_path.is_absolute():
                    # Security: verify the path is within allowed download directories
                    is_safe = False
                    for slskd_dir in slskd_download_dirs:
                        try:
                            dl_path.resolve().relative_to(slskd_dir.resolve())
                            is_safe = True
                            break
                        except ValueError:
                            continue
                    if is_safe:
                        candidate_paths.append(dl_path)
                    else:
                        print(f"slskd: Ignoring absolute path outside download dirs: {dl_path}")
                else:
                    for slskd_dir in slskd_download_dirs:
                        candidate_paths.append(slskd_dir / dl_path)
                        candidate_paths.append(slskd_dir / username / dl_path)

            for slskd_dir in slskd_download_dirs:
                candidate_paths.append(slskd_dir / username / source_filename)

            for potential_path in candidate_paths:
                # Security: resolve and verify the path is within allowed directories
                try:
                    resolved = potential_path.resolve()
                    is_safe = False
                    for slskd_dir in slskd_download_dirs:
                        try:
                            resolved.relative_to(slskd_dir.resolve())
                            is_safe = True
                            break
                        except ValueError:
                            continue
                    if not is_safe:
                        print(f"slskd: Skipping path outside download dirs: {resolved}")
                        continue
                except (OSError, ValueError):
                    continue

                if potential_path.exists():
                    import shutil
                    dest_path = dest_dir / source_filename
                    shutil.copy2(potential_path, dest_path)
                    print(f"slskd: Copied {potential_path} to {dest_path}")
                    return dest_path

            # If not found, search recursively in the username folder
            for slskd_dir in slskd_download_dirs:
                user_dir = slskd_dir / username
                if user_dir.exists():
                    for found_file in user_dir.rglob(source_filename):
                        if found_file.is_file():
                            import shutil
                            dest_path = dest_dir / source_filename
                            shutil.copy2(found_file, dest_path)
                            print(f"slskd: Found and copied {found_file} to {dest_path}")
                            return dest_path

            # List what's actually there for debugging
            for slskd_dir in slskd_download_dirs:
                if slskd_dir.exists():
                    print(f"slskd: Downloads directory contents ({slskd_dir}):")
                    for item in slskd_dir.iterdir():
                        print(f"  - {item.name}/")
                        if item.is_dir():
                            for subitem in list(item.iterdir())[:5]:
                                print(f"      {subitem.name}")
                else:
                    print(f"slskd: Downloads directory not found: {slskd_dir}")

            raise Exception(
                "Downloaded file not found at expected location. "
                "Check that the slskd downloads path is mounted into MusicGrabber."
            )

    except Exception as e:
        print(f"slskd download error: {e}")
        raise

def apply_metadata_to_file(file_path: Path, artist: str, title: str, album: str = "Singles", year: str = None):
    """Apply metadata to audio file using mutagen (supports multiple formats)"""
    try:
        suffix = file_path.suffix.lower()

        if suffix == '.flac':
            audio = FLAC(str(file_path))
            audio["ARTIST"] = artist
            audio["TITLE"] = title
            audio["ALBUM"] = album
            if year:
                audio["DATE"] = year
            audio.save()

        elif suffix == '.mp3':
            from mutagen.easyid3 import EasyID3
            from mutagen.mp3 import MP3
            try:
                audio = EasyID3(str(file_path))
            except Exception:
                # If no ID3 tag exists, create one
                mp3 = MP3(str(file_path))
                mp3.add_tags()
                mp3.save()
                audio = EasyID3(str(file_path))
            audio["artist"] = artist
            audio["title"] = title
            audio["album"] = album
            if year:
                audio["date"] = year
            audio.save()

        elif suffix in ['.m4a', '.mp4']:
            from mutagen.mp4 import MP4
            audio = MP4(str(file_path))
            audio["\xa9ART"] = [artist]
            audio["\xa9nam"] = [title]
            audio["\xa9alb"] = [album]
            if year:
                audio["\xa9day"] = [year]
            audio.save()

        elif suffix in ['.ogg', '.opus']:
            from mutagen.oggopus import OggOpus
            from mutagen.oggvorbis import OggVorbis
            try:
                if suffix == '.opus':
                    audio = OggOpus(str(file_path))
                else:
                    audio = OggVorbis(str(file_path))
                audio["ARTIST"] = artist
                audio["TITLE"] = title
                audio["ALBUM"] = album
                if year:
                    audio["DATE"] = year
                audio.save()
            except Exception:
                pass  # Some ogg variants may not be supported

        # For .webm and other unsupported formats, skip metadata (yt-dlp handles it)

    except Exception:
        # If metadata application fails, continue anyway
        pass

def check_duplicate(artist: str, title: str) -> Optional[Path]:
    """Check if a track already exists in the library (any audio format)"""
    try:
        artist_dir = SINGLES_DIR / sanitize_filename(artist)
        if not artist_dir.exists():
            return None

        sanitized_title = sanitize_filename(title)

        # Check for exact filename match in any supported format
        for ext in ['.flac', '.opus', '.m4a', '.webm', '.mp3', '.ogg']:
            expected_file = artist_dir / f"{sanitized_title}{ext}"
            if expected_file.exists():
                return expected_file

        # Check for similar files (case-insensitive) in any audio format
        title_lower = sanitized_title.lower()
        for ext in ['*.flac', '*.opus', '*.m4a', '*.webm', '*.mp3', '*.ogg']:
            for file in artist_dir.glob(ext):
                if file.stem.lower() == title_lower:
                    return file

        return None
    except Exception:
        return None

@app.get("/", response_class=HTMLResponse)
def root():
    """Serve the main UI"""
    return FileResponse("static/index.html")

@app.get("/api/config")
def get_config():
    """Expose server configuration and version for the UI"""
    api_key = get_setting("api_key", "")
    return {
        "version": VERSION,
        "default_convert_to_flac": get_setting_bool("default_convert_to_flac", True),
        "auth_required": bool(api_key)
    }


# =============================================================================
# Settings API
# =============================================================================

# Define which settings are sensitive (should be masked in GET response)
SENSITIVE_SETTINGS = {
    "slskd_pass", "navidrome_pass", "jellyfin_api_key",
    "smtp_pass", "telegram_webhook_url", "api_key", "youtube_cookies"
}

# Define all configurable settings with their types and defaults
SETTINGS_SCHEMA = {
    # General
    "music_dir": {"type": "str", "default": "/music", "env": "MUSIC_DIR"},
    "enable_musicbrainz": {"type": "bool", "default": True, "env": "ENABLE_MUSICBRAINZ"},
    "enable_lyrics": {"type": "bool", "default": True, "env": "ENABLE_LYRICS"},
    "default_convert_to_flac": {"type": "bool", "default": True, "env": "DEFAULT_CONVERT_TO_FLAC"},
    # Soulseek/slskd
    "slskd_url": {"type": "str", "default": "", "env": "SLSKD_URL"},
    "slskd_user": {"type": "str", "default": "", "env": "SLSKD_USER"},
    "slskd_pass": {"type": "str", "default": "", "env": "SLSKD_PASS", "sensitive": True},
    "slskd_downloads_path": {"type": "str", "default": "", "env": "SLSKD_DOWNLOADS_PATH"},
    # Navidrome
    "navidrome_url": {"type": "str", "default": "", "env": "NAVIDROME_URL"},
    "navidrome_user": {"type": "str", "default": "", "env": "NAVIDROME_USER"},
    "navidrome_pass": {"type": "str", "default": "", "env": "NAVIDROME_PASS", "sensitive": True},
    # Jellyfin
    "jellyfin_url": {"type": "str", "default": "", "env": "JELLYFIN_URL"},
    "jellyfin_api_key": {"type": "str", "default": "", "env": "JELLYFIN_API_KEY", "sensitive": True},
    # Notifications
    "notify_on": {"type": "str", "default": "playlists,bulk,errors", "env": "NOTIFY_ON"},
    "telegram_webhook_url": {"type": "str", "default": "", "env": "TELEGRAM_WEBHOOK_URL", "sensitive": True},
    "smtp_host": {"type": "str", "default": "", "env": "SMTP_HOST"},
    "smtp_port": {"type": "int", "default": 587, "env": "SMTP_PORT"},
    "smtp_user": {"type": "str", "default": "", "env": "SMTP_USER"},
    "smtp_pass": {"type": "str", "default": "", "env": "SMTP_PASS", "sensitive": True},
    "smtp_from": {"type": "str", "default": "", "env": "SMTP_FROM"},
    "smtp_to": {"type": "str", "default": "", "env": "SMTP_TO"},
    "smtp_tls": {"type": "bool", "default": True, "env": "SMTP_TLS"},
    # YouTube
    "youtube_cookies": {"type": "str", "default": "", "env": "YOUTUBE_COOKIES", "sensitive": True},
    "youtube_bot_backoff_min": {"type": "int", "default": BOT_BACKOFF_MIN_SECONDS, "env": "YOUTUBE_BOT_BACKOFF_MIN"},
    "youtube_bot_backoff_max": {"type": "int", "default": BOT_BACKOFF_MAX_SECONDS, "env": "YOUTUBE_BOT_BACKOFF_MAX"},
    # Security
    "api_key": {"type": "str", "default": "", "env": "API_KEY", "sensitive": True},
}


def _get_typed_setting(key: str) -> any:
    """Get a setting with proper type conversion based on schema."""
    schema = SETTINGS_SCHEMA.get(key, {"type": "str", "default": ""})
    default = schema["default"]
    if schema["type"] == "bool":
        return get_setting_bool(key, default)
    elif schema["type"] == "int":
        return get_setting_int(key, default)
    return get_setting(key, default)


def _is_env_override(key: str) -> bool:
    """Check if a setting is being overridden by an environment variable."""
    schema = SETTINGS_SCHEMA.get(key, {})
    env_key = schema.get("env", key.upper())
    return os.getenv(env_key) is not None


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


class TestSlskdRequest(BaseModel):
    url: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None


@app.post("/api/settings/test/slskd")
async def test_slskd_connection(request: TestSlskdRequest = None):
    """Test connection to slskd server. Uses form values if provided, otherwise saved settings."""
    # Use provided values or fall back to saved settings
    url = (request.url if request and request.url else None) or _get_typed_setting("slskd_url")
    user = (request.username if request and request.username else None) or _get_typed_setting("slskd_user")
    password = (request.password if request and request.password else None) or _get_typed_setting("slskd_pass")

    if not url:
        return {"success": False, "message": "slskd URL not configured"}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # Try to authenticate
            auth_response = await client.post(
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


class TestNavidromeRequest(BaseModel):
    url: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None


@app.post("/api/settings/test/navidrome")
async def test_navidrome_connection(request: TestNavidromeRequest = None):
    """Test connection to Navidrome server. Uses form values if provided, otherwise saved settings."""
    url = (request.url if request and request.url else None) or _get_typed_setting("navidrome_url")
    user = (request.username if request and request.username else None) or _get_typed_setting("navidrome_user")
    password = (request.password if request and request.password else None) or _get_typed_setting("navidrome_pass")

    if not url:
        return {"success": False, "message": "Navidrome URL not configured"}

    try:
        # Navidrome uses subsonic API - ping endpoint
        import hashlib
        import secrets
        salt = secrets.token_hex(8)
        token = hashlib.md5((password + salt).encode()).hexdigest()

        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"{url.rstrip('/')}/rest/ping",
                params={
                    "u": user,
                    "t": token,
                    "s": salt,
                    "v": "1.16.0",
                    "c": "MusicGrabber",
                    "f": "json"
                }
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


class TestJellyfinRequest(BaseModel):
    url: Optional[str] = None
    api_key: Optional[str] = None


@app.post("/api/settings/test/jellyfin")
async def test_jellyfin_connection(request: TestJellyfinRequest = None):
    """Test connection to Jellyfin server. Uses form values if provided, otherwise saved settings."""
    url = (request.url if request and request.url else None) or _get_typed_setting("jellyfin_url")
    api_key = (request.api_key if request and request.api_key else None) or _get_typed_setting("jellyfin_api_key")

    if not url:
        return {"success": False, "message": "Jellyfin URL not configured"}
    if not api_key:
        return {"success": False, "message": "Jellyfin API key not configured"}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
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


class TestYouTubeCookiesRequest(BaseModel):
    cookies: Optional[str] = None


@app.post("/api/settings/test/youtube-cookies")
def test_youtube_cookies(request: TestYouTubeCookiesRequest = None):
    """Test YouTube cookies by fetching info for a known public video.
    Uses form value if provided, otherwise the saved cookies."""
    import tempfile

    cookies_text = (request.cookies if request and request.cookies else None)
    if cookies_text is None:
        cookies_text = get_setting("youtube_cookies", "")

    if not cookies_text.strip():
        return {"success": False, "message": "No cookies provided"}

    # Basic format validation — Netscape cookies.txt should have tab-separated lines
    if not _has_valid_cookie_entries(cookies_text):
        return {"success": False, "message": "No cookie entries found (only comments or blank lines)"}

    lines = _cookie_lines_for_domain_check(cookies_text)
    has_youtube_cookie = any(".youtube.com" in l or ".google.com" in l for l in lines)
    if not has_youtube_cookie:
        return {"success": False, "message": "No YouTube or Google cookies found. Export cookies from youtube.com."}

    # Write to a temp file and test with yt-dlp
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

        # Clean up temp file
        Path(tmp_path).unlink(missing_ok=True)

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
        Path(tmp_path).unlink(missing_ok=True)
        return {"success": False, "message": "Test timed out"}
    except Exception as e:
        return {"success": False, "message": f"Test failed: {str(e)}"}


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


@app.get("/api/preview/{video_id}")
def get_preview_url(video_id: str):
    """Get a streamable audio URL for preview playback

    Uses yt-dlp to extract a direct audio stream URL that can be played in the browser.
    """
    try:
        # Get the best audio stream URL (without downloading)
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

def search_youtube(query: str, limit: int) -> list[dict]:
    """Search YouTube and return normalized results"""
    try:
        fetch_limit = max(limit * YOUTUBE_SEARCH_MULTIPLIER, YOUTUBE_SEARCH_MIN_FETCH)

        cmd = [
            "yt-dlp",
            *_ytdlp_base_args(),
            "--dump-json",
            "--flat-playlist",
            "--no-warnings",
            f"ytsearch{fetch_limit}:{query}",
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_YTDLP_SEARCH)

        if result.returncode != 0:
            return []

        results = []
        for line in result.stdout.strip().split('\n'):
            if not line:
                continue
            try:
                data = json.loads(line)
                is_playlist = data.get("_type") == "playlist" or "playlist" in data.get("ie_key", "").lower()
                video_count = data.get("playlist_count") or data.get("n_entries")

                title = data.get("title", "Unknown")
                channel = data.get("channel", data.get("uploader", "Unknown"))
                quality_score = score_search_result(title, channel)

                results.append({
                    "video_id": data.get("id", ""),
                    "title": title,
                    "channel": channel,
                    "duration": parse_duration(data.get("duration", 0) or 0) if not is_playlist else "",
                    "thumbnail": data.get("thumbnail", f"https://i.ytimg.com/vi/{data.get('id')}/mqdefault.jpg"),
                    "is_playlist": is_playlist,
                    "video_count": video_count,
                    "source": "youtube",
                    "quality": None,
                    "quality_score": quality_score,
                    "slskd_username": None,
                    "slskd_filename": None,
                })
            except json.JSONDecodeError:
                continue

        results.sort(key=lambda x: x["quality_score"], reverse=True)
        return results[:limit]

    except Exception as e:
        print(f"YouTube search error: {e}")
        return []


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


@app.post("/api/download")
def download(request: DownloadRequest, background_tasks: BackgroundTasks):
    """Queue a download job"""
    job_id = str(uuid.uuid4())[:8]

    # Extract artist/title if not provided
    artist = request.artist
    title = request.title

    if not artist:
        # We'll extract from the full video info during download
        pass

    # Create job record
    conn = get_db()

    # Determine source type
    source = "youtube"
    if request.source == "soulseek" and request.slskd_username and request.slskd_filename:
        source = "soulseek"

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
    conn.close()

    # Queue the download based on source
    if request.download_type == "playlist":
        background_tasks.add_task(process_playlist_download, job_id, request.video_id, title, request.convert_to_flac)
    elif source == "soulseek":
        background_tasks.add_task(
            process_slskd_download,
            job_id,
            request.slskd_username,
            request.slskd_filename,
            artist or "",
            title,
            request.convert_to_flac
        )
    else:
        background_tasks.add_task(process_download, job_id, request.video_id, request.convert_to_flac)

    return {"job_id": job_id, "status": "queued"}


def process_playlist_download(job_id: str, playlist_id: str, playlist_name: str, convert_to_flac: bool = True):
    """Process a playlist download job"""
    conn = get_db()

    try:
        # Update status to downloading
        conn.execute("UPDATE jobs SET status = ? WHERE id = ?", ("downloading", job_id))
        conn.commit()

        # Get playlist information and extract all video IDs
        info_cmd = [
            "yt-dlp",
            *_ytdlp_base_args(),
            "--dump-json",
            "--flat-playlist",
            "--no-warnings",
            f"https://www.youtube.com/playlist?list={playlist_id}"
        ]

        info_result = subprocess.run(info_cmd, capture_output=True, text=True, timeout=TIMEOUT_YTDLP_PLAYLIST)
        if info_result.returncode != 0:
            raise Exception("Failed to get playlist info")

        # Parse all videos from playlist
        videos = []
        for line in info_result.stdout.strip().split('\n'):
            if not line:
                continue
            try:
                data = json.loads(line)
                if data.get("id"):
                    videos.append({
                        "id": data["id"],
                        "title": data.get("title", "Unknown"),
                        "channel": data.get("channel", data.get("uploader", "Unknown"))
                    })
            except json.JSONDecodeError:
                continue

        if not videos:
            raise Exception("No videos found in playlist")

        # Update total tracks
        conn.execute(
            "UPDATE jobs SET total_tracks = ? WHERE id = ?",
            (len(videos), job_id)
        )
        conn.commit()

        # Download each video in the playlist
        downloaded_files = []
        completed_tracks = 0
        failed_tracks = 0
        skipped_tracks = 0
        for _, video in enumerate(videos, 1):
            track_label = video.get("title", "Unknown")
            try:
                video_id = video["id"]

                # Get detailed video info
                detail_cmd = [
                    "yt-dlp",
                    *_ytdlp_base_args(),
                    "--dump-json",
                    "--no-warnings",
                    f"https://www.youtube.com/watch?v={video_id}"
                ]

                detail_result = subprocess.run(detail_cmd, capture_output=True, text=True, timeout=TIMEOUT_YTDLP_INFO)
                if detail_result.returncode != 0:
                    failed_tracks += 1
                    continue

                info = json.loads(detail_result.stdout)
                full_title = info.get("title", "Unknown")
                channel = info.get("channel", info.get("uploader", "Unknown"))
                artist, title = extract_artist_title(full_title, channel)

                # Check for duplicates
                existing_file = check_duplicate(artist, title)
                if existing_file:
                    skipped_tracks += 1
                    continue

                # Create artist directory under Singles
                artist_dir = SINGLES_DIR / sanitize_filename(artist)
                artist_dir.mkdir(parents=True, exist_ok=True)

                # Download with best audio quality
                output_template = str(artist_dir / f"{sanitize_filename(title)}.%(ext)s")

                flac_args = ["--audio-format", "flac"] if convert_to_flac else []
                download_cmd = [
                    "yt-dlp",
                    *_ytdlp_base_args(),
                    "-f", "bestaudio/best",
                    "-x",
                    *flac_args,
                    "--audio-quality", "0",
                    "--embed-metadata",
                    "--embed-thumbnail",
                    "--convert-thumbnails", "jpg",
                    "--ppa", "ffmpeg:-c:v mjpeg -vf crop=\"'if(gt(ih,iw),iw,ih)':'if(gt(iw,ih),ih,iw)'\"",
                    "--add-metadata",
                    "--parse-metadata", f"%(artist,channel,uploader)s:%(meta_artist)s",
                    "--parse-metadata", f"%(track,title)s:%(meta_title)s",
                    "-o", output_template,
                    "--no-warnings",
                    f"https://www.youtube.com/watch?v={video_id}"
                ]

                download_result = None
                download_timed_out = False
                for attempt in range(1 + YTDLP_403_MAX_RETRIES):
                    _sleep_if_botted()
                    try:
                        download_result = subprocess.run(
                            download_cmd,
                            capture_output=True,
                            text=True,
                            timeout=TIMEOUT_YTDLP_DOWNLOAD
                        )
                    except subprocess.TimeoutExpired:
                        download_timed_out = True
                        break
                    if download_result.returncode == 0:
                        break
                    if _is_ytdlp_403(download_result.stderr) and attempt < YTDLP_403_MAX_RETRIES:
                        print(f"YouTube 403 for {video_id}, retrying (attempt {attempt + 1})")
                        time.sleep(YTDLP_403_RETRY_DELAY * (attempt + 1))
                    else:
                        break

                if download_timed_out or (download_result and _should_retry_without_cookies(download_result.stderr)):
                    _note_bot_block()

                # If cookies are present and download failed, try once more without cookies
                has_cookies = COOKIES_FILE.exists() and COOKIES_FILE.stat().st_size > 0
                if (download_timed_out or (download_result and download_result.returncode != 0)) and has_cookies:
                    if download_timed_out or _should_retry_without_cookies(download_result.stderr):
                        download_cmd_no_cookies = _strip_cookies_args(download_cmd)
                        try:
                            download_result = subprocess.run(
                                download_cmd_no_cookies,
                                capture_output=True,
                                text=True,
                                timeout=TIMEOUT_YTDLP_DOWNLOAD
                            )
                            download_timed_out = False
                        except subprocess.TimeoutExpired:
                            download_timed_out = True

                if download_timed_out or download_result.returncode != 0:
                    failed_tracks += 1
                    continue

                # Find the downloaded file (extension depends on convert_to_flac setting)
                audio_file = None
                sanitized_title = sanitize_filename(title)
                for ext in ['.flac', '.opus', '.m4a', '.webm', '.mp3', '.ogg']:
                    candidate = artist_dir / f"{sanitized_title}{ext}"
                    if candidate.exists():
                        audio_file = candidate
                        break

                if not audio_file:
                    failed_tracks += 1
                    continue

                # Try to enrich metadata with MusicBrainz
                mb_metadata = lookup_musicbrainz(artist, title)
                if mb_metadata:
                    # Use MusicBrainz metadata
                    apply_metadata_to_file(
                        audio_file,
                        mb_metadata.get("artist", artist),
                        mb_metadata.get("title", title),
                        mb_metadata.get("album", "Singles"),
                        mb_metadata.get("year")
                    )
                else:
                    # Use cleaned YouTube metadata
                    apply_metadata_to_file(audio_file, artist, title, "Singles")

                downloaded_files.append(str(audio_file.relative_to(SINGLES_DIR)))
                completed_tracks += 1

            except Exception as track_error:
                # Track this individual failure and continue
                print(f"Playlist track failed: {track_label} - {track_error}")
                failed_tracks += 1

            finally:
                processed_tracks = completed_tracks + failed_tracks + skipped_tracks
                conn.execute(
                    "UPDATE jobs SET completed_tracks = ?, failed_tracks = ?, skipped_tracks = ? WHERE id = ?",
                    (processed_tracks, failed_tracks, skipped_tracks, job_id)
                )
                conn.commit()

        # Generate M3U playlist file
        if downloaded_files:
            m3u_path = SINGLES_DIR / f"{sanitize_filename(playlist_name)}.m3u"
            with open(m3u_path, 'w', encoding='utf-8') as f:
                f.write("#EXTM3U\n")
                for file_path in downloaded_files:
                    f.write(f"{file_path}\n")
            set_file_permissions(m3u_path)

            conn.execute(
                "UPDATE jobs SET m3u_path = ? WHERE id = ?",
                (str(m3u_path.relative_to(MUSIC_DIR)), job_id)
            )
            conn.commit()

        # Trigger library rescans if configured
        trigger_navidrome_scan()
        trigger_jellyfin_scan()

        # Update job status based on results
        final_status = "completed"
        error_message = None

        if failed_tracks:
            final_status = "completed_with_errors"
            error_message = f"{failed_tracks} track(s) failed"
            if skipped_tracks:
                error_message += f", {skipped_tracks} skipped (duplicates)"

        if final_status == "completed":
            error_message = None
        conn.execute(
            "UPDATE jobs SET status = ?, error = ?, completed_at = ? WHERE id = ?",
            (final_status, error_message, datetime.now().isoformat(), job_id)
        )
        conn.commit()

        # Send Telegram notification for playlist
        send_telegram_notification(
            notification_type="playlist",
            title=playlist_name,
            playlist_name=playlist_name,
            source="youtube",
            status=final_status,
            error=error_message,
            track_count=len(videos),
            failed_count=failed_tracks,
            skipped_count=skipped_tracks
        )

    except Exception as e:
        conn.execute(
            "UPDATE jobs SET status = ?, error = ?, completed_at = ? WHERE id = ?",
            ("failed", str(e), datetime.now().isoformat(), job_id)
        )
        conn.commit()

        # Send Telegram notification for playlist failure
        send_telegram_notification(
            notification_type="error",
            title=playlist_name,
            playlist_name=playlist_name,
            source="youtube",
            status="failed",
            error=str(e)
        )

    finally:
        conn.close()


def process_slskd_download(job_id: str, username: str, filename: str, artist: str, title: str, convert_to_flac: bool = True):
    """Process a Soulseek download job via slskd"""
    conn = get_db()

    try:
        # Update status to downloading
        conn.execute("UPDATE jobs SET status = ? WHERE id = ?", ("downloading", job_id))
        conn.commit()

        # If artist/title not provided, extract from filename
        if not artist or not title:
            artist, title = extract_track_info_from_path(filename)

        # Update job with extracted info
        conn.execute(
            "UPDATE jobs SET title = ?, artist = ? WHERE id = ?",
            (title, artist, job_id)
        )
        conn.commit()

        # Check for duplicates
        existing_file = check_duplicate(artist, title)
        if existing_file:
            conn.execute(
                "UPDATE jobs SET status = ?, completed_at = ?, error = ? WHERE id = ?",
                ("completed", datetime.now().isoformat(), f"Already exists: {existing_file.name}", job_id)
            )
            conn.commit()
            return

        # Create artist directory under Singles
        artist_dir = SINGLES_DIR / sanitize_filename(artist)
        artist_dir.mkdir(parents=True, exist_ok=True)

        # Download from slskd with retries on common queue/abort failures
        downloaded_file = None
        attempts = 0
        tried_candidates = set()
        candidate_queue = [(username, filename)]
        last_error = None

        while candidate_queue:
            cand_username, cand_filename = candidate_queue.pop(0)
            if (cand_username, cand_filename) in tried_candidates:
                continue
            tried_candidates.add((cand_username, cand_filename))

            try:
                downloaded_file = download_from_slskd(cand_username, cand_filename, artist_dir)
                break
            except Exception as e:
                last_error = str(e)
                print(f"slskd download attempt failed: {last_error}")
                if attempts >= SLSKD_MAX_RETRIES or not should_retry_slskd_error(last_error):
                    break
                attempts += 1

                # Refresh candidates from a new search if we don't have any left
                if not candidate_queue:
                    retry_query = f"{artist} {title}".strip()
                    retry_results = search_slskd(retry_query, timeout_secs=TIMEOUT_SLSKD_SEARCH)
                    for r in retry_results:
                        candidate = (r.get("slskd_username", ""), r.get("slskd_filename", ""))
                        if candidate[0] and candidate[1] and candidate not in tried_candidates:
                            candidate_queue.append(candidate)

        if not downloaded_file:
            raise Exception(last_error or "Soulseek download failed")

        if not downloaded_file or not downloaded_file.exists():
            raise Exception("Download completed but file not found")

        # Rename to our standard naming
        sanitized_title = sanitize_filename(title)
        source_ext = downloaded_file.suffix.lower()

        # Determine final filename
        if convert_to_flac and source_ext != '.flac':
            # Convert to FLAC
            final_file = artist_dir / f"{sanitized_title}.flac"
            convert_cmd = [
                "ffmpeg", "-y", "-i", str(downloaded_file),
                "-c:a", "flac", str(final_file)
            ]
            result = subprocess.run(convert_cmd, capture_output=True, timeout=TIMEOUT_FFMPEG_CONVERT)
            if result.returncode == 0:
                downloaded_file.unlink()  # Remove original
            else:
                # Conversion failed, keep original with new name
                final_file = artist_dir / f"{sanitized_title}{source_ext}"
                downloaded_file.rename(final_file)
        else:
            # Keep original format
            final_file = artist_dir / f"{sanitized_title}{source_ext}"
            if downloaded_file != final_file:
                downloaded_file.rename(final_file)

        # Set permissions for NAS/SMB compatibility
        set_file_permissions(final_file)

        # Apply metadata
        mb_metadata = lookup_musicbrainz(artist, title)
        if mb_metadata:
            apply_metadata_to_file(
                final_file,
                mb_metadata.get("artist", artist),
                mb_metadata.get("title", title),
                mb_metadata.get("album", "Singles"),
                mb_metadata.get("year")
            )
        else:
            apply_metadata_to_file(final_file, artist, title, "Singles")

        # Fetch and save lyrics
        lyrics = fetch_lyrics(artist, title)
        if lyrics:
            save_lyrics_file(final_file, lyrics)
            print(f"Saved lyrics for {artist} - {title}")
        else:
            print(f"No lyrics found for {artist} - {title}")

        # Trigger library rescans if configured
        trigger_navidrome_scan()
        trigger_jellyfin_scan()

        # Update job status
        conn.execute(
            "UPDATE jobs SET status = ?, error = NULL, completed_at = ? WHERE id = ?",
            ("completed", datetime.now().isoformat(), job_id)
        )
        conn.execute(
            "UPDATE watched_playlist_tracks SET downloaded_at = datetime('now') WHERE job_id = ?",
            (job_id,)
        )
        conn.commit()

        print(f"slskd: Successfully downloaded {artist} - {title}")

        # Send Telegram notification for Soulseek single
        send_telegram_notification(
            notification_type="single",
            title=title,
            artist=artist,
            source="soulseek",
            status="completed"
        )

    except Exception as e:
        print(f"slskd download failed: {e}")
        conn.execute(
            "UPDATE jobs SET status = ?, error = ?, completed_at = ? WHERE id = ?",
            ("failed", str(e), datetime.now().isoformat(), job_id)
        )
        conn.commit()

        # Send Telegram notification for Soulseek failure
        send_telegram_notification(
            notification_type="error",
            title=title if 'title' in dir() else filename,
            artist=artist if 'artist' in dir() else None,
            source="soulseek",
            status="failed",
            error=str(e)
        )

    finally:
        conn.close()


def process_download(job_id: str, video_id: str, convert_to_flac: bool = True):
    """Process a download job"""
    conn = get_db()
    
    try:
        # Update status to downloading
        conn.execute("UPDATE jobs SET status = ? WHERE id = ?", ("downloading", job_id))
        conn.commit()
        
        # First, get video info for proper metadata
        info_cmd = [
            "yt-dlp",
            *_ytdlp_base_args(),
            "--dump-json",
            "--no-warnings",
            f"https://www.youtube.com/watch?v={video_id}"
        ]

        info_result = subprocess.run(info_cmd, capture_output=True, text=True, timeout=TIMEOUT_YTDLP_INFO)
        if info_result.returncode != 0:
            if _is_ytdlp_403(info_result.stderr):
                has_cookies = COOKIES_FILE.exists() and COOKIES_FILE.stat().st_size > 0
                hint = "Your cookies may have expired — try re-exporting them in Settings." if has_cookies else "Add browser cookies in Settings to authenticate."
                raise Exception(f"YouTube blocked this request (403). {hint}")
            raise Exception("Failed to get video info")

        info = json.loads(info_result.stdout)
        
        # Extract artist and title
        full_title = info.get("title", "Unknown")
        channel = info.get("channel", info.get("uploader", "Unknown"))
        artist, title = extract_artist_title(full_title, channel)

        # Update job with extracted info
        conn.execute(
            "UPDATE jobs SET title = ?, artist = ? WHERE id = ?",
            (title, artist, job_id)
        )
        conn.commit()

        # Check for duplicates
        existing_file = check_duplicate(artist, title)
        if existing_file:
            # Mark as completed without downloading
            conn.execute(
                "UPDATE jobs SET status = ?, completed_at = ?, error = ? WHERE id = ?",
                ("completed", datetime.now().isoformat(), f"Already exists: {existing_file.name}", job_id)
            )
            conn.execute(
                "UPDATE watched_playlist_tracks SET downloaded_at = datetime('now') WHERE job_id = ?",
                (job_id,)
            )
            conn.commit()
            return

        # Create artist directory under Singles
        artist_dir = SINGLES_DIR / sanitize_filename(artist)
        artist_dir.mkdir(parents=True, exist_ok=True)

        # Download with best audio quality
        output_template = str(artist_dir / f"{sanitize_filename(title)}.%(ext)s")

        flac_args = ["--audio-format", "flac"] if convert_to_flac else []
        download_cmd = [
            "yt-dlp",
            *_ytdlp_base_args(),
            "-f", "bestaudio/best",
            "-x",  # Extract audio
            *flac_args,
            "--audio-quality", "0",  # Best quality
            "--embed-metadata",
            "--embed-thumbnail",
            "--convert-thumbnails", "jpg",  # For embedding
            "--ppa", "ffmpeg:-c:v mjpeg -vf crop=\"'if(gt(ih,iw),iw,ih)':'if(gt(iw,ih),ih,iw)'\"",  # Square thumbnail
            "--add-metadata",
            "--parse-metadata", f"%(artist,channel,uploader)s:%(meta_artist)s",
            "--parse-metadata", f"%(track,title)s:%(meta_title)s",
            "-o", output_template,
            "--no-warnings",
            f"https://www.youtube.com/watch?v={video_id}"
        ]

        download_result = None
        download_timed_out = False
        for attempt in range(1 + YTDLP_403_MAX_RETRIES):
            _sleep_if_botted()
            try:
                download_result = subprocess.run(
                    download_cmd,
                    capture_output=True,
                    text=True,
                    timeout=TIMEOUT_YTDLP_DOWNLOAD
                )
            except subprocess.TimeoutExpired:
                download_timed_out = True
                break
            if download_result.returncode == 0:
                break
            if _is_ytdlp_403(download_result.stderr) and attempt < YTDLP_403_MAX_RETRIES:
                print(f"YouTube 403 for {video_id}, retrying (attempt {attempt + 1})")
                time.sleep(YTDLP_403_RETRY_DELAY * (attempt + 1))
            else:
                break

        if download_timed_out or (download_result and _should_retry_without_cookies(download_result.stderr)):
            _note_bot_block()

        # If cookies are present and download failed, try once more without cookies
        has_cookies = COOKIES_FILE.exists() and COOKIES_FILE.stat().st_size > 0
        if (download_timed_out or (download_result and download_result.returncode != 0)) and has_cookies:
            if download_timed_out or _should_retry_without_cookies(download_result.stderr):
                download_cmd_no_cookies = _strip_cookies_args(download_cmd)
                try:
                    download_result = subprocess.run(
                        download_cmd_no_cookies,
                        capture_output=True,
                        text=True,
                        timeout=TIMEOUT_YTDLP_DOWNLOAD
                    )
                    download_timed_out = False
                except subprocess.TimeoutExpired:
                    download_timed_out = True

        if download_timed_out:
            raise Exception("Download timed out (no progress)")

        if download_result.returncode != 0:
            error_msg = f"Download failed: {download_result.stderr}"
            if _is_ytdlp_403(download_result.stderr):
                has_cookies = COOKIES_FILE.exists() and COOKIES_FILE.stat().st_size > 0
                if has_cookies:
                    error_msg = "YouTube blocked this download (403). Your cookies may have expired — try re-exporting them in Settings."
                else:
                    error_msg = "YouTube blocked this download (403). Add browser cookies in Settings to authenticate."
            raise Exception(error_msg)

        # Find the downloaded file (extension depends on convert_to_flac setting)
        audio_file = None
        sanitized_title = sanitize_filename(title)
        for ext in ['.flac', '.opus', '.m4a', '.webm', '.mp3', '.ogg']:
            candidate = artist_dir / f"{sanitized_title}{ext}"
            if candidate.exists():
                audio_file = candidate
                break

        if audio_file:
            # Set permissions for NAS/SMB compatibility
            set_file_permissions(audio_file)

            # Try to enrich metadata with MusicBrainz
            mb_metadata = lookup_musicbrainz(artist, title)
            if mb_metadata:
                # Use MusicBrainz metadata
                apply_metadata_to_file(
                    audio_file,
                    mb_metadata.get("artist", artist),
                    mb_metadata.get("title", title),
                    mb_metadata.get("album", "Singles"),
                    mb_metadata.get("year")
                )
            else:
                # Use cleaned YouTube metadata
                apply_metadata_to_file(audio_file, artist, title, "Singles")

            # Fetch and save lyrics
            lyrics = fetch_lyrics(artist, title)
            if lyrics:
                save_lyrics_file(audio_file, lyrics)
                print(f"Saved lyrics for {artist} - {title}")
            else:
                print(f"No lyrics found for {artist} - {title}")

        # Trigger library rescans if configured
        trigger_navidrome_scan()
        trigger_jellyfin_scan()

        # Update job status
        conn.execute(
            "UPDATE jobs SET status = ?, error = NULL, completed_at = ? WHERE id = ?",
            ("completed", datetime.now().isoformat(), job_id)
        )
        conn.execute(
            "UPDATE watched_playlist_tracks SET downloaded_at = datetime('now') WHERE job_id = ?",
            (job_id,)
        )
        conn.commit()

        # Send Telegram notification for single track
        send_telegram_notification(
            notification_type="single",
            title=title,
            artist=artist,
            source="youtube",
            status="completed"
        )

    except Exception as e:
        conn.execute(
            "UPDATE jobs SET status = ?, error = ?, completed_at = ? WHERE id = ?",
            ("failed", str(e), datetime.now().isoformat(), job_id)
        )
        conn.commit()

        # Send Telegram notification for failure
        send_telegram_notification(
            notification_type="error",
            title=title if 'title' in dir() else video_id,
            artist=artist if 'artist' in dir() else None,
            source="youtube",
            status="failed",
            error=str(e)
        )

    finally:
        conn.close()


def trigger_navidrome_scan():
    """Trigger a Navidrome library scan via API"""
    navidrome_url = get_setting("navidrome_url")
    navidrome_user = get_setting("navidrome_user")
    navidrome_pass = get_setting("navidrome_pass")

    if not (navidrome_url and navidrome_user and navidrome_pass):
        return

    try:
        # Navidrome uses subsonic API
        salt = uuid.uuid4().hex[:8]
        token = hashlib.md5(f"{navidrome_pass}{salt}".encode()).hexdigest()

        params = {
            "u": navidrome_user,
            "t": token,
            "s": salt,
            "v": "1.16.1",
            "c": "music-grabber",
            "f": "json"
        }

        with httpx.Client(timeout=TIMEOUT_HTTP_REQUEST) as client:
            client.get(
                f"{navidrome_url}/rest/startScan",
                params=params
            )
    except Exception:
        pass  # Non-critical, scan will happen on schedule anyway


def trigger_jellyfin_scan():
    """Trigger a Jellyfin library scan via API"""
    jellyfin_url = get_setting("jellyfin_url")
    jellyfin_api_key = get_setting("jellyfin_api_key")

    if not (jellyfin_url and jellyfin_api_key):
        return

    try:
        with httpx.Client(timeout=TIMEOUT_HTTP_REQUEST) as client:
            client.post(
                f"{jellyfin_url}/Library/Refresh",
                headers={"X-Emby-Token": jellyfin_api_key}
            )
    except Exception:
        pass  # Non-critical, scan will happen on schedule anyway


def _build_notification_message(
    notification_type: str,
    title: str,
    artist: str = None,
    source: str = None,
    status: str = "completed",
    error: str = None,
    track_count: int = None,
    failed_count: int = None,
    skipped_count: int = None,
    playlist_name: str = None
) -> tuple[str, str]:
    """Build notification message text and subject line.

    Returns:
        Tuple of (message_body, subject_line)
    """
    if status == "failed":
        status_text = "[FAILED]"
    elif status == "completed_with_errors":
        status_text = "[PARTIAL]"
    else:
        status_text = "[OK]"

    lines = [f"MusicGrabber {status_text}"]
    subject = f"MusicGrabber {status_text}"

    if notification_type == "single":
        track_info = f"{artist} - {title}" if artist else title
        lines.append(track_info)
        subject = f"{subject} - {track_info}"
        if source:
            lines.append(f"Source: {source.capitalize()}")
    elif notification_type == "playlist":
        playlist_info = playlist_name or title
        lines.append(f"Playlist: {playlist_info}")
        subject = f"{subject} - Playlist: {playlist_info}"
        if track_count:
            summary_parts = [f"{track_count} tracks"]
            if failed_count:
                summary_parts.append(f"{failed_count} failed")
            if skipped_count:
                summary_parts.append(f"{skipped_count} skipped")
            lines.append(", ".join(summary_parts))
    elif notification_type == "bulk":
        lines.append(f"Bulk import: {title}")
        subject = f"{subject} - Bulk import"
        if track_count:
            summary_parts = [f"{track_count} tracks"]
            if failed_count:
                summary_parts.append(f"{failed_count} failed")
            if skipped_count:
                summary_parts.append(f"{skipped_count} skipped")
            lines.append(", ".join(summary_parts))

    if error:
        lines.append(f"Error: {error}")

    return "\n".join(lines), subject


def _should_notify(notification_type: str, status: str, error: str = None) -> bool:
    """Check if notifications should be sent for this type."""
    notify_on = get_setting("notify_on", "playlists,bulk,errors")
    enabled_types = [t.strip().lower() for t in notify_on.split(",")]

    type_map = {
        "single": "singles",
        "playlist": "playlists",
        "bulk": "bulk",
        "error": "errors"
    }

    config_type = type_map.get(notification_type, notification_type)
    is_error = status == "failed" or error

    return config_type in enabled_types or (is_error and "errors" in enabled_types)


def _send_telegram(message: str):
    """Send notification via Telegram webhook."""
    telegram_url = get_setting("telegram_webhook_url")
    if not telegram_url:
        return

    try:
        with httpx.Client(timeout=TIMEOUT_HTTP_REQUEST) as client:
            client.post(telegram_url, json={"text": message})
    except Exception:
        pass


def _send_email(subject: str, message: str):
    """Send notification via SMTP email."""
    smtp_host = get_setting("smtp_host")
    smtp_to = get_setting("smtp_to")

    if not smtp_host or not smtp_to:
        return

    smtp_port = get_setting_int("smtp_port", 587)
    smtp_user = get_setting("smtp_user")
    smtp_pass = get_setting("smtp_pass")
    smtp_from = get_setting("smtp_from")
    smtp_tls = get_setting_bool("smtp_tls", True)

    try:
        msg = MIMEText(message)
        msg["Subject"] = subject
        msg["From"] = smtp_from or smtp_user
        msg["To"] = smtp_to

        if smtp_tls:
            server = smtplib.SMTP(smtp_host, smtp_port)
            server.starttls()
        else:
            server = smtplib.SMTP(smtp_host, smtp_port)

        if smtp_user and smtp_pass:
            server.login(smtp_user, smtp_pass)

        server.sendmail(msg["From"], smtp_to.split(","), msg.as_string())
        server.quit()
    except Exception:
        pass


def send_telegram_notification(
    notification_type: str,
    title: str,
    artist: str = None,
    source: str = None,
    status: str = "completed",
    error: str = None,
    track_count: int = None,
    failed_count: int = None,
    skipped_count: int = None,
    playlist_name: str = None
):
    """Send notifications to all configured channels (Telegram, Email).

    Args:
        notification_type: One of 'single', 'playlist', 'bulk', 'error'
        title: Track title or import/playlist name
        artist: Artist name (for singles)
        source: Download source (youtube/soulseek)
        status: Job status (completed/failed/completed_with_errors)
        error: Error message if failed
        track_count: Total tracks (for playlists/bulk)
        failed_count: Number of failed tracks
        skipped_count: Number of skipped tracks
        playlist_name: Name of playlist (for playlist downloads)
    """
    if not _should_notify(notification_type, status, error):
        return

    message, subject = _build_notification_message(
        notification_type, title, artist, source, status,
        error, track_count, failed_count, skipped_count, playlist_name
    )

    _send_telegram(message)
    _send_email(subject, message)


def create_bulk_playlist(bulk_import_id: str, playlist_name: str, expected_count: int):
    """Create an M3U playlist from a bulk import after all downloads complete

    Waits for all jobs with the matching playlist_name to complete, then generates the M3U file.
    """
    # Wait for all downloads to complete (with timeout)
    max_wait_time = PLAYLIST_WAIT_MAX
    check_interval = PLAYLIST_WAIT_INTERVAL
    waited = 0

    while waited < max_wait_time:
        conn = get_db()
        cursor = conn.execute(
            "SELECT COUNT(*) as total, SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed FROM jobs WHERE playlist_name = ?",
            (bulk_import_id,)
        )
        row = cursor.fetchone()
        conn.close()

        total, completed = row
        completed = completed or 0

        # All downloads complete
        if completed >= expected_count or total == completed:
            break

        time.sleep(check_interval)
        waited += check_interval

    # Gather all successfully downloaded files
    conn = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(
        "SELECT artist, title FROM jobs WHERE playlist_name = ? AND status = 'completed' AND error IS NULL ORDER BY created_at",
        (bulk_import_id,)
    )
    jobs = [dict(row) for row in cursor.fetchall()]
    conn.close()

    if not jobs:
        return  # No successful downloads

    # Build M3U playlist
    playlist_files = []
    for job in jobs:
        artist = job.get("artist", "Unknown")
        title = job.get("title", "Unknown")

        # Construct expected file path (any supported format)
        artist_dir = SINGLES_DIR / sanitize_filename(artist)
        audio_file = None
        for ext in ['.flac', '.opus', '.m4a', '.webm', '.mp3', '.ogg']:
            candidate = artist_dir / f"{sanitize_filename(title)}{ext}"
            if candidate.exists():
                audio_file = candidate
                break

        if audio_file:
            # Store relative path from Singles directory
            rel_path = audio_file.relative_to(SINGLES_DIR)
            playlist_files.append(str(rel_path))

    if playlist_files:
        # Create M3U file
        m3u_path = SINGLES_DIR / f"{sanitize_filename(playlist_name)}.m3u"
        with open(m3u_path, 'w', encoding='utf-8') as f:
            f.write("#EXTM3U\n")
            for file_path in playlist_files:
                f.write(f"{file_path}\n")
        set_file_permissions(m3u_path)


@app.get("/api/jobs")
def get_jobs(limit: int = 20):
    """Get recent jobs"""
    conn = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(
        "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?",
        (limit,)
    )
    jobs = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return {"jobs": jobs}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    """Get a specific job"""
    conn = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Job not found")

    return dict(row)


@app.post("/api/jobs/{job_id}/retry")
def retry_job(job_id: str, background_tasks: BackgroundTasks):
    """Retry a failed job"""
    conn = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
    row = cursor.fetchone()

    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Job not found")

    job = dict(row)

    # Only allow retrying failed jobs
    if job["status"] != "failed":
        conn.close()
        raise HTTPException(status_code=400, detail="Only failed jobs can be retried")

    # Reset job status
    conn.execute(
        "UPDATE jobs SET status = ?, error = NULL, completed_at = NULL WHERE id = ?",
        ("queued", job_id)
    )
    conn.commit()
    conn.close()

    # Re-queue the job based on source type
    convert_to_flac = bool(job.get("convert_to_flac", 1))

    if job["download_type"] == "playlist":
        background_tasks.add_task(process_playlist_download, job_id, job["video_id"], job["playlist_name"], convert_to_flac)
    elif job.get("source") == "soulseek" and job.get("slskd_username") and job.get("slskd_filename"):
        # Retry Soulseek download with stored metadata
        background_tasks.add_task(
            process_slskd_download,
            job_id,
            job["slskd_username"],
            job["slskd_filename"],
            job.get("artist", ""),
            job.get("title", ""),
            convert_to_flac
        )
    else:
        background_tasks.add_task(process_download, job_id, job["video_id"], convert_to_flac)

    return {"job_id": job_id, "status": "queued"}


@app.delete("/api/jobs/cleanup")
def cleanup_jobs(status: Optional[str] = None):
    """Delete completed, failed, or stale jobs

    Args:
        status: Optional filter - 'completed', 'failed', 'stale', or None for all non-active
    """
    conn = get_db()

    # First, mark any stale jobs as failed so they get cleaned up
    cleanup_stale_jobs()

    if status == "completed":
        cursor = conn.execute("DELETE FROM jobs WHERE status IN ('completed', 'completed_with_errors')")
    elif status == "failed":
        cursor = conn.execute("DELETE FROM jobs WHERE status = 'failed'")
    elif status == "stale":
        # Force-remove anything still stuck in downloading/queued regardless of age
        cursor = conn.execute("DELETE FROM jobs WHERE status IN ('downloading', 'queued')")
    else:
        cursor = conn.execute("DELETE FROM jobs WHERE status IN ('completed', 'completed_with_errors', 'failed')")

    deleted_count = cursor.rowcount
    conn.commit()
    conn.close()

    return {"deleted": deleted_count}


def clean_bulk_import_line(line: str) -> str:
    """Clean a line from bulk import text

    Removes common prefixes like:
    - Numbers: "1.", "1)", "01."
    - Bullets: "•", "-", "*"
    - Comments: "#"
    - Extra whitespace and tabs
    """
    # Strip whitespace
    line = line.strip()

    # Skip comments
    if line.startswith('#'):
        return ""

    # Remove common list prefixes: "1. ", "1) ", "01. ", etc.
    line = re.sub(r'^\d+[\.\)]\s*', '', line)

    # Remove bullet points at start
    line = re.sub(r'^[•\-\*]\s*', '', line)

    # Remove common music symbols
    line = re.sub(r'[♫♪🎵🎶]', '', line)

    # Normalise multiple spaces/tabs to single space
    line = re.sub(r'\s+', ' ', line)

    return line.strip()


def start_bulk_import_for_tracks(
    tracks: list[tuple[str, str]],
    convert_to_flac: bool,
    watch_playlist_id: Optional[str] = None,
) -> str:
    """Create a bulk import job from a list of (artist, title) tuples."""
    import_id = str(uuid.uuid4())[:8]

    conn = get_db()
    conn.execute(
        """INSERT INTO bulk_imports
           (id, status, total_tracks, create_playlist, playlist_name, convert_to_flac, watch_playlist_id)
           VALUES (?, 'pending', ?, 0, NULL, ?, ?)""",
        (import_id, len(tracks), int(convert_to_flac), watch_playlist_id)
    )

    for line_num, (artist, song) in enumerate(tracks, 1):
        conn.execute(
            "INSERT INTO bulk_import_tracks (import_id, line_num, artist, song, status) VALUES (?, ?, ?, ?, 'pending')",
            (import_id, line_num, artist, song)
        )

    conn.commit()
    conn.close()

    worker_thread = threading.Thread(target=process_bulk_import_worker, args=(import_id,))
    worker_thread.daemon = True
    worker_thread.start()

    return import_id


def process_bulk_import_worker(import_id: str):
    """Background worker to process bulk import tracks one by one

    Handles rate limiting with exponential backoff:
    - 1 second delay between searches
    - On 429: wait 30s, then 60s, then 120s
    - Tracks progress in database for resilience
    """
    conn = get_db()
    conn.row_factory = sqlite3.Row

    # Get import details
    cursor = conn.execute("SELECT * FROM bulk_imports WHERE id = ?", (import_id,))
    import_row = cursor.fetchone()
    if not import_row:
        conn.close()
        return

    convert_to_flac = bool(import_row["convert_to_flac"])
    create_playlist = bool(import_row["create_playlist"])
    playlist_name = import_row["playlist_name"]
    watch_playlist_id = import_row["watch_playlist_id"]

    # Update status to processing
    conn.execute("UPDATE bulk_imports SET status = 'processing' WHERE id = ?", (import_id,))
    conn.commit()

    # Rate limiting state
    base_delay = BULK_IMPORT_SEARCH_DELAY
    backoff_delays = BULK_IMPORT_BACKOFF_DELAYS
    current_backoff_index = 0
    consecutive_successes = 0

    try:
        while True:
            # Get next pending track
            cursor = conn.execute(
                "SELECT * FROM bulk_import_tracks WHERE import_id = ? AND status = 'pending' ORDER BY line_num LIMIT 1",
                (import_id,)
            )
            track = cursor.fetchone()

            if not track:
                # No more pending tracks - we're done
                break

            track_id = track["id"]
            artist = track["artist"]
            song = track["song"]
            line_num = track["line_num"]

            # Mark track as searching
            conn.execute("UPDATE bulk_import_tracks SET status = 'searching' WHERE id = ?", (track_id,))
            conn.commit()

            # Search for the song
            try:
                search_query = f"{artist} {song}"
                cmd = [
                    "yt-dlp",
                    *_ytdlp_base_args(),
                    "--dump-json",
                    "--flat-playlist",
                    "--no-warnings",
                    f"ytsearch10:{search_query}",
                ]

                result = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_YTDLP_SEARCH)

                # Check for rate limiting (429 in stderr)
                if "429" in result.stderr or "Too Many Requests" in result.stderr:
                    # Rate limited - apply backoff
                    delay = backoff_delays[min(current_backoff_index, len(backoff_delays) - 1)]
                    current_backoff_index += 1
                    consecutive_successes = 0

                    # Update import with rate limit info
                    rate_limited_until = datetime.now().isoformat()
                    conn.execute(
                        "UPDATE bulk_imports SET rate_limited_until = ? WHERE id = ?",
                        (rate_limited_until, import_id)
                    )
                    conn.execute("UPDATE bulk_import_tracks SET status = 'pending' WHERE id = ?", (track_id,))
                    conn.commit()

                    time.sleep(delay)
                    continue

                # Success - reset backoff
                consecutive_successes += 1
                if consecutive_successes >= BULK_IMPORT_BACKOFF_RESET_AFTER:
                    current_backoff_index = max(0, current_backoff_index - 1)
                    consecutive_successes = 0

                # Clear rate limit flag
                conn.execute("UPDATE bulk_imports SET rate_limited_until = NULL WHERE id = ?", (import_id,))

                if result.returncode != 0 or not result.stdout.strip():
                    # Search failed
                    conn.execute(
                        "UPDATE bulk_import_tracks SET status = 'failed', error = ? WHERE id = ?",
                        ("No results found", track_id)
                    )
                    conn.execute(
                        "UPDATE bulk_imports SET searched = searched + 1, failed = failed + 1 WHERE id = ?",
                        (import_id,)
                    )
                    conn.commit()
                    time.sleep(base_delay)
                    continue

                # Parse results and find best match
                search_results = []
                for search_line in result.stdout.strip().split('\n'):
                    if not search_line:
                        continue
                    try:
                        data = json.loads(search_line)
                        title = data.get("title", "")
                        channel = data.get("channel", data.get("uploader", ""))
                        video_id = data.get("id")

                        if video_id:
                            score = score_search_result(title, channel)
                            search_results.append({
                                "video_id": video_id,
                                "title": title,
                                "channel": channel,
                                "score": score
                            })
                    except json.JSONDecodeError:
                        continue

                if not search_results:
                    conn.execute(
                        "UPDATE bulk_import_tracks SET status = 'failed', error = ? WHERE id = ?",
                        ("No valid results", track_id)
                    )
                    conn.execute(
                        "UPDATE bulk_imports SET searched = searched + 1, failed = failed + 1 WHERE id = ?",
                        (import_id,)
                    )
                    conn.commit()
                    time.sleep(base_delay)
                    continue

                # Sort by score and pick best
                search_results.sort(key=lambda x: x["score"], reverse=True)
                best_match = search_results[0]
                video_id = best_match["video_id"]

                # Create download job
                job_id = str(uuid.uuid4())[:8]

                if create_playlist:
                    conn.execute(
                        "INSERT INTO jobs (id, video_id, title, artist, status, download_type, playlist_name, source, convert_to_flac) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (job_id, video_id, song, artist, "queued", "single", import_id, "youtube", int(convert_to_flac))
                    )
                else:
                    conn.execute(
                        "INSERT INTO jobs (id, video_id, title, artist, status, download_type, source, convert_to_flac) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (job_id, video_id, song, artist, "queued", "single", "youtube", int(convert_to_flac))
                    )

                # Update track as queued
                conn.execute(
                    "UPDATE bulk_import_tracks SET status = 'queued', job_id = ?, video_id = ? WHERE id = ?",
                    (job_id, video_id, track_id)
                )
                if watch_playlist_id:
                    track_hash = hash_track(artist, song)
                    conn.execute(
                        "UPDATE watched_playlist_tracks SET job_id = ? WHERE playlist_id = ? AND track_hash = ?",
                        (job_id, watch_playlist_id, track_hash)
                    )
                conn.execute(
                    "UPDATE bulk_imports SET searched = searched + 1, queued = queued + 1 WHERE id = ?",
                    (import_id,)
                )
                conn.commit()

                # Start the download in a thread
                download_thread = threading.Thread(
                    target=process_download,
                    args=(job_id, video_id, convert_to_flac)
                )
                download_thread.start()

            except subprocess.TimeoutExpired:
                conn.execute(
                    "UPDATE bulk_import_tracks SET status = 'failed', error = ? WHERE id = ?",
                    ("Search timeout", track_id)
                )
                conn.execute(
                    "UPDATE bulk_imports SET searched = searched + 1, failed = failed + 1 WHERE id = ?",
                    (import_id,)
                )
                conn.commit()

            except Exception as e:
                conn.execute(
                    "UPDATE bulk_import_tracks SET status = 'failed', error = ? WHERE id = ?",
                    (str(e)[:200], track_id)
                )
                conn.execute(
                    "UPDATE bulk_imports SET searched = searched + 1, failed = failed + 1 WHERE id = ?",
                    (import_id,)
                )
                conn.commit()

            # Standard delay between searches
            time.sleep(base_delay)

        # All tracks processed - mark import as complete
        conn.execute(
            "UPDATE bulk_imports SET status = 'completed', completed_at = CURRENT_TIMESTAMP WHERE id = ?",
            (import_id,)
        )
        conn.commit()

        # Get final counts for notification
        cursor = conn.execute(
            "SELECT total_tracks, queued, failed, skipped FROM bulk_imports WHERE id = ?",
            (import_id,)
        )
        final_row = cursor.fetchone()
        final_queued = final_row["queued"] if final_row else 0
        final_failed = final_row["failed"] if final_row else 0
        final_skipped = final_row["skipped"] if final_row else 0
        final_total = final_row["total_tracks"] if final_row else 0

        # Send Telegram notification for bulk import
        bulk_status = "completed_with_errors" if final_failed > 0 else "completed"
        send_telegram_notification(
            notification_type="bulk",
            title=playlist_name or f"Bulk import {import_id}",
            status=bulk_status,
            track_count=final_total,
            failed_count=final_failed,
            skipped_count=final_skipped
        )

        # Create playlist if requested
        if create_playlist:
            cursor = conn.execute("SELECT queued FROM bulk_imports WHERE id = ?", (import_id,))
            row = cursor.fetchone()
            if row and row["queued"] > 0:
                playlist_thread = threading.Thread(
                    target=create_bulk_playlist,
                    args=(import_id, playlist_name or f"Playlist {import_id}", row["queued"])
                )
                playlist_thread.start()

    except Exception as e:
        conn.execute(
            "UPDATE bulk_imports SET status = 'error', error = ? WHERE id = ?",
            (str(e)[:500], import_id)
        )
        conn.commit()

        # Send Telegram notification for bulk import failure
        send_telegram_notification(
            notification_type="error",
            title=playlist_name or f"Bulk import {import_id}",
            status="failed",
            error=str(e)
        )

    finally:
        conn.close()


@app.post("/api/bulk-import-async")
def bulk_import_async(request: AsyncBulkImportRequest):
    """Start an async bulk import job

    Returns immediately with import_id. Use /api/bulk-import/{id}/status to poll progress.
    Downloads start as soon as tracks are found, while searching continues in background.
    Multiple concurrent imports are supported - each has independent state in the database.
    """

    lines = request.songs.strip().split('\n')
    import_id = str(uuid.uuid4())[:8]

    # Parse and validate all lines first
    tracks_to_import = []
    for line_num, line in enumerate(lines, 1):
        original_line = line
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
    conn = get_db()
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
    conn.close()

    # Start background worker for this import
    worker_thread = threading.Thread(target=process_bulk_import_worker, args=(import_id,))
    worker_thread.daemon = True
    worker_thread.start()

    return {
        "import_id": import_id,
        "total_tracks": len(tracks_to_import),
        "status": "pending"
    }


@app.get("/api/bulk-import/{import_id}/status")
def get_bulk_import_status(import_id: str):
    """Get status of a bulk import job

    Returns progress info for polling UI updates.
    """
    conn = get_db()
    conn.row_factory = sqlite3.Row

    cursor = conn.execute("SELECT * FROM bulk_imports WHERE id = ?", (import_id,))
    import_row = cursor.fetchone()

    if not import_row:
        conn.close()
        raise HTTPException(status_code=404, detail="Import not found")

    # Get recent track statuses for display - show most recently processed first
    # Prioritise queued/failed over pending, then by line_num descending within processed
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

    conn.close()

    # "queued" from bulk_imports = tracks that were successfully searched
    # "still_queued" = tracks waiting to download (not yet completed or failed)
    # "failed" from bulk_imports = search failures
    # download_failed_count = download failures (separate from search failures)
    total_failed = import_row["failed"] + download_failed_count

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
        "complete": import_row["status"] in ("completed", "error")
    }


@app.get("/api/bulk-imports")
def list_bulk_imports(limit: int = 10):
    """List recent bulk imports"""
    conn = get_db()
    conn.row_factory = sqlite3.Row

    cursor = conn.execute(
        "SELECT * FROM bulk_imports ORDER BY created_at DESC LIMIT ?",
        (limit,)
    )
    imports = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return {"imports": imports}


@app.post("/api/spotify-playlist")
def fetch_spotify_playlist(request: SpotifyPlaylistRequest):
    """Fetch track list from a public Spotify playlist or album URL

    Uses Spotify's embed endpoint which contains track data in a parseable format.
    Returns tracks in "Artist - Song" format ready for bulk import.
    """
    # Validate and extract ID from URL - support both playlists and albums
    playlist_match = re.match(r'https?://open\.spotify\.com/playlist/([a-zA-Z0-9]+)', request.url)
    album_match = re.match(r'https?://open\.spotify\.com/album/([a-zA-Z0-9]+)', request.url)

    if playlist_match:
        spotify_id = playlist_match.group(1)
        spotify_type = "playlist"
    elif album_match:
        spotify_id = album_match.group(1)
        spotify_type = "album"
    else:
        raise HTTPException(status_code=400, detail="Invalid Spotify URL. Expected playlist or album URL.")

    # Fetch the embed page - this contains track data unlike the main page
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

    # Extract name - first "title" match is usually the playlist/album name
    playlist_name = f"Spotify {spotify_type.title()}"
    title_matches = re.findall(r'"title":"([^"]+)"', html_content)
    if title_matches:
        playlist_name = title_matches[0]

    # Extract tracks using title/subtitle pattern
    # The embed page has tracks as alternating "title":"SONG","subtitle":"ARTIST" pairs
    # We need to find these pairs and combine them
    tracks = []

    # Find all title and subtitle values
    titles = re.findall(r'"title":"([^"]+)"', html_content)
    subtitles = re.findall(r'"subtitle":"([^"]+)"', html_content)

    # Skip the first title (playlist name) and first subtitle (usually "Spotify")
    if len(titles) > 1 and len(subtitles) > 1:
        # The titles and subtitles should align: titles[1] is first track, subtitles[1] is its artist
        track_titles = titles[1:]  # Skip playlist name
        track_artists = subtitles[1:]  # Skip "Spotify"

        # Pair them up
        for title, artist in zip(track_titles, track_artists):
            # Decode unicode escapes like \u0026 -> & using json.loads (safest method)
            try:
                title = json.loads(f'"{title}"')
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass  # Keep original if decode fails
            try:
                artist = json.loads(f'"{artist}"')
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass  # Keep original if decode fails
            tracks.append(f"{artist} - {title}")

    if not tracks:
        raise HTTPException(
            status_code=422,
            detail=f"Could not extract tracks from {spotify_type}. It may be empty or Spotify's page structure may have changed."
        )

    # The embed endpoint only returns ~100 tracks max. If we got close to that limit,
    # the playlist may be truncated. Use headless browser to get the full list.
    if len(tracks) >= 95:
        print(f"Spotify embed returned {len(tracks)} tracks (near limit), trying headless browser...")

        try:
            browser_result = fetch_spotify_playlist_via_browser(spotify_id, spotify_type)
            if browser_result["count"] > len(tracks):
                print(f"Headless browser returned {browser_result['count']} tracks (embed had {len(tracks)})")
                return browser_result
        except HTTPException as e:
            print(f"Headless browser failed ({e.detail}), using embed results")
        except Exception as e:
            print(f"Headless browser error: {e}, using embed results")

        # If all methods failed or returned same count, return embed with warning
        return {
            "tracks": tracks,
            "playlist_name": playlist_name,
            "count": len(tracks),
            "warning": f"Playlist may be truncated at {len(tracks)} tracks. Full extraction failed."
        }

    return {
        "tracks": tracks,
        "playlist_name": playlist_name,
        "count": len(tracks)
    }


@app.post("/api/bulk-import")
def bulk_import(request: BulkImportRequest, background_tasks: BackgroundTasks):
    """Import a list of songs and automatically search/download them

    Accepts text in format:
    Artist - Song Title
    Artist – Song Title (with em dash)

    Handles common junk like numbered lists, bullets, comments, etc.

    Can optionally create an M3U playlist from the imported songs.

    Returns summary of queued jobs
    """
    lines = request.songs.strip().split('\n')
    queued_jobs = []
    failed_lines = []

    # Generate a bulk import ID if creating a playlist
    bulk_import_id = str(uuid.uuid4())[:8] if request.create_playlist else None

    for line_num, line in enumerate(lines, 1):
        original_line = line
        line = clean_bulk_import_line(line)

        # Skip empty or comment lines
        if not line:
            continue

        # Skip lines that are too long (likely not a song)
        if len(line) > 200:
            failed_lines.append({"line": line_num, "text": original_line[:100] + "...", "reason": "Line too long"})
            continue

        # Try to parse "Artist - Song" format (supporting different dash types)
        match = re.match(r'^(.+?)\s*[-–—]\s*(.+)$', line)
        if not match:
            failed_lines.append({"line": line_num, "text": original_line, "reason": "Invalid format (need: Artist - Song)"})
            continue

        artist, song = match.groups()
        artist = artist.strip()
        song = song.strip()

        # Validate artist and song aren't empty
        if not artist or not song:
            failed_lines.append({"line": line_num, "text": original_line, "reason": "Empty artist or song"})
            continue

        # Validate reasonable lengths
        if len(artist) < 1 or len(song) < 1:
            failed_lines.append({"line": line_num, "text": original_line, "reason": "Artist or song too short"})
            continue

        # Search for the song - fetch multiple results to find best match
        try:
            search_query = f"{artist} {song}"
            cmd = [
                "yt-dlp",
                *_ytdlp_base_args(),
                "--dump-json",
                "--flat-playlist",
                "--no-warnings",
                f"ytsearch10:{search_query}",  # Fetch 10 results to find best match
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_YTDLP_SEARCH)

            if result.returncode != 0:
                failed_lines.append({"line": line_num, "text": original_line, "reason": "Search failed"})
                continue

            # Parse all results and score them
            if not result.stdout.strip():
                failed_lines.append({"line": line_num, "text": original_line, "reason": "No results found"})
                continue

            search_results = []
            for search_line in result.stdout.strip().split('\n'):
                if not search_line:
                    continue
                try:
                    data = json.loads(search_line)
                    title = data.get("title", "")
                    channel = data.get("channel", data.get("uploader", ""))
                    video_id = data.get("id")

                    if video_id:
                        score = score_search_result(title, channel)
                        search_results.append({
                            "video_id": video_id,
                            "title": title,
                            "channel": channel,
                            "score": score
                        })
                except json.JSONDecodeError:
                    continue

            if not search_results:
                failed_lines.append({"line": line_num, "text": original_line, "reason": "No valid results"})
                continue

            # Sort by score and pick the best match
            search_results.sort(key=lambda x: x["score"], reverse=True)
            best_match = search_results[0]
            video_id = best_match["video_id"]

            # Create job
            job_id = str(uuid.uuid4())[:8]
            conn = get_db()

            # Store playlist info if creating a playlist
            if request.create_playlist:
                conn.execute(
                    "INSERT INTO jobs (id, video_id, title, artist, status, download_type, playlist_name, source, convert_to_flac) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (job_id, video_id, song, artist, "queued", "single", bulk_import_id, "youtube", int(request.convert_to_flac))
                )
            else:
                conn.execute(
                    "INSERT INTO jobs (id, video_id, title, artist, status, download_type, source, convert_to_flac) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (job_id, video_id, song, artist, "queued", "single", "youtube", int(request.convert_to_flac))
                )
            conn.commit()
            conn.close()

            # Queue download
            background_tasks.add_task(process_download, job_id, video_id, request.convert_to_flac)

            queued_jobs.append({
                "job_id": job_id,
                "artist": artist,
                "song": song,
                "video_id": video_id
            })

        except subprocess.TimeoutExpired:
            failed_lines.append({"line": line_num, "text": original_line, "reason": "Search timeout"})
        except Exception as e:
            failed_lines.append({"line": line_num, "text": original_line, "reason": str(e)})

    # Schedule playlist creation task if requested
    if request.create_playlist and queued_jobs:
        playlist_name = request.playlist_name or f"Playlist {bulk_import_id}"
        background_tasks.add_task(
            create_bulk_playlist,
            bulk_import_id,
            playlist_name,
            len(queued_jobs)
        )

    return {
        "queued": len(queued_jobs),
        "failed": len(failed_lines),
        "jobs": queued_jobs,
        "failures": failed_lines,
        "playlist_id": bulk_import_id if request.create_playlist else None
    }


# =============================================================================
# Watched Playlists API
# =============================================================================

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

    # YouTube playlist
    youtube_playlist = re.match(r'https?://(www\.)?(youtube\.com|youtu\.be)/playlist\?list=([a-zA-Z0-9_-]+)', url)
    if youtube_playlist:
        return "youtube", youtube_playlist.group(3)

    raise HTTPException(status_code=400, detail="Invalid playlist URL. Supported: Spotify playlists/albums, YouTube playlists.")


def fetch_playlist_tracks(url: str, platform: str) -> tuple[list[tuple[str, str]], str]:
    """Fetch tracks from a playlist URL

    Returns (list of (artist, title) tuples, playlist_name)
    """
    if platform == "spotify":
        # Reuse existing Spotify fetch logic
        request = SpotifyPlaylistRequest(url=url)
        result = fetch_spotify_playlist(request)

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
        playlist_id = re.search(r'list=([a-zA-Z0-9_-]+)', url).group(1)

        info_cmd = [
            "yt-dlp",
            *_ytdlp_base_args(),
            "--dump-json",
            "--flat-playlist",
            "--no-warnings",
            f"https://www.youtube.com/playlist?list={playlist_id}"
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
                    artist, clean_title = extract_artist_title(title, channel)
                    tracks.append((artist, clean_title))
            except json.JSONDecodeError:
                continue

        if not tracks:
            raise HTTPException(status_code=422, detail="No tracks found in YouTube playlist")

        return tracks, playlist_name

    raise HTTPException(status_code=400, detail=f"Unsupported platform: {platform}")


def refresh_watched_playlist(playlist_id: str) -> dict:
    """Fetch playlist and queue any new tracks for download

    Returns dict with refresh results
    """
    conn = get_db()
    conn.row_factory = sqlite3.Row

    playlist = conn.execute(
        "SELECT * FROM watched_playlists WHERE id = ?", (playlist_id,)
    ).fetchone()

    if not playlist:
        conn.close()
        return {"error": "Playlist not found", "playlist_id": playlist_id}

    playlist = dict(playlist)

    try:
        # Fetch current tracks
        tracks, _ = fetch_playlist_tracks(playlist["url"], playlist["platform"])

        # Load existing track state (including job status)
        track_rows = conn.execute(
            """SELECT wpt.track_hash, wpt.downloaded_at, wpt.job_id, j.status as job_status
               FROM watched_playlist_tracks wpt
               LEFT JOIN jobs j ON wpt.job_id = j.id
               WHERE wpt.playlist_id = ?""",
            (playlist_id,)
        ).fetchall()
        tracked = {row["track_hash"]: row for row in track_rows}

        new_tracks = []
        missing_tracks = []
        for artist, title in tracks:
            track_hash = hash_track(artist, title)
            existing = tracked.get(track_hash)
            if not existing:
                new_tracks.append((artist, title, track_hash))
                continue

            if existing["downloaded_at"]:
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

        # Insert any new tracks so they are tracked before download
        for artist, title, track_hash in new_tracks:
            conn.execute("""
                INSERT INTO watched_playlist_tracks
                (playlist_id, track_hash, artist, title)
                VALUES (?, ?, ?, ?)
            """, (playlist_id, track_hash, artist, title))

        tracks_to_import = [(artist, title) for artist, title, _ in new_tracks + missing_tracks]
        import_id = None
        if tracks_to_import:
            import_id = start_bulk_import_for_tracks(
                tracks_to_import,
                bool(playlist["convert_to_flac"]),
                watch_playlist_id=playlist_id
            )

        # Update playlist metadata
        conn.execute("""
            UPDATE watched_playlists
            SET last_checked = datetime('now'), last_track_count = ?
            WHERE id = ?
        """, (len(tracks), playlist_id))

        conn.commit()
        conn.close()

        queued_count = len(tracks_to_import)
        if queued_count:
            print(
                f"Watched playlist '{playlist['name']}': {len(new_tracks)} new tracks, "
                f"{len(missing_tracks)} missing tracks, {queued_count} queued"
            )

        return {
            "playlist_id": playlist_id,
            "name": playlist["name"],
            "total_tracks": len(tracks),
            "new_tracks": len(new_tracks),
            "missing_tracks": len(missing_tracks),
            "queued": queued_count,
            "import_id": import_id,
            "jobs": []
        }

    except HTTPException as e:
        conn.execute(
            "UPDATE watched_playlists SET last_checked = datetime('now') WHERE id = ?",
            (playlist_id,)
        )
        conn.commit()
        conn.close()
        return {
            "playlist_id": playlist_id,
            "name": playlist["name"],
            "error": e.detail
        }
    except Exception as e:
        conn.execute(
            "UPDATE watched_playlists SET last_checked = datetime('now') WHERE id = ?",
            (playlist_id,)
        )
        conn.commit()
        conn.close()
        return {
            "playlist_id": playlist_id,
            "name": playlist["name"],
            "error": str(e)
        }


@app.post("/api/watched-playlists")
def add_watched_playlist(request: WatchedPlaylistRequest):
    """Add a new playlist to watch for new tracks"""
    # Detect platform and validate URL
    platform, playlist_ext_id = detect_playlist_platform(request.url)

    # Check for duplicate
    conn = get_db()
    conn.row_factory = sqlite3.Row

    existing = conn.execute(
        "SELECT id FROM watched_playlists WHERE url = ?", (request.url,)
    ).fetchone()

    if existing:
        conn.close()
        raise HTTPException(status_code=409, detail="This playlist is already being watched")

    # Fetch playlist to get name and initial tracks
    try:
        tracks, playlist_name = fetch_playlist_tracks(request.url, platform)
    except HTTPException:
        conn.close()
        raise

    # Create playlist record
    playlist_id = str(uuid.uuid4())[:8]

    conn.execute("""
        INSERT INTO watched_playlists
        (id, url, name, platform, refresh_interval_hours, convert_to_flac, last_track_count)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (playlist_id, request.url, playlist_name, platform,
          request.refresh_interval_hours, int(request.convert_to_flac), len(tracks)))

    # Insert all current tracks as "seen" (downloads will be queued via bulk import)
    for artist, title in tracks:
        track_hash = hash_track(artist, title)
        conn.execute("""
            INSERT OR IGNORE INTO watched_playlist_tracks
            (playlist_id, track_hash, artist, title)
            VALUES (?, ?, ?, ?)
        """, (playlist_id, track_hash, artist, title))

    conn.commit()
    conn.close()

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
    conn = get_db()
    conn.row_factory = sqlite3.Row

    playlists = conn.execute("""
        SELECT
            wp.*,
            (SELECT COUNT(*) FROM watched_playlist_tracks wpt WHERE wpt.playlist_id = wp.id) as tracked_count,
            (SELECT COUNT(*) FROM watched_playlist_tracks wpt WHERE wpt.playlist_id = wp.id AND wpt.downloaded_at IS NOT NULL) as downloaded_count
        FROM watched_playlists wp
        ORDER BY wp.created_at DESC
    """).fetchall()

    conn.close()

    return {
        "playlists": [dict(p) for p in playlists]
    }


@app.get("/api/watched-playlists/{playlist_id}")
def get_watched_playlist(playlist_id: str):
    """Get details of a watched playlist including track history"""
    conn = get_db()
    conn.row_factory = sqlite3.Row

    playlist = conn.execute(
        "SELECT * FROM watched_playlists WHERE id = ?", (playlist_id,)
    ).fetchone()

    if not playlist:
        conn.close()
        raise HTTPException(status_code=404, detail="Watched playlist not found")

    tracks = conn.execute("""
        SELECT * FROM watched_playlist_tracks
        WHERE playlist_id = ?
        ORDER BY first_seen DESC
    """, (playlist_id,)).fetchall()

    conn.close()

    return {
        "playlist": dict(playlist),
        "tracks": [dict(t) for t in tracks]
    }


@app.put("/api/watched-playlists/{playlist_id}")
def update_watched_playlist(playlist_id: str, request: WatchedPlaylistUpdate):
    """Update watched playlist settings"""
    conn = get_db()
    conn.row_factory = sqlite3.Row

    playlist = conn.execute(
        "SELECT * FROM watched_playlists WHERE id = ?", (playlist_id,)
    ).fetchone()

    if not playlist:
        conn.close()
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

    conn.close()

    return {"playlist": dict(updated)}


@app.delete("/api/watched-playlists/{playlist_id}")
def delete_watched_playlist(playlist_id: str):
    """Remove a watched playlist and its track history"""
    conn = get_db()
    conn.row_factory = sqlite3.Row

    playlist = conn.execute(
        "SELECT * FROM watched_playlists WHERE id = ?", (playlist_id,)
    ).fetchone()

    if not playlist:
        conn.close()
        raise HTTPException(status_code=404, detail="Watched playlist not found")

    # Delete tracks first (FK constraint)
    conn.execute("DELETE FROM watched_playlist_tracks WHERE playlist_id = ?", (playlist_id,))
    conn.execute("DELETE FROM watched_playlists WHERE id = ?", (playlist_id,))
    conn.commit()
    conn.close()

    return {"message": f"Deleted watched playlist '{playlist['name']}'"}


@app.post("/api/watched-playlists/{playlist_id}/refresh")
def refresh_single_playlist(playlist_id: str):
    """Force an immediate refresh of a specific watched playlist"""
    conn = get_db()
    conn.row_factory = sqlite3.Row

    playlist = conn.execute(
        "SELECT * FROM watched_playlists WHERE id = ?", (playlist_id,)
    ).fetchone()

    if not playlist:
        conn.close()
        raise HTTPException(status_code=404, detail="Watched playlist not found")

    conn.close()

    result = refresh_watched_playlist(playlist_id)
    return result


@app.post("/api/watched-playlists/check-all")
def check_all_watched_playlists():
    """Check all playlists due for refresh (called by cron)

    Only refreshes playlists where:
    - enabled = 1
    - last_checked is NULL OR last_checked + refresh_interval_hours < now
    """
    conn = get_db()
    conn.row_factory = sqlite3.Row

    # Get playlists due for refresh
    playlists = conn.execute("""
        SELECT id, name FROM watched_playlists
        WHERE enabled = 1
        AND (last_checked IS NULL
             OR datetime(last_checked, '+' || refresh_interval_hours || ' hours') < datetime('now'))
    """).fetchall()

    conn.close()

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
            conn = get_db()
            conn.row_factory = sqlite3.Row

            playlists = conn.execute("""
                SELECT id, name FROM watched_playlists
                WHERE enabled = 1
                AND (last_checked IS NULL
                     OR datetime(last_checked, '+' || refresh_interval_hours || ' hours') < datetime('now'))
            """).fetchall()

            conn.close()

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
        sleep_seconds = WATCHED_PLAYLIST_CHECK_HOURS * 3600
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

    scheduler_thread = threading.Thread(target=watched_playlist_scheduler, daemon=True)
    scheduler_thread.start()


# Start scheduler on module load (runs in Docker)
start_scheduler()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
