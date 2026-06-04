#!/usr/bin/env python3
"""
Music Grabber - A self-hosted music acquisition service
Searches music sources, downloads best quality audio with optional conversion to FLAC/Opus/MP3, drops into Navidrome/Jellyfin library
"""

import contextlib
import json
import os
import re
import sqlite3
import subprocess
import tempfile
import uuid
import unicodedata
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
import httpx

from constants import (
    VERSION, MUSIC_DIR, DB_PATH, COOKIES_FILE, ROOT_PATH,
    TIMEOUT_LISTENBRAINZ,
    TIMEOUT_YTDLP_INFO,
    TIMEOUT_YTDLP_PREVIEW,
    TIMEOUT_SLSKD_SEARCH,
    WATCHED_PLAYLIST_CHECK_HOURS,
    SEARCH_LOG_RETENTION_DAYS,
    STALE_JOB_TIMEOUT,
    DOWNLOAD_TOKEN_TTL_SECONDS,
    AUDIO_EXTENSIONS,
)
from db import db_conn, init_db, start_stale_job_monitor, cleanup_stale_jobs, cleanup_old_search_logs, upsert_album_track_lock
from settings import (
    get_setting, get_setting_bool, set_setting, set_user_setting, get_singles_dir, get_playlists_dir,
    get_albums_dir,
    SETTINGS_SCHEMA, SENSITIVE_SETTINGS, USER_SETTINGS_KEYS, _get_typed_setting, _is_env_override,
)
from models import (
    SearchRequest, DownloadRequest, PlaylistFetchRequest,
    AsyncBulkImportRequest, WatchedPlaylistRequest, WatchedPlaylistUpdate,
    WatchedArtistRequest, WatchedArtistUpdate,
    SettingsUpdate, SearchResult, BlacklistRequest,
    TestSlskdRequest, TestNavidromeRequest, TestJellyfinRequest, TestLidarrRequest, TestYouTubeCookiesRequest,
    TestAppriseRequest, TestSpotifyCookiesRequest, RetryMissingTrackRequest, QueueMissingTrackCandidateRequest,
    AlbumDownloadRequest, ExploreRequest, PatchTagsRequest,
    LoginRequest, ChangePasswordRequest, CreateUserRequest,
    SetUserPasswordRequest, SetUserRoleRequest,
    DownloadTokenRequest,
)
from middleware import AuthMiddleware, invalidate_users_cache
from auth import (
    verify_password, create_session, delete_session, get_user_by_username,
    get_user_by_id, list_users, create_user, update_password, delete_user,
    clear_failed_login, create_download_token, is_login_allowed,
    password_hash_for_timing, record_failed_login,
)
from youtube import (
    _has_valid_cookie_entries, _cookie_lines_for_domain_check, _sync_cookies_file,
    _ytdlp_base_args, _is_ytdlp_403, parse_duration,
    get_cookies_expiry,
)
from search import search_source, search_all, get_available_sources, SOURCE_REGISTRY
import servicecheck
from slskd import slskd_enabled, search_slskd
from downloads import (
    process_download, process_playlist_download, process_slskd_download,
    rebuild_watched_playlist_m3u, rebuild_album_m3u,
    trigger_navidrome_scan, trigger_jellyfin_scan,
)
from bulk_import import clean_bulk_import_line, start_bulk_import_for_tracks, process_bulk_import_worker
from watched_playlists import (
    detect_playlist_platform, fetch_playlist_tracks, refresh_watched_playlist,
    fetch_listenbrainz_createdfor, start_scheduler,
)
from watched_artists import refresh_watched_artist, start_artist_scheduler
from upgrades import (
    start_upgrade_scheduler, run_scan_all, get_candidates,
    get_candidates_page, search_candidate, dismiss_candidate,
    perform_upgrade, perform_upgrade_all,
)
from metadata import search_artist_mbid, fetch_artist_albums, fetch_album_tracks, apply_metadata_to_file, guess_musicbrainz_tags, MusicBrainzUnavailable
from utils import clean_title, hash_track, is_valid_youtube_id, sanitize_filename, sanitize_playlist_name, set_file_permissions, spawn_daemon_thread, subsonic_auth_params
from coverart import fetch_cover_art_url

URL_BASED_SOURCES = {"soundcloud", "mp3phoenix", "zvu4no", "freemp3cloud", "monochrome"}
DIRECT_PREVIEW_SOURCES = {"mp3phoenix", "zvu4no", "freemp3cloud"}
MONOCHROME_PREVIEW_SOURCES = {"monochrome"}


def _request_root_path(request: Request | None = None) -> str:
    """Return the externally-visible app prefix, normalised like '/musicgrabber'."""
    raw = ""
    if request is not None:
        raw = (request.scope.get("root_path") or "").strip()
    raw = raw or ROOT_PATH
    if not raw or raw == "/":
        return ""
    return "/" + raw.strip("/")


def _app_path(path: str, request: Request | None = None) -> str:
    """Prefix an app-local path with the configured root path."""
    path = path if path.startswith("/") else f"/{path}"
    root_path = _request_root_path(request)
    return f"{root_path}{path}" if root_path else path


def _user_scope(user_id: str | None, is_admin: bool) -> tuple[str, tuple]:
    """Return a SQL WHERE fragment and params for scoping rows to the current user.

    Admins see their own rows plus legacy rows (user_id IS NULL) left over from
    single-user mode. Regular users see only their own rows — legacy rows are
    the admin's history, not theirs.

    Usage:
        frag, params = _user_scope(user_id, is_admin)
        conn.execute(f"SELECT ... FROM jobs WHERE {frag}", params)
    """
    if is_admin:
        return "(user_id = ? OR user_id IS NULL)", (user_id,)
    return "user_id = ?", (user_id,)


def _is_peon(request: Request) -> bool:
    user = getattr(request.state, "user", None) or {}
    return user.get("role") == "peon"


def _enforce_peon_format(request: Request, body) -> None:
    """Peons cannot toggle conversion on or off. Force their requests onto whatever
    the admin has set globally for default_convert_to_flac. Belt to the UI's braces:
    the toggle is hidden, but a peon with the dev tools open could still try to send
    their own value, so we overwrite it server-side."""
    if not _is_peon(request):
        return
    if hasattr(body, "convert_to_flac"):
        body.convert_to_flac = get_setting_bool("default_convert_to_flac", True)


# =============================================================================
# Application Setup
# =============================================================================

app = FastAPI(title="Music Grabber", version=VERSION, root_path=ROOT_PATH)
app.mount("/static", StaticFiles(directory="static"), name="static")

# Ensure directories exist
get_singles_dir().mkdir(parents=True, exist_ok=True)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# Initialise database and start background monitors
init_db()
cleanup_old_search_logs(SEARCH_LOG_RETENTION_DAYS)
start_stale_job_monitor()
start_scheduler()
start_artist_scheduler()
start_upgrade_scheduler()
servicecheck.start_health_checks()  # initial background health sweep at boot

# Sync cookies file from settings at startup
_sync_cookies_file()

# Register middleware
app.add_middleware(AuthMiddleware)


# =============================================================================
# Basic Routes
# =============================================================================

def _static_cache_bust() -> str:
    """Cache-bust token for static assets.

    VERSION alone is not enough: within a (DEV) cycle the version stays put while
    app.js / index.html change repeatedly, so the browser would happily serve a
    stale cached app.js against the same ?v=. We fold in the newest static-file
    mtime (set fresh by the Docker COPY on every build) so any change to the
    served files busts the cache, version bump or not.
    """
    try:
        static_dir = Path("static")
        names = ("app.js", "index.html", "release-notes.js")
        mtimes = [(static_dir / n).stat().st_mtime for n in names if (static_dir / n).exists()]
        stamp = int(max(mtimes)) if mtimes else 0
        return f"{VERSION}.{stamp}"
    except Exception:
        return VERSION


@app.get("/", response_class=HTMLResponse)
def root(request: Request):
    """Serve the main UI"""
    html = Path("static/index.html").read_text(encoding="utf-8")
    root_path = _request_root_path(request)
    html = html.replace("__ROOT_PATH__", root_path)
    # Cache-bust static assets so browser fetches fresh files after an update
    html = html.replace("__CACHE_BUST__", _static_cache_bust())
    return HTMLResponse(content=html)


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse("static/favicon.png", media_type="image/png")

def _is_volume_mounted() -> bool:
    """Check if the configured music directory appears to be a mounted volume.

    Compares device IDs: if the music dir is on a different device than /,
    it's likely a mounted volume. This helps detect misconfigured setups
    where users forgot to mount their music directory.
    """
    try:
        configured_music_dir = Path(get_setting("music_dir", str(MUSIC_DIR)))
        root_stat = os.stat("/")
        music_stat = os.stat(configured_music_dir)
        # Different device ID means it's a mount point
        return root_stat.st_dev != music_stat.st_dev
    except OSError:
        # Can't stat, assume it's fine
        return True


@app.get("/api/config")
def get_config(request: Request):
    """Expose server configuration and version for the UI"""
    api_key = get_setting("api_key", "")
    organise_by_artist = get_setting_bool("organise_by_artist", True)
    configured_music_dir = Path(get_setting("music_dir", str(MUSIC_DIR)))
    singles_dir = get_singles_dir()
    playlists_dir = get_playlists_dir()

    # Build human-readable example paths relative to the music root so the
    # frontend can show the user exactly where their files will land.
    try:
        singles_example = str(singles_dir.relative_to(configured_music_dir))
    except ValueError:
        singles_example = str(singles_dir)
    if organise_by_artist:
        singles_example += "/Artist Name"
    singles_example += "/Track Title.flac"

    playlists_example = None
    if playlists_dir:
        try:
            pl_rel = str(playlists_dir.relative_to(configured_music_dir))
        except ValueError:
            pl_rel = str(playlists_dir)
        playlists_example = f"{pl_rel}/Playlist Name/Artist - Title.flac"

    with db_conn() as conn:
        user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    # Multi-user mode kicks in only when there are 2+ accounts. A single-user
    # install with one account (the owner) stays login-free, same as no-account mode.
    users_exist = user_count >= 2

    return {
        "version": VERSION,
        "default_convert_to_flac": get_setting_bool("default_convert_to_flac", True),
        "audio_format": get_setting("audio_format", "flac"),
        "playlists_subdir": get_setting("playlists_subdir", ""),
        "organise_by_artist": organise_by_artist,
        "singles_path_example": singles_example,
        "playlists_path_example": playlists_example,
        "music_dir": get_setting("music_dir", str(MUSIC_DIR)),
        "root_path": _request_root_path(request),
        "auth_required": bool(api_key),
        "auth_mode": "session",
        "users_exist": users_exist,
        "volume_mounted": _is_volume_mounted(),
        "singles_only_mode": get_setting_bool("singles_only_mode", False),
    }


# =============================================================================
# Music directory listing (for subfolder picker)
# =============================================================================

@app.get("/api/music-dirs")
def list_music_dirs(path: str = "", recursive: bool = False, max_depth: int | None = None):
    """List subdirectories of MUSIC_DIR (or a subpath) for the subfolder picker.

    Returns directory names/paths only  -  no hidden/system dirs, sorted alphabetically.
    Filters out dotfiles and @-prefixed system dirs (Synology, etc.).
    The path parameter lets users browse deeper into the tree.
    Set recursive=true to list descendants as full paths relative to MUSIC_DIR.
    Optionally pass max_depth to cap recursive traversal depth.
    """
    # Sanitise: normalise separators, strip edges, reject traversal segments
    clean = path.strip().replace("\\", "/").strip("/")
    segments = [segment for segment in clean.split("/") if segment and segment != "."]
    if any(segment == ".." for segment in segments):
        raise HTTPException(status_code=400, detail="Path traversal not allowed")
    clean = "/".join(segments)

    configured_music_dir = Path(get_setting("music_dir", str(MUSIC_DIR)))
    target = configured_music_dir / clean if clean else configured_music_dir
    target_resolved = target.resolve()
    music_root = configured_music_dir.resolve()
    # Make sure we haven't escaped MUSIC_DIR
    try:
        target.resolve().relative_to(music_root)
    except ValueError:
        raise HTTPException(status_code=400, detail="Path outside music directory")

    if max_depth is not None and max_depth < 1:
        raise HTTPException(status_code=400, detail="max_depth must be >= 1")

    try:
        if recursive:
            dirs = []
            if target.is_dir():
                for dirpath, dirnames, _ in os.walk(target):
                    # Prune hidden/system directories at each level.
                    dirnames[:] = [
                        name for name in dirnames
                        if not name.startswith((".", "@"))
                    ]
                    rel_from_target = Path(dirpath).resolve().relative_to(target_resolved)
                    depth = len(rel_from_target.parts)
                    if max_depth is not None and depth >= max_depth:
                        dirnames[:] = []
                    rel = Path(dirpath).resolve().relative_to(music_root).as_posix()
                    if rel in {".", clean}:
                        continue
                    if max_depth is not None and depth > max_depth:
                        continue
                    dirs.append(rel)
            dirs.sort(key=str.casefold)
        else:
            dirs = sorted(
                [
                    d.name for d in target.iterdir()
                    if d.is_dir() and not d.name.startswith((".", "@"))
                ],
                key=str.casefold,
            )
    except FileNotFoundError:
        dirs = []

    return {
        "path": clean,
        "directories": dirs,
    }


# =============================================================================
# Playlist listing (watched + physical .m3u files)
# =============================================================================

@app.get("/api/playlists")
def list_playlists(http_request: Request):
    """List available playlists for the playlist routing selector.

    Returns watched playlists (from DB) and any physical .m3u files found in the
    Playlists directory. Watched playlists take priority if names collide.
    """
    user_id = getattr(http_request.state, "user_id", None)
    is_admin = getattr(http_request.state, "is_admin", False)
    results = {}

    # Physical .m3u files from Playlists dir (lowest priority)
    playlists_dir = get_playlists_dir(user_id=user_id)
    if playlists_dir and playlists_dir.exists():
        try:
            m3u_files = sorted(playlists_dir.rglob("*.m3u"), key=lambda p: p.stem.casefold())
        except OSError:
            m3u_files = []
        for m3u in m3u_files:
            name = m3u.stem
            results[name] = {
                "name": name,
                "is_watched": False,
                "watched_id": None,
                "platform": None,
            }

    # Watched playlists from DB (higher priority  -  overwrite any same-named .m3u entry)
    _scope_frag, _scope_params = _user_scope(user_id, is_admin)
    with db_conn() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT id, name, platform, sync_mode FROM watched_playlists WHERE {_scope_frag} ORDER BY name COLLATE NOCASE",
            _scope_params
        ).fetchall()

    for row in rows:
        results[row["name"]] = {
            "name": row["name"],
            "is_watched": True,
            "watched_id": row["id"],
            "platform": row["platform"],
            "sync_mode": row["sync_mode"] or "append",
        }

    return {"playlists": sorted(results.values(), key=lambda p: p["name"].casefold())}


# =============================================================================
# Auth & User Management Routes
# =============================================================================

@app.post("/api/auth/login")
def login(body: LoginRequest):
    allowed, _retry_after = is_login_allowed(body.username)
    if not allowed:
        # Keep the same generic response to avoid leaking account state.
        raise HTTPException(status_code=401, detail="Invalid username or password")

    user = get_user_by_username(body.username)
    password_ok = verify_password(body.password, password_hash_for_timing(user))
    if not user or not user.get("is_active") or not password_ok:
        record_failed_login(body.username)
        raise HTTPException(status_code=401, detail="Invalid username or password")

    clear_failed_login(body.username)
    token = create_session(user["id"])
    return {
        "token": token,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "role": user["role"],
            "force_password_change": bool(user["force_password_change"]),
        },
    }


@app.post("/api/auth/logout")
def logout(request: Request):
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
        if token:
            delete_session(token)
    return {"ok": True}


@app.get("/api/auth/me")
def get_me(request: Request):
    if request.state.user_id is None:
        return {"id": None, "username": "admin", "role": "admin", "force_password_change": False}
    return request.state.user


