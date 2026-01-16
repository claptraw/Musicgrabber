# Music Grabber 🎵

**v1.1.0**

A self-hosted music acquisition service. Search YouTube, tap a result, and it downloads the best quality audio as FLAC straight into your music library.

## Features

- **Mobile-friendly UI** — designed for quick searches from your phone
- **Hover to preview** — on desktop, hover over a result for 2 seconds to hear a preview
- **YouTube search** — finds tracks and playlists via yt-dlp
- **Playlist support** — download entire playlists with automatic M3U generation
- **Bulk import** — paste or upload a text file of songs to auto-search and queue
- **Best quality FLAC** — extracts highest available audio quality
- **Enhanced metadata** — MusicBrainz lookups with fallback to cleaned YouTube data
- **Auto-organise** — creates `Singles/Artist/Title.flac` structure
- **Duplicate detection** — skips already-downloaded tracks
- **Job queue** — track download progress, retry failed jobs, manage history
- **Optional Navidrome integration** — auto-triggers library rescan

## Screenshots

| Search & Results | Bulk Import | Queue |
|:---:|:---:|:---:|
| ![Search and Results](assets/SearchAndResults.png) | ![Bulk Import](assets/BulkImport.png) | ![Queue](assets/Queue.png) |

## Quick Start

1. **Clone and configure**
   ```bash
   git clone <your-repo> music-grabber
   cd music-grabber
   ```

2. **Edit docker-compose.yml**

   Update the music volume path:
   ```yaml
   volumes:
     - /mnt/music:/music  # <-- your music directory
   ```

3. **Build and run**
   ```bash
   docker compose up -d --build
   ```

4. **Access the UI**

   Open `http://your-server:38274` on your phone or browser.

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MUSIC_DIR` | `/music` | Music library root inside container |
| `DB_PATH` | `/data/music_grabber.db` | SQLite database path |
| `ENABLE_MUSICBRAINZ` | `true` | Enable MusicBrainz metadata lookups |
| `NAVIDROME_URL` | - | Navidrome server URL (e.g., `http://navidrome:4533`) |
| `NAVIDROME_USER` | - | Navidrome username for API |
| `NAVIDROME_PASS` | - | Navidrome password for API |

### Navidrome Auto-Rescan

To automatically trigger a library scan after downloads, add your Navidrome credentials:

```yaml
environment:
  - NAVIDROME_URL=http://navidrome:4533
  - NAVIDROME_USER=admin
  - NAVIDROME_PASS=yourpassword
```

If running on the same Docker network as Navidrome, use the container name as the hostname.

### Reverse Proxy (Caddy example)

```
music.yourdomain.com {
    reverse_proxy music-grabber:8080
}
```

## Usage

### Search and Download

1. **Single tracks** — Search for a song, tap/click the result to download
2. **Preview** — On desktop, hover over a result for 2 seconds to hear a preview (cached for quick replays)
3. **Playlists** — Search for a playlist URL or name, tap the playlist result to download all tracks
4. **Processing feedback** — Shows "Processing..." immediately when tapped, then "Added to queue ✓"

### Bulk Import

Upload a text file or paste a list of songs in the format:
```
ABBA – Dancing Queen
ABBA – Super Trouper
Backstreet Boys – I Want It That Way
```

The app will:
- Search YouTube for each song automatically
- Queue downloads for best matches
- Show success/failure summary
- All processing happens in-memory (files are not stored on server)

Supports various dash formats: `-`, `–`, `—`

### Queue Management

- **View progress** — See queued, in-progress, completed, and failed jobs
- **Retry failed** — Click retry on individual failed downloads
- **Clear queue** — Remove all remembered jobs with the "Clear Queue" button
- **Bulk cleanup** — Use API endpoints to remove completed/failed jobs in bulk

## File Structure

Downloads are organised as:
```
/music/
└── Singles/
    ├── Artist Name/
    │   └── Track Title.flac
    └── Playlist Name.m3u
```

- All tracks go into `Singles/Artist/` directories, even from playlists
- Playlist downloads generate `.m3u` files with relative paths
- Artist and title are extracted from YouTube metadata
- Common patterns like "Artist - Title" are parsed automatically
- YouTube annotations (Official Audio, Lyrics, etc.) are cleaned from titles

### Metadata

When `ENABLE_MUSICBRAINZ=true`:
1. Searches MusicBrainz for accurate artist, title, album, and year
2. Falls back to cleaned YouTube metadata if not found
3. Sets album to "Singles" by default
4. Embeds cover art from YouTube thumbnails

### Duplicate Detection

Before downloading, checks if the track already exists:
- Exact filename match
- Case-insensitive matching
- Skips download and reports as duplicate

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/` | Web UI |
| `POST` | `/api/search` | Search YouTube (`{"query": "...", "limit": 15}`) |
| `GET` | `/api/preview/{video_id}` | Get streamable audio URL for preview |
| `POST` | `/api/download` | Queue download (`{"video_id": "...", "title": "...", "download_type": "single/playlist"}`) |
| `POST` | `/api/bulk-import` | Bulk import songs (`{"songs": "Artist - Song\n..."}`) |
| `GET` | `/api/jobs` | List recent jobs |
| `GET` | `/api/jobs/{id}` | Get job status |
| `POST` | `/api/jobs/{id}/retry` | Retry a failed download |
| `DELETE` | `/api/jobs/cleanup` | Delete jobs (`?status=completed/failed/both`) |

## Updating yt-dlp

YouTube changes frequently. To update yt-dlp inside the container:

```bash
docker compose exec music-grabber yt-dlp -U
```

Or rebuild the image to get the latest version:

```bash
docker compose build --no-cache
docker compose up -d
```

## Troubleshooting

**Downloads failing?**
- Check `docker compose logs music-grabber`
- YouTube may have changed something — try updating yt-dlp
- Some videos are region-locked or age-restricted

**Navidrome not seeing new files?**
- Verify the volume mount paths match
- Check Navidrome's scan interval if auto-rescan isn't configured
- Manually trigger a scan in Navidrome's UI

**Can't access from phone?**
- Ensure port 38274 is open on your firewall
- If using a reverse proxy, check the configuration

**Bulk import not finding songs?**
- Check the format is "Artist - Song" (with a dash separator)
- Try more specific search terms
- Some obscure tracks may not be on YouTube
- Check the results summary for failed searches

**Metadata quality issues?**
- Ensure `ENABLE_MUSICBRAINZ=true` in environment variables
- MusicBrainz lookups are rate-limited (1 request/second)
- Some tracks may not be in the MusicBrainz database
- YouTube metadata is used as fallback

## Contributors

Built with a mix of human creativity and AI assistance.

- **Karl** — Creator and maintainer
- **Claude (Anthropic)** — AI pair programmer

## License

Do whatever you want with it. 🤷
