"""
MusicGrabber - Application Constants

All shared constants in one place for easy tuning.
"""

import os
from pathlib import Path

VERSION = "2.9.1"


def _normalise_root_path(value: str) -> str:
    value = (value or "").strip()
    if not value or value == "/":
        return ""
    return "/" + value.strip("/")

# Timeout values (in seconds)
TIMEOUT_YTDLP_INFO = 30          # Getting video/playlist info
TIMEOUT_YTDLP_SEARCH = 30        # Search queries
TIMEOUT_YTDLP_DOWNLOAD = int(os.getenv("TIMEOUT_YTDLP_DOWNLOAD", "300"))  # Downloading a track (5 minutes)
TIMEOUT_YTDLP_PREVIEW = 15       # Getting preview URL
TIMEOUT_YTDLP_PLAYLIST = 60      # Getting playlist contents
TIMEOUT_FFMPEG_CONVERT = int(os.getenv("TIMEOUT_FFMPEG_CONVERT", "120"))  # Converting audio formats
TIMEOUT_HTTP_REQUEST = 10        # MusicBrainz, LRClib, Navidrome API calls
TIMEOUT_HTTP_SPOTIFY = 30        # Spotify embed fetch
SPOTIFY_EMBED_MAX_ATTEMPTS = 3   # Spotify's embed edge throws transient 502/503/504s; retry before giving up
SPOTIFY_EMBED_RETRY_BACKOFF = 1.5  # Seconds, multiplied by attempt number for a simple linear backoff
TIMEOUT_SLSKD_SEARCH = 12        # Soulseek search polling
TIMEOUT_SLSKD_DOWNLOAD = 600     # Soulseek download (10 minutes)
TIMEOUT_SLSKD_API = 30           # slskd API calls
TIMEOUT_SPOTIFY_BROWSER = 180    # Headless browser for large playlists (3 minutes)
SPOTIFY_BROWSER_STALL_SECONDS = 30  # No-progress cutoff while scrolling long Spotify playlists
TIMEOUT_AMAZON_BROWSER = 180     # Amazon Music playlist scraping (3 minutes)
TIMEOUT_FPCALC = 30              # Audio fingerprinting via fpcalc
TIMEOUT_MP3PHOENIX_SEARCH = 15   # mp3phoenix AJAX search
TIMEOUT_MP3PHOENIX_DOWNLOAD = int(os.getenv("TIMEOUT_MP3PHOENIX_DOWNLOAD", "120"))  # mp3phoenix direct MP3 stream
TIMEOUT_ZVU4NO_SEARCH = 15       # zvu4no HTML search
TIMEOUT_ZVU4NO_DOWNLOAD = int(os.getenv("TIMEOUT_ZVU4NO_DOWNLOAD", "120"))  # zvu4no direct MP3 stream
TIMEOUT_FREEMP3CLOUD_SEARCH = 20    # FreeMp3Cloud landing + form POST (two round-trips)
TIMEOUT_FREEMP3CLOUD_DOWNLOAD = int(os.getenv("TIMEOUT_FREEMP3CLOUD_DOWNLOAD", "120"))  # FreeMp3Cloud direct MP3 stream
TIMEOUT_MONOCHROME_SEARCH = 15   # Monochrome/Qobuz search and proxy lookups
TIMEOUT_MONOCHROME_DOWNLOAD = int(os.getenv("TIMEOUT_MONOCHROME_DOWNLOAD", "300"))  # Qobuz FLAC CDN download (FLACs are big)
STALE_JOB_TIMEOUT = 900          # Mark downloading/queued jobs as failed after 15 minutes
STALE_JOB_CHECK_INTERVAL = 120   # Check for stale jobs every 2 minutes
LIBRARY_RECONCILE_INTERVAL = int(os.getenv("LIBRARY_RECONCILE_INTERVAL", "1800"))  # Reconcile deleted/renamed files every 30 minutes