@app.post("/api/auth/download-token")
def issue_download_token(request: Request, body: DownloadTokenRequest):
    """Issue a short-lived single-use token for downloading one specific job."""
    job_id = (body.job_id or "").strip()
    if not job_id:
        raise HTTPException(status_code=400, detail="job_id is required")

    # Single-user mode has no session token in play; direct links are fine.
    if request.state.user_id is None:
        return {"url": _app_path(f"/api/jobs/{job_id}/download", request), "expires_in": 0}

    user_id = request.state.user_id
    is_admin = request.state.is_admin
    with db_conn() as conn:
        _scope_frag, _scope_params = _user_scope(user_id, is_admin)
        row = conn.execute(
            f"""SELECT 1 FROM jobs
                WHERE id = ?
                  AND status IN ('completed', 'completed_with_errors')
                  AND COALESCE(file_deleted, 0) = 0
                  AND {_scope_frag}
                LIMIT 1""",
            (job_id, *_scope_params),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")

    token = create_download_token(user_id, job_id)
    return {
        "url": _app_path(f"/api/jobs/{job_id}/download?download_token={token}", request),
        "expires_in": DOWNLOAD_TOKEN_TTL_SECONDS,
    }


@app.put("/api/auth/password")
def change_own_password(request: Request, body: ChangePasswordRequest):
    if request.state.user_id is None:
        raise HTTPException(status_code=400, detail="Password change not available in single-user mode")
    user = get_user_by_id(request.state.user_id)
    if not user or not verify_password(body.current_password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Current password incorrect")
    if not body.new_password or len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    current_token = None
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        current_token = auth_header[7:].strip() or None
    update_password(request.state.user_id, body.new_password, keep_session_token=current_token)
    return {"ok": True}


@app.get("/api/users")
def get_users(request: Request):
    if not request.state.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return {"users": list_users()}


@app.post("/api/users")
def create_new_user(request: Request, body: CreateUserRequest):
    if not request.state.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    if body.role not in ("admin", "user", "peon"):
        raise HTTPException(status_code=400, detail="Role must be 'admin', 'user', or 'peon'")
    if not body.username:
        raise HTTPException(status_code=400, detail="Username cannot be empty")
    if not body.password or len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    # First user must be admin — they inherit the single-user instance
    with db_conn() as conn:
        user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if user_count == 0 and body.role != "admin":
        raise HTTPException(status_code=400, detail="The first account must be an admin")
    first_user = user_count == 0
    # Adding the 2nd account flips us from single-user to session-required mode.
    # The current admin has no session, so subsequent requests will 401 — the
    # frontend uses this flag to bounce them to login instead of leaving the
    # user list stuck on the old single-user view.
    crosses_into_session_mode = user_count == 1
    try:
        new_id = create_user(body.username, body.password, body.role)
    except ValueError:
        raise HTTPException(status_code=409, detail="Username already exists")
    invalidate_users_cache()
    return {
        "ok": True,
        "user_id": new_id,
        "first_user": first_user,
        "requires_login": crosses_into_session_mode,
    }


@app.delete("/api/users/{user_id}")
def remove_user(request: Request, user_id: str):
    if not request.state.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    if user_id == request.state.user_id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    if not get_user_by_id(user_id):
        raise HTTPException(status_code=404, detail="User not found")
    delete_user(user_id)
    invalidate_users_cache()
    return {"ok": True}


@app.put("/api/users/{user_id}/password")
def admin_set_password(request: Request, user_id: str, body: SetUserPasswordRequest):
    if not request.state.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    if not get_user_by_id(user_id):
        raise HTTPException(status_code=404, detail="User not found")
    if not body.new_password or len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    update_password(user_id, body.new_password)
    return {"ok": True}


@app.put("/api/users/{user_id}/role")
def set_user_role(request: Request, user_id: str, body: SetUserRoleRequest):
    if not request.state.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    if body.role not in ("admin", "user", "peon"):
        raise HTTPException(status_code=400, detail="Role must be 'admin', 'user', or 'peon'")
    if not get_user_by_id(user_id):
        raise HTTPException(status_code=404, detail="User not found")
    if user_id == request.state.user_id:
        raise HTTPException(status_code=400, detail="Cannot change your own role")
    with db_conn() as conn:
        conn.execute("UPDATE users SET role = ? WHERE id = ?", (body.role, user_id))
        conn.commit()
    return {"ok": True}


@app.put("/api/users/{user_id}/force-password-change")
def flag_password_reset(request: Request, user_id: str):
    """Flag a user's account so they must set a new password on next login."""
    if not request.state.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    if user_id == request.state.user_id:
        raise HTTPException(status_code=400, detail="Cannot force-reset your own password")
    if not get_user_by_id(user_id):
        raise HTTPException(status_code=404, detail="User not found")
    with db_conn() as conn:
        conn.execute("UPDATE users SET force_password_change = 1 WHERE id = ?", (user_id,))
        # Also kill all their active sessions — they'll need to log in fresh and change immediately
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        conn.commit()
    return {"ok": True}


# =============================================================================
# Settings API
# =============================================================================

def _clear_spotify_cookies_expired(user_id: str | None) -> None:
    """Reset the expired flag after fresh Spotify cookies are saved."""
    if user_id:
        set_user_setting(user_id, "spotify_cookies_expired", "false")
    else:
        set_setting("spotify_cookies_expired", "false")


@app.get("/api/settings")
def get_settings(request: Request):
    """Get settings for the current user.

    Admins get the full schema (global settings with their user_settings merged on top).
    Regular users get only their user-scoped keys, falling back to the global value.
    """
    settings = {}
    env_overrides = []

    # Determine which keys to expose for this request.
    # Admins see everything; regular users see only their own slice.
    if request.state.is_admin:
        keys_to_return = SETTINGS_SCHEMA.keys()
    else:
        keys_to_return = USER_SETTINGS_KEYS

    user_id = request.state.user_id

    for key in keys_to_return:
        schema = SETTINGS_SCHEMA.get(key, {"type": "str", "default": "", "sensitive": False})
        # Use user_id so per-user overrides are applied where relevant
        value = _get_typed_setting(key, user_id=user_id)
        is_sensitive = schema.get("sensitive", False)

        # Only expose env-override information for keys the caller can see
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
def update_settings(updates: SettingsUpdate, request: Request):
    """Update settings. Only non-None values are updated. Returns updated settings.

    Per-user keys are written to user_settings (or global when in single-user mode).
    Global keys can only be written by admins. Peons are read-only — they inherit
    the admin's globals for everything and have no Settings UI to begin with.
    """
    # Peons have no business writing settings — Settings tab is hidden, but the
    # API endpoint stays open to other roles, so this is the belt to the UI's braces.
    user = getattr(request.state, "user", None) or {}
    if user.get("role") == "peon":
        raise HTTPException(status_code=403, detail="Read-only account")

    updated_keys = []
    user_id = request.state.user_id

    for key, value in updates.model_dump(exclude_none=True).items():
        if key not in SETTINGS_SCHEMA:
            continue

        # Env-var overrides are immutable; skip silently
        if _is_env_override(key):
            continue

        # Convert booleans to string for storage
        if isinstance(value, bool):
            value = "true" if value else "false"
        else:
            value = str(value)

        # Validate subfolder settings to keep writes under MUSIC_DIR.
        if key in ("singles_subdir", "playlists_subdir", "albums_subdir"):
            raw = value.strip().replace("\\", "/")
            if raw == "." or raw == "":
                value = raw
            else:
                parts = [
                    part.strip()
                    for part in raw.split("/")
                    if part.strip() and part.strip() != "."
                ]
                if any(part == ".." for part in parts):
                    raise HTTPException(status_code=400, detail=f"Invalid {key.replace('_', ' ')} path")
                default_subdir = {
                    "singles_subdir": "Singles",
                    "playlists_subdir": "Playlists",
                    "albums_subdir": "Albums",
                }[key]
                value = "/".join(parts) or default_subdir
                try:
                    configured_music_dir = Path(get_setting("music_dir", str(MUSIC_DIR)))
                    (configured_music_dir / value).resolve().relative_to(configured_music_dir.resolve())
                except ValueError:
                    raise HTTPException(status_code=400, detail=f"{key.replace('_', ' ')} must stay within music directory")

        # Only allow sensible chmod values; a free-text octal field is a footgun
        if key == "file_permissions" and value not in ("666", "777"):
            raise HTTPException(status_code=400, detail="file_permissions must be 666 or 777")

        # Validate cookie format before saving
        if key == "youtube_cookies" and value.strip() and not _has_valid_cookie_entries(value):
            raise HTTPException(
                status_code=400,
                detail="Invalid cookies format. Paste Netscape-format cookies.txt content."
            )
        if key == "spotify_cookies" and value.strip():
            if not _has_valid_cookie_entries(value):
                raise HTTPException(
                    status_code=400,
                    detail="Invalid cookies format. Paste Netscape-format cookies.txt content."
                )
            lines = _cookie_lines_for_domain_check(value)
            has_sp_dc = any(
                len(l.split("\t")) >= 7 and l.split("\t")[5] == "sp_dc"
                for l in lines if not l.startswith("#")
            )
            if not has_sp_dc:
                raise HTTPException(
                    status_code=400,
                    detail="No sp_dc cookie found. Export cookies from open.spotify.com while logged in."
                )

        # Route the write: user-scoped keys go to user_settings (or global in single-user mode);
        # global keys require admin privileges.
        if key in USER_SETTINGS_KEYS:
            if user_id is None:
                # Single-user mode: no user table row, fall back to global store
                set_setting(key, value)
            else:
                set_user_setting(user_id, key, value)
        else:
            # Global setting: only admins may touch these
            if not request.state.is_admin:
                continue
            set_setting(key, value)

        updated_keys.append(key)

    # Sync cookies file if YouTube cookies were updated
    if "youtube_cookies" in updated_keys:
        _sync_cookies_file()

    # Clear the expired flag when fresh Spotify cookies are saved
    if "spotify_cookies" in updated_keys:
        _clear_spotify_cookies_expired(user_id)

    return {
        "updated": updated_keys,
        "settings": get_settings(request)["settings"]
    }


# =============================================================================
# Settings Test Endpoints
# =============================================================================

@app.post("/api/settings/test/slskd")
def test_slskd_connection(http_request: Request, body: TestSlskdRequest = None):
    """Test connection to slskd server. Uses form values if provided, otherwise saved settings."""
    if not http_request.state.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    url = (body.url if body and body.url else None) or _get_typed_setting("slskd_url")
    user = (body.username if body and body.username else None) or _get_typed_setting("slskd_user")
    password = (body.password if body and body.password else None) or _get_typed_setting("slskd_pass")
    downloads_path = (body.downloads_path if body and body.downloads_path else None) or _get_typed_setting("slskd_downloads_path")

    if not url:
        return {"success": False, "message": "slskd URL not configured"}

    try:
        with httpx.Client(timeout=10) as client:
            auth_response = client.post(
                f"{url.rstrip('/')}/api/v0/session",
                json={"username": user, "password": password}
            )
            if auth_response.status_code == 200:
                if not downloads_path:
                    return {
                        "success": True,
                        "warning": True,
                        "message": "Connected to slskd. Search works. For downloads to import, slskd and MusicGrabber must point at the same folder on your server: mount slskd's completed-downloads directory into MusicGrabber at the same path, then enter that path in the field below. See the README for a step-by-step example.",
                    }
                path = Path(downloads_path).expanduser()
                if not path.exists():
                    return {
                        "success": True,
                        "warning": True,
                        "message": f"Connected to slskd, but downloads path is not visible to MusicGrabber: {downloads_path}",
                    }
                if not path.is_dir():
                    return {
                        "success": True,
                        "warning": True,
                        "message": f"Connected to slskd, but downloads path is not a directory: {downloads_path}",
                    }
                return {"success": True, "message": "Connected to slskd and downloads path is accessible"}
            else:
                return {"success": False, "message": f"Authentication failed: {auth_response.status_code}"}
    except httpx.TimeoutException:
        return {"success": False, "message": "Connection timed out"}
    except Exception as e:
        print(f"slskd connection test error: {type(e).__name__}: {e}")
        return {"success": False, "message": "Connection failed  -  check server logs for details"}


@app.post("/api/settings/test/navidrome")
def test_navidrome_connection(http_request: Request, body: TestNavidromeRequest = None):
    """Test connection to Navidrome server. Uses form values if provided, otherwise saved settings."""
    user_id = http_request.state.user_id
    is_admin = http_request.state.is_admin
    # Non-admins can only test their saved settings, not probe arbitrary URLs
    url = (body.url if body and body.url and is_admin else None) or _get_typed_setting("navidrome_url", user_id=user_id)
    user = (body.username if body and body.username and is_admin else None) or _get_typed_setting("navidrome_user", user_id=user_id)
    password = (body.password if body and body.password and is_admin else None) or _get_typed_setting("navidrome_pass", user_id=user_id)

    if not url:
        return {"success": False, "message": "Navidrome URL not configured"}

    try:
        params = subsonic_auth_params(user, password)

        with httpx.Client(timeout=10) as client:
            response = client.get(
                f"{url.rstrip('/')}/rest/ping",
                params=params
            )
            if response.status_code != 200:
                return {"success": False, "message": f"Connection failed: {response.status_code}"}

            data = response.json()
            if data.get("subsonic-response", {}).get("status") != "ok":
                return {"success": False, "message": "Authentication failed"}

            # Ping succeeded  -  now check and auto-enable real paths via Navidrome's native API.
            # Navidrome returns synthetic paths by default (Artist/Album/Track.mp3), which are
            # useless for M3U playlist entries. We auto-flip any MusicGrabber player records we
            # find that have reportRealPath=false so users don't have to do it manually.
            real_path_enabled = False
            real_path_auto_fixed = False
            try:
                auth_resp = client.post(
                    f"{url.rstrip('/')}/auth/login",
                    json={"username": user, "password": password}
                )
                if auth_resp.status_code == 200:
                    token = auth_resp.json().get("token")
                    if token:
                        nd_headers = {"X-ND-Authorization": f"Bearer {token}"}
                        players_resp = client.get(
                            f"{url.rstrip('/')}/api/player",
                            headers=nd_headers
                        )
                        if players_resp.status_code == 200:
                            for player in players_resp.json():
                                if "musicgrabber" not in (player.get("client") or "").lower():
                                    continue
                                if player.get("reportRealPath"):
                                    real_path_enabled = True
                                else:
                                    # Auto-enable real paths for this player
                                    updated = {**player, "reportRealPath": True}
                                    put_resp = client.put(
                                        f"{url.rstrip('/')}/api/player/{player['id']}",
                                        json=updated,
                                        headers=nd_headers
                                    )
                                    if put_resp.status_code == 200:
                                        real_path_enabled = True
                                        real_path_auto_fixed = True
            except Exception as e:
                print(f"Navidrome real-path check failed: {type(e).__name__}: {e}")  # Non-critical

            if real_path_enabled:
                msg = ("Connected to Navidrome successfully. Real file paths auto-enabled for MusicGrabber."
                       if real_path_auto_fixed else "Connected to Navidrome successfully")
                return {
                    "success": True,
                    "message": msg,
                    "real_path": True
                }
            return {
                "success": True,
                "message": "Connected to Navidrome successfully",
                "real_path": False,
                "real_path_hint": (
                    "Real file paths could not be enabled automatically. Without them, Navidrome "
                    "duplicate detection still works but M3U playlist entries cannot be populated "
                    "for Navidrome-only tracks. To fix this manually, add "
                    "ND_SUBSONIC_DEFAULTREPORTREALPATH=true to your Navidrome docker-compose "
                    "environment, or enable \"Report real path\" for MusicGrabber in "
                    "Navidrome admin > Players."
                )
            }
    except httpx.TimeoutException:
        return {"success": False, "message": "Connection timed out"}
    except Exception as e:
        print(f"Navidrome connection test error: {type(e).__name__}: {e}")
        return {"success": False, "message": "Connection failed  -  check server logs for details"}


@app.post("/api/settings/test/jellyfin")
def test_jellyfin_connection(http_request: Request, body: TestJellyfinRequest = None):
    """Test connection to Jellyfin server. Uses form values if provided, otherwise saved settings."""
    user_id = http_request.state.user_id
    is_admin = http_request.state.is_admin
    # Non-admins can only test their saved settings, not probe arbitrary URLs
    url = (body.url if body and body.url and is_admin else None) or _get_typed_setting("jellyfin_url", user_id=user_id)
    api_key = (body.api_key if body and body.api_key and is_admin else None) or _get_typed_setting("jellyfin_api_key", user_id=user_id)

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
        print(f"Jellyfin connection test error: {type(e).__name__}: {e}")
        return {"success": False, "message": "Connection failed  -  check server logs for details"}


@app.post("/api/settings/test/lidarr")
def test_lidarr_connection(http_request: Request, body: TestLidarrRequest = None):
    """Test connection to Lidarr. Uses form values if provided, otherwise saved settings."""
    user_id = http_request.state.user_id
    is_admin = http_request.state.is_admin
    # Non-admins can only test their saved settings, not probe arbitrary URLs
    url = (body.url if body and body.url and is_admin else None) or _get_typed_setting("lidarr_url", user_id=user_id)
    api_key = (body.api_key if body and body.api_key and is_admin else None) or _get_typed_setting("lidarr_api_key", user_id=user_id)

    if not url:
        return {"success": False, "message": "Lidarr URL not configured"}
    if not api_key:
        return {"success": False, "message": "Lidarr API key not configured"}

    try:
        with httpx.Client(timeout=10) as client:
            response = client.get(
                f"{url.rstrip('/')}/api/v1/system/status",
                headers={"X-Api-Key": api_key}
            )
            if response.status_code == 200:
                data = response.json()
                version = data.get("version", "unknown")
                artist_resp = client.get(
                    f"{url.rstrip('/')}/api/v1/artist",
                    headers={"X-Api-Key": api_key}
                )
                artist_count = len(artist_resp.json()) if artist_resp.status_code == 200 else 0
                return {"success": True, "message": f"Connected to Lidarr v{version} ({artist_count} artists)"}
            elif response.status_code == 401:
                return {"success": False, "message": "Invalid API key"}
            else:
                return {"success": False, "message": f"Connection failed: {response.status_code}"}
    except httpx.TimeoutException:
        return {"success": False, "message": "Connection timed out"}
    except Exception as e:
        print(f"Lidarr connection test error: {type(e).__name__}: {e}")
        return {"success": False, "message": "Connection failed  -  check server logs for details"}


@app.post("/api/settings/test/youtube-cookies")
def test_youtube_cookies(http_request: Request, body: TestYouTubeCookiesRequest = None):
    """Test YouTube cookies by fetching info for a known public video.
    Uses form value if provided, otherwise the saved cookies."""
    user_id = http_request.state.user_id
    cookies_text = (body.cookies if body and body.cookies else None)
    if cookies_text is None:
        cookies_text = get_setting("youtube_cookies", "", user_id=user_id)

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

        # Use the YouTube Music Charts page as the test target. It's public, globally
        # available, and yt-dlp extracts it as a flat playlist  -  so we get real output
        # to confirm yt-dlp + cookies are working together without needing age-restricted
        # content or account-specific URLs. If cookies are broken they'll cause a 403 here
        # too; if they're fine, we get tracks back and can report success confidently.
        # A plain public video would also work but gives no way to distinguish "cookies
        # loaded fine" from "cookies were silently ignored".
        TEST_URL = "https://music.youtube.com/playlist?list=PLFgquLnL59alCl_2TQvOiD5Vgm1hCaGSI"

        test_cmd = [
            "yt-dlp",
            "--cookies", tmp_path,
            "--flat-playlist",
            "--dump-json",
            "--no-warnings",
            "--playlist-items", "1",
            TEST_URL,
        ]

        result = subprocess.run(test_cmd, capture_output=True, text=True, timeout=TIMEOUT_YTDLP_INFO)
        stderr = result.stderr.lower()

        if result.returncode == 0 and result.stdout.strip():
            return {"success": True, "message": "Cookies loaded and working"}

        if _is_ytdlp_403(result.stderr):
            return {"success": False, "message": "Cookies rejected by YouTube (403). They may be expired  -  try re-exporting."}
        if "sign in" in stderr or "login" in stderr or "not logged in" in stderr:
            return {"success": False, "message": "Cookies rejected  -  YouTube says you're not logged in. Re-export cookies while logged in."}

        # Test playlist unavailable or yt-dlp returned nothing  -  not necessarily a cookie problem
        print(f"Cookie test: test URL failed or returned no output. stderr={result.stderr[:300]!r}")
        return {"success": True, "message": "Cookies loaded (test inconclusive  -  real-world auth untested)"}

    except subprocess.TimeoutExpired:
        return {"success": False, "message": "Test timed out"}
    except Exception as e:
        print(f"YouTube cookie test error: {type(e).__name__}: {e}")
        return {"success": False, "message": "Cookie test failed  -  check server logs for details"}
    finally:
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)


@app.post("/api/settings/test/spotify-cookies")
def test_spotify_cookies(http_request: Request, body: TestSpotifyCookiesRequest = None):
    """Test Spotify cookies by hitting a known private-friendly endpoint.
    Uses the form value if provided, otherwise the saved cookies.
    """
    user_id = http_request.state.user_id
    cookies_text = (body.cookies if body and body.cookies else None)
    if cookies_text is None:
        cookies_text = get_setting("spotify_cookies", "", user_id=user_id)

    if not cookies_text.strip():
        return {"success": False, "message": "No cookies provided"}

    if not _has_valid_cookie_entries(cookies_text):
        return {"success": False, "message": "No cookie entries found (only comments or blank lines)"}

    lines = _cookie_lines_for_domain_check(cookies_text)
    sp_dc = next(
        (l.split("\t")[6].strip() for l in lines
         if not l.startswith("#") and len(l.split("\t")) >= 7 and l.split("\t")[5] == "sp_dc"),
        None
    )
    if not sp_dc:
        return {"success": False, "message": "No sp_dc cookie found. Export cookies from open.spotify.com while logged in."}

    # Test by fetching a small known public playlist embed with the cookie.
    # The embed endpoint is the same one used for real fetches, so if it works
    # here it'll work everywhere. Spotify's Varnish CDN blocks the internal
    # token exchange endpoint from non-browser origins, so the embed is the
    # reliable way to confirm the session is alive from a server context.
    # "Top 50 - Global" is stable, public, and always has tracks in the JSON blob.
    TEST_PLAYLIST_ID = "37i9dQZEVXbMDoHDwVN2tF"
    try:
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            resp = client.get(
                f"https://open.spotify.com/embed/playlist/{TEST_PLAYLIST_ID}",
                cookies={"sp_dc": sp_dc},
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                }
            )
        if resp.status_code in (401, 403):
            return {"success": False, "message": "Cookies rejected by Spotify — they may have expired. Re-export from open.spotify.com."}
        if resp.status_code != 200:
            return {"success": False, "message": f"Spotify returned {resp.status_code} — cookies may be invalid"}

        # If the embed returned track data, the session is working
        import re as _re
        titles = _re.findall(r'"title":"([^"]+)"', resp.text)
        if len(titles) > 1:
            return {"success": True, "message": "Spotify cookies are valid and authenticated"}

        return {"success": False, "message": "Cookies loaded but Spotify returned no track data — they may be expired"}

    except Exception as e:
        print(f"Spotify cookie test error: {type(e).__name__}: {e}")
        return {"success": False, "message": "Cookie test failed — check server logs for details"}


@app.post("/api/settings/test/apprise")
def test_apprise_notification(http_request: Request, body: TestAppriseRequest = None):
    """Send a test notification via Apprise. Uses form URL if provided, otherwise saved setting."""
    user_id = http_request.state.user_id
    url = (body.url if body and body.url else None) or get_setting("apprise_url", "", user_id=user_id)

    if not url:
        return {"success": False, "message": "Apprise URL not configured"}

    try:
        import apprise
        a = apprise.Apprise()
        if not a.add(url):
            return {"success": False, "message": "Invalid Apprise URL  -  could not parse the notification service"}
        result = a.notify(title="MusicGrabber", body="Test notification from MusicGrabber. If you can see this, it works!")
        if result:
            return {"success": True, "message": "Test notification sent successfully"}
        return {"success": False, "message": "Apprise returned failure  -  check the URL and service configuration"}
    except ImportError:
        return {"success": False, "message": "Apprise library not installed  -  rebuild the Docker image"}
    except Exception as e:
        print(f"Apprise test error: {type(e).__name__}: {e}")
        return {"success": False, "message": "Notification failed  -  check server logs for details"}


