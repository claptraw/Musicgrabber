# Changelog

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
