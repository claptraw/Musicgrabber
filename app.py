#!/usr/bin/env python3
"""
Music Grabber - A self-hosted music acquisition service
Searches YouTube, downloads best quality audio as FLAC, drops into Navidrome library
"""

import asyncio
import hashlib
import json
import os
import re
import subprocess
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from mutagen.flac import FLAC
from mutagen.id3 import APIC

app = FastAPI(title="Music Grabber", version="1.2.0")

# Configuration from environment
MUSIC_DIR = Path(os.getenv("MUSIC_DIR", "/music"))
SINGLES_DIR = MUSIC_DIR / "Singles"
DB_PATH = Path(os.getenv("DB_PATH", "/data/music_grabber.db"))
NAVIDROME_URL = os.getenv("NAVIDROME_URL", "")
NAVIDROME_USER = os.getenv("NAVIDROME_USER", "")
NAVIDROME_PASS = os.getenv("NAVIDROME_PASS", "")
ENABLE_MUSICBRAINZ = os.getenv("ENABLE_MUSICBRAINZ", "true").lower() == "true"
ENABLE_LYRICS = os.getenv("ENABLE_LYRICS", "true").lower() == "true"

# Ensure directories exist
SINGLES_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def init_db():
    """Initialize SQLite database for job tracking"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            video_id TEXT,
            title TEXT,
            artist TEXT,
            status TEXT DEFAULT 'queued',
            error TEXT,
            download_type TEXT DEFAULT 'single',
            playlist_name TEXT,
            total_tracks INTEGER,
            completed_tracks INTEGER DEFAULT 0,
            m3u_path TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


init_db()


class SearchRequest(BaseModel):
    query: str
    limit: int = 15


class DownloadRequest(BaseModel):
    video_id: str
    title: str
    artist: Optional[str] = None
    download_type: str = "single"  # "single" or "playlist"


class BulkImportRequest(BaseModel):
    songs: str  # Multi-line text with "Artist - Song" format
    create_playlist: bool = False
    playlist_name: Optional[str] = None


class SearchResult(BaseModel):
    video_id: str
    title: str
    channel: str
    duration: str
    thumbnail: str
    is_playlist: bool = False
    video_count: Optional[int] = None


def parse_duration(seconds: float) -> str:
    """Convert seconds to MM:SS or HH:MM:SS format"""
    seconds = int(seconds)
    if seconds < 3600:
        return f"{seconds // 60}:{seconds % 60:02d}"
    return f"{seconds // 3600}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"


def score_search_result(title: str, channel: str) -> int:
    """Score a search result to prioritise official content over live versions

    Higher score = better match
    Lower score = worse match (live, cover, remix, etc.)
    """
    title_lower = title.lower()
    channel_lower = channel.lower()
    score = 100  # Start with base score

    # Penalties for live performances
    if re.search(r'\b(live|concert|tour|performance|unplugged)\b', title_lower):
        score -= 50

    # Penalties for covers, remixes, instrumentals
    if re.search(r'\b(cover|remix|instrumental|karaoke|acoustic version)\b', title_lower):
        score -= 40

    # Penalties for lyric videos (usually lower quality)
    if re.search(r'\b(lyric|lyrics)\b', title_lower):
        score -= 20

    # Penalties for fan uploads or unofficial
    if re.search(r'\b(fan|unofficial|tribute)\b', title_lower):
        score -= 30

    # Bonuses for official content
    if re.search(r'\b(official|vevo)\b', title_lower):
        score += 30

    if re.search(r'\b(official|vevo)\b', channel_lower):
        score += 40

    # Bonus for "official music video" or "official video"
    if re.search(r'official\s*(music)?\s*video', title_lower):
        score += 25

    # Bonus for official audio
    if re.search(r'official\s*audio', title_lower):
        score += 20

    # Penalty for reaction videos, compilations
    if re.search(r'\b(reaction|react|compilation|mashup|vs)\b', title_lower):
        score -= 60

    # Penalty for extended versions (often DJ mixes)
    if re.search(r'\b(extended|extended mix|extended version)\b', title_lower):
        score -= 15

    return score


def sanitize_filename(name: str) -> str:
    """Remove/replace characters that are problematic in filenames"""
    # Remove or replace problematic characters
    name = re.sub(r'[<>:"/\\|?*]', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name[:200]  # Limit length


def clean_title(title: str) -> str:
    """Clean up YouTube title by removing common suffixes and annotations"""
    # Remove common video type annotations
    title = re.sub(r'\s*\(Official.*?\)', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s*\[Official.*?\]', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s*Official\s*(Music\s*)?Video', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s*\(.*?Music\s*Video.*?\)', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s*\[.*?Music\s*Video.*?\]', '', title, flags=re.IGNORECASE)

    # Remove lyrics annotations
    title = re.sub(r'\s*\(Lyrics?\)', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s*\[Lyrics?\]', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s*\(.*?Lyric.*?\)', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s*\[.*?Lyric.*?\]', '', title, flags=re.IGNORECASE)

    # Remove audio/video quality annotations
    title = re.sub(r'\s*\(Audio\)', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s*\[Audio\]', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s*\(HD\)', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s*\[HD\]', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s*\(HQ\)', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s*\[HQ\]', '', title, flags=re.IGNORECASE)

    # Remove remaster/remastered annotations
    title = re.sub(r'\s*\(.*?Remaster.*?\)', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s*\[.*?Remaster.*?\]', '', title, flags=re.IGNORECASE)

    return title.strip()


def extract_artist_title(full_title: str, channel: str) -> tuple[str, str]:
    """Try to extract artist and title from YouTube video title"""
    # Common patterns: "Artist - Title", "Artist — Title", "Artist | Title"
    patterns = [
        r'^(.+?)\s*[-–—]\s*(.+)$',
        r'^(.+?)\s*\|\s*(.+)$',
    ]

    for pattern in patterns:
        match = re.match(pattern, full_title)
        if match:
            artist, title = match.groups()
            return artist.strip(), clean_title(title)

    # Fallback: use channel as artist, full title as title
    # Remove common channel suffixes like "VEVO", "Official"
    artist = re.sub(r'\s*(VEVO|Official|Music)$', '', channel, flags=re.IGNORECASE)
    return artist.strip(), clean_title(full_title)


async def lookup_musicbrainz(artist: str, title: str) -> Optional[dict]:
    """Look up track metadata from MusicBrainz"""
    if not ENABLE_MUSICBRAINZ:
        return None

    try:
        import httpx

        # Search for recording
        async with httpx.AsyncClient(timeout=10) as client:
            # MusicBrainz requires a User-Agent
            headers = {"User-Agent": "MusicGrabber/1.0.0 (https://github.com/yourrepo)"}

            # Search for the recording
            search_url = "https://musicbrainz.org/ws/2/recording/"
            params = {
                "query": f'artist:"{artist}" AND recording:"{title}"',
                "fmt": "json",
                "limit": 1
            }

            response = await client.get(search_url, params=params, headers=headers)

            if response.status_code != 200:
                return None

            data = response.json()

            if not data.get("recordings"):
                return None

            recording = data["recordings"][0]

            # Extract metadata
            metadata = {
                "title": recording.get("title"),
                "artist": recording["artist-credit"][0]["name"] if recording.get("artist-credit") else None,
            }

            # Get release information for album and date
            if recording.get("releases"):
                release = recording["releases"][0]
                metadata["album"] = release.get("title")
                metadata["date"] = release.get("date")

                # Extract year from date
                if metadata.get("date"):
                    year_match = re.match(r'(\d{4})', metadata["date"])
                    if year_match:
                        metadata["year"] = year_match.group(1)

            return metadata

    except Exception:
        # If MusicBrainz lookup fails, just continue without it
        return None


async def fetch_lyrics(artist: str, title: str) -> Optional[str]:
    """Fetch synced lyrics from LRClib API"""
    if not ENABLE_LYRICS:
        return None

    try:
        import httpx

        async with httpx.AsyncClient(timeout=10) as client:
            headers = {"User-Agent": "MusicGrabber/1.1.0 (https://gitlab.com/g33kphr33k/musicgrabber)"}

            # Try the get endpoint first (exact match)
            params = {
                "artist_name": artist,
                "track_name": title
            }

            response = await client.get(
                "https://lrclib.net/api/get",
                params=params,
                headers=headers
            )

            if response.status_code == 200:
                data = response.json()
                # Prefer synced lyrics, fall back to plain
                if data.get("syncedLyrics"):
                    return data["syncedLyrics"]
                elif data.get("plainLyrics"):
                    return data["plainLyrics"]

            # If exact match fails, try search
            search_params = {"q": f"{artist} {title}"}
            search_response = await client.get(
                "https://lrclib.net/api/search",
                params=search_params,
                headers=headers
            )

            if search_response.status_code == 200:
                results = search_response.json()
                if results:
                    # Return first match with synced lyrics, or first with plain
                    for result in results:
                        if result.get("syncedLyrics"):
                            return result["syncedLyrics"]
                    for result in results:
                        if result.get("plainLyrics"):
                            return result["plainLyrics"]

            return None

    except Exception:
        # If lyrics lookup fails, just continue without
        return None


def save_lyrics_file(flac_path: Path, lyrics: str):
    """Save lyrics as .lrc file alongside the FLAC"""
    lrc_path = flac_path.with_suffix(".lrc")
    lrc_path.write_text(lyrics, encoding="utf-8")


def apply_metadata_to_file(file_path: Path, artist: str, title: str, album: str = "Singles", year: str = None):
    """Apply metadata to FLAC file using mutagen"""
    try:
        audio = FLAC(str(file_path))

        # Set basic metadata
        audio["ARTIST"] = artist
        audio["TITLE"] = title
        audio["ALBUM"] = album

        if year:
            audio["DATE"] = year

        audio.save()
    except Exception:
        # If metadata application fails, continue anyway
        pass


def check_duplicate(artist: str, title: str) -> Optional[Path]:
    """Check if a track already exists in the library"""
    try:
        artist_dir = SINGLES_DIR / sanitize_filename(artist)
        if not artist_dir.exists():
            return None

        # Check for exact filename match
        expected_file = artist_dir / f"{sanitize_filename(title)}.flac"
        if expected_file.exists():
            return expected_file

        # Check for similar files (case-insensitive)
        title_lower = sanitize_filename(title).lower()
        for file in artist_dir.glob("*.flac"):
            if file.stem.lower() == title_lower:
                return file

        return None
    except Exception:
        return None


@app.get("/", response_class=HTMLResponse)
async def root():
    """Serve the main UI"""
    return FileResponse("/app/static/index.html")


@app.get("/api/preview/{video_id}")
async def get_preview_url(video_id: str):
    """Get a streamable audio URL for preview playback

    Uses yt-dlp to extract a direct audio stream URL that can be played in the browser.
    """
    try:
        # Get the best audio stream URL (without downloading)
        cmd = [
            "yt-dlp",
            "-f", "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio",
            "-g",  # Get URL only, don't download
            "--no-warnings",
            f"https://www.youtube.com/watch?v={video_id}"
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)

        if result.returncode != 0:
            raise HTTPException(status_code=500, detail="Failed to get preview URL")

        audio_url = result.stdout.strip()

        if not audio_url:
            raise HTTPException(status_code=404, detail="No audio stream found")

        return {"url": audio_url, "video_id": video_id}

    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Preview request timed out")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/search")
async def search(request: SearchRequest):
    """Search YouTube for music"""
    try:
        # Fetch more results than requested so we can filter and re-rank
        fetch_limit = max(request.limit * 3, 30)

        # Use yt-dlp to search YouTube
        cmd = [
            "yt-dlp",
            "--dump-json",
            "--flat-playlist",
            "--no-warnings",
            f"ytsearch{fetch_limit}:{request.query}",
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode != 0:
            raise HTTPException(status_code=500, detail="Search failed")

        results = []
        for line in result.stdout.strip().split('\n'):
            if not line:
                continue
            try:
                data = json.loads(line)
                is_playlist = data.get("_type") == "playlist" or "playlist" in data.get("ie_key", "").lower()
                video_count = data.get("playlist_count") or data.get("n_entries")

                title = data.get("title", "Unknown")
                channel = data.get("channel", data.get("uploader", "Unknown"))

                # Calculate quality score
                quality_score = score_search_result(title, channel)

                results.append({
                    "video_id": data.get("id", ""),
                    "title": title,
                    "channel": channel,
                    "duration": parse_duration(data.get("duration", 0) or 0) if not is_playlist else "",
                    "thumbnail": data.get("thumbnail", f"https://i.ytimg.com/vi/{data.get('id')}/mqdefault.jpg"),
                    "is_playlist": is_playlist,
                    "video_count": video_count,
                    "score": quality_score
                })
            except json.JSONDecodeError:
                continue

        # Sort by score (highest first) and return top results
        results.sort(key=lambda x: x["score"], reverse=True)

        # Convert to SearchResult objects
        final_results = []
        for item in results[:request.limit]:
            final_results.append(SearchResult(
                video_id=item["video_id"],
                title=item["title"],
                channel=item["channel"],
                duration=item["duration"],
                thumbnail=item["thumbnail"],
                is_playlist=item["is_playlist"],
                video_count=item["video_count"]
            ))

        return {"results": final_results}

    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Search timed out")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/download")
async def download(request: DownloadRequest, background_tasks: BackgroundTasks):
    """Queue a download job"""
    job_id = str(uuid.uuid4())[:8]

    # Extract artist/title if not provided
    artist = request.artist
    title = request.title

    if not artist:
        # We'll extract from the full video info during download
        pass

    # Create job record
    conn = sqlite3.connect(DB_PATH)
    if request.download_type == "playlist":
        conn.execute(
            "INSERT INTO jobs (id, video_id, title, status, download_type, playlist_name) VALUES (?, ?, ?, ?, ?, ?)",
            (job_id, request.video_id, title, "queued", "playlist", title)
        )
    else:
        conn.execute(
            "INSERT INTO jobs (id, video_id, title, artist, status, download_type) VALUES (?, ?, ?, ?, ?, ?)",
            (job_id, request.video_id, title, artist or "", "queued", "single")
        )
    conn.commit()
    conn.close()

    # Queue the download
    if request.download_type == "playlist":
        background_tasks.add_task(process_playlist_download, job_id, request.video_id, title)
    else:
        background_tasks.add_task(process_download, job_id, request.video_id)

    return {"job_id": job_id, "status": "queued"}


async def process_playlist_download(job_id: str, playlist_id: str, playlist_name: str):
    """Process a playlist download job"""
    conn = sqlite3.connect(DB_PATH)

    try:
        # Update status to downloading
        conn.execute("UPDATE jobs SET status = ? WHERE id = ?", ("downloading", job_id))
        conn.commit()

        # Get playlist information and extract all video IDs
        info_cmd = [
            "yt-dlp",
            "--dump-json",
            "--flat-playlist",
            "--no-warnings",
            f"https://www.youtube.com/playlist?list={playlist_id}"
        ]

        info_result = subprocess.run(info_cmd, capture_output=True, text=True, timeout=60)
        if info_result.returncode != 0:
            raise Exception("Failed to get playlist info")

        # Parse all videos from playlist
        videos = []
        for line in info_result.stdout.strip().split('\n'):
            if not line:
                continue
            try:
                data = json.loads(line)
                if data.get("id"):
                    videos.append({
                        "id": data["id"],
                        "title": data.get("title", "Unknown"),
                        "channel": data.get("channel", data.get("uploader", "Unknown"))
                    })
            except json.JSONDecodeError:
                continue

        if not videos:
            raise Exception("No videos found in playlist")

        # Update total tracks
        conn.execute(
            "UPDATE jobs SET total_tracks = ? WHERE id = ?",
            (len(videos), job_id)
        )
        conn.commit()

        # Download each video in the playlist
        downloaded_files = []
        for idx, video in enumerate(videos, 1):
            try:
                video_id = video["id"]

                # Get detailed video info
                detail_cmd = [
                    "yt-dlp",
                    "--dump-json",
                    "--no-warnings",
                    f"https://www.youtube.com/watch?v={video_id}"
                ]

                detail_result = subprocess.run(detail_cmd, capture_output=True, text=True, timeout=30)
                if detail_result.returncode != 0:
                    continue

                info = json.loads(detail_result.stdout)
                full_title = info.get("title", "Unknown")
                channel = info.get("channel", info.get("uploader", "Unknown"))
                artist, title = extract_artist_title(full_title, channel)

                # Check for duplicates
                existing_file = check_duplicate(artist, title)
                if existing_file:
                    # Skip download, but count as completed
                    conn.execute(
                        "UPDATE jobs SET completed_tracks = ? WHERE id = ?",
                        (idx, job_id)
                    )
                    conn.commit()
                    continue

                # Create artist directory under Singles
                artist_dir = SINGLES_DIR / sanitize_filename(artist)
                artist_dir.mkdir(parents=True, exist_ok=True)

                # Download with best audio quality, convert to FLAC
                output_template = str(artist_dir / f"{sanitize_filename(title)}.%(ext)s")

                download_cmd = [
                    "yt-dlp",
                    "-x",
                    "--audio-format", "flac",
                    "--audio-quality", "0",
                    "--embed-metadata",
                    "--embed-thumbnail",
                    "--convert-thumbnails", "jpg",
                    "--ppa", "ffmpeg:-c:v mjpeg -vf crop=\"'if(gt(ih,iw),iw,ih)':'if(gt(iw,ih),ih,iw)'\"",
                    "--add-metadata",
                    "--parse-metadata", f"%(artist,channel,uploader)s:%(meta_artist)s",
                    "--parse-metadata", f"%(track,title)s:%(meta_title)s",
                    "-o", output_template,
                    "--no-warnings",
                    f"https://www.youtube.com/watch?v={video_id}"
                ]

                download_result = subprocess.run(
                    download_cmd,
                    capture_output=True,
                    text=True,
                    timeout=300
                )

                if download_result.returncode == 0:
                    # Track downloaded file
                    flac_file = artist_dir / f"{sanitize_filename(title)}.flac"
                    if flac_file.exists():
                        # Try to enrich metadata with MusicBrainz
                        mb_metadata = await lookup_musicbrainz(artist, title)
                        if mb_metadata:
                            # Use MusicBrainz metadata
                            apply_metadata_to_file(
                                flac_file,
                                mb_metadata.get("artist", artist),
                                mb_metadata.get("title", title),
                                mb_metadata.get("album", "Singles"),
                                mb_metadata.get("year")
                            )
                        else:
                            # Use cleaned YouTube metadata
                            apply_metadata_to_file(flac_file, artist, title, "Singles")

                        downloaded_files.append(str(flac_file.relative_to(SINGLES_DIR)))

                    # Update progress
                    conn.execute(
                        "UPDATE jobs SET completed_tracks = ? WHERE id = ?",
                        (idx, job_id)
                    )
                    conn.commit()

            except Exception:
                # Continue with next track even if one fails
                continue

        # Generate M3U playlist file
        if downloaded_files:
            m3u_path = SINGLES_DIR / f"{sanitize_filename(playlist_name)}.m3u"
            with open(m3u_path, 'w', encoding='utf-8') as f:
                f.write("#EXTM3U\n")
                for file_path in downloaded_files:
                    f.write(f"{file_path}\n")

            conn.execute(
                "UPDATE jobs SET m3u_path = ? WHERE id = ?",
                (str(m3u_path.relative_to(MUSIC_DIR)), job_id)
            )
            conn.commit()

        # Trigger Navidrome rescan if configured
        if NAVIDROME_URL and NAVIDROME_USER and NAVIDROME_PASS:
            await trigger_navidrome_scan()

        # Update job status
        conn.execute(
            "UPDATE jobs SET status = ?, completed_at = ? WHERE id = ?",
            ("completed", datetime.now().isoformat(), job_id)
        )
        conn.commit()

    except Exception as e:
        conn.execute(
            "UPDATE jobs SET status = ?, error = ?, completed_at = ? WHERE id = ?",
            ("failed", str(e), datetime.now().isoformat(), job_id)
        )
        conn.commit()

    finally:
        conn.close()


async def process_download(job_id: str, video_id: str):
    """Process a download job"""
    conn = sqlite3.connect(DB_PATH)
    
    try:
        # Update status to downloading
        conn.execute("UPDATE jobs SET status = ? WHERE id = ?", ("downloading", job_id))
        conn.commit()
        
        # First, get video info for proper metadata
        info_cmd = [
            "yt-dlp",
            "--dump-json",
            "--no-warnings",
            f"https://www.youtube.com/watch?v={video_id}"
        ]
        
        info_result = subprocess.run(info_cmd, capture_output=True, text=True, timeout=30)
        if info_result.returncode != 0:
            raise Exception("Failed to get video info")
        
        info = json.loads(info_result.stdout)
        
        # Extract artist and title
        full_title = info.get("title", "Unknown")
        channel = info.get("channel", info.get("uploader", "Unknown"))
        artist, title = extract_artist_title(full_title, channel)

        # Update job with extracted info
        conn.execute(
            "UPDATE jobs SET title = ?, artist = ? WHERE id = ?",
            (title, artist, job_id)
        )
        conn.commit()

        # Check for duplicates
        existing_file = check_duplicate(artist, title)
        if existing_file:
            # Mark as completed without downloading
            conn.execute(
                "UPDATE jobs SET status = ?, completed_at = ?, error = ? WHERE id = ?",
                ("completed", datetime.now().isoformat(), f"Already exists: {existing_file.name}", job_id)
            )
            conn.commit()
            return

        # Create artist directory under Singles
        artist_dir = SINGLES_DIR / sanitize_filename(artist)
        artist_dir.mkdir(parents=True, exist_ok=True)
        
        # Download with best audio quality, convert to FLAC
        output_template = str(artist_dir / f"{sanitize_filename(title)}.%(ext)s")
        
        download_cmd = [
            "yt-dlp",
            "-x",  # Extract audio
            "--audio-format", "flac",
            "--audio-quality", "0",  # Best quality
            "--embed-metadata",
            "--embed-thumbnail",
            "--convert-thumbnails", "jpg",  # For embedding
            "--ppa", "ffmpeg:-c:v mjpeg -vf crop=\"'if(gt(ih,iw),iw,ih)':'if(gt(iw,ih),ih,iw)'\"",  # Square thumbnail
            "--add-metadata",
            "--parse-metadata", f"%(artist,channel,uploader)s:%(meta_artist)s",
            "--parse-metadata", f"%(track,title)s:%(meta_title)s",
            "-o", output_template,
            "--no-warnings",
            f"https://www.youtube.com/watch?v={video_id}"
        ]
        
        download_result = subprocess.run(
            download_cmd, 
            capture_output=True, 
            text=True, 
            timeout=300
        )
        
        if download_result.returncode != 0:
            raise Exception(f"Download failed: {download_result.stderr}")

        # Apply enhanced metadata to downloaded file
        flac_file = artist_dir / f"{sanitize_filename(title)}.flac"
        if flac_file.exists():
            # Try to enrich metadata with MusicBrainz
            mb_metadata = await lookup_musicbrainz(artist, title)
            if mb_metadata:
                # Use MusicBrainz metadata
                apply_metadata_to_file(
                    flac_file,
                    mb_metadata.get("artist", artist),
                    mb_metadata.get("title", title),
                    mb_metadata.get("album", "Singles"),
                    mb_metadata.get("year")
                )
            else:
                # Use cleaned YouTube metadata
                apply_metadata_to_file(flac_file, artist, title, "Singles")

            # Fetch and save lyrics
            lyrics = await fetch_lyrics(artist, title)
            if lyrics:
                save_lyrics_file(flac_file, lyrics)

        # Trigger Navidrome rescan if configured
        if NAVIDROME_URL and NAVIDROME_USER and NAVIDROME_PASS:
            await trigger_navidrome_scan()

        # Update job status
        conn.execute(
            "UPDATE jobs SET status = ?, completed_at = ? WHERE id = ?",
            ("completed", datetime.now().isoformat(), job_id)
        )
        conn.commit()
        
    except Exception as e:
        conn.execute(
            "UPDATE jobs SET status = ?, error = ?, completed_at = ? WHERE id = ?",
            ("failed", str(e), datetime.now().isoformat(), job_id)
        )
        conn.commit()
    
    finally:
        conn.close()


async def trigger_navidrome_scan():
    """Trigger a Navidrome library scan via API"""
    try:
        import hashlib
        import httpx

        # Navidrome uses subsonic API
        salt = uuid.uuid4().hex[:8]
        token = hashlib.md5(f"{NAVIDROME_PASS}{salt}".encode()).hexdigest()

        params = {
            "u": NAVIDROME_USER,
            "t": token,
            "s": salt,
            "v": "1.16.1",
            "c": "music-grabber",
            "f": "json"
        }

        async with httpx.AsyncClient() as client:
            await client.get(
                f"{NAVIDROME_URL}/rest/startScan",
                params=params,
                timeout=10
            )
    except Exception:
        pass  # Non-critical, scan will happen on schedule anyway


async def create_bulk_playlist(bulk_import_id: str, playlist_name: str, expected_count: int):
    """Create an M3U playlist from a bulk import after all downloads complete

    Waits for all jobs with the matching playlist_name to complete, then generates the M3U file.
    """
    # Wait for all downloads to complete (with timeout)
    max_wait_time = 3600  # 1 hour max
    check_interval = 10  # Check every 10 seconds
    waited = 0

    while waited < max_wait_time:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.execute(
            "SELECT COUNT(*) as total, SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed FROM jobs WHERE playlist_name = ?",
            (bulk_import_id,)
        )
        row = cursor.fetchone()
        conn.close()

        total, completed = row
        completed = completed or 0

        # All downloads complete
        if completed >= expected_count or total == completed:
            break

        await asyncio.sleep(check_interval)
        waited += check_interval

    # Gather all successfully downloaded files
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(
        "SELECT artist, title FROM jobs WHERE playlist_name = ? AND status = 'completed' AND error IS NULL ORDER BY created_at",
        (bulk_import_id,)
    )
    jobs = [dict(row) for row in cursor.fetchall()]
    conn.close()

    if not jobs:
        return  # No successful downloads

    # Build M3U playlist
    playlist_files = []
    for job in jobs:
        artist = job.get("artist", "Unknown")
        title = job.get("title", "Unknown")

        # Construct expected file path
        artist_dir = SINGLES_DIR / sanitize_filename(artist)
        flac_file = artist_dir / f"{sanitize_filename(title)}.flac"

        if flac_file.exists():
            # Store relative path from Singles directory
            rel_path = flac_file.relative_to(SINGLES_DIR)
            playlist_files.append(str(rel_path))

    if playlist_files:
        # Create M3U file
        m3u_path = SINGLES_DIR / f"{sanitize_filename(playlist_name)}.m3u"
        with open(m3u_path, 'w', encoding='utf-8') as f:
            f.write("#EXTM3U\n")
            for file_path in playlist_files:
                f.write(f"{file_path}\n")


@app.get("/api/jobs")
async def get_jobs(limit: int = 20):
    """Get recent jobs"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(
        "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?",
        (limit,)
    )
    jobs = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return {"jobs": jobs}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    """Get a specific job"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Job not found")

    return dict(row)


@app.post("/api/jobs/{job_id}/retry")
async def retry_job(job_id: str, background_tasks: BackgroundTasks):
    """Retry a failed job"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
    row = cursor.fetchone()

    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Job not found")

    job = dict(row)

    # Only allow retrying failed jobs
    if job["status"] != "failed":
        conn.close()
        raise HTTPException(status_code=400, detail="Only failed jobs can be retried")

    # Reset job status
    conn.execute(
        "UPDATE jobs SET status = ?, error = NULL, completed_at = NULL WHERE id = ?",
        ("queued", job_id)
    )
    conn.commit()
    conn.close()

    # Re-queue the job
    if job["download_type"] == "playlist":
        background_tasks.add_task(process_playlist_download, job_id, job["video_id"], job["playlist_name"])
    else:
        background_tasks.add_task(process_download, job_id, job["video_id"])

    return {"job_id": job_id, "status": "queued"}


