"""
MusicGrabber - Pydantic Request/Response Models
"""

import re
from datetime import date as _date
from typing import Optional
from pydantic import AliasChoices, BaseModel, Field, field_validator
from constants import DEFAULT_CONVERT_AUDIO, MAX_SEARCH_QUERY_LENGTH

_UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.IGNORECASE)


def _convert_audio_field(default=DEFAULT_CONVERT_AUDIO, *, prefix=""):
    """Canonical conversion flag with the pre-v4 name as an input alias.

    `prefix` covers the settings-level spelling (`default_convert_audio`), which
    was renamed at the same time and for the same reason.
    """
    return Field(
        default=default,
        validation_alias=AliasChoices(f"{prefix}convert_audio", f"{prefix}convert_to_flac"),
    )


def _validate_mbid(v: str | None) -> str | None:
    if v and not _UUID_RE.match(v):
        raise ValueError("Invalid MBID format (expected UUID)")
    return v


def _validate_from_date(v: str | None) -> str | None:
    """A from_date is compared against MusicBrainz release dates as a plain string,
    so anything that is not a real ISO date sorts somewhere daft and quietly filters
    out either everything or nothing. Better to refuse it at the door."""
    if v is None:
        return v
    try:
        _date.fromisoformat(v)
    except ValueError:
        raise ValueError("Invalid from_date (expected YYYY-MM-DD)")
    return v


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=MAX_SEARCH_QUERY_LENGTH)
    limit: int = 15
    source: str = "all"  # "youtube", "soundcloud", "zvu4no", "freemp3cloud", or "all"

class DownloadRequest(BaseModel):
    video_id: str
    title: str
    artist: Optional[str] = None
    search_token: Optional[str] = None
    # Duration advertised by the exact search result the user clicked. This is
    # independent of canonical MusicBrainz duration, so intentional live/remix
    # choices remain valid while CDN samples can be recognised after download.
    selected_duration_secs: Optional[float] = Field(
        default=None, gt=0, le=86400, allow_inf_nan=False,
    )
    download_type: str = "single"  # "single" or "playlist"
    convert_audio: bool = _convert_audio_field()
    # Source routing
    source: str = "youtube"  # "youtube", "soundcloud", "zvu4no", "freemp3cloud", or "soulseek"
    source_url: Optional[str] = None  # Full URL for non-YouTube sources (e.g. SoundCloud/zvu4no)
    # Soulseek-specific fields
    slskd_username: Optional[str] = None
    slskd_filename: Optional[str] = None
    slskd_size: Optional[int] = None
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
    url: str  # Spotify, YouTube, Apple Music, Amazon Music, SoundCloud, etc. playlist URL


class AsyncBulkImportRequest(BaseModel):
    songs: str  # Multi-line text with "Artist - Song" format
    create_playlist: bool = False
    playlist_name: Optional[str] = None
    playlist_source_url: Optional[str] = None
    convert_audio: bool = _convert_audio_field()
    use_playlists_dir: bool = False  # Save files to Playlists folder instead of Singles
    preferred_sources: Optional[str] = None  # Comma-separated source IDs or "all"
    priority_source: Optional[str] = None  # Tie-breaker within the same automatic quality tier

class WatchedPlaylistRequest(BaseModel):
    url: str  # Spotify, YouTube, Apple Music, Amazon Music, SoundCloud, etc. playlist URL
    refresh_interval_hours: float = 24
    convert_audio: bool = _convert_audio_field()
    make_m3u: bool = False
    use_playlists_dir: bool = False  # Save files to Playlists folder instead of Singles
    sync_mode: str = "append"  # "append" = grow forever; "mirror" = track upstream removals in M3U
    preferred_sources: str = "all"  # Comma-separated source IDs or "all"
    priority_source: Optional[str] = None  # Tie-breaker within the same automatic quality tier
    custom_subdir: Optional[str] = None  # Override destination folder (relative to music_dir)

class WatchedPlaylistUpdate(BaseModel):
    refresh_interval_hours: Optional[float] = None
    enabled: Optional[bool] = None
    convert_audio: Optional[bool] = _convert_audio_field(None)
    make_m3u: Optional[bool] = None
    use_playlists_dir: Optional[bool] = None
    sync_mode: Optional[str] = None  # "append" or "mirror"
    preferred_sources: Optional[str] = None  # Comma-separated source IDs or "all"
    priority_source: Optional[str] = None  # Same-tier tie-breaker; empty string clears it
    custom_subdir: Optional[str] = None  # Override destination folder (relative to music_dir)

