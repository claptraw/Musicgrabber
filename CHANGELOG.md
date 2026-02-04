# Changelog

## v1.8.2 (2026-02-03)

### Added
- **PUID/PGID support**: Run the container as a specific user/group for correct file ownership (like *arr stack). Set `PUID=1000` and `PGID=1000` in your environment to match your host user
- **Preview button visibility**: The play/preview button on search results is now always visible (dimmed) and highlights on hover, making the feature more discoverable

### Fixed
- **Queue timestamps ignore timezone**: Timestamps in the queue now correctly respect the user's timezone. SQLite stores times in UTC, and the frontend now properly interprets them as UTC before converting to local time

## v1.8.1 (2026-01-31)

### Added
- **Settings clear buttons**: All text and password settings now have an inline "Clear" button that clears the field and saves in a single click

### Fixed
- **Download permission errors**: When yt-dlp fails with a permission denied error on temp file rename (e.g. `Brunette.temp.flac` → `Brunette.flac`), leftover `.temp.*` files are now cleaned up and the download is retried automatically. Applies to both single track and playlist downloads

## v1.8.0 (2026-01-31)

### Changed
- **Codebase split**: Monolithic `app.py` (~4778 lines) split into 15 focused modules — `app.py` is now a thin route layer, with main logic in `constants.py`, `models.py`, `db.py`, `settings.py`, `utils.py`, `middleware.py`, `youtube.py`, `slskd.py`, `spotify.py`, `metadata.py`, `notifications.py`, `downloads.py`, `bulk_import.py`, and `watched_playlists.py`
- **Notification function renamed**: `send_telegram_notification` → `send_notification`
- **Dockerfile**: Now copies all Python modules (`COPY *.py`) instead of just `app.py`
- **YouTube backoff settings**: Warns when min/max are misconfigured and swapped
- **Title cleaning**: Consolidated title cleanup regexes into a single pass
- **Search ranking**: YouTube scoring now uses query-aware token matching and stricter artist/title alignment
- **DB connections**: Switched call sites to a context-managed connection helper to ensure closes on error
- **YouTube cookies**: Added a Settings upload button and automatic cooldown when cookies appear stale
- **Background work**: Standardized background downloads/retries to use daemon threads
- **Background threads**: Centralized the daemon thread helper in `utils` for shared use
- **Bulk import search**: Reused shared YouTube search parsing/scoring logic to avoid drift
- **SQLite pooling**: Added a small connection pool for reuse
- **SQLite pooling fix**: Enabled cross-thread connections for pooled reuse in FastAPI
- **File permissions**: Audio files now get `0o666` instead of `0o777` (no execute bit)
- **Bulk import progress**: Progress display now tracks downloads through to completion instead of showing "Complete" while tracks are still downloading
- **YouTube Topic channels**: Artist names from YouTube auto-generated "- Topic" channels are now cleaned up properly
- **Rate limiting**: Added periodic cleanup to prevent long-lived IP entries from accumulating
- **Scheduler jitter**: Watched playlist checks add a small random offset to avoid synchronized polling

### Removed
- **Sync bulk import endpoint**: Removed `/api/bulk-import` (the sync, event-loop-blocking version). Use `/api/bulk-import-async` instead
- **Legacy bulk import model**: Removed unused `BulkImportRequest`
- **Notification alias**: Removed unused `send_telegram_notification` alias

### Fixed
- **Search scoring**: Removed duplicate cover/remix penalty in YouTube scoring
- **YouTube ID validation**: Added basic ID validation before building yt-dlp URLs
- **Title splitting**: Hyphens in compound words (e.g. "T-4") no longer incorrectly split artist from title
- **Variable safety**: `process_download` no longer uses fragile `dir()` checks for variable existence
- **Playlist track failures**: Fixed `NameError` (`processed_tracks` -> `completed_tracks`) that caused a single track failure to kill the entire playlist job
- **Download success path**: Fixed indentation bug where successful first-attempt downloads skipped metadata, library scans, and job completion
- **Connection pool safety**: `row_factory` is now reset when connections are returned to the pool, preventing leaked state between callers
- **DB rollback semantics**: Only roll back open transactions on `db_conn()` exit
- **YouTube cookie test cleanup**: Temp cookie files are now cleaned up on all failure paths
- **yt-dlp retry logic**: Consolidated cookie/backoff retry logic to avoid drift across download paths
- **API key compare**: Constant-time comparison for API keys
- **Search input validation**: Added max length constraints to search queries
- **MusicBrainz UA**: Standardized the User-Agent URL used for MusicBrainz lookups