# Bulk import settings
BULK_IMPORT_SEARCH_DELAY = 1.0           # Seconds between searches (be courteous to all sources)
PRIORITY_SOURCE_BOOST = 500              # Quality-score bonus applied to the user-chosen "preferred source" during bulk import / watched playlist refreshes. Big enough to win nearly every close call without nuking the strict-artist-match safety net.

# Playlist creation
PLAYLIST_WAIT_MAX = 3600         # Max seconds to wait for downloads to complete (1 hour)
PLAYLIST_WAIT_INTERVAL = 10      # Seconds between completion checks

# Search and results
YOUTUBE_SEARCH_MULTIPLIER = 3    # Fetch N times more results than requested for scoring
YOUTUBE_SEARCH_MIN_FETCH = 30    # Minimum results to fetch for scoring
SOUNDCLOUD_SEARCH_MULTIPLIER = 2 # Less noise on SoundCloud, so fewer extras needed
SOUNDCLOUD_SEARCH_MIN_FETCH = 15 # Minimum results to fetch for scoring
SLSKD_MAX_RESULTS = 20           # Max Soulseek results to return
SEARCH_MAX_PER_SOURCE = 4        # Max results any single source can contribute to an "All" search
SEARCH_MAX_PER_SOURCE_YOUTUBE = 6
SEARCH_MAX_PER_SOURCE_MP3PHOENIX = 4
SEARCH_MAX_PER_SOURCE_SOUNDCLOUD = 4
SEARCH_MAX_PER_SOURCE_ZVU4NO = 4
SEARCH_MAX_PER_SOURCE_FREEMP3CLOUD = 4
SEARCH_MAX_PER_SOURCE_SOULSEEK = 6
SEARCH_MAX_PER_SOURCE_MONOCHROME = 6
SLSKD_MIN_QUALITY_SCORE = 50     # Minimum quality score to include result
SLSKD_MATCH_CONFIDENCE_FLOOR = float(os.getenv("SLSKD_MATCH_CONFIDENCE_FLOOR", "0.55"))  # 0.0-1.0; reject worse than this
MAX_SEARCH_QUERY_LENGTH = 512    # Max characters allowed in search input
SEARCH_LOG_RETENTION_DAYS = 90   # Keep search analytics for N days

# File handling
MAX_FILENAME_LENGTH = 200        # Maximum characters in sanitised filenames
COOKIES_FILE = Path("/data/cookies.txt")  # yt-dlp cookies file path
AUDIO_EXTENSIONS = ['.flac', '.opus', '.m4a', '.webm', '.mp3', '.ogg']

# YouTube 403 retry
YTDLP_403_MAX_RETRIES = 2       # Retry attempts on 403/Forbidden errors
YTDLP_403_RETRY_DELAY = 3       # Seconds between retries

# YouTube bot/backoff handling
BOT_BACKOFF_MIN_SECONDS = 5
BOT_BACKOFF_MAX_SECONDS = 20

# YouTube player client override (empty = yt-dlp default / web client)
YTDLP_PLAYER_CLIENT = os.getenv("YTDLP_PLAYER_CLIENT", "")

# Rate limiting
RATE_LIMIT_REQUESTS = 200        # Max requests per IP per window  -  single-user tool, be generous
RATE_LIMIT_WINDOW = 60           # Window size in seconds

# Login hardening
LOGIN_MAX_ATTEMPTS = int(os.getenv("LOGIN_MAX_ATTEMPTS", "5"))
LOGIN_LOCKOUT_SECONDS = int(os.getenv("LOGIN_LOCKOUT_SECONDS", "900"))
LOGIN_ATTEMPT_WINDOW = int(os.getenv("LOGIN_ATTEMPT_WINDOW", "900"))

# Download token auth (for browser file downloads without exposing session tokens in URLs)
DOWNLOAD_TOKEN_TTL_SECONDS = int(os.getenv("DOWNLOAD_TOKEN_TTL_SECONDS", "60"))

