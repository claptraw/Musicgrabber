# MusicGrabber

Self-hosted music acquisition service. Search YouTube/Soulseek, download best quality audio as FLAC, auto-organise into library.

## Stack

- **Backend**: Python/FastAPI (`app.py` - single file, ~2900 lines)
- **Frontend**: Vanilla HTML/JS (`static/index.html`)
- **Database**: SQLite (job queue)
- **Container**: Docker

## Key Features

- YouTube search + download via yt-dlp
- Soulseek/slskd integration (in progress - needs VPN port forwarding to work properly)
- Bulk import (text file of "Artist - Title" lines)
- Spotify playlist/album import (via embed API or headless browser)
- Playlist support with M3U generation
- MusicBrainz metadata lookups
- LRClib lyrics fetching
- Navidrome + Jellyfin library refresh triggers
- Duplicate detection

## File Structure

```
/music/Singles/Artist Name/Track Title.flac
```

## Environment Variables

Key ones: `MUSIC_DIR`, `DB_PATH`, `ENABLE_MUSICBRAINZ`, `ENABLE_LYRICS`, `DEFAULT_CONVERT_TO_FLAC`, `NAVIDROME_*`, `JELLYFIN_*`, `SLSKD_*`

See `docker-compose.yml` and `README.md` for full list.

## Deployment

- Docker Hub: `g33kphr33k/musicgrabber`
- Port: 38274 -> 8080
- Karl's slskd is on a VM at 192.168.122.109:5030, behind Surfshark VPN (no port forwarding, so downloads get rejected)

## Current State

- v1.6.0
- Centralised constants at top of app.py for easy tuning (timeouts, limits, delays)
- Dynamic version display (frontend fetches from API)
- Async bulk import with parallel search/download
- Soulseek integration code complete but untested due to VPN port forwarding issues
- YouTube downloading works fine
- Jellyfin integration added

## Feature Requests

See `.claude/requests.md`


## Git
Add "built with a human and a grumpy-AI combo" to commits rather than stating co-authored

## Docker
When pushing, trigger a build "docker push g33kphr33k/musicgrabber:latest", etc.