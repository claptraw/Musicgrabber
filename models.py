"""
MusicGrabber - Pydantic Request/Response Models
"""

import re
from typing import Optional
from pydantic import BaseModel, Field, field_validator
from constants import DEFAULT_CONVERT_TO_FLAC, MAX_SEARCH_QUERY_LENGTH

_UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.IGNORECASE)


def _validate_mbid(v: str | None) -> str | None:
    if v and not _UUID_RE.match(v):
        raise ValueError("Invalid MBID format (expected UUID)")
    return v


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=MAX_SEARCH_QUERY_LENGTH)
    limit: int = 15
    source: str = "all"  # "youtube", "soundcloud", "monochrome", or "all"

class DownloadRequest(BaseModel):
    video_id: str
    title: str
    artist: Optional[str] = None
    search_token: Optional[str] = None
    download_type: str = "single"  # "single" or "playlist"
    convert_to_flac: bool = DEFAULT_CONVERT_TO_FLAC  # Whether to convert to FLAC or keep original format
    # Source routing
    source: str = "youtube"  # "youtube", "soundcloud", "monochrome", or "soulseek"
    source_url: Optional[str] = None  # Full URL for non-YouTube sources (e.g. SoundCloud/Monochrome)
    # Soulseek-specific fields
    slskd_username: Optional[str] = None
    slskd_filename: Optional[str] = None
    # Playlist routing  -  optional, defaults to Singles
    playlist_name: Optional[str] = None  # Name of target playlist (M3U stem)
    use_playlists_dir: bool = False  # Route into Playlists dir instead of Singles
    # Album routing (search results -> selected album track)
    album_release_mbid: Optional[str] = None
    _validate_album_release_mbid = field_validator("album_release_mbid")(_validate_mbid)
    album_artist: Optional[str] = None
    album_name: Optional[str] = None
    album_track_title: Optional[str] = None
    album_track_number: Optional[int] = None
    album_track_total: Optional[int] = None

class PlaylistFetchRequest(BaseModel):
    url: str  # Spotify, Amazon Music, etc. playlist URL


class AsyncBulkImportRequest(BaseModel):
    songs: str  # Multi-line text with "Artist - Song" format
    create_playlist: bool = False
    playlist_name: Optional[str] = None
    convert_to_flac: bool = DEFAULT_CONVERT_TO_FLAC
    use_playlists_dir: bool = False  # Save files to Playlists folder instead of Singles

class WatchedPlaylistRequest(BaseModel):
    url: str  # Spotify, YouTube, or Amazon Music playlist URL
    refresh_interval_hours: int = 24
    convert_to_flac: bool = DEFAULT_CONVERT_TO_FLAC
    make_m3u: bool = False
    use_playlists_dir: bool = False  # Save files to Playlists folder instead of Singles
    sync_mode: str = "append"  # "append" = grow forever; "mirror" = track upstream removals in M3U
    preferred_sources: str = "all"  # Comma-separated source IDs or "all"

class WatchedPlaylistUpdate(BaseModel):
    refresh_interval_hours: Optional[int] = None
    enabled: Optional[bool] = None
    convert_to_flac: Optional[bool] = None
    make_m3u: Optional[bool] = None
    use_playlists_dir: Optional[bool] = None
    sync_mode: Optional[str] = None  # "append" or "mirror"
    preferred_sources: Optional[str] = None  # Comma-separated source IDs or "all"