# Transport security
HTTPS_ONLY = os.getenv("HTTPS_ONLY", "false").lower() == "true"
HSTS_MAX_AGE = int(os.getenv("HSTS_MAX_AGE", "31536000"))
ALLOW_API_KEY_QUERY_PARAM = os.getenv("ALLOW_API_KEY_QUERY_PARAM", "false").lower() == "true"

# Configuration from environment - structural paths
MUSIC_DIR = Path(os.getenv("MUSIC_DIR", "/music"))
DB_PATH = Path(os.getenv("DB_PATH", "/data/music_grabber.db"))
ROOT_PATH = _normalise_root_path(os.getenv("ROOT_PATH", ""))

# Other settings that don't change at runtime (not in UI)
SLSKD_REQUIRE_FREE_SLOT = os.getenv("SLSKD_REQUIRE_FREE_SLOT", "true").lower() == "true"
SLSKD_MAX_RETRIES = int(os.getenv("SLSKD_MAX_RETRIES", "5"))
WATCHED_PLAYLIST_CHECK_HOURS = int(os.getenv("WATCHED_PLAYLIST_CHECK_HOURS", "24"))
# How often the track-upgrades scan walks the library (hours). Cheap and network-free,
# so daily is plenty; this is not time-sensitive.
UPGRADE_SCAN_INTERVAL_HOURS = int(os.getenv("UPGRADE_SCAN_INTERVAL_HOURS", "24"))
# How long a per-candidate upgrade search result stays cached before a revisit
# re-searches (seconds). Keeps the Watched Upgrades page snappy without re-hammering
# sources on every visit. Default 4 hours.
UPGRADE_SEARCH_TTL_SECONDS = int(os.getenv("UPGRADE_SEARCH_TTL_SECONDS", str(4 * 3600)))
# Minimum match confidence (0..1) for a found result to count as the same track.
# Deliberately high: we are proposing to replace a file, not just rank a search.
UPGRADE_MATCH_FLOOR = float(os.getenv("UPGRADE_MATCH_FLOOR", "0.6"))
WATCHED_REFRESH_STALE_SECONDS = int(os.getenv("WATCHED_REFRESH_STALE_SECONDS", "1800"))
# How many consecutive "not found" (404) refreshes before we assume a watched
# playlist has genuinely vanished upstream and auto-pause it. We wait for a few
# strikes so a transient blip, a private playlist, or an expired login token
# doesn't get a playlist paused on the strength of one bad fetch.
WATCHED_GONE_STRIKES_BEFORE_PAUSE = int(os.getenv("WATCHED_GONE_STRIKES_BEFORE_PAUSE", "3"))

# AcoustID audio fingerprinting  -  because guessing metadata from titles
# is about as reliable as asking YouTube commenters for facts.
# API key is now configurable via Settings; this is just the confidence threshold.
ACOUSTID_MIN_SCORE = 0.8         # Below this, the match is too dodgy to trust
MIN_SONG_DURATION_SECS = 30      # Files shorter than this are too brief to fingerprint reliably
MAX_AUDIO_START_OFFSET_SECS = 1.0  # Start offsets above this indicate a preview segment, not a full track
MB_DURATION_TOLERANCE = 0.10     # 10% either side of MusicBrainz expected duration; outside = wrong track
SILENCE_DETECT_DURATION = 8.0    # Seconds of continuous silence that flags a sabotaged track
SILENCE_DETECT_NOISE = -50.0     # dB threshold below which audio counts as silence
SILENCE_DETECT_MIN_START = 15.0  # Ignore silence that starts before this point (legitimate intros)
SILENCE_DETECT_MAX_END_FRAC = 0.60  # Only scan the first 60% of the track — leaves hidden/secret tracks alone