@app.delete("/api/jobs/cleanup")
async def cleanup_jobs(status: Optional[str] = None):
    """Delete completed or failed jobs

    Args:
        status: Optional filter - 'completed', 'failed', or None for both
    """
    conn = sqlite3.connect(DB_PATH)

    if status == "completed":
        cursor = conn.execute("DELETE FROM jobs WHERE status = 'completed'")
    elif status == "failed":
        cursor = conn.execute("DELETE FROM jobs WHERE status = 'failed'")
    else:
        cursor = conn.execute("DELETE FROM jobs WHERE status IN ('completed', 'failed')")

    deleted_count = cursor.rowcount
    conn.commit()
    conn.close()

    return {"deleted": deleted_count}


def clean_bulk_import_line(line: str) -> str:
    """Clean a line from bulk import text

    Removes common prefixes like:
    - Numbers: "1.", "1)", "01."
    - Bullets: "•", "-", "*"
    - Comments: "#"
    - Extra whitespace and tabs
    """
    # Strip whitespace
    line = line.strip()

    # Skip comments
    if line.startswith('#'):
        return ""

    # Remove common list prefixes: "1. ", "1) ", "01. ", etc.
    line = re.sub(r'^\d+[\.\)]\s*', '', line)

    # Remove bullet points at start
    line = re.sub(r'^[•\-\*]\s*', '', line)

    # Remove common music symbols
    line = re.sub(r'[♫♪🎵🎶]', '', line)

    # Normalize multiple spaces/tabs to single space
    line = re.sub(r'\s+', ' ', line)

    return line.strip()