class SettingsUpdate(BaseModel):
    """Settings that can be updated via the UI"""
    # General
    music_dir: Optional[str] = None
    enable_musicbrainz: Optional[bool] = None
    enable_lyrics: Optional[bool] = None
    default_convert_to_flac: Optional[bool] = None
    audio_format: Optional[str] = None  # "flac" or "opus"
    min_audio_bitrate: Optional[int] = None
    singles_subdir: Optional[str] = None
    playlists_subdir: Optional[str] = None
    albums_subdir: Optional[str] = None
    organise_by_artist: Optional[bool] = None
    auto_album_singles: Optional[bool] = None
    auto_album_singles_use_albums_dir: Optional[bool] = None
    # Search sources
    source_youtube_enabled: Optional[bool] = None
    source_mp3phoenix_enabled: Optional[bool] = None
    source_soundcloud_enabled: Optional[bool] = None
    source_monochrome_enabled: Optional[bool] = None
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
    # Apprise notifications
    apprise_url: Optional[str] = None
    # YouTube
    youtube_cookies: Optional[str] = None
    spotify_browser_timeout_seconds: Optional[int] = None
    spotify_browser_stall_seconds: Optional[int] = None
    # Spotify
    spotify_cookies: Optional[str] = None
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
    # Multi-source support
    source: str = "youtube"  # "youtube", "soundcloud", "monochrome", or "soulseek"
    source_url: Optional[str] = None  # Full URL for non-YouTube sources
    quality: Optional[str] = None  # e.g., "LOSSLESS", "HI_RES_LOSSLESS", None for YouTube
    quality_score: int = 40  # For sorting (higher = better)
    slskd_username: Optional[str] = None
    slskd_filename: Optional[str] = None
    # Monochrome-specific extras (available when source == "monochrome")
    album: Optional[str] = None  # Album title from Tidal metadata

class BlacklistRequest(BaseModel):
    """Report a bad track / block an uploader."""
    job_id: Optional[str] = None
    video_id: Optional[str] = None
    uploader: Optional[str] = None
    source: str = "youtube"  # "youtube", "soundcloud", "monochrome", or "soulseek"
    reason: str = "other"  # wrong_track, poor_quality, slowed_pitched, contentid, other
    note: Optional[str] = None  # Optional free-text detail
    block_uploader: bool = False  # Also blacklist the uploader

class TestSlskdRequest(BaseModel):
    url: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None

class TestNavidromeRequest(BaseModel):
    url: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None

class TestJellyfinRequest(BaseModel):
    url: Optional[str] = None
    api_key: Optional[str] = None

class TestLidarrRequest(BaseModel):
    url: Optional[str] = None
    api_key: Optional[str] = None

class TestAppriseRequest(BaseModel):
    url: Optional[str] = None

class TestYouTubeCookiesRequest(BaseModel):
    cookies: Optional[str] = None

class TestSpotifyCookiesRequest(BaseModel):
    cookies: Optional[str] = None

class WatchedArtistRequest(BaseModel):
    mbid: str
    name: str
    from_date: str  # YYYY-MM-DD
    refresh_interval_hours: int = 24
    convert_to_flac: bool = DEFAULT_CONVERT_TO_FLAC
    _validate_mbid = field_validator("mbid")(_validate_mbid)

class WatchedArtistUpdate(BaseModel):
    enabled: Optional[bool] = None
    refresh_interval_hours: Optional[int] = None
    convert_to_flac: Optional[bool] = None
    from_date: Optional[str] = None

class AlbumDownloadRequest(BaseModel):
    artist: str
    album_title: str
    release_mbid: str
    make_m3u: bool = False
    m3u_name: Optional[str] = None
    convert_to_flac: bool = DEFAULT_CONVERT_TO_FLAC
    _validate_release_mbid = field_validator("release_mbid")(_validate_mbid)


class RetryMissingTrackRequest(BaseModel):
    artist: str
    title: str

class ExploreRequest(BaseModel):
    artist: str
    mode: str = "easy"   # easy / medium / hard
    limit: int = 25


# Auth and user management models

class LoginRequest(BaseModel):
    username: str
    password: str

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str = "user"

class SetUserPasswordRequest(BaseModel):
    new_password: str

class SetUserRoleRequest(BaseModel):
    role: str


class DownloadTokenRequest(BaseModel):
    job_id: str