@app.get("/api/settings/youtube-cookies/status")
def youtube_cookies_status(http_request: Request):
    """Return non-sensitive status for the cookies file."""
    user_id = http_request.state.user_id
    cookies_text = get_setting("youtube_cookies", "", user_id=user_id)
    has_setting = bool(cookies_text.strip())
    file_exists = COOKIES_FILE.exists()
    file_size = COOKIES_FILE.stat().st_size if file_exists else 0
    file_mtime = COOKIES_FILE.stat().st_mtime if file_exists else None
    expiry = get_cookies_expiry(cookies_text) if has_setting else None
    return {
        "has_setting": has_setting,
        "file_exists": file_exists,
        "file_size": file_size,
        "file_mtime": file_mtime,
        "file_has_valid_entries": _has_valid_cookie_entries(cookies_text) if has_setting else False,
        "auth_cookie_expiry": expiry,  # Unix timestamp of soonest auth cookie expiry, or null
    }


# =============================================================================
# Statistics API
# =============================================================================

def _extract_search_artist(query: str) -> str | None:
    """Best-effort artist extraction from common search query formats."""
    q = (query or "").strip()
    if not q:
        return None

    # "Artist - Song", "Artist – Song", "Artist  -  Song"
    split_match = re.split(r"\s*[-–—]\s*", q, maxsplit=1)
    if len(split_match) == 2 and split_match[0].strip():
        return split_match[0].strip()[:120]

    # Fallback: first 3 words is usually artist-ish, avoids giant free text blobs
    words = q.split()
    if not words:
        return None
    return " ".join(words[:3])[:120]


def _log_search(query: str, result_count: int, source: str = "youtube", user_id: str | None = None) -> str:
    """Log search requests for dashboard analytics and return tracking token."""
    artist = _extract_search_artist(query)
    search_token = uuid.uuid4().hex
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO search_logs (query, artist, result_count, source, search_token, user_id) VALUES (?, ?, ?, ?, ?, ?)",
            (query.strip(), artist, int(result_count), source, search_token, user_id)
        )
        conn.commit()
    return search_token


def _validated_search_token(search_token: str | None, user_id: str | None = None) -> str | None:
    """Only accept server-issued search tokens that exist in search_logs, scoped to the user."""
    token = (search_token or "").strip().lower()
    if not token or not re.fullmatch(r"[0-9a-f]{32}", token):
        return None

    with db_conn() as conn:
        if user_id is not None:
            row = conn.execute(
                "SELECT 1 FROM search_logs WHERE search_token = ? AND user_id = ? LIMIT 1",
                (token, user_id)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT 1 FROM search_logs WHERE search_token = ? LIMIT 1",
                (token,)
            ).fetchone()
    return token if row else None


@app.get("/api/stats")
def get_stats(http_request: Request):
    """Return download statistics for the dashboard."""
    if not http_request.state.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    with db_conn() as conn:
        conn.row_factory = sqlite3.Row

        # Overall counts by status
        rows = conn.execute(
            "SELECT status, COUNT(*) as count FROM jobs GROUP BY status"
        ).fetchall()
        status_counts = {r["status"]: r["count"] for r in rows}

        # Total completed tracks
        total = sum(status_counts.values())
        completed = status_counts.get("completed", 0) + status_counts.get("completed_with_errors", 0)
        failed = status_counts.get("failed", 0)

        # Source breakdown (youtube vs soulseek)
        source_rows = conn.execute(
            "SELECT COALESCE(source, 'youtube') as source, COUNT(*) as count "
            "FROM jobs WHERE status IN ('completed', 'completed_with_errors') "
            "GROUP BY source"
        ).fetchall()
        sources = {r["source"]: r["count"] for r in source_rows}

        # Downloads completed per day (last 30 days)
        daily_rows = conn.execute(
            "SELECT DATE(completed_at) as day, COUNT(*) as count "
            "FROM jobs WHERE status IN ('completed', 'completed_with_errors') "
            "AND completed_at IS NOT NULL "
            "AND completed_at >= DATE('now', '-30 days') "
            "GROUP BY day ORDER BY day"
        ).fetchall()
        daily = [{"day": r["day"], "count": r["count"]} for r in daily_rows]

        # Top artists (by completed downloads)  -  case-insensitive grouping,
        # display the most popular casing variant for each artist
        artist_rows = conn.execute(
            "SELECT artist, total_count as count FROM ("
            "  SELECT artist, "
            "    SUM(COUNT(*)) OVER (PARTITION BY LOWER(artist)) as total_count, "
            "    ROW_NUMBER() OVER (PARTITION BY LOWER(artist) ORDER BY COUNT(*) DESC) as rn "
            "  FROM jobs "
            "  WHERE status IN ('completed', 'completed_with_errors') "
            "  AND artist IS NOT NULL AND artist != '' "
            "  GROUP BY artist"
            ") WHERE rn = 1 "
            "ORDER BY count DESC LIMIT 10"
        ).fetchall()
        top_artists = [{"artist": r["artist"], "count": r["count"]} for r in artist_rows]

        # Recent downloads (last 10 completed)
        recent_rows = conn.execute(
            "SELECT title, artist, source, completed_at "
            "FROM jobs WHERE status IN ('completed', 'completed_with_errors') "
            "ORDER BY completed_at DESC LIMIT 10"
        ).fetchall()
        recent = [
            {"title": r["title"], "artist": r["artist"], "source": r["source"], "completed_at": r["completed_at"]}
            for r in recent_rows
        ]

        # Search history stats
        search_summary = conn.execute(
            "SELECT COUNT(*) as total_searches, "
            "SUM(CASE WHEN result_count > 0 THEN 1 ELSE 0 END) as successful_searches "
            "FROM search_logs"
        ).fetchone()
        total_searches = int(search_summary["total_searches"] or 0)
        successful_searches = int(search_summary["successful_searches"] or 0)

        # Case-insensitive grouping  -  display the most popular casing variant
        searched_artist_rows = conn.execute(
            "SELECT artist, total_count as count, total_successful as successful_searches FROM ("
            "  SELECT artist, "
            "    SUM(COUNT(*)) OVER (PARTITION BY LOWER(artist)) as total_count, "
            "    SUM(SUM(CASE WHEN result_count > 0 THEN 1 ELSE 0 END)) OVER (PARTITION BY LOWER(artist)) as total_successful, "
            "    ROW_NUMBER() OVER (PARTITION BY LOWER(artist) ORDER BY COUNT(*) DESC) as rn "
            "  FROM search_logs "
            "  WHERE artist IS NOT NULL AND artist != '' "
            "  GROUP BY artist"
            ") WHERE rn = 1 "
            "ORDER BY count DESC, artist ASC "
            "LIMIT 10"
        ).fetchall()
        top_searched_artists = [
            {
                "artist": r["artist"],
                "count": int(r["count"]),
                "successful_searches": int(r["successful_searches"] or 0),
            }
            for r in searched_artist_rows
        ]

        search_to_download = conn.execute(
            "SELECT COUNT(*) as converted_searches "
            "FROM search_logs s "
            "WHERE EXISTS ("
            "  SELECT 1 FROM jobs j "
            "  WHERE j.search_token = s.search_token "
            "  AND j.status IN ('completed', 'completed_with_errors')"
            ")"
        ).fetchone()
        converted_searches = int(search_to_download["converted_searches"] or 0)

        # Storage usage  -  scan both Singles and Playlists dirs, deduplicate by inode
        storage_bytes = 0
        file_count = 0
        seen_inodes: set[tuple] = set()
        try:
            for base_dir in [d for d in (get_singles_dir(), get_playlists_dir()) if d is not None]:
                if not base_dir.exists():
                    continue
                for f in base_dir.rglob("*"):
                    if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS:
                        st = f.stat()
                        key = (st.st_dev, st.st_ino)
                        if key not in seen_inodes:
                            storage_bytes += st.st_size
                            file_count += 1
                            seen_inodes.add(key)
        except OSError:
            pass

    return {
        "total_jobs": total,
        "completed": completed,
        "failed": failed,
        "sources": sources,
        "daily": daily,
        "top_artists": top_artists,
        "total_searches": total_searches,
        "successful_searches": successful_searches,
        "converted_searches": converted_searches,
        "top_searched_artists": top_searched_artists,
        "recent": recent,
        "storage_bytes": storage_bytes,
        "file_count": file_count,
    }


@app.delete("/api/stats")
def reset_stats(http_request: Request, confirm: bool = False):
    """Reset dashboard stats without touching active queue items."""
    if not http_request.state.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    if not confirm:
        raise HTTPException(status_code=400, detail="Confirmation required (use ?confirm=true)")

    # Keep active work intact, but clear historical job/search data used by stats
    cleanup_stale_jobs()

    with db_conn() as conn:
        deleted_jobs = conn.execute(
            "DELETE FROM jobs WHERE status IN ('completed', 'completed_with_errors', 'failed')"
        ).rowcount
        deleted_searches = conn.execute("DELETE FROM search_logs").rowcount
        conn.execute("DELETE FROM search_decisions")
        conn.commit()

    return {"deleted_jobs": deleted_jobs, "deleted_searches": deleted_searches}