class SettingsUpdate(BaseModel):
    """Settings that can be updated via the UI"""
    # General
    music_dir: Optional[str] = None
    enable_musicbrainz: Optional[bool] = None
    enable_deezer_metadata: Optional[bool] = None
    enable_lyrics: Optional[bool] = None
    default_convert_audio: Optional[bool] = _convert_audio_field(None, prefix="default_")
    audio_format: Optional[str] = None  # "flac", "alac", "opus", or "mp3"
    mp3_bitrate: Optional[str] = None    # "v0", "v2", or "320k"/"256k"/"192k"/"128k"
    opus_bitrate: Optional[str] = None   # "320k"/"256k"/"192k"/"128k"/"96k"
    alac_bitrate: Optional[str] = None   # "lossless" (true ALAC) or AAC kbps "320k"/"256k"/"192k"/"128k"
    min_audio_bitrate: Optional[int] = None
    normalise_lossy_audio: Optional[bool] = None
    enable_replaygain: Optional[bool] = None
    replaygain_replace_existing: Optional[bool] = None
    auto_import_dir: Optional[str] = None
    reject_live_versions: Optional[bool] = None
    playlist_comment_tagging: Optional[bool] = None
    enable_track_upgrades: Optional[bool] = None
    upgrade_scan_interval_hours: Optional[int] = None
    singles_subdir: Optional[str] = None
    playlists_subdir: Optional[str] = None
    albums_subdir: Optional[str] = None
    organise_by_artist: Optional[bool] = None
    include_track_number_in_filename: Optional[bool] = None
    auto_album_singles: Optional[bool] = None
    auto_album_singles_use_albums_dir: Optional[bool] = None
    playlist_album_as_name: Optional[bool] = None
    singles_only_mode: Optional[bool] = None
    file_permissions: Optional[str] = None

    @field_validator("file_permissions")
    @classmethod
    def validate_file_permissions(cls, v):
        if v is not None and v not in ("666", "777"):
            raise ValueError("file_permissions must be 666 or 777")
        return v

    # Search sources
    source_youtube_enabled: Optional[bool] = None
    source_soundcloud_enabled: Optional[bool] = None
    source_zvu4no_enabled: Optional[bool] = None
    source_freemp3cloud_enabled: Optional[bool] = None
    source_soulseek_enabled: Optional[bool] = None
    source_monochrome_enabled: Optional[bool] = None
    source_offline_fallback: Optional[bool] = None
    source_health_checks_enabled: Optional[bool] = None
    source_health_check_interval_minutes: Optional[int] = None
    source_health_cooldown_minutes: Optional[int] = None
    search_concurrency: Optional[int] = Field(default=None, ge=1, le=5)
    # Monochrome (Qobuz / Tidal via hifi-api)
    monochrome_hifi_api_url: Optional[str] = None
    monochrome_qobuz_proxy_url: Optional[str] = None
    monochrome_qbdlx_fallback_enabled: Optional[bool] = None
    monochrome_browser_fallback_enabled: Optional[bool] = None
    # Soulseek/slskd
    slskd_url: Optional[str] = None
    slskd_user: Optional[str] = None
    slskd_pass: Optional[str] = None
    slskd_downloads_path: Optional[str] = None
    slskd_move_completed: Optional[bool] = None
    # Duplicate checking
    skip_dupes: Optional[bool] = None
    navidrome_dupe_check: Optional[bool] = None
    # Navidrome
    navidrome_url: Optional[str] = None
    navidrome_user: Optional[str] = None
    navidrome_pass: Optional[str] = None
    # Jellyfin
    jellyfin_url: Optional[str] = None
    jellyfin_api_key: Optional[str] = None
    # Lidarr
    lidarr_url: Optional[str] = None
    lidarr_api_key: Optional[str] = None
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
    # Apple Music
    apple_music_user_token: Optional[str] = None
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
    source: str = "youtube"  # "youtube", "soundcloud", "zvu4no", "freemp3cloud", or "soulseek"
    source_url: Optional[str] = None  # Full URL for non-YouTube sources
    quality: Optional[str] = None  # e.g., None for YouTube, format string for others
    relevance_score: int = 40  # Artist/title/duration/source rank; higher is better
    # 0 = source declined to say (YouTube/SoundCloud), 1-5 = the shared tier
    # scale in constants.py. Drives the UI's minimum-quality filter.
    quality_tier: int = 0
    slskd_username: Optional[str] = None
    slskd_filename: Optional[str] = None
    slskd_size: Optional[int] = None
    album: Optional[str] = None
    year: Optional[int] = None
    size_bytes: Optional[int] = None  # File size where the source declares one
    bitrate: Optional[int] = None     # kbps, declared or worked out from size/duration
    match_confidence: Optional[float] = None  # 0.0-1.0, Soulseek path scoring only

