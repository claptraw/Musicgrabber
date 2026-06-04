"""
MusicGrabber - Settings Management

Environment variable > DB value > default hierarchy.
Per-user settings layer sits between env vars and global DB values.
"""

import os
from pathlib import Path

from constants import (
    BOT_BACKOFF_MIN_SECONDS, BOT_BACKOFF_MAX_SECONDS,
    TIMEOUT_SPOTIFY_BROWSER, SPOTIFY_BROWSER_STALL_SECONDS,
    MUSIC_DIR, DB_PATH,
    MONOCHROME_HIFI_API_URL, MONOCHROME_QOBUZ_PROXY_URL,
    QBDLX_FALLBACK_ENABLED,
)
from db import db_conn


def get_setting(key: str, default: str = "", user_id: str | None = None) -> str:
    """Get a setting value.

    Lookup order:
    1. Environment variable (always wins)
    2. user_settings table (if user_id provided and key is a user-scoped setting)
    3. Global settings table
    4. Schema default / provided default
    """
    # Check environment variable first (uppercase, with underscores)
    env_key = key.upper().replace(".", "_")
    env_value = os.getenv(env_key)
    if env_value is not None:
        return env_value

    # Per-user setting (only for user-scoped keys when a user_id is given)
    if user_id and key in USER_SETTINGS_KEYS:
        try:
            with db_conn() as conn:
                row = conn.execute(
                    "SELECT value FROM user_settings WHERE user_id = ? AND key = ?",
                    (user_id, key)
                ).fetchone()
            if row and row[0] is not None:
                return row[0]
        except Exception:
            pass
        # Private keys don't inherit from global settings — new users start blank.
        if key in USER_PRIVATE_KEYS:
            return default

    # Fall back to global database
    try:
        with db_conn() as conn:
            cursor = conn.execute("SELECT value FROM settings WHERE key = ?", (key,))
            row = cursor.fetchone()
        if row and row[0] is not None:
            return row[0]
    except Exception:
        pass

    return default


def get_setting_bool(key: str, default: bool = False, user_id: str | None = None) -> bool:
    value = get_setting(key, str(default).lower(), user_id=user_id)
    return value.lower() in ("true", "1", "yes", "on")


def get_setting_int(key: str, default: int = 0, user_id: str | None = None) -> int:
    value = get_setting(key, str(default), user_id=user_id)
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def set_setting(key: str, value: str) -> None:
    """Set a global setting value in the database."""
    with db_conn() as conn:
        conn.execute("""
            INSERT INTO settings (key, value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET value = ?, updated_at = CURRENT_TIMESTAMP
        """, (key, value, value))
        conn.commit()


def set_user_setting(user_id: str, key: str, value: str) -> None:
    """Set a per-user setting in the user_settings table."""
    with db_conn() as conn:
        conn.execute("""
            INSERT INTO user_settings (user_id, key, value, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, key) DO UPDATE SET value = ?, updated_at = CURRENT_TIMESTAMP
        """, (user_id, key, value, value))
        conn.commit()


def get_user_settings(user_id: str) -> dict:
    """Get all settings for a specific user from user_settings table."""
    with db_conn() as conn:
        cursor = conn.execute(
            "SELECT key, value FROM user_settings WHERE user_id = ?", (user_id,)
        )
        return {row[0]: row[1] for row in cursor.fetchall()}


def get_all_settings(user_id: str | None = None) -> dict:
    """Get all settings from the database. If user_id given, user_settings override global ones."""
    with db_conn() as conn:
        cursor = conn.execute("SELECT key, value FROM settings")
        result = {row[0]: row[1] for row in cursor.fetchall()}

    if user_id:
        with db_conn() as conn:
            cursor = conn.execute(
                "SELECT key, value FROM user_settings WHERE user_id = ?", (user_id,)
            )
            for row in cursor.fetchall():
                result[row[0]] = row[1]

    return result


# Define which settings are sensitive (should be masked in GET response)
SENSITIVE_SETTINGS = {
    "slskd_pass", "navidrome_pass", "jellyfin_api_key", "lidarr_api_key",
    "smtp_pass", "telegram_webhook_url", "api_key", "youtube_cookies",
    "spotify_cookies", "apple_music_user_token",
}