@app.get("/api/mismatches")
def get_mismatches(http_request: Request, limit: int = 200):
    """Return recent watched-track match mismatches for investigation."""
    if not http_request.state.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    with db_conn() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT m.id, m.job_id, m.playlist_id, m.expected_artist, m.expected_title,
                      m.actual_artist, m.actual_title, m.exp_normalised, m.got_normalised,
                      m.created_at, wp.name AS playlist_name
               FROM watched_match_mismatches m
               LEFT JOIN watched_playlists wp ON wp.id = m.playlist_id
               ORDER BY m.created_at DESC
               LIMIT ?""",
            (min(limit, 500),),
        ).fetchall()
    return {"mismatches": [dict(r) for r in rows]}


@app.delete("/api/mismatches")
def clear_mismatches(http_request: Request):
    """Clear the mismatch log."""
    if not http_request.state.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    with db_conn() as conn:
        deleted = conn.execute("DELETE FROM watched_match_mismatches").rowcount
        conn.commit()
    return {"deleted": deleted}


@app.post("/api/mismatches/{mismatch_id}/accept")
def accept_mismatch(mismatch_id: int, http_request: Request):
    """Force-accept a mismatched track: re-download it but skip the name comparison."""
    if not http_request.state.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")

    with db_conn() as conn:
        conn.row_factory = sqlite3.Row
        mismatch = conn.execute(
            "SELECT * FROM watched_match_mismatches WHERE id = ?", (mismatch_id,)
        ).fetchone()
        if not mismatch:
            raise HTTPException(status_code=404, detail="Mismatch not found")

        job_id = mismatch["job_id"]
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        job = dict(job) if job else None

        user_id = (job.get("user_id") if job else None) or http_request.state.user_id

        if job:
            # Original job still exists, reset it with the skip flag
            conn.execute(
                "UPDATE jobs SET status = 'queued', error = NULL, completed_at = NULL, "
                "file_deleted = 0, skip_mismatch_check = 1 WHERE id = ?",
                (job_id,)
            )
        else:
            # Job was cleaned up; create a fresh one from the mismatch record
            job_id = str(uuid.uuid4())[:8]
            artist = mismatch["expected_artist"] or ""
            title = mismatch["expected_title"] or ""
            convert_to_flac = get_setting_bool("default_convert_to_flac", True, user_id=user_id)
            conn.execute(
                """INSERT INTO jobs
                   (id, title, artist, status, download_type, source, convert_to_flac, skip_mismatch_check, user_id)
                   VALUES (?, ?, ?, 'queued', 'single', 'youtube', ?, 1, ?)""",
                (job_id, title, artist, int(convert_to_flac), user_id)
            )
            # Re-link the watched playlist track to this new job so the mismatch skip works
            if mismatch["playlist_id"]:
                track_hash = hash_track(artist, title)
                conn.execute(
                    "UPDATE watched_playlist_tracks SET job_id = ?, downloaded_at = NULL "
                    "WHERE playlist_id = ? AND track_hash = ?",
                    (job_id, mismatch["playlist_id"], track_hash)
                )
            job = dict(conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone())

        # Clean up the mismatch record since we're dealing with it
        conn.execute("DELETE FROM watched_match_mismatches WHERE id = ?", (mismatch_id,))
        conn.commit()

    # Re-queue the download
    convert_to_flac = bool(job.get("convert_to_flac", 1))

    # Restore watched-playlist routing so the file lands in the right folder, not Singles.
    _pl_name, _use_pl_dir = None, False
    if mismatch.get("playlist_id"):
        with db_conn() as conn:
            conn.row_factory = sqlite3.Row
            pl_row = conn.execute(
                "SELECT name, use_playlists_dir FROM watched_playlists WHERE id = ?",
                (mismatch["playlist_id"],)
            ).fetchone()
            if pl_row and pl_row["use_playlists_dir"]:
                _pl_name = pl_row["name"]
                _use_pl_dir = True

    if job.get("source") == "soulseek" and job.get("slskd_username") and job.get("slskd_filename"):
        spawn_daemon_thread(
            process_slskd_download,
            job_id,
            job["slskd_username"],
            job["slskd_filename"],
            job.get("artist", ""),
            job.get("title", ""),
            convert_to_flac,
            user_id=user_id,
            override_dir=job.get("override_dir"),
            playlist_name=_pl_name,
            use_playlists_dir=_use_pl_dir,
            slskd_size=job.get("slskd_size"),
        )
    else:
        prior_id = job.get("video_id") or ""
        attempted = {prior_id} if prior_id else set()
        new_id = prior_id
        new_source_url = job.get("source_url")
        artist_hint = job.get("artist") or ""
        title_hint = job.get("title") or ""
        if artist_hint or title_hint:
            query = f"{artist_hint} - {title_hint}".strip(" -")
            try:
                for cand in search_all(query, limit=12)[0]:
                    cand_id = (cand.get("video_id") or "").strip()
                    if cand_id and cand_id not in attempted:
                        new_id = cand_id
                        new_source_url = cand.get("source_url")
                        break
            except Exception as e:
                print(f"Force-accept alternate search failed for job {job_id}: {e}")
        spawn_daemon_thread(
            process_download,
            job_id,
            new_id,
            convert_to_flac,
            source_url=new_source_url,
            playlist_name=_pl_name,
            use_playlists_dir=_use_pl_dir,
            user_id=user_id,
            override_dir=job.get("override_dir"),
            skip_dupe_check=bool(job.get("override_dir")),
            attempted_ids=attempted,
        )

    return {"status": "queued", "job_id": job_id}


@app.get("/api/jobs/{job_id}/score-rationale")
def get_score_rationale(job_id: str, http_request: Request):
    """Return the scoring rationale for an automated download decision.

    Shows why the scorer picked one candidate over its rivals, so users
    can debug bad picks without becoming intimate with Docker logs.
    """
    user_id = http_request.state.user_id
    is_admin = http_request.state.is_admin

    with db_conn() as conn:
        conn.row_factory = sqlite3.Row
        # Verify the job belongs to this user (or they're admin)
        job = conn.execute("SELECT user_id FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        if not is_admin and job["user_id"] and job["user_id"] != user_id:
            raise HTTPException(status_code=403, detail="Not your job")

        row = conn.execute(
            "SELECT query, decision_json, created_at FROM search_decisions WHERE job_id = ? LIMIT 1",
            (job_id,),
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="No scoring data for this job")

    try:
        decision = json.loads(row["decision_json"])
    except (ValueError, TypeError):
        raise HTTPException(status_code=500, detail="Corrupt scoring data")

    return {
        "query": row["query"],
        "decision": decision,
        "created_at": row["created_at"],
    }


# =============================================================================
# Search API
# =============================================================================

@app.get("/api/preview/{video_id}")
def get_preview_url(video_id: str, source: str = "youtube", url: str = None):
    """Get a streamable audio URL for preview playback."""
    try:
        # Direct MP3 sources: the source_url is already streamable  -  hand it
        # straight to the browser, no yt-dlp round-trip needed.
        if source in DIRECT_PREVIEW_SOURCES:
            if not url:
                raise HTTPException(status_code=400, detail=f"{source.capitalize()} preview requires url parameter")
            return {"url": url, "video_id": video_id}

        if source in MONOCHROME_PREVIEW_SOURCES:
            if not url:
                raise HTTPException(status_code=400, detail="Monochrome preview requires url parameter")
            from monochrome import get_monochrome_preview_url
            from urllib.parse import urlparse as _urlparse, parse_qs as _parse_qs
            _parsed = _urlparse(url)
            if not _parsed.netloc:
                raise HTTPException(status_code=400, detail="Invalid Monochrome source URL")
            _isrc = (_parse_qs(_parsed.query).get("isrc") or [""])[0]
            if not _isrc:
                raise HTTPException(status_code=400, detail="Monochrome source URL missing ISRC")
            cdn_url = get_monochrome_preview_url(_isrc)
            return {"url": cdn_url, "video_id": video_id}

        if source == "youtube":
            if not is_valid_youtube_id(video_id):
                raise HTTPException(status_code=400, detail="Invalid YouTube video ID")
            target_url = f"https://www.youtube.com/watch?v={video_id}"
            base_args = _ytdlp_base_args()
        elif source in URL_BASED_SOURCES:
            if not url:
                raise HTTPException(status_code=400, detail=f"{source.capitalize()} preview requires url parameter")
            target_url = url
            base_args = []  # No cookies needed for URL-based non-YouTube sources
        else:
            raise HTTPException(status_code=400, detail=f"Preview not supported for source: {source}")

        # SoundCloud returns HLS (.m3u8) for bestaudio which browsers can't
        # play natively  -  prefer the direct HTTP MP3 stream for previews.
        # Format IDs vary by track: older ones use http_mp3_1_0, newer ones
        # use http_mp3_standard. Both resolve to a direct .mp3 on cf-media.sndcdn.com.
        if source == "soundcloud":
            fmt = "http_mp3_1_0/http_mp3_standard/bestaudio[protocol=http]/best"
        else:
            fmt = "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best"

        cmd = [
            "yt-dlp",
            *base_args,
            "-f", fmt,
            "-g",  # Get URL only, don't download
            "--no-warnings",
            target_url,
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_YTDLP_PREVIEW)

        if result.returncode != 0:
            raise HTTPException(status_code=500, detail="Failed to get preview URL")

        audio_url = result.stdout.strip()

        if not audio_url:
            raise HTTPException(status_code=404, detail="No audio stream found")

        return {"url": audio_url, "video_id": video_id}

    except HTTPException:
        raise
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Preview request timed out")
    except Exception as e:
        print(f"preview error: {e}")
        raise HTTPException(status_code=500, detail="Failed to get preview URL")


@app.get("/api/sources")
def list_sources():
    """Return available search sources for the frontend source selector."""
    return {"sources": get_available_sources()}


@app.get("/api/sources/health")
def sources_health():
    """Per-source health snapshot; parked sources carry their cooldown reason."""
    return {"sources": servicecheck.health_snapshot()}


@app.post("/api/search")
def search(request: SearchRequest, http_request: Request):
    """Search for music across configured sources."""
    try:
        source = request.source
        album_suggestion = None
        if source == "all":
            raw_results, album_suggestion = search_all(request.query, request.limit, include_soulseek=False)
        elif source in SOURCE_REGISTRY:
            raw_results = search_source(source, request.query, request.limit)
        else:
            raise HTTPException(status_code=400, detail=f"Unknown source: {source}")

        final_results = []
        for item in raw_results[:request.limit]:
            final_results.append(SearchResult(
                video_id=item["video_id"],
                title=item["title"],
                artist=None,
                channel=item["channel"],
                duration=item["duration"],
                thumbnail=item["thumbnail"],
                is_playlist=item.get("is_playlist", False),
                video_count=item.get("video_count"),
                source=item["source"],
                source_url=item.get("source_url"),
                quality=item["quality"],
                quality_score=item["quality_score"],
                slskd_username=item["slskd_username"],
                slskd_filename=item["slskd_filename"],
                slskd_size=item.get("slskd_size") or item.get("size"),
            ))

        search_token = None
        try:
            search_token = _log_search(request.query, len(final_results), source=source, user_id=http_request.state.user_id)
        except Exception as log_error:
            print(f"search log error: {log_error}")

        # Surface any enabled-but-parked sources so the UI can explain the gaps.
        parked = [
            u for u in servicecheck.unavailable_sources()
            if get_setting_bool(
                f"source_{u['id']}_enabled",
                SOURCE_REGISTRY.get(u["id"], {}).get("default_enabled", True),
            )
        ]
        resp = {
            "results": final_results,
            "slskd_enabled": slskd_enabled(),
            "search_token": search_token,
            "unavailable_sources": parked,
        }
        if album_suggestion:
            resp["album_suggestion"] = album_suggestion
        return resp

    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Search timed out")
    except HTTPException:
        raise
    except Exception as e:
        print(f"search error: {e}")
        raise HTTPException(status_code=500, detail="Search failed")


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
                slskd_size=r.get("slskd_size") or r.get("size"),
            ))

        return {"results": final_results, "slskd_enabled": True}

    except Exception as e:
        print(f"slskd search error: {type(e).__name__}: {e}")
        return {"results": [], "slskd_enabled": True, "error": "Search failed  -  check server logs for details"}


@app.get("/api/search/artwork")
def search_artwork(artist: str, title: str):
    """Return a cover art URL for display in search results.

    Tries iTunes then Deezer. Returns the remote URL so the browser loads
    it directly; no image bytes are proxied through MusicGrabber.
    """
    artist = (artist or "").strip()
    title = (title or "").strip()
    if not artist or not title:
        return {"url": None}
    url = fetch_cover_art_url(artist, title)
    return {"url": url}


@app.post("/api/explore/similar")
def explore_similar(request: ExploreRequest):
    """Return a list of similar artists via MusicBrainz + ListenBrainz Labs.
    Two-step: MusicBrainz name search → artist MBID, then ListenBrainz Labs
    similar-artists → ranked list of similar artist names. No auth required.
    Returns {artist} entries; the frontend searches each artist to find a top track."""
    artist = request.artist.strip()
    if not artist:
        raise HTTPException(status_code=400, detail="Artist name required")

    limit = max(1, min(request.limit, 25))  # Hard cap at 25

    MB_ALGO = "session_based_days_7500_session_300_contribution_5_threshold_10_limit_100_filter_True_skip_30"
    MB_HEADERS = {"User-Agent": f"MusicGrabber/{VERSION} (self-hosted music tool)"}

    with httpx.Client(timeout=TIMEOUT_LISTENBRAINZ, headers=MB_HEADERS) as client:
        # Step 1: look up the artist MBID via MusicBrainz search
        try:
            mb_resp = client.get(
                "https://musicbrainz.org/ws/2/artist",
                params={"query": artist, "limit": 1, "fmt": "json"},
            )
            mb_resp.raise_for_status()
            artists = mb_resp.json().get("artists", [])
            if not artists:
                return {"artists": [], "artist": artist}
            mbid = artists[0]["id"]
        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail="MusicBrainz timed out")
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"MusicBrainz lookup failed: {e}")

        # Step 2: ListenBrainz Labs similar-artists  -  fully public, no token needed
        try:
            lb_resp = client.get(
                "https://labs.api.listenbrainz.org/similar-artists/json",
                params={"artist_mbids": mbid, "algorithm": MB_ALGO},
            )
            lb_resp.raise_for_status()
            similar = lb_resp.json()
        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail="ListenBrainz Labs timed out")
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"ListenBrainz Labs failed: {e}")

    # Results are pre-sorted by score descending  -  just take the top N names
    result_artists = [
        {"artist": entry["name"]}
        for entry in similar
        if entry.get("name")
    ][:limit]

    return {"artists": result_artists, "artist": artist}


# =============================================================================
# Download API
# =============================================================================

@app.post("/api/download")
def download(body: DownloadRequest, http_request: Request):
    """Queue a download job"""
    _enforce_peon_format(http_request, body)
    job_id = str(uuid.uuid4())[:8]
    user_id = http_request.state.user_id

    # Extract artist/title if not provided
    artist = body.artist
    title = body.title

    # Create job record
    with db_conn() as conn:
        # Determine source type
        source = body.source or "youtube"
        if source == "soulseek" and not (body.slskd_username and body.slskd_filename):
            source = "youtube"  # Fallback if slskd fields missing

        # Validate based on source
        if source == "youtube":
            if not body.video_id or not is_valid_youtube_id(body.video_id):
                raise HTTPException(status_code=400, detail="Invalid YouTube video ID")
        elif source in URL_BASED_SOURCES:
            if not body.source_url:
                raise HTTPException(status_code=400, detail=f"{source.capitalize()} download requires source_url")

        # Build source URL for tracking
        if source == "soulseek":
            source_url = f"soulseek://{body.slskd_username}/{body.slskd_filename}" if body.slskd_username else None
        elif source in URL_BASED_SOURCES:
            source_url = body.source_url
        elif body.download_type == "playlist":
            source_url = f"https://www.youtube.com/playlist?list={body.video_id}" if body.video_id else None
        else:
            source_url = f"https://www.youtube.com/watch?v={body.video_id}" if body.video_id else None

        album_release_mbid = (body.album_release_mbid or "").strip() or None
        album_artist = (body.album_artist or "").strip() or None
        album_name = (body.album_name or "").strip() or None
        album_track_title = (body.album_track_title or "").strip() or None
        album_track_number = int(body.album_track_number) if body.album_track_number else None
        album_track_total = int(body.album_track_total) if body.album_track_total else None
        if album_track_number is not None and album_track_number <= 0:
            album_track_number = None
        if album_track_total is not None and album_track_total <= 0:
            album_track_total = None
        override_dir = None

        album_fields_present = any([album_release_mbid, album_artist, album_name, album_track_title, album_track_number, album_track_total])
        if album_fields_present:
            if body.download_type == "playlist":
                raise HTTPException(status_code=400, detail="Album routing cannot be used with playlist downloads")
            # Full album routing requires MBID + name + track title.
            # Exception: no_mbid folder-only routing just needs album_name + album_artist.
            if album_release_mbid and (not album_name or not album_track_title):
                raise HTTPException(status_code=400, detail="album_name and album_track_title are required when album_release_mbid is provided")
            if not album_release_mbid and not album_name:
                raise HTTPException(status_code=400, detail="album_name is required for album folder routing")
            route_artist = album_artist or (artist or "").strip()
            if not route_artist:
                raise HTTPException(status_code=400, detail="album_artist (or artist) is required for album routing")
            override_dir = str(get_albums_dir(user_id=user_id) / sanitize_filename(route_artist) / sanitize_filename(album_name))

        valid_search_token = _validated_search_token(body.search_token, user_id=user_id)

        if body.download_type == "playlist":
            conn.execute(
                """INSERT INTO jobs
                   (id, video_id, title, status, download_type, playlist_name, source, convert_to_flac, source_url, search_token, user_id,
                    override_dir, album_release_mbid, album_name, album_track_title, album_track_number, album_track_total)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    job_id, body.video_id, title, "queued", "playlist", title, "youtube", int(body.convert_to_flac), source_url, valid_search_token, user_id,
                    None, None, None, None, None, None,
                )
            )
        else:
            conn.execute(
                """INSERT INTO jobs
                   (id, video_id, title, artist, status, download_type, source, slskd_username, slskd_filename, slskd_size, convert_to_flac, source_url, search_token, user_id,
                    override_dir, album_release_mbid, album_name, album_track_title, album_track_number, album_track_total)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    job_id, body.video_id, title, artist or "", "queued", "single", source,
                    body.slskd_username, body.slskd_filename, body.slskd_size, int(body.convert_to_flac), source_url, valid_search_token, user_id,
                    override_dir, album_release_mbid, album_name, album_track_title, album_track_number, album_track_total,
                )
            )
        conn.commit()

    # If this is an album-routed single track, register a lock so any retry
    # can skip dupe check without relying on the transient skip_dupe_check flag.
    if override_dir and album_track_title:
        _route_artist = album_artist or (artist or "").strip()
        upsert_album_track_lock(album_release_mbid, album_name or "", _route_artist, album_track_title, job_id)

    # Queue the download based on source
    if body.download_type == "playlist":
        spawn_daemon_thread(process_playlist_download, job_id, body.video_id, title, body.convert_to_flac, True,
                            user_id=user_id)
    elif source == "soulseek":
        spawn_daemon_thread(
            process_slskd_download,
            job_id,
            body.slskd_username,
            body.slskd_filename,
            artist or "",
            title,
            body.convert_to_flac,
            user_id=user_id,
            override_dir=override_dir,
            playlist_name=body.playlist_name,
            use_playlists_dir=body.use_playlists_dir,
            slskd_size=body.slskd_size,
        )
    elif source in URL_BASED_SOURCES:
        spawn_daemon_thread(
            process_download, job_id, body.video_id, body.convert_to_flac,
            source_url=source_url,
            playlist_name=body.playlist_name,
            use_playlists_dir=body.use_playlists_dir,
            user_id=user_id,
            override_dir=override_dir,
            skip_dupe_check=bool(override_dir),
        )
    else:
        spawn_daemon_thread(
            process_download, job_id, body.video_id, body.convert_to_flac,
            playlist_name=body.playlist_name,
            use_playlists_dir=body.use_playlists_dir,
            user_id=user_id,
            override_dir=override_dir,
            skip_dupe_check=bool(override_dir),
        )

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
def get_jobs(limit: int = 20, http_request: Request = None):
    """Get recent jobs"""
    from pathlib import Path as _Path
    user_id = http_request.state.user_id if http_request else None
    is_admin = http_request.state.is_admin if http_request else True
    with db_conn() as conn:
        conn.row_factory = sqlite3.Row
        _scope_frag, _scope_params = _user_scope(user_id, is_admin)
        rows = conn.execute(
            f"SELECT * FROM jobs WHERE {_scope_frag} ORDER BY created_at DESC LIMIT ?",
            (*_scope_params, limit)
        ).fetchall()

        # Pre-fetch resolved_paths for all jobs in one query.
        # check_duplicate only walks Singles; playlist-folder tracks would be
        # falsely marked deleted without this.
        if rows:
            job_ids = [r["id"] for r in rows]
            placeholders = ",".join("?" * len(job_ids))
            rp_rows = conn.execute(
                f"SELECT job_id, resolved_path FROM watched_playlist_tracks WHERE job_id IN ({placeholders}) AND resolved_path IS NOT NULL",
                job_ids,
            ).fetchall()
            resolved_paths: dict = {r["job_id"]: r["resolved_path"] for r in rp_rows}
        else:
            resolved_paths = {}

        jobs = []
        stale_ids = []  # Jobs that claim file exists but it doesn't
        for row in rows:
            job = dict(row)
            job['created_at'] = _ensure_utc_suffix(job.get('created_at'))
            job['completed_at'] = _ensure_utc_suffix(job.get('completed_at'))

            # Keep the queue API cheap. Full library reconciliation can walk a
            # large/remote music tree and is handled by the background monitor;
            # here we only verify exact paths already stored on watched rows.
            if (job.get('status') in ('completed', 'completed_with_errors')
                    and not job.get('file_deleted')
                    and job.get('artist') and job.get('title')):
                rp = resolved_paths.get(job['id'])
                if rp and _Path(rp).is_absolute() and not _Path(rp).exists():
                    job['file_deleted'] = 1
                    stale_ids.append(job['id'])

            jobs.append(job)

        # Batch-update any jobs whose files have gone walkabout
        if stale_ids:
            conn.executemany(
                "UPDATE jobs SET file_deleted = 1 WHERE id = ?",
                [(jid,) for jid in stale_ids]
            )
            conn.commit()

    return {"jobs": jobs}


@app.get("/api/jobs/downloadable")
def get_downloadable_jobs(page: int = 1, per_page: int = 50, http_request: Request = None):
    """Return completed jobs with files available to save to device, newest first."""
    offset = (page - 1) * per_page
    user_id = http_request.state.user_id if http_request else None
    is_admin = http_request.state.is_admin if http_request else True
    with db_conn() as conn:
        conn.row_factory = sqlite3.Row
        _scope_frag, _scope_params = _user_scope(user_id, is_admin)
        total = conn.execute(
            f"SELECT COUNT(*) FROM jobs WHERE status = 'completed' AND (file_deleted IS NULL OR file_deleted = 0) AND {_scope_frag}",
            _scope_params
        ).fetchone()[0]
        rows = conn.execute(
            f"""SELECT id, title, artist, status, created_at, completed_at
               FROM jobs
               WHERE status = 'completed' AND (file_deleted IS NULL OR file_deleted = 0)
                 AND {_scope_frag}
               ORDER BY completed_at DESC
               LIMIT ? OFFSET ?""",
            (*_scope_params, per_page, offset)
        ).fetchall()
    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": max(1, (total + per_page - 1) // per_page),
        "jobs": [dict(r) for r in rows],
    }


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, http_request: Request):
    """Get a specific job"""
    user_id = http_request.state.user_id
    is_admin = http_request.state.is_admin
    with db_conn() as conn:
        conn.row_factory = sqlite3.Row
        _scope_frag, _scope_params = _user_scope(user_id, is_admin)
        cursor = conn.execute(
            f"SELECT * FROM jobs WHERE id = ? AND {_scope_frag}",
            (job_id, *_scope_params)
        )
        row = cursor.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Job not found")

    job = dict(row)
    job['created_at'] = _ensure_utc_suffix(job.get('created_at'))
    job['completed_at'] = _ensure_utc_suffix(job.get('completed_at'))
    return job


@app.get("/api/jobs/{job_id}/download")
def download_job_file(job_id: str, http_request: Request):
    """Serve the downloaded audio file for a job directly to the browser."""
    user_id = http_request.state.user_id
    is_admin = http_request.state.is_admin
    with db_conn() as conn:
        conn.row_factory = sqlite3.Row
        _scope_frag, _scope_params = _user_scope(user_id, is_admin)
        cursor = conn.execute(
            f"SELECT * FROM jobs WHERE id = ? AND {_scope_frag}",
            (job_id, *_scope_params)
        )
        row = cursor.fetchone()
        # Also fish out the resolved_path from watched_playlist_tracks if it exists.
        # This is more reliable than reconstructing the path, especially for playlist folders.
        rp_cursor = conn.execute(
            "SELECT resolved_path FROM watched_playlist_tracks WHERE job_id = ? AND resolved_path IS NOT NULL LIMIT 1",
            (job_id,)
        )
        rp_row = rp_cursor.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Job not found")

    job = dict(row)

    if job.get("file_deleted"):
        raise HTTPException(status_code=404, detail="File has been deleted")

    if job.get("status") not in ("completed", "completed_with_errors"):
        raise HTTPException(status_code=404, detail="Job not yet complete")

    artist = job.get("artist", "")
    title = job.get("title", "")
    if not title:
        raise HTTPException(status_code=404, detail="Job has no title")

    from utils import check_duplicate, sanitize_filename

    file_path = None

    # Prefer the stored resolved_path (set at download time, works for playlist folders too).
    if rp_row and rp_row["resolved_path"]:
        candidate = Path(rp_row["resolved_path"])
        if candidate.is_absolute() and candidate.exists():
            file_path = candidate

    # Fall back to walking the Singles layout (covers non-watched single downloads).
    if file_path is None:
        file_path = check_duplicate(artist, title, user_id=user_id)

    if not file_path or not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")

    # Use "Artist - Title.ext" as the download filename regardless of where it lives.
    download_name = f"{sanitize_filename(artist)} - {sanitize_filename(title)}{file_path.suffix}" if artist else f"{sanitize_filename(title)}{file_path.suffix}"

    return FileResponse(
        path=str(file_path),
        media_type="application/octet-stream",
        filename=download_name,
    )


_AUDIO_MIME_TYPES = {
    ".flac": "audio/flac", ".opus": "audio/ogg", ".ogg": "audio/ogg",
    ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".webm": "audio/webm",
}


@app.get("/api/jobs/{job_id}/stream")
def stream_job_file(job_id: str, http_request: Request):
    """Stream the audio file for in-browser playback."""
    user_id = http_request.state.user_id
    is_admin = http_request.state.is_admin
    with db_conn() as conn:
        conn.row_factory = sqlite3.Row
        _scope_frag, _scope_params = _user_scope(user_id, is_admin)
        row = conn.execute(
            f"SELECT * FROM jobs WHERE id = ? AND {_scope_frag}",
            (job_id, *_scope_params)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")

    job = dict(row)
    artist = job.get("artist", "")
    title = job.get("title", "")
    if not title:
        raise HTTPException(status_code=404, detail="Job has no title")

    from utils import check_duplicate

    file_path = check_duplicate(artist, title, user_id=user_id)
    if not file_path or not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")

    mime = _AUDIO_MIME_TYPES.get(file_path.suffix.lower(), "audio/mpeg")
    return FileResponse(path=str(file_path), media_type=mime)


@app.post("/api/jobs/{job_id}/retry")
def retry_job(job_id: str, http_request: Request):
    """Retry a failed job"""
    user_id = http_request.state.user_id
    is_admin = http_request.state.is_admin
    with db_conn() as conn:
        conn.row_factory = sqlite3.Row
        _scope_frag, _scope_params = _user_scope(user_id, is_admin)
        cursor = conn.execute(
            f"SELECT * FROM jobs WHERE id = ? AND {_scope_frag}",
            (job_id, *_scope_params)
        )
        row = cursor.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Job not found")

        job = dict(row)

        # Allow retrying failed jobs or re-downloading completed jobs
        if job["status"] not in ("failed", "completed", "completed_with_errors"):
            raise HTTPException(status_code=400, detail="Only failed or completed jobs can be retried")

        # Reset job status
        conn.execute(
            "UPDATE jobs SET status = ?, error = NULL, completed_at = NULL, file_deleted = 0 WHERE id = ?",
            ("queued", job_id)
        )
        conn.commit()

    # Re-queue the job based on source type
    convert_to_flac = bool(job.get("convert_to_flac", 1))

    # Restore watched-playlist routing so retried jobs land in Playlists, not Singles.
    # The routing flags are not stored on the job row itself, so we look them up via
    # watched_playlist_tracks (queue-track-candidate path) or bulk_import_tracks (refresh path).
    _pl_name, _use_pl_dir = None, False
    if job["download_type"] != "playlist":
        with db_conn() as conn:
            conn.row_factory = sqlite3.Row
            pl_row = conn.execute("""
                SELECT wp.name, wp.use_playlists_dir
                FROM watched_playlist_tracks wpt
                JOIN watched_playlists wp ON wp.id = wpt.playlist_id
                WHERE wpt.job_id = ?
            """, (job_id,)).fetchone()
            if not pl_row:
                pl_row = conn.execute("""
                    SELECT wp.name, wp.use_playlists_dir
                    FROM bulk_import_tracks bit
                    JOIN bulk_imports bi ON bi.id = bit.import_id
                    JOIN watched_playlists wp ON wp.id = bi.watch_playlist_id
                    WHERE bit.job_id = ?
                """, (job_id,)).fetchone()
            if pl_row and pl_row["use_playlists_dir"]:
                _pl_name = pl_row["name"]
                _use_pl_dir = True

    if job["download_type"] == "playlist":
        spawn_daemon_thread(process_playlist_download, job_id, job["video_id"], job["playlist_name"], convert_to_flac, True,
                            user_id=user_id)
    elif job.get("source") == "soulseek" and job.get("slskd_username") and job.get("slskd_filename"):
        spawn_daemon_thread(
            process_slskd_download,
            job_id,
            job["slskd_username"],
            job["slskd_filename"],
            job.get("artist", ""),
            job.get("title", ""),
            convert_to_flac,
            user_id=user_id,
            override_dir=job.get("override_dir"),
            playlist_name=_pl_name,
            use_playlists_dir=_use_pl_dir,
            slskd_size=job.get("slskd_size"),
        )
    else:
        # For both YouTube and URL-based sources (SoundCloud, mp3phoenix):
        # search across all sources and pick the best untried candidate.
        # Re-trying the same source_url or video_id that already failed is pointless.
        prior_id = job.get("video_id") or ""
        attempted = {prior_id} if prior_id else set()
        new_id = prior_id
        new_source_url = None
        artist_hint = job.get("artist") or ""
        title_hint  = job.get("title") or ""
        if artist_hint or title_hint:
            query = f"{artist_hint} - {title_hint}".strip(" -")
            try:
                for cand in search_all(query, limit=12)[0]:
                    cand_id = (cand.get("video_id") or "").strip()
                    if cand_id and cand_id not in attempted:
                        new_id = cand_id
                        new_source_url = cand.get("source_url")
                        break
            except Exception as e:
                print(f"Re-download alternate search failed for job {job_id}: {e}")
        spawn_daemon_thread(
            process_download,
            job_id,
            new_id,
            convert_to_flac,
            source_url=new_source_url,
            playlist_name=_pl_name,
            use_playlists_dir=_use_pl_dir,
            user_id=user_id,
            override_dir=job.get("override_dir"),
            skip_dupe_check=bool(job.get("override_dir")),
            attempted_ids=attempted,
        )

    return {"job_id": job_id, "status": "queued"}


@app.post("/api/jobs/{job_id}/force-accept")
def force_accept_job(job_id: str, http_request: Request):
    """Set skip_mismatch_check on a job and retry it. Handy shortcut from the queue card."""
    with db_conn() as conn:
        conn.execute(
            "UPDATE jobs SET skip_mismatch_check = 1 WHERE id = ?", (job_id,)
        )
        # Tidy up any mismatch log entries for this job while we're at it
        conn.execute(
            "DELETE FROM watched_match_mismatches WHERE job_id = ?", (job_id,)
        )
        conn.commit()
    # Delegate to the normal retry flow (which will now honour the skip flag)
    return retry_job(job_id, http_request)


@app.delete("/api/jobs/{job_id}/file")
def delete_job_file(job_id: str, http_request: Request):
    """Delete the downloaded audio file (and lyrics) for a completed job."""
    user_id = http_request.state.user_id
    is_admin = http_request.state.is_admin
    with db_conn() as conn:
        conn.row_factory = sqlite3.Row
        _scope_frag, _scope_params = _user_scope(user_id, is_admin)
        cursor = conn.execute(
            f"SELECT * FROM jobs WHERE id = ? AND {_scope_frag}",
            (job_id, *_scope_params)
        )
        row = cursor.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Job not found")

    job = dict(row)
    if job["status"] not in ("completed", "completed_with_errors"):
        raise HTTPException(status_code=400, detail="Only completed jobs have files to delete")

    artist = job.get("artist")
    title = job.get("title")
    if not artist or not title:
        raise HTTPException(status_code=400, detail="Job has no artist/title metadata")

    from utils import check_duplicate, move_to_trash
    existing = check_duplicate(artist, title, user_id=user_id)
    if not existing:
        # File already gone, just mark it and move on
        with db_conn() as conn:
            conn.execute("UPDATE jobs SET file_deleted = 1 WHERE id = ?", (job_id,))
            conn.commit()
        return {"deleted": [], "job_id": job_id, "trashed": False}

    # Move the file to the trash bin. Covers both playlist-owned and library files;
    # the whole point of the trash is that nothing gets permanently nuked, so the
    # user can restore without re-downloading.
    trashed_files = []
    trash_result = move_to_trash(existing, user_id=user_id)
    if trash_result:
        trashed_files.append(existing.name)
        lrc_trash = trash_result.with_suffix(".lrc")
        if lrc_trash.exists():
            trashed_files.append(lrc_trash.name)
    else:
        # Trash failed; fall back to permanent delete so we don't leave the user stuck
        try:
            existing.unlink()
            trashed_files.append(existing.name)
            lrc_file = existing.with_suffix(".lrc")
            if lrc_file.exists():
                lrc_file.unlink()
                trashed_files.append(lrc_file.name)
            # Clean up empty parent directory
            track_dir = existing.parent
            if track_dir.exists() and not any(track_dir.iterdir()):
                track_dir.rmdir()
        except OSError as e:
            raise HTTPException(status_code=500, detail=f"Failed to delete: {e}")

    with db_conn() as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("UPDATE jobs SET file_deleted = 1 WHERE id = ?", (job_id,))

        # Clear downloaded_at so the track reappears as missing and can be retried/replaced.
        conn.execute(
            "UPDATE watched_playlist_tracks SET downloaded_at = NULL WHERE job_id = ?",
            (job_id,)
        )

        # Fetch playlist info for M3U rebuild (if this job belongs to a watched playlist)
        playlist_row = conn.execute(
            """SELECT wp.id, wp.name, wp.make_m3u, wp.use_playlists_dir, wp.sync_mode
               FROM watched_playlists wp
               JOIN bulk_imports bi ON bi.watch_playlist_id = wp.id
               JOIN bulk_import_tracks bt ON bt.import_id = bi.id AND bt.job_id = ?
               WHERE wp.make_m3u = 1
               LIMIT 1""",
            (job_id,)
        ).fetchone()

        conn.commit()

    # Rebuild M3U to remove this track from the playlist file
    if playlist_row:
        rebuild_watched_playlist_m3u(
            playlist_row["id"], playlist_row["name"],
            use_playlists_dir=bool(playlist_row["use_playlists_dir"]),
            sync_mode=playlist_row["sync_mode"] or "append",
        )

    return {"deleted": trashed_files, "job_id": job_id, "trashed": bool(trashed_files)}


@app.patch("/api/jobs/{job_id}/tags")
def patch_job_tags(job_id: str, body: PatchTagsRequest, http_request: Request):
    """Correct the artist, title, and/or album tags on a completed download."""
    user_id = http_request.state.user_id
    is_admin = http_request.state.is_admin

    new_artist = body.artist.strip()
    new_title  = body.title.strip()
    new_album  = body.album.strip()
    new_album_artist = body.album_artist.strip()
    new_year = body.year.strip()
    new_track_number = body.track_number if body.track_number and body.track_number > 0 else None
    new_track_total = body.track_total if body.track_total and body.track_total > 0 else None
    if not new_artist or not new_title:
        raise HTTPException(status_code=422, detail="Artist and title are required")
    if new_track_number and new_track_total and new_track_number > new_track_total:
        raise HTTPException(status_code=422, detail="Track number cannot be greater than total tracks")

    with db_conn() as conn:
        conn.row_factory = sqlite3.Row
        _scope_frag, _scope_params = _user_scope(user_id, is_admin)
        row = conn.execute(
            f"SELECT * FROM jobs WHERE id = ? AND {_scope_frag}",
            (job_id, *_scope_params)
        ).fetchone()
        rp_playlist = conn.execute(
            "SELECT resolved_path FROM watched_playlist_tracks WHERE job_id = ? AND resolved_path IS NOT NULL LIMIT 1",
            (job_id,)
        ).fetchone()
        rp_artist = conn.execute(
            "SELECT resolved_path FROM watched_artist_tracks WHERE job_id = ? AND resolved_path IS NOT NULL LIMIT 1",
            (job_id,)
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Job not found")

    job = dict(row)
    if job["status"] not in ("completed", "completed_with_errors"):
        raise HTTPException(status_code=400, detail="Only completed jobs can be edited")
    if job.get("file_deleted"):
        raise HTTPException(status_code=400, detail="File has been deleted")

    old_artist = job.get("artist") or ""
    old_title  = job.get("title") or ""
    if not old_artist or not old_title:
        raise HTTPException(status_code=400, detail="Job has no artist/title metadata")

    # Resolve the file on disk — same priority order as delete_job_file
    from utils import check_duplicate
    file_path = None
    for rp_row in (rp_playlist, rp_artist):
        if rp_row and rp_row["resolved_path"]:
            candidate = Path(rp_row["resolved_path"])
            if candidate.is_absolute() and candidate.exists():
                file_path = candidate
                break
    if file_path is None:
        file_path = check_duplicate(old_artist, old_title, user_id=user_id)
    if not file_path or not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")

    # Work out whether we need to rename (artist or title changed)
    needs_rename = (new_artist != old_artist) or (new_title != old_title)
    new_file_path = file_path
    if needs_rename:
        new_stem = f"{sanitize_filename(new_artist)} - {sanitize_filename(new_title)}"
        new_file_path = file_path.parent / (new_stem + file_path.suffix)
        if new_file_path != file_path and new_file_path.exists():
            raise HTTPException(status_code=409, detail="A file with that artist and title already exists")

    # Write tags first (in-place, file stays at old path)
    try:
        apply_metadata_to_file(
            file_path,
            artist=new_artist,
            title=new_title,
            album=new_album,
            year=new_year or None,
            track_number=new_track_number,
            track_total=new_track_total,
            album_artist=new_album_artist or None,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to write tags: {e}")

    # Rename the file and sidecar if needed
    if needs_rename and new_file_path != file_path:
        try:
            file_path.rename(new_file_path)
            set_file_permissions(new_file_path)
            old_lrc = file_path.with_suffix(".lrc")
            if old_lrc.exists():
                try:
                    old_lrc.rename(new_file_path.with_suffix(".lrc"))
                    set_file_permissions(new_file_path.with_suffix(".lrc"))
                except OSError:
                    pass  # Stranded .lrc is a minor nuisance, not worth failing the whole request
        except OSError as e:
            raise HTTPException(status_code=500, detail=f"Tags written but rename failed: {e}")

    # Update the database now that disk is consistent
    resolved_str = str(new_file_path)
    with db_conn() as conn:
        conn.execute(
            "UPDATE jobs SET artist = ?, title = ?, album_name = ? WHERE id = ?",
            (new_artist, new_title, new_album, job_id)
        )
        if rp_playlist:
            conn.execute(
                "UPDATE watched_playlist_tracks SET resolved_path = ? WHERE job_id = ?",
                (resolved_str, job_id)
            )
        if rp_artist:
            conn.execute(
                "UPDATE watched_artist_tracks SET resolved_path = ? WHERE job_id = ?",
                (resolved_str, job_id)
            )
        conn.commit()

    # Fire-and-forget rescan so library managers pick up the rename/retag
    spawn_daemon_thread(trigger_navidrome_scan, user_id=user_id)
    spawn_daemon_thread(trigger_jellyfin_scan, user_id=user_id)

    return {
        "success": True,
        "artist": new_artist,
        "title": new_title,
        "album": new_album,
        "album_artist": new_album_artist,
        "year": new_year,
        "track_number": new_track_number,
        "track_total": new_track_total,
    }


@app.get("/api/jobs/{job_id}/musicbrainz-guess")
def get_job_musicbrainz_guess(job_id: str, artist: str, title: str, http_request: Request):
    """Return a best-effort MusicBrainz tag guess for a queue item."""
    user_id = http_request.state.user_id
    is_admin = http_request.state.is_admin
    artist = (artist or "").strip()
    title = (title or "").strip()
    offset_raw = (http_request.query_params.get("offset") or "0").strip()
    if not artist or not title:
        raise HTTPException(status_code=422, detail="Artist and title are required")
    try:
        offset = int(offset_raw)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid guess offset")
    if offset < 0:
        raise HTTPException(status_code=422, detail="Invalid guess offset")

    with db_conn() as conn:
        conn.row_factory = sqlite3.Row
        _scope_frag, _scope_params = _user_scope(user_id, is_admin)
        row = conn.execute(
            f"SELECT id, status, file_deleted FROM jobs WHERE id = ? AND {_scope_frag}",
            (job_id, *_scope_params)
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    if row["status"] not in ("completed", "completed_with_errors"):
        raise HTTPException(status_code=400, detail="Only completed jobs can be edited")
    if row["file_deleted"]:
        raise HTTPException(status_code=400, detail="File has been deleted")

    guess = guess_musicbrainz_tags(artist, title, offset=offset)
    if not guess:
        raise HTTPException(status_code=404, detail="No more suitable MusicBrainz matches found")

    return guess


@app.delete("/api/jobs/cleanup")
def cleanup_jobs(http_request: Request, status: Optional[str] = None):
    """Delete completed, failed, or stale jobs.

    Admins clear the lot; standard users only clear their own rows.
    Peons get bounced; the UI hides the button but a curious one with dev
    tools open shouldn't get to nuke anything.
    """
    user = getattr(http_request.state, "user", None) or {}
    if user.get("role") == "peon":
        raise HTTPException(status_code=403, detail="Peons cannot clear the queue")

    # First, mark any stale jobs as failed so they get cleaned up
    cleanup_stale_jobs()

    is_admin = http_request.state.is_admin
    user_id = http_request.state.user_id
    scope_clause = "" if is_admin else " AND user_id = ?"
    scope_params: tuple = () if is_admin else (user_id,)

    with db_conn() as conn:
        if status == "completed":
            cursor = conn.execute(
                f"DELETE FROM jobs WHERE status IN ('completed', 'completed_with_errors'){scope_clause}",
                scope_params,
            )
        elif status == "failed":
            cursor = conn.execute(
                f"DELETE FROM jobs WHERE status = 'failed'{scope_clause}",
                scope_params,
            )
        elif status == "stale":
            cursor = conn.execute(
                f"DELETE FROM jobs WHERE status IN ('downloading', 'queued') "
                f"AND created_at < datetime('now', ? || ' seconds'){scope_clause}",
                (str(-STALE_JOB_TIMEOUT), *scope_params),
            )
        else:
            cursor = conn.execute(
                f"DELETE FROM jobs WHERE status IN ('completed', 'completed_with_errors', 'failed'){scope_clause}",
                scope_params,
            )

        deleted_count = cursor.rowcount
        conn.commit()

    return {"deleted": deleted_count}


# =============================================================================
# Trash Bin API
# =============================================================================

@app.get("/api/trash")
def list_trash(http_request: Request):
    """List all files sitting in the trash bin."""
    from settings import get_trash_dir
    user_id = http_request.state.user_id
    trash_dir = get_trash_dir(user_id=user_id)
    if not trash_dir.exists():
        return {"files": [], "total_size": 0}

    files = []
    total_size = 0
    walk_errors = []

    def _on_walk_error(err):
        walk_errors.append(str(err))
        print(f"[trash] Failed to read {getattr(err, 'filename', trash_dir)}: {err}")

    for dirpath, _dirnames, filenames in os.walk(trash_dir, onerror=_on_walk_error):
        for fname in filenames:
            fp = Path(dirpath) / fname
            if fp.suffix.lower() == ".lrc":
                continue
            try:
                rel = fp.relative_to(trash_dir)
                stat = fp.stat()
                size = stat.st_size
                total_size += size
                files.append({
                    "path": str(rel),
                    "name": fp.stem,
                    "ext": fp.suffix,
                    "size": size,
                    "modified": stat.st_mtime,
                })
            except OSError as e:
                print(f"[trash] Failed to inspect {fp}: {e}")
                continue

    if walk_errors and not files:
        raise HTTPException(status_code=500, detail="Trash exists but could not be read")

    # Sort newest first so the most recently trashed files appear at the top
    files.sort(key=lambda f: f["modified"], reverse=True)
    return {"files": files, "total_size": total_size}


@app.get("/api/trash/stream")
def stream_trash_file(http_request: Request, path: str = ""):
    """Stream a trashed audio file for in-browser playback. Have a listen before you decide."""
    from settings import get_trash_dir
    user_id = http_request.state.user_id
    if not path:
        raise HTTPException(status_code=400, detail="No path specified")

    trash_dir = get_trash_dir(user_id=user_id)
    trash_path = trash_dir / path

    try:
        trash_path.resolve().relative_to(trash_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path")

    if not trash_path.exists():
        raise HTTPException(status_code=404, detail="File not found in trash")

    mime = _AUDIO_MIME_TYPES.get(trash_path.suffix.lower(), "audio/mpeg")
    return FileResponse(path=str(trash_path), media_type=mime)


@app.post("/api/trash/restore")
def restore_trash_file(http_request: Request, path: str = ""):
    """Restore a specific file from the trash to its original library location."""
    from utils import restore_from_trash
    from settings import get_trash_dir
    user_id = http_request.state.user_id
    if not path:
        raise HTTPException(status_code=400, detail="No path specified")

    trash_dir = get_trash_dir(user_id=user_id)
    trash_path = trash_dir / path

    # Safety: make sure the resolved path is actually inside the trash dir
    try:
        trash_path.resolve().relative_to(trash_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path")

    if not trash_path.exists():
        raise HTTPException(status_code=404, detail="File not found in trash")

    restored = restore_from_trash(trash_path, user_id=user_id)
    if not restored:
        raise HTTPException(status_code=500, detail="Failed to restore file")

    return {"restored": str(restored.name), "path": str(restored)}


@app.delete("/api/trash")
def empty_trash(http_request: Request):
    """Permanently delete everything in the trash bin. No coming back from this one."""
    if not http_request.state.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")

    from settings import get_trash_dir
    import shutil
    user_id = http_request.state.user_id
    trash_dir = get_trash_dir(user_id=user_id)
    if not trash_dir.exists():
        return {"deleted": 0}

    count = sum(1 for _ in trash_dir.rglob("*") if _.is_file())
    shutil.rmtree(trash_dir, ignore_errors=True)
    return {"deleted": count}


@app.delete("/api/trash/file")
def delete_trash_file(http_request: Request, path: str = ""):
    """Permanently delete a single file from the trash. Truly gone this time."""
    from settings import get_trash_dir
    user_id = http_request.state.user_id
    if not path:
        raise HTTPException(status_code=400, detail="No path specified")

    trash_dir = get_trash_dir(user_id=user_id)
    trash_path = trash_dir / path

    try:
        trash_path.resolve().relative_to(trash_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path")

    if not trash_path.exists():
        raise HTTPException(status_code=404, detail="File not found in trash")

    try:
        trash_path.unlink()
        # Also remove the lyrics sidecar if present
        lrc = trash_path.with_suffix(".lrc")
        if lrc.exists():
            lrc.unlink()
        # Clean up empty directories
        parent = trash_path.parent
        while parent != trash_dir and str(parent).startswith(str(trash_dir)):
            try:
                if parent.exists() and not any(parent.iterdir()):
                    parent.rmdir()
            except OSError:
                break
            parent = parent.parent
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete: {e}")

    return {"deleted": trash_path.name}


# =============================================================================
# Blacklist / Report API
# =============================================================================

@app.post("/api/blacklist")
def add_blacklist_entry(request: BlacklistRequest, http_request: Request):
    """Report a bad track and/or block an uploader."""
    if not http_request.state.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    if not request.video_id and not request.uploader:
        raise HTTPException(status_code=400, detail="Need at least a video_id or uploader to blacklist")

    entries_created = []

    with db_conn() as conn:
        # Blacklist the specific video
        if request.video_id:
            # Upsert  -  if the same video_id is already blacklisted, update the reason
            existing = conn.execute(
                "SELECT id FROM blacklist WHERE video_id = ?", (request.video_id,)
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE blacklist SET reason = ?, note = ?, job_id = COALESCE(?, job_id) WHERE id = ?",
                    (request.reason, request.note, request.job_id, existing[0])
                )
                entries_created.append({"type": "video", "id": existing[0], "updated": True})
            else:
                cursor = conn.execute(
                    "INSERT INTO blacklist (video_id, source, reason, note, job_id) VALUES (?, ?, ?, ?, ?)",
                    (request.video_id, request.source, request.reason, request.note, request.job_id)
                )
                entries_created.append({"type": "video", "id": cursor.lastrowid})

        # Optionally blacklist the uploader too
        if request.block_uploader and request.uploader:
            uploader_lower = request.uploader.lower()
            existing = conn.execute(
                "SELECT id FROM blacklist WHERE lower(uploader) = ? AND source = ? AND (video_id IS NULL OR video_id = '')",
                (uploader_lower, request.source)
            ).fetchone()
            if not existing:
                cursor = conn.execute(
                    "INSERT INTO blacklist (uploader, source, reason, note, job_id) VALUES (?, ?, ?, ?, ?)",
                    (request.uploader, request.source, request.reason, request.note, request.job_id)
                )
                entries_created.append({"type": "uploader", "id": cursor.lastrowid})

        conn.commit()

    return {"entries": entries_created}


@app.get("/api/blacklist")
def list_blacklist(http_request: Request, limit: int = 100, offset: int = 0):
    """List all blacklist entries for the management UI."""
    if not http_request.state.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    with db_conn() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM blacklist ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset)
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM blacklist").fetchone()[0]

    return {
        "entries": [dict(r) for r in rows],
        "total": total
    }


@app.delete("/api/blacklist/{entry_id}")
def remove_blacklist_entry(entry_id: int, http_request: Request):
    """Remove a blacklist entry by ID."""
    if not http_request.state.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    with db_conn() as conn:
        cursor = conn.execute("DELETE FROM blacklist WHERE id = ?", (entry_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Blacklist entry not found")
        conn.commit()

    return {"deleted": entry_id}


# =============================================================================
# Bulk Import API
# =============================================================================

@app.post("/api/bulk-import-async")
def bulk_import_async(body: AsyncBulkImportRequest, http_request: Request):
    """Start an async bulk import job"""
    _enforce_peon_format(http_request, body)
    lines = body.songs.strip().split('\n')
    import_id = str(uuid.uuid4())[:8]
    user_id = http_request.state.user_id

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

    # Normalise priority_source: empty / "any" / "all" all mean "no preference".
    _priority_source = (body.priority_source or "").strip().lower() or None
    if _priority_source in ("any", "all", "none"):
        _priority_source = None
    _preferred_sources = (body.preferred_sources or "all").strip().lower() or "all"
    if _preferred_sources in ("any", "none"):
        _preferred_sources = "all"
    _playlist_name = body.playlist_name
    if body.create_playlist:
        _playlist_name = sanitize_playlist_name(body.playlist_name, body.playlist_source_url or import_id)

    # Create bulk import record
    with db_conn() as conn:
        conn.execute(
            """INSERT INTO bulk_imports
               (id, status, total_tracks, create_playlist, playlist_name, convert_to_flac, use_playlists_dir, user_id, preferred_sources, priority_source)
               VALUES (?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?)""",
            (import_id, len(tracks_to_import), int(body.create_playlist),
             _playlist_name, int(body.convert_to_flac), int(body.use_playlists_dir), user_id, _preferred_sources, _priority_source)
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
def get_bulk_import_status(import_id: str, http_request: Request):
    """Get status of a bulk import job"""
    user_id = http_request.state.user_id
    is_admin = http_request.state.is_admin
    with db_conn() as conn:
        conn.row_factory = sqlite3.Row

        _scope_frag, _scope_params = _user_scope(user_id, is_admin)
        cursor = conn.execute(
            f"SELECT * FROM bulk_imports WHERE id = ? AND {_scope_frag}",
            (import_id, *_scope_params)
        )
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

        # Count download statuses by joining bulk_import_tracks with jobs.
        # dupe_skipped: completed jobs whose error field starts with "Already exists"
        # (the duplicate-skip path in process_download marks the job completed and
        # stuffs a human-readable reason into error). It's the only API-visible
        # signal that the library dupe check actually short-circuited the work.
        cursor = conn.execute(
            """SELECT
                   SUM(CASE WHEN j.status IN ('completed', 'completed_with_errors') THEN 1 ELSE 0 END) as completed,
                   SUM(CASE WHEN j.status = 'failed' THEN 1 ELSE 0 END) as download_failed,
                   SUM(CASE WHEN j.status IN ('queued', 'downloading') THEN 1 ELSE 0 END) as still_queued,
                   SUM(CASE WHEN j.status IN ('completed', 'completed_with_errors')
                             AND (j.error LIKE 'Already exists%' OR j.error LIKE 'Already exists in %')
                            THEN 1 ELSE 0 END) as dupe_skipped
               FROM bulk_import_tracks t
               JOIN jobs j ON t.job_id = j.id
               WHERE t.import_id = ?""",
            (import_id,)
        )
        row = cursor.fetchone()
        completed_count = row[0] or 0
        download_failed_count = row[1] or 0
        still_queued_count = row[2] or 0
        dupe_skipped_count = row[3] or 0

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
        "dupe_skipped": dupe_skipped_count,
        "rate_limited": import_row["rate_limited_until"] is not None,
        "error": import_row["error"],
        "recent_tracks": recent_tracks,
        "complete": all_done
    }


@app.get("/api/bulk-imports")
def list_bulk_imports(limit: int = 10, http_request: Request = None):
    """List recent bulk imports"""
    user_id = http_request.state.user_id if http_request else None
    is_admin = http_request.state.is_admin if http_request else True
    with db_conn() as conn:
        conn.row_factory = sqlite3.Row

        _scope_frag, _scope_params = _user_scope(user_id, is_admin)
        cursor = conn.execute(
            f"SELECT * FROM bulk_imports WHERE {_scope_frag} ORDER BY created_at DESC LIMIT ?",
            (*_scope_params, limit)
        )
        imports = [dict(row) for row in cursor.fetchall()]

    return {"imports": imports}


# =============================================================================
# Playlist Fetch API (Spotify, Amazon Music)
# =============================================================================

@app.post("/api/fetch-playlist")
@app.post("/api/spotify-playlist")  # Backwards compat
def fetch_playlist(request: Request, body: PlaylistFetchRequest):
    """Fetch track list from a supported public playlist URL."""
    url = body.url.strip()
    platform, _ = detect_playlist_platform(url)

    # fetch_playlist_tracks returns (artist, title) tuples - reformat to the
    # "Artist - Title" strings the bulk import UI expects, plus a playlist name.
    user_id = request.state.user_id
    tracks_tuples, playlist_name, warning = fetch_playlist_tracks(url, platform, user_id=user_id)
    playlist_name = sanitize_playlist_name(playlist_name, url)
    tracks = [f"{artist} - {title}" for artist, title in tracks_tuples]
    resp = {"tracks": tracks, "playlist_name": playlist_name, "count": len(tracks), "platform": platform}
    if warning:
        resp["warning"] = warning
    return resp


# =============================================================================
# Watched Playlists API
# =============================================================================

@app.post("/api/watched-playlists")
def add_watched_playlist(body: WatchedPlaylistRequest, http_request: Request):
    """Add a new playlist to watch for new tracks"""
    _enforce_peon_format(http_request, body)
    platform, platform_id = detect_playlist_platform(body.url)
    user_id = http_request.state.user_id

    # ListenBrainz username: fan out into one watched playlist per "Created for You" playlist
    if platform == "listenbrainz_user":
        lb_playlists = fetch_listenbrainz_createdfor(platform_id)

        # LB playlists are weekly  -  mirror mode is the sensible default
        sync_mode = "mirror"
        # Weekly refresh makes sense since LB regenerates them weekly
        refresh_hours = max(body.refresh_interval_hours, 168)

        created = []
        skipped = 0

        with db_conn() as conn:
            conn.row_factory = sqlite3.Row
            for lb in lb_playlists:
                existing = conn.execute(
                    "SELECT id FROM watched_playlists WHERE url = ? AND (user_id = ? OR (user_id IS NULL AND ? IS NULL))",
                    (lb["playlist_url"], user_id, user_id)
                ).fetchone()
                if not existing:
                    # LB rotates playlist UUIDs weekly, so also match by username + name prefix
                    # (the part before ", week of YYYY-MM-DD") to avoid duplicate watched playlists.
                    name_prefix = lb["name"].split(", week of")[0]
                    existing = conn.execute(
                        """SELECT id FROM watched_playlists
                           WHERE lb_username = ? AND name LIKE ?
                           AND (user_id = ? OR (user_id IS NULL AND ? IS NULL))""",
                        (platform_id, name_prefix + "%", user_id, user_id)
                    ).fetchone()
                if existing:
                    skipped += 1
                    continue

                playlist_id = str(uuid.uuid4())[:8]
                _lb_priority = (body.priority_source or "").strip().lower() or None
                if _lb_priority in ("any", "all", "none"):
                    _lb_priority = None
                conn.execute("""
                    INSERT INTO watched_playlists
                    (id, url, name, platform, refresh_interval_hours, convert_to_flac, make_m3u, use_playlists_dir, sync_mode, last_track_count, user_id, preferred_sources, priority_source, lb_username, custom_subdir)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (playlist_id, lb["playlist_url"], lb["name"], "listenbrainz",
                      refresh_hours, int(body.convert_to_flac),
                      int(body.make_m3u), int(body.use_playlists_dir), sync_mode, len(lb["tracks"]), user_id,
                      body.preferred_sources or "all", _lb_priority, platform_id,
                      (body.custom_subdir or "").strip() or None))

                for artist, title in lb["tracks"]:
                    track_hash = hash_track(artist, title)
                    conn.execute("""
                        INSERT OR IGNORE INTO watched_playlist_tracks
                        (playlist_id, track_hash, artist, title)
                        VALUES (?, ?, ?, ?)
                    """, (playlist_id, track_hash, artist, title))

                created.append({"id": playlist_id, "name": lb["name"], "track_count": len(lb["tracks"])})

            conn.commit()

        if not created:
            raise HTTPException(status_code=409, detail="All ListenBrainz 'Created for You' playlists are already being watched")

        # Kick off bulk imports in background for each new playlist
        for i, lb in enumerate([p for p in lb_playlists if any(c["name"] == p["name"] for c in created)]):
            playlist_id = created[i]["id"]
            if lb["tracks"]:
                start_bulk_import_for_tracks(
                    lb["tracks"],
                    body.convert_to_flac,
                    watch_playlist_id=playlist_id,
                    use_playlists_dir=body.use_playlists_dir,
                    user_id=user_id,
                    preferred_sources=body.preferred_sources or "all",
                    priority_source=body.priority_source,
                    custom_subdir=(body.custom_subdir or "").strip() or None,
                )

        return {
            "created": len(created),
            "skipped": skipped,
            "playlists": created,
            "message": f"Added {len(created)} ListenBrainz playlist(s)"
            + (f" ({skipped} already watched)" if skipped else ""),
        }

    with db_conn() as conn:
        # Check for duplicate
        conn.row_factory = sqlite3.Row

        existing = conn.execute(
            "SELECT id FROM watched_playlists WHERE url = ? AND (user_id = ? OR (user_id IS NULL AND ? IS NULL))",
            (body.url, user_id, user_id)
        ).fetchone()

        if existing:
            raise HTTPException(status_code=409, detail="This playlist is already being watched")

        # Fetch playlist to get name and initial tracks
        try:
            tracks, playlist_name, fetch_warning = fetch_playlist_tracks(body.url, platform, user_id=user_id)
            playlist_name = sanitize_playlist_name(playlist_name, body.url)
        except HTTPException:
            raise

        # Create playlist record
        playlist_id = str(uuid.uuid4())[:8]

        sync_mode = body.sync_mode if body.sync_mode in ("append", "mirror") else "append"
        _new_priority = (body.priority_source or "").strip().lower() or None
        if _new_priority in ("any", "all", "none"):
            _new_priority = None
        conn.execute("""
            INSERT INTO watched_playlists
            (id, url, name, platform, refresh_interval_hours, convert_to_flac, make_m3u, use_playlists_dir, sync_mode, last_track_count, user_id, preferred_sources, priority_source, custom_subdir)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (playlist_id, body.url, playlist_name, platform,
              body.refresh_interval_hours, int(body.convert_to_flac),
              int(body.make_m3u), int(body.use_playlists_dir), sync_mode, len(tracks), user_id,
              "soundcloud" if platform == "soundcloud" and (not body.preferred_sources or body.preferred_sources == "all") else (body.preferred_sources or "all"),
              _new_priority,
              (body.custom_subdir or "").strip() or None))

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
            body.convert_to_flac,
            watch_playlist_id=playlist_id,
            use_playlists_dir=body.use_playlists_dir,
            user_id=user_id,
            preferred_sources=body.preferred_sources or "all",
            priority_source=body.priority_source,
            custom_subdir=(body.custom_subdir or "").strip() or None,
        )

    resp = {
        "id": playlist_id,
        "name": playlist_name,
        "platform": platform,
        "track_count": len(tracks),
        "refresh_interval_hours": body.refresh_interval_hours,
        "import_id": import_id,
        "message": f"Now watching '{playlist_name}' with {len(tracks)} tracks queued for download"
    }
    if fetch_warning:
        resp["warning"] = fetch_warning
    return resp


@app.get("/api/watched-playlists")
def list_watched_playlists(http_request: Request):
    """List all watched playlists"""
    user_id = http_request.state.user_id
    is_admin = http_request.state.is_admin
    with db_conn() as conn:
        conn.row_factory = sqlite3.Row

        _scope_frag, _scope_params = _user_scope(user_id, is_admin)
        playlists = conn.execute(f"""
            SELECT
                wp.*,
                (SELECT COUNT(*) FROM watched_playlist_tracks wpt WHERE wpt.playlist_id = wp.id) as tracked_count,
                (SELECT COUNT(*) FROM watched_playlist_tracks wpt WHERE wpt.playlist_id = wp.id AND wpt.downloaded_at IS NOT NULL) as downloaded_count
            FROM watched_playlists wp
            WHERE {_scope_frag}
            ORDER BY wp.created_at DESC
        """, _scope_params).fetchall()

    return {
        "playlists": [dict(p) for p in playlists]
    }


@app.get("/api/watched-playlists/schedule")
def get_watched_schedule():
    """Get the current watched playlist check schedule"""
    return {
        "check_interval_hours": WATCHED_PLAYLIST_CHECK_HOURS,
        "enabled": WATCHED_PLAYLIST_CHECK_HOURS > 0
    }


@app.get("/api/watched-playlists/{playlist_id}")
def get_watched_playlist(playlist_id: str, http_request: Request):
    """Get details of a watched playlist including track history"""
    user_id = http_request.state.user_id
    is_admin = http_request.state.is_admin
    with db_conn() as conn:
        conn.row_factory = sqlite3.Row

        _scope_frag, _scope_params = _user_scope(user_id, is_admin)
        playlist = conn.execute(
            f"SELECT * FROM watched_playlists WHERE id = ? AND {_scope_frag}",
            (playlist_id, *_scope_params)
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
def update_watched_playlist(playlist_id: str, request: WatchedPlaylistUpdate, http_request: Request):
    """Update watched playlist settings"""
    if _is_peon(http_request):
        request.convert_to_flac = None  # Peons cannot change conversion setting
    user_id = http_request.state.user_id
    is_admin = http_request.state.is_admin
    with db_conn() as conn:
        conn.row_factory = sqlite3.Row

        _scope_frag, _scope_params = _user_scope(user_id, is_admin)
        playlist = conn.execute(
            f"SELECT * FROM watched_playlists WHERE id = ? AND {_scope_frag}",
            (playlist_id, *_scope_params)
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
            if request.enabled:
                # Resuming gives the playlist a clean slate: drop any auto-pause
                # note and reset the gone-strike counter so it isn't re-paused on
                # the next single hiccup.
                updates.append("auto_paused = 0")
                updates.append("pause_reason = NULL")
                updates.append("gone_strikes = 0")

        if request.convert_to_flac is not None:
            updates.append("convert_to_flac = ?")
            params.append(int(request.convert_to_flac))

        if request.make_m3u is not None:
            updates.append("make_m3u = ?")
            params.append(int(request.make_m3u))

        if request.use_playlists_dir is not None:
            updates.append("use_playlists_dir = ?")
            params.append(int(request.use_playlists_dir))

        if request.sync_mode is not None and request.sync_mode in ("append", "mirror"):
            updates.append("sync_mode = ?")
            params.append(request.sync_mode)

        if request.preferred_sources is not None:
            updates.append("preferred_sources = ?")
            params.append(request.preferred_sources or "all")

        if request.priority_source is not None:
            # Empty / "any" / "all" / "none" all clear the override.
            _p = (request.priority_source or "").strip().lower() or None
            if _p in ("any", "all", "none"):
                _p = None
            updates.append("priority_source = ?")
            params.append(_p)

        if request.custom_subdir is not None:
            updates.append("custom_subdir = ?")
            params.append((request.custom_subdir or "").strip() or None)

        if updates:
            params.append(playlist_id)
            conn.execute(
                f"UPDATE watched_playlists SET {', '.join(updates)} WHERE id = ?",
                params
            )
            conn.commit()

        # Fetch updated record
        updated = conn.execute(
            f"SELECT * FROM watched_playlists WHERE id = ? AND {_scope_frag}",
            (playlist_id, *_scope_params)
        ).fetchone()

    return {"playlist": dict(updated)}


@app.delete("/api/watched-playlists/{playlist_id}")
def delete_watched_playlist(playlist_id: str, http_request: Request):
    """Remove a watched playlist and its track history"""
    user_id = http_request.state.user_id
    is_admin = http_request.state.is_admin
    with db_conn() as conn:
        conn.row_factory = sqlite3.Row

        _scope_frag, _scope_params = _user_scope(user_id, is_admin)
        playlist = conn.execute(
            f"SELECT * FROM watched_playlists WHERE id = ? AND {_scope_frag}",
            (playlist_id, *_scope_params)
        ).fetchone()

        if not playlist:
            raise HTTPException(status_code=404, detail="Watched playlist not found")

        # Delete tracks first (FK constraint)
        conn.execute("DELETE FROM watched_playlist_tracks WHERE playlist_id = ?", (playlist_id,))
        conn.execute("DELETE FROM watched_playlists WHERE id = ?", (playlist_id,))
        conn.commit()

    return {"message": f"Deleted watched playlist '{playlist['name']}'"}


@app.get("/api/watched-playlists/{playlist_id}/missing")
def get_missing_watched_tracks(playlist_id: str, http_request: Request):
    """Return tracks that were never successfully downloaded for a watched playlist.

    A track is 'missing' if it has no downloaded_at timestamp and either has no job,
    or its job ended in failure. Tracks still actively queued or downloading are excluded.
    """
    user_id = http_request.state.user_id
    is_admin = http_request.state.is_admin
    with db_conn() as conn:
        conn.row_factory = sqlite3.Row

        _scope_frag, _scope_params = _user_scope(user_id, is_admin)
        playlist = conn.execute(
            f"SELECT name FROM watched_playlists WHERE id = ? AND {_scope_frag}",
            (playlist_id, *_scope_params)
        ).fetchone()

        if not playlist:
            raise HTTPException(status_code=404, detail="Watched playlist not found")

        rows = conn.execute(
            """SELECT wpt.artist, wpt.title, wpt.first_seen, wpt.removed_at, j.status as job_status
               FROM watched_playlist_tracks wpt
               LEFT JOIN jobs j ON wpt.job_id = j.id
               WHERE wpt.playlist_id = ?
                 AND wpt.downloaded_at IS NULL
                 AND (j.status IS NULL OR j.status IN ('failed', 'completed_with_errors'))""",
            (playlist_id,)
        ).fetchall()

    return {
        "playlist_name": playlist["name"],
        "missing": [dict(r) for r in rows],
        "count": len(rows),
    }


@app.get("/api/watched-playlists/{playlist_id}/tracks")
def get_watched_playlist_tracks(playlist_id: str, http_request: Request):
    """Return all tracks for a watched playlist with their download status.

    Each track includes its current job status so the UI can show downloaded,
    failed, pending, and mirror-removed tracks in one place.
    """
    user_id = http_request.state.user_id
    is_admin = http_request.state.is_admin
    with db_conn() as conn:
        conn.row_factory = sqlite3.Row

        _scope_frag, _scope_params = _user_scope(user_id, is_admin)
        playlist = conn.execute(
            f"SELECT name FROM watched_playlists WHERE id = ? AND {_scope_frag}",
            (playlist_id, *_scope_params)
        ).fetchone()

        if not playlist:
            raise HTTPException(status_code=404, detail="Watched playlist not found")

        rows = conn.execute(
            """SELECT wpt.artist, wpt.title, wpt.downloaded_at, wpt.job_id,
                      wpt.first_seen, wpt.removed_at,
                      j.status as job_status, j.error as job_error
               FROM watched_playlist_tracks wpt
               LEFT JOIN jobs j ON wpt.job_id = j.id
               WHERE wpt.playlist_id = ?
               ORDER BY wpt.first_seen""",
            (playlist_id,)
        ).fetchall()

    tracks = [dict(r) for r in rows]
    downloaded = sum(1 for t in tracks if t["downloaded_at"])
    failed = sum(1 for t in tracks if not t["downloaded_at"] and t["job_status"] in ("failed", "completed_with_errors", None))
    pending = sum(1 for t in tracks if not t["downloaded_at"] and t["job_status"] in ("queued", "downloading"))

    return {
        "playlist_name": playlist["name"],
        "tracks": tracks,
        "total": len(tracks),
        "downloaded": downloaded,
        "failed": failed,
        "pending": pending,
    }


@app.post("/api/watched-playlists/{playlist_id}/retry-track")
def retry_missing_track(playlist_id: str, request: RetryMissingTrackRequest, http_request: Request):
    """Manually retry a single missing track for a watched playlist.

    Searches all sources for the best match and queues a download, routing the
    result back into the watched playlist's folder and M3U (if enabled).
    """
    user_id = http_request.state.user_id
    is_admin = http_request.state.is_admin
    with db_conn() as conn:
        conn.row_factory = sqlite3.Row
        _scope_frag, _scope_params = _user_scope(user_id, is_admin)
        playlist = conn.execute(
            f"SELECT id, name, convert_to_flac, use_playlists_dir, preferred_sources, priority_source, custom_subdir FROM watched_playlists WHERE id = ? AND {_scope_frag}",
            (playlist_id, *_scope_params)
        ).fetchone()

    if not playlist:
        raise HTTPException(status_code=404, detail="Watched playlist not found")

    custom_subdir = (playlist["custom_subdir"] or "").strip() or None
    # Kick off a single-track bulk import linked to this watched playlist so the
    # M3U rebuild and watched_playlist_tracks update happen automatically on completion.
    import_id = start_bulk_import_for_tracks(
        tracks=[(request.artist, request.title)],
        convert_to_flac=bool(playlist["convert_to_flac"]),
        watch_playlist_id=playlist_id,
        use_playlists_dir=bool(playlist["use_playlists_dir"]),
        user_id=user_id,
        preferred_sources=playlist["preferred_sources"] or "all",
        priority_source=playlist["priority_source"],
        custom_subdir=custom_subdir,
    )

    return {"import_id": import_id, "status": "queued", "message": f"Searching for {request.artist} - {request.title}"}


@app.get("/api/watched-playlists/{playlist_id}/track-candidates")
def get_watched_playlist_track_candidates(
    playlist_id: str,
    artist: str,
    title: str,
    http_request: Request,
    limit: int = 4,
):
    """Return top manual-pick candidates for a missing watched-playlist track."""
    user_id = http_request.state.user_id
    is_admin = http_request.state.is_admin
    artist = (artist or "").strip()
    title = (title or "").strip()
    if not artist or not title:
        raise HTTPException(status_code=400, detail="artist and title are required")

    with db_conn() as conn:
        conn.row_factory = sqlite3.Row
        _scope_frag, _scope_params = _user_scope(user_id, is_admin)
        playlist = conn.execute(
            f"""SELECT id, preferred_sources
                FROM watched_playlists
                WHERE id = ? AND {_scope_frag}""",
            (playlist_id, *_scope_params)
        ).fetchone()

    if not playlist:
        raise HTTPException(status_code=404, detail="Watched playlist not found")

    preferred_sources = playlist["preferred_sources"] or "all"
    sources = None if preferred_sources == "all" else [s.strip() for s in preferred_sources.split(",") if s.strip()]
    query = f"{artist} - {title}".strip(" -")
    fetch_limit = max(1, min(limit, 10))
    results, _ = search_all(query, limit=fetch_limit, sources=sources, include_soulseek=True)

    return {
        "query": query,
        "results": results[:fetch_limit],
    }


@app.post("/api/watched-playlists/{playlist_id}/queue-track-candidate")
def queue_watched_playlist_track_candidate(
    playlist_id: str,
    request: QueueMissingTrackCandidateRequest,
    http_request: Request,
):
    """Queue a specific candidate for a missing watched-playlist track."""
    user_id = http_request.state.user_id
    is_admin = http_request.state.is_admin
    source = (request.source or "youtube").strip().lower()
    artist = (request.artist or "").strip()
    title = (request.title or "").strip()
    if not artist or not title:
        raise HTTPException(status_code=400, detail="artist and title are required")

    if source == "youtube":
        if not request.video_id or not is_valid_youtube_id(request.video_id):
            raise HTTPException(status_code=400, detail="Invalid YouTube video ID")
    elif source in URL_BASED_SOURCES:
        if not request.source_url:
            raise HTTPException(status_code=400, detail=f"{source.capitalize()} candidate requires source_url")
    elif source == "soulseek":
        if not (request.slskd_username and request.slskd_filename):
            raise HTTPException(status_code=400, detail="Soulseek candidate requires username and filename")
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported source: {source}")

    with db_conn() as conn:
        conn.row_factory = sqlite3.Row
        _scope_frag, _scope_params = _user_scope(user_id, is_admin)
        playlist = conn.execute(
            f"""SELECT id, name, convert_to_flac, use_playlists_dir, custom_subdir
                FROM watched_playlists
                WHERE id = ? AND {_scope_frag}""",
            (playlist_id, *_scope_params)
        ).fetchone()
        if not playlist:
            raise HTTPException(status_code=404, detail="Watched playlist not found")

        track_hash = hash_track(artist, title)
        track = conn.execute(
            "SELECT playlist_id FROM watched_playlist_tracks WHERE playlist_id = ? AND track_hash = ?",
            (playlist_id, track_hash),
        ).fetchone()
        if not track:
            raise HTTPException(status_code=404, detail="Watched playlist track not found")

        job_id = str(uuid.uuid4())[:8]
        if source == "soulseek":
            source_url = f"soulseek://{request.slskd_username}/{request.slskd_filename}"
        elif source in URL_BASED_SOURCES:
            source_url = request.source_url
        else:
            source_url = f"https://www.youtube.com/watch?v={request.video_id}"

        conn.execute(
            """INSERT INTO jobs
               (id, video_id, title, artist, status, download_type, source, slskd_username, slskd_filename,
                slskd_size, convert_to_flac, source_url, user_id)
               VALUES (?, ?, ?, ?, 'queued', 'single', ?, ?, ?, ?, ?, ?, ?)""",
            (
                job_id,
                request.video_id,
                title,
                artist,
                source,
                request.slskd_username,
                request.slskd_filename,
                request.slskd_size,
                int(bool(playlist["convert_to_flac"])),
                source_url,
                user_id,
            ),
        )
        conn.execute(
            "UPDATE watched_playlist_tracks SET job_id = ?, downloaded_at = NULL WHERE playlist_id = ? AND track_hash = ?",
            (job_id, playlist_id, track_hash),
        )
        conn.commit()

    convert_to_flac = bool(playlist["convert_to_flac"])
    custom_subdir = (playlist["custom_subdir"] or "").strip() or None
    playlist_name = playlist["name"] if (playlist["use_playlists_dir"] or custom_subdir) else None
    use_playlists_dir = bool(playlist["use_playlists_dir"])

    if source == "soulseek":
        spawn_daemon_thread(
            process_slskd_download,
            job_id,
            request.slskd_username,
            request.slskd_filename,
            artist,
            title,
            convert_to_flac,
            user_id=user_id,
            playlist_name=playlist_name,
            use_playlists_dir=use_playlists_dir,
            custom_subdir=custom_subdir,
            slskd_size=request.slskd_size,
        )
    else:
        spawn_daemon_thread(
            process_download,
            job_id,
            request.video_id,
            convert_to_flac,
            source_url=source_url,
            playlist_name=playlist_name,
            use_playlists_dir=use_playlists_dir,
            user_id=user_id,
            custom_subdir=custom_subdir,
        )

    return {"job_id": job_id, "status": "queued", "message": f"Queued {artist} - {title}"}


@app.post("/api/watched-playlists/{playlist_id}/refresh")
def refresh_single_playlist(playlist_id: str, http_request: Request):
    """Force an immediate refresh of a specific watched playlist"""
    user_id = http_request.state.user_id
    is_admin = http_request.state.is_admin
    with db_conn() as conn:
        conn.row_factory = sqlite3.Row

        _scope_frag, _scope_params = _user_scope(user_id, is_admin)
        playlist = conn.execute(
            f"SELECT * FROM watched_playlists WHERE id = ? AND {_scope_frag}",
            (playlist_id, *_scope_params)
        ).fetchone()

        if not playlist:
            raise HTTPException(status_code=404, detail="Watched playlist not found")

    result = refresh_watched_playlist(playlist_id)
    return result


@app.post("/api/watched-playlists/check-all")
def check_all_watched_playlists(http_request: Request):
    """Check all playlists due for refresh (called by cron)"""
    if not http_request.state.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
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


# ---------------------------------------------------------------------------
# Watched Artists
# ---------------------------------------------------------------------------

@app.get("/api/watched-artists/search")
def search_watched_artist(q: str):
    """Search MusicBrainz for an artist by name. Returns up to 5 candidates."""
    if not q or not q.strip():
        return {"results": []}
    results = search_artist_mbid(q.strip())
    return {"results": results}


@app.post("/api/watched-artists")
def add_watched_artist(body: WatchedArtistRequest, http_request: Request):
    """Add an artist to watch. Seeds all known singles then queues any after from_date."""
    _enforce_peon_format(http_request, body)
    user_id = http_request.state.user_id

    with db_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM watched_artists WHERE mbid = ? AND (user_id = ? OR (user_id IS NULL AND ? IS NULL))",
            (body.mbid, user_id, user_id)
        ).fetchone()
        if existing:
            raise HTTPException(status_code=400, detail=f"Already watching this artist (id: {existing[0]})")

    artist_id = str(uuid.uuid4())[:8]

    with db_conn() as conn:
        conn.execute(
            """INSERT INTO watched_artists
               (id, name, mbid, from_date, refresh_interval_hours, convert_to_flac, user_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (artist_id, body.name, body.mbid, body.from_date,
             body.refresh_interval_hours, int(body.convert_to_flac), user_id)
        )
        conn.commit()

    # The first refresh seeds the entire back-catalogue from MusicBrainz, which
    # for a prolific artist (Radiohead, looking at you) can take a couple of
    # minutes. Run it in the background so the request returns immediately; the UI
    # already polls refresh_state/refresh_stage to show progress. Running it inline
    # would leave the HTTP request hanging long enough to trip client/proxy timeouts.
    spawn_daemon_thread(refresh_watched_artist, artist_id)
    return {
        "id": artist_id,
        "name": body.name,
        "mbid": body.mbid,
        "from_date": body.from_date,
        "refresh_state": "running",
        "seeding": True,
    }


@app.get("/api/watched-artists")
def list_watched_artists(http_request: Request):
    """List all watched artists with track counts."""
    user_id = http_request.state.user_id
    is_admin = http_request.state.is_admin
    with db_conn() as conn:
        conn.row_factory = sqlite3.Row
        _scope_frag, _scope_params = _user_scope(user_id, is_admin)
        rows = conn.execute(
            f"""SELECT wa.*,
                (SELECT COUNT(*) FROM watched_artist_tracks wat WHERE wat.artist_id = wa.id) as tracked_count,
                (SELECT COUNT(*) FROM watched_artist_tracks wat WHERE wat.artist_id = wa.id AND wat.downloaded_at IS NOT NULL) as downloaded_count
               FROM watched_artists wa
               WHERE {_scope_frag}
               ORDER BY wa.name COLLATE NOCASE""",
            _scope_params
        ).fetchall()
    return {"artists": [dict(r) for r in rows]}


@app.get("/api/upgrades/candidates")
def list_upgrade_candidates(http_request: Request, page: int = 1, per_page: int = 10):
    """Paginated list of library files sitting below target quality (Watched Upgrades).

    Read-only: each row carries any cached search result, but searching is triggered
    per-row by the client (see /search below). Hidden from peons.
    """
    if _is_peon(http_request):
        raise HTTPException(status_code=403, detail="Not available for this account")
    if not get_setting_bool("enable_track_upgrades", False):
        return {"enabled": False, "items": [], "total": 0, "page": 1, "pages": 0}
    data = get_candidates_page(http_request.state.user_id, page=page, per_page=per_page)
    data["enabled"] = True
    return data


@app.post("/api/upgrades/candidates/{candidate_id}/search")
def search_upgrade_candidate(candidate_id: int, http_request: Request, force: bool = False):
    """Search for a better copy of one candidate (network). Client calls this per row, ~1/s."""
    if _is_peon(http_request):
        raise HTTPException(status_code=403, detail="Not available for this account")
    if not get_setting_bool("enable_track_upgrades", False):
        raise HTTPException(status_code=400, detail="Track upgrades are disabled")
    result = search_candidate(http_request.state.user_id, candidate_id, force=force)
    if result is None:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return result


@app.post("/api/upgrades/candidates/{candidate_id}/dismiss")
def dismiss_upgrade_candidate(candidate_id: int, http_request: Request):
    """Stop suggesting an upgrade for this file (until the file itself changes)."""
    if _is_peon(http_request):
        raise HTTPException(status_code=403, detail="Not available for this account")
    if not dismiss_candidate(http_request.state.user_id, candidate_id):
        raise HTTPException(status_code=404, detail="Candidate not found")
    return {"status": "ok"}


@app.post("/api/upgrades/candidates/{candidate_id}/upgrade")
def upgrade_candidate(candidate_id: int, http_request: Request, force: bool = False):
    """Download the proposed copy, verify it, and swap it in if it passes every check.

    Synchronous: it downloads and fingerprints, so it can take a little while. The old
    file goes to quarantine (manual purge), never deleted. Hidden from peons.
    force=true skips the same-recording gates (still quarantines the old file).
    """
    if _is_peon(http_request):
        raise HTTPException(status_code=403, detail="Not available for this account")
    if not get_setting_bool("enable_track_upgrades", False):
        raise HTTPException(status_code=400, detail="Track upgrades are disabled")
    result = perform_upgrade(http_request.state.user_id, candidate_id, force=force)
    if result.get("status") == "error" and result.get("reason") == "Candidate not found":
        raise HTTPException(status_code=404, detail="Candidate not found")
    return result


@app.post("/api/upgrades/upgrade-all")
def upgrade_all(http_request: Request):
    """Best-effort upgrade of every below-target candidate that has a proposal.

    Runs in the background (downloads take time); the UI refreshes afterwards.
    """
    if _is_peon(http_request):
        raise HTTPException(status_code=403, detail="Not available for this account")
    if not get_setting_bool("enable_track_upgrades", False):
        raise HTTPException(status_code=400, detail="Track upgrades are disabled")
    user_id = http_request.state.user_id
    spawn_daemon_thread(perform_upgrade_all, user_id)
    return {"status": "started"}


@app.post("/api/upgrades/rescan")
def rescan_upgrades(http_request: Request):
    """Kick off an immediate library scan (admin only). Cheap and network-free."""
    if not http_request.state.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    if not get_setting_bool("enable_track_upgrades", False):
        raise HTTPException(status_code=400, detail="Track upgrades are disabled")
    return {"status": "ok", "totals": run_scan_all()}


@app.put("/api/watched-artists/{artist_id}")
def update_watched_artist(artist_id: str, request: WatchedArtistUpdate, http_request: Request):
    """Update a watched artist's settings."""
    if _is_peon(http_request):
        request.convert_to_flac = None  # Peons cannot change conversion setting
    user_id = http_request.state.user_id
    is_admin = http_request.state.is_admin
    updates: list[str] = []
    params: list = []
    if request.enabled is not None:
        updates.append("enabled = ?"); params.append(int(request.enabled))
    if request.refresh_interval_hours is not None:
        updates.append("refresh_interval_hours = ?"); params.append(request.refresh_interval_hours)
    if request.convert_to_flac is not None:
        updates.append("convert_to_flac = ?"); params.append(int(request.convert_to_flac))
    if request.from_date is not None:
        updates.append("from_date = ?"); params.append(request.from_date)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    _scope_frag, _scope_params = _user_scope(user_id, is_admin)
    with db_conn() as conn:
        conn.execute(
            f"UPDATE watched_artists SET {', '.join(updates)} WHERE id = ? AND {_scope_frag}",
            (*params, artist_id, *_scope_params)
        )
        conn.commit()
        conn.row_factory = sqlite3.Row
        updated = conn.execute(
            f"SELECT * FROM watched_artists WHERE id = ? AND {_scope_frag}",
            (artist_id, *_scope_params)
        ).fetchone()
    if not updated:
        raise HTTPException(status_code=404, detail="Artist not found")
    return dict(updated)


@app.delete("/api/watched-artists/{artist_id}")
def delete_watched_artist(artist_id: str, http_request: Request):
    """Remove a watched artist and all its tracked tracks."""
    user_id = http_request.state.user_id
    is_admin = http_request.state.is_admin
    with db_conn() as conn:
        _scope_frag, _scope_params = _user_scope(user_id, is_admin)
        artist = conn.execute(
            f"SELECT name FROM watched_artists WHERE id = ? AND {_scope_frag}",
            (artist_id, *_scope_params)
        ).fetchone()
        if not artist:
            raise HTTPException(status_code=404, detail="Artist not found")
        conn.execute("DELETE FROM watched_artist_tracks WHERE artist_id = ?", (artist_id,))
        conn.execute("DELETE FROM watched_artists WHERE id = ?", (artist_id,))
        conn.commit()
    return {"success": True, "message": f"Stopped watching {artist[0]}"}


@app.post("/api/watched-artists/{artist_id}/refresh")
def refresh_single_artist(artist_id: str, http_request: Request):
    """Manually trigger a refresh for one watched artist."""
    user_id = http_request.state.user_id
    is_admin = http_request.state.is_admin
    with db_conn() as conn:
        _scope_frag, _scope_params = _user_scope(user_id, is_admin)
        row = conn.execute(
            f"SELECT id FROM watched_artists WHERE id = ? AND {_scope_frag}",
            (artist_id, *_scope_params)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Artist not found")
    # Background, same as create: a manual refresh of a prolific artist would
    # otherwise hang the request for minutes. The UI polls refresh_state.
    spawn_daemon_thread(refresh_watched_artist, artist_id)
    return {"artist_id": artist_id, "refresh_state": "running", "started": True}


@app.get("/api/watched-artists/{artist_id}/tracks")
def get_watched_artist_tracks(artist_id: str, http_request: Request, limit: int = 50, offset: int = 0):
    """Return a page of tracked singles for a watched artist with job status.

    Prolific artists run to hundreds of singles, so the list is paginated for
    display ('cycles'). limit<=0 means "everything" for callers that still want
    the lot. Always returns the total so the UI can render a pager.
    """
    user_id = http_request.state.user_id
    is_admin = http_request.state.is_admin
    # Clamp to sane bounds; limit<=0 is the "give me all of it" escape hatch.
    paged = limit > 0
    limit = min(limit, 500) if paged else -1
    offset = max(offset, 0)
    with db_conn() as conn:
        conn.row_factory = sqlite3.Row
        _scope_frag, _scope_params = _user_scope(user_id, is_admin)
        artist = conn.execute(
            f"SELECT name FROM watched_artists WHERE id = ? AND {_scope_frag}",
            (artist_id, *_scope_params)
        ).fetchone()
        if not artist:
            raise HTTPException(status_code=404, detail="Artist not found")
        total = conn.execute(
            "SELECT COUNT(*) FROM watched_artist_tracks WHERE artist_id = ?",
            (artist_id,)
        ).fetchone()[0]
        query = """SELECT wat.artist, wat.title, wat.release_date, wat.downloaded_at,
                      wat.resolved_path, wat.job_id, j.status as job_status, j.error as job_error
               FROM watched_artist_tracks wat
               LEFT JOIN jobs j ON wat.job_id = j.id
               WHERE wat.artist_id = ?
               ORDER BY wat.release_date DESC NULLS LAST, wat.title"""
        params: tuple = (artist_id,)
        if paged:
            query += " LIMIT ? OFFSET ?"
            params = (artist_id, limit, offset)
        tracks = conn.execute(query, params).fetchall()
    return {
        "artist": artist[0],
        "tracks": [dict(t) for t in tracks],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@app.get("/api/watched-artists/{artist_id}/missing")
def get_missing_artist_tracks(artist_id: str, http_request: Request):
    """Return singles that haven't been downloaded and have no active job."""
    user_id = http_request.state.user_id
    is_admin = http_request.state.is_admin
    with db_conn() as conn:
        conn.row_factory = sqlite3.Row
        _scope_frag, _scope_params = _user_scope(user_id, is_admin)
        artist = conn.execute(
            f"SELECT name FROM watched_artists WHERE id = ? AND {_scope_frag}",
            (artist_id, *_scope_params)
        ).fetchone()
        if not artist:
            raise HTTPException(status_code=404, detail="Artist not found")
        tracks = conn.execute(
            """SELECT wat.artist, wat.title, wat.release_date, j.status as job_status, j.error as job_error
               FROM watched_artist_tracks wat
               LEFT JOIN jobs j ON wat.job_id = j.id
               WHERE wat.artist_id = ?
                 AND wat.downloaded_at IS NULL
                 AND (j.status IS NULL OR j.status NOT IN ('queued', 'downloading'))
               ORDER BY wat.release_date DESC NULLS LAST, wat.title""",
            (artist_id,)
        ).fetchall()
    return {"artist": artist[0], "tracks": [dict(t) for t in tracks]}


@app.post("/api/watched-artists/{artist_id}/retry-track")
def retry_missing_artist_track(artist_id: str, request: RetryMissingTrackRequest, http_request: Request):
    """Retry downloading a specific missing single."""
    user_id = http_request.state.user_id
    is_admin = http_request.state.is_admin
    with db_conn() as conn:
        _scope_frag, _scope_params = _user_scope(user_id, is_admin)
        artist = conn.execute(
            f"SELECT name, convert_to_flac FROM watched_artists WHERE id = ? AND {_scope_frag}",
            (artist_id, *_scope_params)
        ).fetchone()
        if not artist:
            raise HTTPException(status_code=404, detail="Artist not found")
    import_id = start_bulk_import_for_tracks(
        [(request.artist, request.title)],
        convert_to_flac=bool(artist[1]),
        watch_artist_id=artist_id,
        user_id=user_id,
    )
    return {"success": True, "import_id": import_id}


@app.post("/api/watched-artists/{artist_id}/retry-all-missing")
def retry_all_missing_artist_tracks(artist_id: str, http_request: Request):
    """Queue all undownloaded singles for this artist in one bulk import."""
    user_id = http_request.state.user_id
    is_admin = http_request.state.is_admin
    with db_conn() as conn:
        conn.row_factory = sqlite3.Row
        _scope_frag, _scope_params = _user_scope(user_id, is_admin)
        artist = conn.execute(
            f"SELECT name, convert_to_flac FROM watched_artists WHERE id = ? AND {_scope_frag}",
            (artist_id, *_scope_params)
        ).fetchone()
        if not artist:
            raise HTTPException(status_code=404, detail="Artist not found")
        tracks = conn.execute(
            """SELECT artist, title FROM watched_artist_tracks
               WHERE artist_id = ?
                 AND downloaded_at IS NULL
                 AND (job_id IS NULL OR job_id NOT IN (
                     SELECT id FROM jobs WHERE status IN ('queued', 'downloading')
                 ))
               ORDER BY release_date DESC NULLS LAST, title""",
            (artist_id,)
        ).fetchall()
    if not tracks:
        return {"success": True, "queued": 0, "import_id": None}
    import_id = start_bulk_import_for_tracks(
        [(t["artist"], t["title"]) for t in tracks],
        convert_to_flac=bool(artist["convert_to_flac"]),
        watch_artist_id=artist_id,
        user_id=user_id,
    )
    return {"success": True, "queued": len(tracks), "import_id": import_id}


@app.post("/api/watched-artists/check-all")
def check_all_watched_artists(http_request: Request):
    """Trigger a refresh for all watched artists that are due."""
    if not http_request.state.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    with db_conn() as conn:
        conn.row_factory = sqlite3.Row
        artists = conn.execute("""
            SELECT id, name FROM watched_artists
            WHERE enabled = 1
            AND (last_checked IS NULL
                 OR datetime(last_checked, '+' || refresh_interval_hours || ' hours') < datetime('now'))
        """).fetchall()

    if not artists:
        return {"checked": 0, "message": "No artists due for refresh", "results": []}

    results = []
    for a in artists:
        result = refresh_watched_artist(a["id"])
        results.append(result)

    total_new = sum(r.get("new_tracks", 0) for r in results)
    return {
        "checked": len(results),
        "total_new_tracks": total_new,
        "results": results,
    }


# =============================================================================
# Album Download Routes
# =============================================================================

@app.get("/api/albums/search-artist")
def albums_search_artist(q: str):
    """Search MusicBrainz for an artist by name.

    Returns up to 5 candidates with mbid, name, disambiguation, and score.
    Returns 503 when MB is unreachable so the UI can show a Retry button
    instead of pretending the artist does not exist.
    """
    if not q or not q.strip():
        raise HTTPException(status_code=400, detail="Query parameter 'q' is required")
    try:
        results = search_artist_mbid(q.strip())
    except MusicBrainzUnavailable as exc:
        raise HTTPException(status_code=503, detail=f"MusicBrainz unreachable: {exc}. Try again in a moment.")
    return {"artists": results}


@app.get("/api/albums/artist/{mbid}/albums")
def albums_list_artist_albums(mbid: str):
    """Fetch studio albums for a MusicBrainz artist MBID.

    Returns [{title, year, release_mbid}, ...] sorted by year.
    503 on MB unavailable.
    """
    if not mbid or not mbid.strip():
        raise HTTPException(status_code=400, detail="Artist MBID is required")
    try:
        albums = fetch_artist_albums(mbid.strip())
    except MusicBrainzUnavailable as exc:
        raise HTTPException(status_code=503, detail=f"MusicBrainz unreachable: {exc}. Try again in a moment.")
    return {"albums": albums}


@app.get("/api/albums/release/{release_mbid}/tracks")
def albums_get_tracklist(release_mbid: str):
    """Fetch the tracklist for a MusicBrainz release MBID.

    Returns [{position, title}, ...] in track order.
    503 on MB unavailable.
    """
    if not release_mbid or not release_mbid.strip():
        raise HTTPException(status_code=400, detail="Release MBID is required")
    try:
        tracks = fetch_album_tracks(release_mbid.strip())
    except MusicBrainzUnavailable as exc:
        raise HTTPException(status_code=503, detail=f"MusicBrainz unreachable: {exc}. Try again in a moment.")
    if not tracks:
        raise HTTPException(status_code=404, detail="No tracks found for this release")
    return {"tracks": tracks}


def _album_track_stem(artist: str, title: str, user_id: str | None = None) -> str:
    """Build the expected filename stem for an album track in override_dir mode."""
    from utils import sanitize_filename
    safe_title = sanitize_filename(title or "") or "Unknown Title"
    if not get_setting_bool("organise_by_artist", True, user_id=user_id):
        safe_artist = sanitize_filename(artist or "Unknown Artist")
        return f"{safe_artist} - {safe_title}"
    return safe_title


def _album_track_status(artist: str, album_title: str, tracks: list[dict], user_id: str | None = None) -> dict:
    """Return existing/missing status for tracklist against the target album directory."""
    from utils import sanitize_filename

    album_dir = get_albums_dir(user_id=user_id) / sanitize_filename(artist) / sanitize_filename(album_title)
    audio_files = [p for p in album_dir.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS] if album_dir.exists() else []
    audio_stems = [p.stem for p in audio_files]

    track_status = []
    for t in tracks:
        title = (t.get("title") or "").strip()
        stem = _album_track_stem(artist, title, user_id=user_id)
        exists_exact = any((album_dir / f"{stem}{ext}").exists() for ext in AUDIO_EXTENSIONS)
        exists_fuzzy = False
        if not exists_exact:
            norm_title = _normalise_album_match_text(title)
            if norm_title:
                for file_stem in audio_stems:
                    norm_stem = _normalise_album_match_text(file_stem)
                    if not norm_stem:
                        continue
                    if norm_stem == norm_title or norm_stem.endswith(f" {norm_title}"):
                        exists_fuzzy = True
                        break
        exists = bool(exists_exact or exists_fuzzy)
        track_status.append({
            "position": t.get("position"),
            "title": title,
            "exists": exists,
        })

    existing_tracks = [t for t in track_status if t["exists"]]
    missing_tracks = [t for t in track_status if not t["exists"]]
    m3u_files = sorted([p.name for p in album_dir.glob("*.m3u") if p.is_file()], key=str.casefold) if album_dir.exists() else []
    return {
        "album_dir": album_dir,
        "tracks": track_status,
        "existing_tracks": existing_tracks,
        "missing_tracks": missing_tracks,
        "m3u_files": m3u_files,
    }


def _normalise_album_match_text(text: str) -> str:
    """Normalise track titles for album-track matching."""
    t = clean_title(text or "")
    t = t.replace("’", "'").replace("‘", "'").replace("`", "'")
    t = unicodedata.normalize("NFKD", t)
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = re.sub(r"\b(?:feat\.?|ft\.?|featuring)\b.*$", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*[\(\[].*?[\)\]]", "", t)
    t = re.sub(r"[^a-z0-9]+", " ", t.lower())
    return re.sub(r"\s+", " ", t).strip()


def _match_album_track(tracks: list[dict], candidate_title: str) -> dict | None:
    """Return a single best album track match for a candidate title."""
    cand = _normalise_album_match_text(candidate_title)
    if not cand:
        return None

    exact = [t for t in tracks if _normalise_album_match_text(t.get("title") or "") == cand]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        return None

    cand_tokens = set(cand.split())
    fuzzy = []
    for t in tracks:
        tn = _normalise_album_match_text(t.get("title") or "")
        if not tn:
            continue
        t_tokens = set(tn.split())
        overlap = len(cand_tokens & t_tokens)
        # Require ≥80% of the shorter token set to overlap; floor at 2 tokens to
        # avoid matching on coincidental single words in short titles like "My Love".
        min_overlap = max(2, round(0.8 * min(len(cand_tokens), len(t_tokens))))
        if overlap >= min_overlap:
            fuzzy.append((overlap, t))
    if len(fuzzy) == 1:
        return fuzzy[0][1]
    return None


@app.post("/api/albums/release/{release_mbid}/match-track")
def albums_match_track(release_mbid: str, body: dict):
    """Match a candidate song to a specific track on the selected album."""
    title = (body.get("title") or "").strip()
    candidate_artist = (body.get("artist") or "").strip()
    album_artist = (body.get("album_artist") or "").strip()
    if not release_mbid or not release_mbid.strip():
        raise HTTPException(status_code=400, detail="Release MBID is required")
    if not title:
        raise HTTPException(status_code=400, detail="Track title is required")

    if candidate_artist and album_artist:
        cand_artist_n = _normalise_album_match_text(candidate_artist)
        album_artist_n = _normalise_album_match_text(album_artist)
        if cand_artist_n and album_artist_n:
            cand_tokens = {t for t in cand_artist_n.split() if len(t) > 1}
            album_tokens = {t for t in album_artist_n.split() if len(t) > 1}
            if album_tokens and not album_tokens.issubset(cand_tokens):
                raise HTTPException(
                    status_code=422,
                    detail=f"Result artist '{candidate_artist}' does not match selected album artist '{album_artist}'",
                )

    tracks = fetch_album_tracks(release_mbid.strip())
    if not tracks:
        raise HTTPException(status_code=404, detail="No tracks found for this release")

    matched = _match_album_track(tracks, title)
    if not matched:
        raise HTTPException(status_code=422, detail=f"No album-track match found for '{title}'")

    try:
        track_number = int((matched.get("position") or "").strip())
    except Exception:
        track_number = None

    return {
        "matched": True,
        "track_title": (matched.get("title") or "").strip(),
        "track_number": track_number,
        "track_total": len(tracks),
    }


@app.get("/api/albums/release/{release_mbid}/missing")
def albums_missing_tracks(release_mbid: str, artist: str, album_title: str, request: Request):
    """Report which tracks are already present on disk for a selected album."""
    artist = (artist or "").strip()
    album_title = (album_title or "").strip()
    if not release_mbid or not release_mbid.strip():
        raise HTTPException(status_code=400, detail="Release MBID is required")
    if not artist or not album_title:
        raise HTTPException(status_code=400, detail="artist and album_title are required")

    tracks = fetch_album_tracks(release_mbid.strip())
    if not tracks:
        raise HTTPException(status_code=404, detail="No tracks found for this release")

    user_id = getattr(request.state, "user_id", None)
    status = _album_track_status(artist, album_title, tracks, user_id=user_id)
    return {
        "album_dir": str(status["album_dir"]),
        "total_tracks": len(status["tracks"]),
        "tracks": status["tracks"],
        "existing_count": len(status["existing_tracks"]),
        "missing_count": len(status["missing_tracks"]),
        "existing_tracks": status["existing_tracks"],
        "missing_tracks": status["missing_tracks"],
        "has_existing_m3u": bool(status["m3u_files"]),
        "existing_m3u_files": status["m3u_files"],
    }


@app.get("/api/albums/dirs")
def albums_list_artists(request: Request):
    """List artist folders found under the Albums directory on disk."""
    user_id = getattr(request.state, "user_id", None)
    base = get_albums_dir(user_id=user_id)
    if not base.exists():
        return {"artists": []}
    artists = sorted(
        [d.name for d in base.iterdir() if d.is_dir() and not d.name.startswith(".")],
        key=str.casefold,
    )
    return {"artists": artists}


@app.get("/api/albums/dirs/{artist}")
def albums_list_albums(artist: str, request: Request):
    """List album folders within an artist directory."""
    if ".." in artist:
        raise HTTPException(status_code=400, detail="Invalid artist name")
    user_id = getattr(request.state, "user_id", None)
    base = get_albums_dir(user_id=user_id)
    artist_dir = base / sanitize_filename(artist)
    if not artist_dir.exists():
        raise HTTPException(status_code=404, detail="Artist directory not found")
    albums = sorted(
        [d.name for d in artist_dir.iterdir() if d.is_dir() and not d.name.startswith(".")],
        key=str.casefold,
    )
    return {"albums": albums}


@app.get("/api/albums/dirs/{artist}/{album}/info")
def albums_dir_info(artist: str, album: str, request: Request):
    """Read .albuminfo sidecar from an album directory and return MB tracklist."""
    if ".." in artist or ".." in album:
        raise HTTPException(status_code=400, detail="Invalid path")
    user_id = getattr(request.state, "user_id", None)
    base = get_albums_dir(user_id=user_id)
    album_dir = base / sanitize_filename(artist) / sanitize_filename(album)
    albuminfo_path = album_dir / ".albuminfo"
    if not albuminfo_path.exists():
        return {"found": False}
    try:
        data = json.loads(albuminfo_path.read_text(encoding="utf-8"))
        release_mbid = (data.get("release_mbid") or "").strip()
        tracks = fetch_album_tracks(release_mbid) if release_mbid else []
        return {
            "found": True,
            "artist": data.get("artist", ""),
            "album": data.get("album", ""),
            "release_mbid": release_mbid,
            "tracks": tracks,
        }
    except Exception:
        return {"found": False}


@app.post("/api/albums/download")
def albums_download(body: AlbumDownloadRequest, http_request: Request):
    """Queue a full album for download.

    Fetches tracklist from MusicBrainz, creates a bulk import job routed to
    Albums/Artist/Album/ instead of the normal Singles layout.
    Returns {import_id} for polling via /api/bulk-import/{id}/status.
    """
    _enforce_peon_format(http_request, body)
    user_id = getattr(http_request.state, "user_id", None)

    artist = body.artist.strip()
    album_title = body.album_title.strip()
    release_mbid = body.release_mbid.strip()
    make_m3u = body.make_m3u
    m3u_name = (body.m3u_name or "").strip()
    convert_to_flac = body.convert_to_flac

    if not artist or not album_title or not release_mbid:
        raise HTTPException(status_code=400, detail="artist, album_title, and release_mbid are required")

    tracks = fetch_album_tracks(release_mbid)
    if not tracks:
        raise HTTPException(status_code=404, detail="Could not fetch tracklist from MusicBrainz")

    status = _album_track_status(artist, album_title, tracks, user_id=user_id)
    album_dir = status["album_dir"]
    missing_tracks = status["missing_tracks"]
    album_dir.mkdir(parents=True, exist_ok=True)

    # Write a .albuminfo sidecar so the picker can restore MBID context without a DB.
    # Atomic write (mkstemp + rename) so a crash mid-write leaves nothing corrupt.
    albuminfo_path = album_dir / ".albuminfo"
    if not albuminfo_path.exists():
        tmp_fd, tmp_path = tempfile.mkstemp(dir=album_dir, suffix=".albuminfo.tmp")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"artist": artist, "album": album_title, "release_mbid": release_mbid}, indent=2))
            Path(tmp_path).rename(albuminfo_path)
            set_file_permissions(albuminfo_path)
        except Exception:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)

    # Refuse to queue if an in-flight import is already targeting this album directory.
    # Catches double-clicks and impatient re-submissions before any files have landed.
    with db_conn() as conn:
        inflight = conn.execute(
            """SELECT id FROM bulk_imports
               WHERE override_dir = ?
                 AND status IN ('pending', 'processing')
                 AND completed_at IS NULL
               LIMIT 1""",
            (str(album_dir),),
        ).fetchone()
    if inflight:
        return {
            "import_id": inflight[0],
            "track_count": len(tracks),
            "queued_count": len(missing_tracks),
            "existing_count": len(status["existing_tracks"]),
            "missing_count": len(missing_tracks),
            "album_dir": str(album_dir),
            "warning": "Download already in progress for this album.",
            "already_queued": True,
        }

    # Queue only missing tracks; already-present tracks are left as-is.
    track_pairs = [(artist, t["title"]) for t in missing_tracks]

    if not track_pairs:
        updated_m3u = None
        if make_m3u:
            updated_m3u = rebuild_album_m3u(album_dir, m3u_name or f"{artist} - {album_title}")
        return {
            "import_id": None,
            "track_count": len(tracks),
            "queued_count": 0,
            "existing_count": len(status["existing_tracks"]),
            "missing_count": 0,
            "album_dir": str(album_dir),
            "m3u_updated": bool(updated_m3u),
            "m3u_path": str(updated_m3u) if updated_m3u else None,
            "warning": "Album already exists on disk. Nothing queued." + (" Existing M3U updated." if updated_m3u else ""),
        }

    from bulk_import import start_bulk_import_for_tracks
    import_id = start_bulk_import_for_tracks(
        tracks=track_pairs,
        convert_to_flac=convert_to_flac,
        user_id=user_id,
        override_dir=str(album_dir),
        album_release_mbid=release_mbid,
        album_total_tracks=len(tracks),
    )

    # If M3U requested, store the album details so create_bulk_playlist can pick it up.
    # We repurpose the existing create_playlist + playlist_name mechanism.
    if make_m3u:
        playlist_label = m3u_name or f"{artist} - {album_title}"
        if playlist_label.lower().endswith(".m3u"):
            playlist_label = playlist_label[:-4]
        playlist_label = sanitize_playlist_name(playlist_label, f"{artist} - {album_title}")
        with db_conn() as conn:
            conn.execute(
                "UPDATE bulk_imports SET create_playlist = 1, playlist_name = ? WHERE id = ?",
                (playlist_label, import_id)
            )
            conn.commit()

    return {
        "import_id": import_id,
        "track_count": len(tracks),
        "queued_count": len(track_pairs),
        "existing_count": len(status["existing_tracks"]),
        "missing_count": len(missing_tracks),
        "album_dir": str(album_dir),
        "warning": f"{len(status['existing_tracks'])} track(s) already existed; queued {len(track_pairs)} missing track(s).",
    }


if __name__ == "__main__":
    import uvicorn
    host = os.getenv("LISTEN_ADDR", "0.0.0.0")
    port = int(os.getenv("LISTEN_PORT", "8080"))
    uvicorn.run(app, host=host, port=port)