## v1.7.1 (2026-01-30)

### Added
- **Watched playlist FLAC controls**: Per-playlist FLAC toggle plus a "Convert to FLAC" option when adding a watched playlist
- **Queue job details**: Click completed/failed items in the queue to expand and see source URL, queued/completed timestamps, and download duration
- **Source URL tracking**: Jobs now store the YouTube URL or Soulseek path they were downloaded from
- **Stale job detection**: Background monitor marks stuck downloading/queued jobs as failed after 15 minutes of no progress. Also runs at startup to catch jobs orphaned by container restarts
- **YouTube cookie support**: Paste browser cookies in Settings to authenticate yt-dlp requests and avoid YouTube 403 bot-detection blocks. Includes a "Test Cookies" button that validates against YouTube before saving
- **YouTube 403 auto-retry**: Downloads that hit a 403/Forbidden error automatically retry up to 2 times with increasing backoff. Failed jobs show a clear hint about cookies in the queue error message

### Changed
- **Watched playlist creation**: Now honours the FLAC setting selected at creation time
- **Settings env lock badge**: Replaced "ENV" with a clearer "CONFIG LOCKED" pill
- **Clear Queue**: Now also cleans up stale/stuck downloads, not just completed and failed jobs
- **YouTube download client**: Default yt-dlp player client set to Android to reduce bot blocks
- **Bot backoff**: Queue now applies a randomized delay after bot/403 signals to ease rate limits

### Fixed
- **Env-locked settings**: Greyed out locked fields and added hover hint explaining they are set via docker-compose.yml
- **Stuck downloads**: Jobs that were permanently stuck in "downloading" status (e.g. from crashed background tasks or container restarts) are now automatically timed out and can be cleared
- **Queue errors**: Completed jobs now clear stale error messages


## v1.7.0 (2026-01-25)

### Added
- **Settings tab**: New UI tab for configuring all integrations without editing docker-compose.yml
  - Configure slskd, Navidrome, Jellyfin connections
  - Set up notification channels (Telegram, SMTP)
  - Toggle MusicBrainz metadata and lyrics fetching
  - Test connection buttons for slskd, Navidrome, Jellyfin
  - Password fields with show/hide toggle
  - Environment variables override database values (shown as locked in UI)
- **API authentication**: Optional API key protection for all endpoints
  - Set API key in Settings or via `API_KEY` environment variable
  - Frontend prompts for key and stores in browser localStorage
  - Clear/change stored key via Settings UI
- **Rate limiting**: 60 requests per minute per IP address
  - Proper 429 responses with `Retry-After` header
  - `X-RateLimit-Limit`, `X-RateLimit-Remaining` headers on all API responses
  - Respects `X-Forwarded-For` for reverse proxy setups

### Changed
- **Configuration approach**: Settings can now be managed via UI instead of environment variables
- **Security section in README**: Updated with API key authentication details

### Fixed
- **Watched playlist scheduler**: Now checks for due playlists immediately on startup instead of waiting for the first interval to elapse
- **Test connection buttons**: Now use current form values instead of requiring save first
- **Test connection result display**: Results now properly appear after testing
- **Settings save**: Only saves fields that have actually changed (prevents saving placeholder text)
- **FLAC toggle sync**: Header FLAC toggle and Settings FLAC checkbox now stay in sync

### Technical Details
- Settings stored in SQLite `settings` table
- `AuthMiddleware` handles API key validation and rate limiting
- `/api/config` endpoint now returns `auth_required` flag
- All fetch calls wrapped in `apiFetch()` for automatic auth header injection

## v1.6.1 (2026-01-22)

### Added
- **Copy playlist URL**: Watched playlists now include a "Copy URL" button in the UI
- **Watched playlist bulk import**: Newly watched playlists now queue downloads via bulk import
- **Notifications**: Get notified when downloads complete or fail
  - Telegram support via webhook URL (`TELEGRAM_WEBHOOK_URL`)
  - Email support via SMTP (`SMTP_HOST`, `SMTP_USER`, etc.)
  - Shared triggers for all channels (`NOTIFY_ON`): singles, playlists, bulk, errors

### Changed
- **Watched playlist refresh**: Refresh now requeues missing tracks and only pulls what is not yet downloaded

### Fixed
- **Watched playlist download tracking**: Completed jobs now update watched track download status
- **Favicon not showing in browser**: Added mount in FastAPI

## v1.6.0 (2026-01-20)

### Added
- **Centralised configuration constants**: All timeout values and magic numbers now defined at top of `app.py` for easy tuning
- **Dynamic version display**: Frontend now fetches version from `/api/config` endpoint instead of hardcoding