# Settings that belong to each user (stored in user_settings table)
USER_SETTINGS_KEYS = {
    "singles_subdir", "playlists_subdir", "albums_subdir", "organise_by_artist", "include_track_number_in_filename", "auto_album_singles", "auto_album_singles_use_albums_dir",
    "navidrome_url", "navidrome_user", "navidrome_pass", "navidrome_dupe_check",
    "jellyfin_url", "jellyfin_api_key",
    "lidarr_url", "lidarr_api_key",
    "notify_on", "telegram_webhook_url", "apprise_url",
    "smtp_host", "smtp_port", "smtp_user", "smtp_pass", "smtp_from", "smtp_to", "smtp_tls",
    "youtube_cookies",
    "spotify_cookies", "spotify_cookies_expired",
    "apple_music_user_token",
    "webhook_url",
}

# These user-scoped keys are personal credentials — a new user with no explicit value
# should get a blank default rather than inheriting whatever the global setting says.
# (Navidrome/Jellyfin are NOT in this set: shared server, shared library.)
USER_PRIVATE_KEYS = {
    "notify_on", "telegram_webhook_url", "apprise_url",
    "smtp_host", "smtp_port", "smtp_user", "smtp_pass", "smtp_from", "smtp_to", "smtp_tls",
    "webhook_url",
    "youtube_cookies",
    "spotify_cookies", "spotify_cookies_expired",
    "apple_music_user_token",
}