# Watched artists  -  MusicBrainz artist search and singles polling
MB_ARTIST_SEARCH_LIMIT = 5       # Candidate results returned when searching by name
TIMEOUT_MUSICBRAINZ_ARTIST = 10  # Artist search + singles listing HTTP timeout

# ListenBrainz API  -  used for similar artist exploration and "Created for You" playlists
# (public API, no auth required for either)
LISTENBRAINZ_API_URL = "https://api.listenbrainz.org"
TIMEOUT_LISTENBRAINZ = 10
TIMEOUT_LISTENBRAINZ_PLAYLIST = 15   # Per-playlist JSPF fetch

# Cover art fallback chain  -  we try really hard to get proper album art
COVER_ART_TIMEOUT = 10           # Per-source HTTP timeout for cover art fetches
ITUNES_SEARCH_URL = "https://itunes.apple.com/search"
DEEZER_SEARCH_URL = "https://api.deezer.com/search"

# Default settings for fields that need startup values
DEFAULT_CONVERT_TO_FLAC = os.getenv("DEFAULT_CONVERT_TO_FLAC", "true").lower() == "true"

# Monochrome (Qobuz/Tidal) — configurable so you can point at a self-hosted hifi-api
MONOCHROME_HIFI_API_URL = os.getenv(
    "MONOCHROME_HIFI_API_URL",
    "https://us-west.monochrome.tf,https://monochrome-api.samidy.com",
)
MONOCHROME_QOBUZ_PROXY_URL = os.getenv(
    "MONOCHROME_QOBUZ_PROXY_URL",
    "https://qobuz.kennyy.com.br,https://mono.scavengerfurs.net,https://qdl-api.monochrome.tf",
)
# The Qobuz proxies are gloriously flaky (502 one second, 200 the next), so we
# sweep the whole list, have a little lie down, then sweep again a few times
# before declaring the source dead and letting the fallback machinery take over.
MONOCHROME_PROXY_RETRY_ROUNDS = int(os.getenv("MONOCHROME_PROXY_RETRY_ROUNDS", "5"))
MONOCHROME_PROXY_RETRY_WAIT = float(os.getenv("MONOCHROME_PROXY_RETRY_WAIT", "3"))

# qbdlx fallback: when every Qobuz proxy is down, sign the official Qobuz API
# ourselves using a shared free-account token (the same pool the qbdlx web UI
# uses). No proxy middleman, so it survives when the proxies are all face-down.
# Heads up: the free shared tokens deliver 16/44.1 lossless FLAC, not 24-bit
# hi-res, so this is a "a real FLAC beats a failed download" safety net.
QBDLX_FALLBACK_ENABLED = os.getenv("QBDLX_FALLBACK_ENABLED", "true").lower() == "true"
QBDLX_SHARED_TOKENS_URL = os.getenv(
    "QBDLX_SHARED_TOKENS_URL",
    "https://citegptapi.f5.si/webhook/qbdlx/shared",
)
QBDLX_QOBUZ_API_BASE = os.getenv("QBDLX_QOBUZ_API_BASE", "https://www.qobuz.com/api.json/0.2/")
QBDLX_TOKEN_CACHE_TTL = int(os.getenv("QBDLX_TOKEN_CACHE_TTL", "600"))  # re-fetch the pool every N seconds

# Source health checks: living the pirate lifestyle means free services come and
# go, so we check whether each source can actually deliver before showing its
# results. A failed check parks the source for a cooldown, then we re-check.
SOURCE_HEALTH_CHECK_INTERVAL = int(os.getenv("SOURCE_HEALTH_CHECK_INTERVAL", "600"))  # re-check a source's health at most this often (seconds)
SOURCE_HEALTH_COOLDOWN = int(os.getenv("SOURCE_HEALTH_COOLDOWN", "600"))              # how long a failed source stays auto-disabled (seconds)
SERVICECHECK_TIMEOUT = int(os.getenv("SERVICECHECK_TIMEOUT", "8"))                    # per-source health probe timeout (seconds)