class BlacklistRequest(BaseModel):
    """Report a bad track / block an uploader."""
    job_id: Optional[str] = None
    video_id: Optional[str] = None
    uploader: Optional[str] = None
    source: str = "youtube"  # "youtube", "soundcloud", "zvu4no", "freemp3cloud", or "soulseek"
    reason: str = "other"  # wrong_track, poor_quality, slowed_pitched, contentid, other
    note: Optional[str] = None  # Optional free-text detail
    block_uploader: bool = False  # Also blacklist the uploader

class TestSlskdRequest(BaseModel):
    url: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    downloads_path: Optional[str] = None

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

class TestEmailRequest(BaseModel):
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    smtp_user: Optional[str] = None
    smtp_pass: Optional[str] = None
    smtp_from: Optional[str] = None
    smtp_to: Optional[str] = None
    smtp_tls: Optional[bool] = None

class TestYouTubeCookiesRequest(BaseModel):
    cookies: Optional[str] = None

class TestSpotifyCookiesRequest(BaseModel):
    cookies: Optional[str] = None

class WatchedArtistRequest(BaseModel):
    mbid: str
    name: str
    from_date: str  # YYYY-MM-DD
    refresh_interval_hours: float = 24
    convert_audio: bool = _convert_audio_field()
    auto_add_albums: bool = False  # Seeds current albums as already-seen; only later arrivals get queued
    watch_singles: bool = True  # Off for an albums-only follow, so no singles get hoovered up
    _validate_mbid = field_validator("mbid")(_validate_mbid)

class WatchedArtistUpdate(BaseModel):
    enabled: Optional[bool] = None
    refresh_interval_hours: Optional[float] = None
    convert_audio: Optional[bool] = _convert_audio_field(None)
    from_date: Optional[str] = None
    auto_add_albums: Optional[bool] = None
    watch_singles: Optional[bool] = None
    _validate_from_date = field_validator("from_date")(_validate_from_date)

class AlbumDownloadRequest(BaseModel):
    artist: str
    album_title: str
    release_mbid: str
    make_m3u: bool = False
    m3u_name: Optional[str] = None
    convert_audio: bool = _convert_audio_field()
    # Set when the grab came from a followed artist's album list, so the album is
    # recorded as already dealt with and auto-add doesn't cheerfully queue it again.
    watch_artist_id: Optional[str] = None
    release_group_mbid: Optional[str] = None
    _validate_release_mbid = field_validator("release_mbid")(_validate_mbid)


class ReleaseGroupResolveRequest(BaseModel):
    """Turn a chosen release-group into a concrete release, ready to download."""
    release_group_mbid: str
    _validate_release_group_mbid = field_validator("release_group_mbid")(_validate_mbid)


class RetryMissingTrackRequest(BaseModel):
    artist: str
    title: str

class OrphanMoveRequest(BaseModel):
    """Absolute paths of orphaned playlist files to move into Singles"""
    files: list[str]

class QueueMissingTrackCandidateRequest(BaseModel):
    artist: str
    title: str
    video_id: str
    source: str = "youtube"
    source_url: Optional[str] = None
    slskd_username: Optional[str] = None
    slskd_filename: Optional[str] = None
    slskd_size: Optional[int] = None

class PatchTagsRequest(BaseModel):
    artist: str = Field(..., min_length=1, max_length=200)
    title: str = Field(..., min_length=1, max_length=200)
    album: str = Field("", max_length=200)
    album_artist: str = Field("", max_length=200)
    year: str = Field("", max_length=4)
    track_number: Optional[int] = Field(None, ge=1, le=999)
    track_total: Optional[int] = Field(None, ge=1, le=999)

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