@app.post("/api/bulk-import")
async def bulk_import(request: BulkImportRequest, background_tasks: BackgroundTasks):
    """Import a list of songs and automatically search/download them

    Accepts text in format:
    Artist - Song Title
    Artist – Song Title (with em dash)

    Handles common junk like numbered lists, bullets, comments, etc.

    Can optionally create an M3U playlist from the imported songs.

    Returns summary of queued jobs
    """
    lines = request.songs.strip().split('\n')
    queued_jobs = []
    failed_lines = []

    # Generate a bulk import ID if creating a playlist
    bulk_import_id = str(uuid.uuid4())[:8] if request.create_playlist else None

    for line_num, line in enumerate(lines, 1):
        original_line = line
        line = clean_bulk_import_line(line)

        # Skip empty or comment lines
        if not line:
            continue

        # Skip lines that are too long (likely not a song)
        if len(line) > 200:
            failed_lines.append({"line": line_num, "text": original_line[:100] + "...", "reason": "Line too long"})
            continue

        # Try to parse "Artist - Song" format (supporting different dash types)
        match = re.match(r'^(.+?)\s*[-–—]\s*(.+)$', line)
        if not match:
            failed_lines.append({"line": line_num, "text": original_line, "reason": "Invalid format (need: Artist - Song)"})
            continue

        artist, song = match.groups()
        artist = artist.strip()
        song = song.strip()

        # Validate artist and song aren't empty
        if not artist or not song:
            failed_lines.append({"line": line_num, "text": original_line, "reason": "Empty artist or song"})
            continue

        # Validate reasonable lengths
        if len(artist) < 1 or len(song) < 1:
            failed_lines.append({"line": line_num, "text": original_line, "reason": "Artist or song too short"})
            continue

        # Search for the song - fetch multiple results to find best match
        try:
            search_query = f"{artist} {song}"
            cmd = [
                "yt-dlp",
                "--dump-json",
                "--flat-playlist",
                "--no-warnings",
                f"ytsearch10:{search_query}",  # Fetch 10 results to find best match
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

            if result.returncode != 0:
                failed_lines.append({"line": line_num, "text": original_line, "reason": "Search failed"})
                continue

            # Parse all results and score them
            if not result.stdout.strip():
                failed_lines.append({"line": line_num, "text": original_line, "reason": "No results found"})
                continue

            search_results = []
            for search_line in result.stdout.strip().split('\n'):
                if not search_line:
                    continue
                try:
                    data = json.loads(search_line)
                    title = data.get("title", "")
                    channel = data.get("channel", data.get("uploader", ""))
                    video_id = data.get("id")

                    if video_id:
                        score = score_search_result(title, channel)
                        search_results.append({
                            "video_id": video_id,
                            "title": title,
                            "channel": channel,
                            "score": score
                        })
                except json.JSONDecodeError:
                    continue

            if not search_results:
                failed_lines.append({"line": line_num, "text": original_line, "reason": "No valid results"})
                continue

            # Sort by score and pick the best match
            search_results.sort(key=lambda x: x["score"], reverse=True)
            best_match = search_results[0]
            video_id = best_match["video_id"]

            # Create job
            job_id = str(uuid.uuid4())[:8]
            conn = sqlite3.connect(DB_PATH)

            # Store playlist info if creating a playlist
            if request.create_playlist:
                conn.execute(
                    "INSERT INTO jobs (id, video_id, title, artist, status, download_type, playlist_name) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (job_id, video_id, song, artist, "queued", "single", bulk_import_id)
                )
            else:
                conn.execute(
                    "INSERT INTO jobs (id, video_id, title, artist, status, download_type) VALUES (?, ?, ?, ?, ?, ?)",
                    (job_id, video_id, song, artist, "queued", "single")
                )
            conn.commit()
            conn.close()

            # Queue download
            background_tasks.add_task(process_download, job_id, video_id)

            queued_jobs.append({
                "job_id": job_id,
                "artist": artist,
                "song": song,
                "video_id": video_id
            })

        except subprocess.TimeoutExpired:
            failed_lines.append({"line": line_num, "text": original_line, "reason": "Search timeout"})
        except Exception as e:
            failed_lines.append({"line": line_num, "text": original_line, "reason": str(e)})

    # Schedule playlist creation task if requested
    if request.create_playlist and queued_jobs:
        playlist_name = request.playlist_name or f"Playlist {bulk_import_id}"
        background_tasks.add_task(
            create_bulk_playlist,
            bulk_import_id,
            playlist_name,
            len(queued_jobs)
        )

    return {
        "queued": len(queued_jobs),
        "failed": len(failed_lines),
        "jobs": queued_jobs,
        "failures": failed_lines,
        "playlist_id": bulk_import_id if request.create_playlist else None
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