# Define all configurable settings with their types and defaults
SETTINGS_SCHEMA = {
    # General
    "music_dir": {"type": "str", "default": "/music", "env": "MUSIC_DIR"},
    "enable_musicbrainz": {"type": "bool", "default": True, "env": "ENABLE_MUSICBRAINZ"},
    "enable_lyrics": {"type": "bool", "default": True, "env": "ENABLE_LYRICS"},
    "default_convert_to_flac": {"type": "bool", "default": True, "env": "DEFAULT_CONVERT_TO_FLAC"},
    "audio_format": {"type": "str", "default": "flac", "env": "AUDIO_FORMAT"},
    "mp3_bitrate": {"type": "str", "default": "v2", "env": "MP3_BITRATE"},
    "opus_bitrate": {"type": "str", "default": "320k", "env": "OPUS_BITRATE"},
    "alac_bitrate": {"type": "str", "default": "lossless", "env": "ALAC_BITRATE"},
    "min_audio_bitrate": {"type": "int", "default": 0, "env": "MIN_AUDIO_BITRATE"},
    # Track upgrades (Lidarr-style). Off by default; opt-in. The scan flags library
    # files sitting below the quality you already download at, ready for a manual upgrade.
    "enable_track_upgrades": {"type": "bool", "default": False, "env": "ENABLE_TRACK_UPGRADES"},
    "upgrade_scan_interval_hours": {"type": "int", "default": 24, "env": "UPGRADE_SCAN_INTERVAL_HOURS"},
    "singles_subdir": {"type": "str", "default": "Singles", "env": "SINGLES_SUBDIR"},
    "playlists_subdir": {"type": "str", "default": "", "env": "PLAYLISTS_SUBDIR"},
    "albums_subdir": {"type": "str", "default": "Albums", "env": "ALBUMS_SUBDIR"},
    "organise_by_artist": {"type": "bool", "default": True, "env": "ORGANISE_BY_ARTIST"},
    "include_track_number_in_filename": {"type": "bool", "default": False, "env": "INCLUDE_TRACK_NUMBER_IN_FILENAME"},
    "auto_album_singles": {"type": "bool", "default": False, "env": "AUTO_ALBUM_SINGLES"},
    "auto_album_singles_use_albums_dir": {"type": "bool", "default": False, "env": "AUTO_ALBUM_SINGLES_USE_ALBUMS_DIR"},
    "singles_only_mode": {"type": "bool", "default": False, "env": "SINGLES_ONLY_MODE"},
    "file_permissions": {"type": "str", "default": "666", "env": "FILE_PERMISSIONS"},
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
    # Lidarr
    "lidarr_url": {"type": "str", "default": "", "env": "LIDARR_URL"},
    "lidarr_api_key": {"type": "str", "default": "", "env": "LIDARR_API_KEY", "sensitive": True},
    # Duplicate checking
    "skip_dupes": {"type": "bool", "default": True, "env": "SKIP_DUPES"},
    "navidrome_dupe_check": {"type": "bool", "default": True, "env": "NAVIDROME_DUPE_CHECK"},
    # Notifications
    "notify_on": {"type": "str", "default": "playlists,bulk,errors", "env": "NOTIFY_ON"},
    "telegram_webhook_url": {"type": "str", "default": "", "env": "TELEGRAM_WEBHOOK_URL", "sensitive": True},
    "apprise_url": {"type": "str", "default": "", "env": "APPRISE_URL"},
    "smtp_host": {"type": "str", "default": "", "env": "SMTP_HOST"},
    "smtp_port": {"type": "int", "default": 587, "env": "SMTP_PORT"},
    "smtp_user": {"type": "str", "default": "", "env": "SMTP_USER"},
    "smtp_pass": {"type": "str", "default": "", "env": "SMTP_PASS", "sensitive": True},
    "smtp_from": {"type": "str", "default": "", "env": "SMTP_FROM"},
    "smtp_to": {"type": "str", "default": "", "env": "SMTP_TO"},
    "smtp_tls": {"type": "bool", "default": True, "env": "SMTP_TLS"},
    # AcoustID fingerprinting
    "acoustid_api_key": {"type": "str", "default": "0NILMQojj4", "env": "ACOUSTID_API_KEY"},
    # Search sources
    "source_youtube_enabled": {"type": "bool", "default": True, "env": "SOURCE_YOUTUBE_ENABLED"},
    "source_mp3phoenix_enabled": {"type": "bool", "default": True, "env": "SOURCE_MP3PHOENIX_ENABLED"},
    "source_soundcloud_enabled": {"type": "bool", "default": True, "env": "SOURCE_SOUNDCLOUD_ENABLED"},
    "source_zvu4no_enabled": {"type": "bool", "default": True, "env": "SOURCE_ZVU4NO_ENABLED"},
    "source_freemp3cloud_enabled": {"type": "bool", "default": True, "env": "SOURCE_FREEMP3CLOUD_ENABLED"},
    "source_soulseek_enabled": {"type": "bool", "default": False, "env": "SOURCE_SOULSEEK_ENABLED"},
    "source_monochrome_enabled": {"type": "bool", "default": True, "env": "SOURCE_MONOCHROME_ENABLED"},
    "source_offline_fallback": {"type": "bool", "default": True, "env": "SOURCE_OFFLINE_FALLBACK"},
    "source_health_checks_enabled": {"type": "bool", "default": True, "env": "SOURCE_HEALTH_CHECKS_ENABLED"},
    "source_health_check_interval_minutes": {"type": "int", "default": 10, "env": "SOURCE_HEALTH_CHECK_INTERVAL_MINUTES"},
    "source_health_cooldown_minutes": {"type": "int", "default": 10, "env": "SOURCE_HEALTH_COOLDOWN_MINUTES"},
    "monochrome_hifi_api_url": {"type": "str", "default": MONOCHROME_HIFI_API_URL, "env": "MONOCHROME_HIFI_API_URL"},
    "monochrome_qobuz_proxy_url": {"type": "str", "default": MONOCHROME_QOBUZ_PROXY_URL, "env": "MONOCHROME_QOBUZ_PROXY_URL"},
    "monochrome_qbdlx_fallback_enabled": {"type": "bool", "default": QBDLX_FALLBACK_ENABLED, "env": "QBDLX_FALLBACK_ENABLED"},
    # YouTube
    "youtube_cookies": {"type": "str", "default": "", "env": "YOUTUBE_COOKIES", "sensitive": True},
    "youtube_bot_backoff_min": {"type": "int", "default": BOT_BACKOFF_MIN_SECONDS, "env": "YOUTUBE_BOT_BACKOFF_MIN"},
    # Spotify
    "spotify_cookies": {"type": "str", "default": "", "sensitive": True},
    "spotify_cookies_expired": {"type": "bool", "default": False},
    # Apple Music
    "apple_music_user_token": {"type": "str", "default": "", "sensitive": True},
    "youtube_bot_backoff_max": {"type": "int", "default": BOT_BACKOFF_MAX_SECONDS, "env": "YOUTUBE_BOT_BACKOFF_MAX"},
    "spotify_browser_timeout_seconds": {
        "type": "int",
        "default": TIMEOUT_SPOTIFY_BROWSER,
        "env": "SPOTIFY_BROWSER_TIMEOUT_SECONDS",
    },
    "spotify_browser_stall_seconds": {
        "type": "int",
        "default": SPOTIFY_BROWSER_STALL_SECONDS,
        "env": "SPOTIFY_BROWSER_STALL_SECONDS",
    },
    # Webhooks
    "webhook_url": {"type": "str", "default": "", "env": "WEBHOOK_URL"},
    # Downloads
    "max_concurrent_downloads": {"type": "int", "default": 3, "env": "MAX_CONCURRENT_DOWNLOADS"},
    # Security
    "api_key": {"type": "str", "default": "", "env": "API_KEY", "sensitive": True},
}