### Changed
- **Version management**: Single `VERSION` constant used throughout backend (FastAPI app, User-Agent strings, API responses)
- **Consistent User-Agent**: All HTTP clients now use `MusicGrabber/{VERSION}` format (fixed outdated `1.1.0` in lyrics fetcher)

### Technical Details
New constants section at top of `app.py`:
- Timeout values: `TIMEOUT_YTDLP_*`, `TIMEOUT_SLSKD_*`, `TIMEOUT_HTTP_*`, `TIMEOUT_FFMPEG_CONVERT`, `TIMEOUT_SPOTIFY_BROWSER`
- Bulk import: `BULK_IMPORT_SEARCH_DELAY`, `BULK_IMPORT_BACKOFF_DELAYS`, `BULK_IMPORT_BACKOFF_RESET_AFTER`
- Playlist: `PLAYLIST_WAIT_MAX`, `PLAYLIST_WAIT_INTERVAL`
- Search: `YOUTUBE_SEARCH_MULTIPLIER`, `YOUTUBE_SEARCH_MIN_FETCH`, `SLSKD_MAX_RESULTS`, `SLSKD_MIN_QUALITY_SCORE`
- Files: `MAX_FILENAME_LENGTH`

### Removed
- Dead code block in Spotify browser scraper (unreachable `for row in []` loop)

## v1.5.2 (2026-01-19)

### Added
- **Full Spotify playlist support**: Large playlists (100+ tracks) now fully supported via headless browser scraping
- **Playwright integration**: Added Chromium-based browser automation for Spotify pages that exceed embed limits
- **Virtualized scroll handling**: Extracts tracks incrementally while scrolling to handle Spotify's lazy-loading
- **Cookie consent automation**: Automatically dismisses Spotify's cookie banner during scraping

### Technical Details
- Spotify's embed endpoint only returns ~100 tracks maximum
- For larger playlists, MusicGrabber launches a headless Chromium browser via Playwright
- The browser loads the full Spotify page, accepts cookies, then scrolls through the tracklist
- Tracks are extracted incrementally during scrolling (Spotify uses virtualized lists that unload off-screen items)
- Only numbered tracks are extracted, filtering out "Recommended" suggestions at the bottom
- Docker image now includes Playwright and Chromium (~400MB additional)
- Added `shm_size: 2gb` to docker-compose for Chromium's shared memory requirements

### Changed
- Dockerfile now installs Playwright and Chromium browser
- Updated README with detailed Spotify integration documentation

### Removed
- Spotify API authentication code (Spotify has disabled new app creation, so this was unusable)

### Fixed
- **Soulseek retry bug**: Failed Soulseek downloads can now be retried correctly (metadata is persisted in jobs table)
- **Playlist status reporting**: Playlist jobs now track individual track failures and report partial success
- **Path traversal protection**: slskd download paths are now validated to prevent copying files from outside allowed directories

### Security
- Added security documentation to README about lack of built-in authentication
- Recommend reverse proxy with auth for external access

## v1.5.1 (2026-01-18)

### Added
- **Async bulk import**: Large playlist imports (1000+ tracks) now process asynchronously with real-time progress tracking
- **Parallel search and download**: Downloads start immediately as tracks are found, rather than waiting for all searches to complete
- **Rate limiting protection**: Automatic exponential backoff (30s, 60s, 120s, 300s) when YouTube returns 429 errors
- **Spotify album support**: Can now import from Spotify album URLs in addition to playlists
- **Jellyfin integration**: Added support for Jellyfin library refresh after downloads (configure via `JELLYFIN_URL` and `JELLYFIN_API_KEY`)
- **Progress UI**: New 5-column progress display showing Searched, Queued, Done, Failed, and Total counts

### Fixed
- Unicode escape errors when parsing Spotify track names with special characters
- Recent Activity now shows recently processed tracks correctly during bulk imports

### Changed
- Removed 70-track limit on bulk imports
- Bulk import state persisted to database for resilience across restarts

## v1.5.0

### Added
- Jellyfin integration for automatic library refresh

## v1.4.1

### Fixed
- Improved slskd download handling for transient failures

## v1.4.0

### Added
- Soulseek/slskd integration for higher quality sources (requires VPN port forwarding)

## v1.3.0

### Added
- Spotify public playlist import
- Various database async fixes

## Earlier versions

- YouTube search and download via yt-dlp
- MusicBrainz metadata lookups
- LRClib lyrics fetching
- Navidrome library refresh trigger
- Duplicate detection
- M3U playlist generation
- FLAC conversion
