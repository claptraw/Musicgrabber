# Music Grabber 🎵

A self-hosted music acquisition service. Search YouTube, tap a result, and it downloads the best quality audio as FLAC straight into your Navidrome library.

## Features

- **Mobile-friendly UI** — designed for quick searches from your phone
- **YouTube search** — finds tracks via yt-dlp
- **Best quality FLAC** — extracts highest available audio quality
- **Smart metadata** — extracts artist/title from video info, embeds thumbnails
- **Auto-organize** — creates `Singles/Artist/Title.flac` structure
- **Job queue** — track download progress, see history
- **Optional Navidrome integration** — auto-triggers library rescan

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
     - /srv/music:/music  # <-- your Navidrome music directory
   ```

3. **Build and run**
   ```bash
   docker compose up -d --build
   ```

4. **Access the UI**
   
   Open `http://your-server:8080` on your phone or browser.

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MUSIC_DIR` | `/music` | Music library root inside container |
| `DB_PATH` | `/data/music_grabber.db` | SQLite database path |
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

## File Structure

Downloads are organized as:
```
/music/
└── Singles/
    └── Artist Name/
        └── Track Title.flac
```

The artist and title are extracted from the YouTube video metadata. Common patterns like "Artist - Title" are parsed automatically.

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/` | Web UI |
| `POST` | `/api/search` | Search YouTube (`{"query": "...", "limit": 15}`) |
| `POST` | `/api/download` | Queue download (`{"video_id": "...", "title": "..."}`) |
| `GET` | `/api/jobs` | List recent jobs |
| `GET` | `/api/jobs/{id}` | Get job status |

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
- Ensure port 8080 is open on your firewall
- If using a reverse proxy, check the configuration

## License

Do whatever you want with it. 🤷