def _get_typed_setting(key: str, user_id: str | None = None):
    """Get a setting with proper type conversion based on schema."""
    schema = SETTINGS_SCHEMA.get(key, {"type": "str", "default": ""})
    default = schema["default"]
    if schema["type"] == "bool":
        return get_setting_bool(key, default, user_id=user_id)
    elif schema["type"] == "int":
        return get_setting_int(key, default, user_id=user_id)
    return get_setting(key, default, user_id=user_id)


def _is_env_override(key: str) -> bool:
    """Check if a setting is being overridden by an environment variable."""
    schema = SETTINGS_SCHEMA.get(key, {})
    env_key = schema.get("env", key.upper())
    return os.getenv(env_key) is not None


def get_singles_dir(user_id: str | None = None) -> Path:
    """Get the singles download directory for a user (or global default).

    A value of "." means the music root itself (no subfolder).
    """
    music_dir = Path(get_setting("music_dir", str(MUSIC_DIR), user_id=user_id))
    subdir = get_setting("singles_subdir", "Singles", user_id=user_id).strip() or "Singles"
    if subdir == ".":
        return music_dir
    return music_dir / subdir


def resolve_custom_subdir(custom_subdir: str, user_id: str | None = None) -> Path:
    """Resolve a per-playlist custom subdir string to an absolute path under music_dir."""
    music_dir = Path(get_setting("music_dir", str(MUSIC_DIR), user_id=user_id))
    subdir = custom_subdir.strip()
    return music_dir if subdir == "." else music_dir / subdir


def get_playlists_dir(user_id: str | None = None) -> Path | None:
    """Get the playlists download directory for a user, or None if disabled."""
    music_dir = Path(get_setting("music_dir", str(MUSIC_DIR), user_id=user_id))
    subdir = get_setting("playlists_subdir", "", user_id=user_id).strip()
    if not subdir:
        return None  # Feature disabled, fall back to Singles behaviour
    if subdir == ".":
        return music_dir
    return music_dir / subdir


def get_trash_dir(user_id: str | None = None) -> Path:
    """Get the trash directory.

    Lives under /data rather than the music volume to avoid FUSE/mergerfs
    filesystem quirks that prevent directory listing on some setups.
    """
    return DB_PATH.parent / ".trash"


def get_albums_dir(user_id: str | None = None) -> Path:
    """Get the albums download directory for a user.

    Files land at: albums_dir / Artist / Album / Track.flac
    Returns music_dir / albums_subdir (default "Albums").
    """
    music_dir = Path(get_setting("music_dir", str(MUSIC_DIR), user_id=user_id))
    subdir = get_setting("albums_subdir", "Albums", user_id=user_id).strip() or "Albums"
    if subdir == ".":
        return music_dir
    return music_dir / subdir


def get_download_dir(artist: str, user_id: str | None = None) -> Path:
    """Get the download directory for a track, respecting the organise-by-artist setting.

    When organise_by_artist is True (default):  /music/Singles/Artist Name/
    When organise_by_artist is False:            /music/Singles/
    """
    from utils import sanitize_filename
    base = get_singles_dir(user_id=user_id)
    if get_setting_bool("organise_by_artist", True, user_id=user_id):
        return base / sanitize_filename(artist)
    return base
