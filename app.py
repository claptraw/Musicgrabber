#!/usr/bin/env python3
"""
Music Grabber - A self-hosted music acquisition service
Searches YouTube, downloads best quality audio with optional conversion to FLAC, drops into Navidrome library
"""

import hashlib
import json
import os
import re
import subprocess
import sqlite3
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
from mutagen.flac import FLAC
import httpx

# Global state for bulk import worker
_bulk_import_worker_running = False
_bulk_import_lock = threading.Lock()

app = FastAPI(title="Music Grabber", version="1.5.1")

# Configuration from environment
MUSIC_DIR = Path(os.getenv("MUSIC_DIR", "/music"))
SINGLES_DIR = MUSIC_DIR / "Singles"
DB_PATH = Path(os.getenv("DB_PATH", "/data/music_grabber.db"))
NAVIDROME_URL = os.getenv("NAVIDROME_URL", "")
NAVIDROME_USER = os.getenv("NAVIDROME_USER", "")
NAVIDROME_PASS = os.getenv("NAVIDROME_PASS", "")
JELLYFIN_URL = os.getenv("JELLYFIN_URL", "")
JELLYFIN_API_KEY = os.getenv("JELLYFIN_API_KEY", "")
ENABLE_MUSICBRAINZ = os.getenv("ENABLE_MUSICBRAINZ", "true").lower() == "true"
ENABLE_LYRICS = os.getenv("ENABLE_LYRICS", "true").lower() == "true"
DEFAULT_CONVERT_TO_FLAC = os.getenv("DEFAULT_CONVERT_TO_FLAC", "true").lower() == "true"

# Soulseek/slskd configuration (optional)
SLSKD_URL = os.getenv("SLSKD_URL", "")  # e.g., http://slskd:5030
SLSKD_USER = os.getenv("SLSKD_USER", "")
SLSKD_PASS = os.getenv("SLSKD_PASS", "")
SLSKD_DOWNLOADS_PATH = os.getenv("SLSKD_DOWNLOADS_PATH", "")  # Path where slskd downloads are accessible
SLSKD_REQUIRE_FREE_SLOT = os.getenv("SLSKD_REQUIRE_FREE_SLOT", "true").lower() == "true"
SLSKD_MAX_RETRIES = int(os.getenv("SLSKD_MAX_RETRIES", "5"))

# slskd auth token cache
_slskd_token = None
_slskd_token_expires = 0

# Ensure directories exist
SINGLES_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# Initialise DB
def get_db() -> sqlite3.Connection:
    """Create a SQLite connection with basic concurrency settings"""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    """Initialize SQLite database for job tracking"""
    conn = get_db()
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

    # Bulk imports table - tracks the overall import job
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bulk_imports (
            id TEXT PRIMARY KEY,
            status TEXT DEFAULT 'pending',
            total_tracks INTEGER DEFAULT 0,
            searched INTEGER DEFAULT 0,
            queued INTEGER DEFAULT 0,
            failed INTEGER DEFAULT 0,
            skipped INTEGER DEFAULT 0,
            create_playlist INTEGER DEFAULT 0,
            playlist_name TEXT,
            convert_to_flac INTEGER DEFAULT 1,
            rate_limited_until TIMESTAMP,
            error TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP
        )
    """)

    # Individual tracks within a bulk import
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bulk_import_tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            import_id TEXT NOT NULL,
            line_num INTEGER,
            artist TEXT,
            song TEXT,
            status TEXT DEFAULT 'pending',
            job_id TEXT,
            video_id TEXT,
            error TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (import_id) REFERENCES bulk_imports(id)
        )
    """)

    # Index for faster lookups
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bulk_import_tracks_import_id ON bulk_import_tracks(import_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bulk_import_tracks_status ON bulk_import_tracks(status)")

    conn.commit()
    conn.close()

init_db()

# Classes
class SearchRequest(BaseModel):
    query: str
    limit: int = 15

class DownloadRequest(BaseModel):
    video_id: str
    title: str
    artist: Optional[str] = None
    download_type: str = "single"  # "single" or "playlist"
    convert_to_flac: bool = DEFAULT_CONVERT_TO_FLAC  # Whether to convert to FLAC or keep original format
    # Soulseek-specific fields
    source: str = "youtube"  # "youtube" or "soulseek"
    slskd_username: Optional[str] = None
    slskd_filename: Optional[str] = None

class BulkImportRequest(BaseModel):
    songs: str  # Multi-line text with "Artist - Song" format
    create_playlist: bool = False
    playlist_name: Optional[str] = None
    convert_to_flac: bool = DEFAULT_CONVERT_TO_FLAC  # Whether to convert to FLAC or keep original format

class SpotifyPlaylistRequest(BaseModel):
    url: str  # Spotify playlist URL

class AsyncBulkImportRequest(BaseModel):
    songs: str  # Multi-line text with "Artist - Song" format
    create_playlist: bool = False
    playlist_name: Optional[str] = None
    convert_to_flac: bool = DEFAULT_CONVERT_TO_FLAC

class SearchResult(BaseModel):
    video_id: str
    title: str
    channel: str
    duration: str
    thumbnail: str
    is_playlist: bool = False
    video_count: Optional[int] = None
    # New fields for multi-source support
    source: str = "youtube"  # "youtube" or "soulseek"
    quality: Optional[str] = None  # e.g., "FLAC", "MP3 320", None for YouTube
    quality_score: int = 40  # For sorting (higher = better)
    slskd_username: Optional[str] = None
    slskd_filename: Optional[str] = None

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

    # Penalties for fan uploads or unofficial - no cell phone video, thanks
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


def lookup_musicbrainz(artist: str, title: str) -> Optional[dict]:
    """Look up track metadata from MusicBrainz"""
    if not ENABLE_MUSICBRAINZ:
        return None

    try:
        # Search for recording
        headers = {"User-Agent": "MusicGrabber/1.5.1 (https://github.com/yourrepo)"}

        search_url = "https://musicbrainz.org/ws/2/recording/"
        params = {
            "query": f'artist:"{artist}" AND recording:"{title}"',
            "fmt": "json",
            "limit": 1
        }

        with httpx.Client(timeout=10) as client:
            response = client.get(search_url, params=params, headers=headers)

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

def fetch_lyrics(artist: str, title: str) -> Optional[str]:
    """Fetch synced lyrics from LRClib API"""
    if not ENABLE_LYRICS:
        return None

    try:
        headers = {"User-Agent": "MusicGrabber/1.1.0 (https://gitlab.com/g33kphr33k/musicgrabber)"}

        with httpx.Client(timeout=10) as client:
            # Try the get endpoint first (exact match)
            params = {
                "artist_name": artist,
                "track_name": title
            }

            response = client.get(
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
            search_response = client.get(
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

    except Exception as e:
        # If lyrics lookup fails, log and continue without
        print(f"Lyrics lookup failed for {artist} - {title}: {e}")
        return None

def save_lyrics_file(flac_path: Path, lyrics: str):
    """Save lyrics as .lrc file alongside the FLAC"""
    lrc_path = flac_path.with_suffix(".lrc")
    lrc_path.write_text(lyrics, encoding="utf-8")


# =============================================================================
# Soulseek/slskd Integration
# =============================================================================

def slskd_enabled() -> bool:
    """Check if slskd integration is configured"""
    return bool(SLSKD_URL and SLSKD_USER and SLSKD_PASS)


def get_slskd_token() -> Optional[str]:
    """Get a valid slskd auth token, refreshing if needed"""
    global _slskd_token, _slskd_token_expires

    if not slskd_enabled():
        return None

    # Return cached token if still valid (with 60s buffer)
    if _slskd_token and time.time() < _slskd_token_expires - 60:
        return _slskd_token

    try:
        with httpx.Client(timeout=10) as client:
            response = client.post(
                f"{SLSKD_URL}/api/v0/session",
                json={"username": SLSKD_USER, "password": SLSKD_PASS}
            )
            if response.status_code == 200:
                data = response.json()
                _slskd_token = data["token"]
                _slskd_token_expires = data["expires"]
                return _slskd_token
    except Exception as e:
        print(f"slskd auth failed: {e}")

    return None


def parse_slskd_quality(file_info: dict) -> tuple[str, int]:
    """
    Parse quality info from slskd file response.
    Returns (quality_label, sort_score) where higher score = better quality.
    """
    filename = file_info.get("filename", "").lower()
    bit_depth = file_info.get("bitDepth", 0)
    sample_rate = file_info.get("sampleRate", 0)
    bit_rate = file_info.get("bitRate", 0)

    # Determine format from filename extension
    if filename.endswith(".flac"):
        if bit_depth >= 24:
            return (f"FLAC {bit_depth}bit/{sample_rate//1000}kHz", 150)
        return ("FLAC", 100)
    elif filename.endswith(".wav"):
        return ("WAV", 95)
    elif filename.endswith(".mp3"):
        if bit_rate >= 320:
            return ("MP3 320", 80)
        elif bit_rate >= 256:
            return ("MP3 256", 70)
        elif bit_rate >= 192:
            return ("MP3 192", 60)
        else:
            return (f"MP3 {bit_rate}", 50)
    elif filename.endswith(".m4a") or filename.endswith(".aac"):
        if bit_rate >= 256:
            return ("AAC 256", 75)
        return (f"AAC {bit_rate}", 65)
    elif filename.endswith(".ogg") or filename.endswith(".opus"):
        return ("OGG/Opus", 70)
    else:
        return ("Unknown", 30)


def normalize_slskd_path(path: str) -> str:
    """Normalize slskd paths for matching"""
    return path.replace("\\", "/").strip()


def get_slskd_local_path(download_info: dict) -> Optional[str]:
    """Try to pull a local file path from slskd download info"""
    for key in ("localPath", "localFilename", "downloadedFilePath", "downloadPath", "path", "fullPath"):
        value = download_info.get(key)
        if value:
            return value
    return None


def should_retry_slskd_error(error_message: str) -> bool:
    """Decide whether to retry based on slskd error text"""
    msg = error_message.lower()
    retry_markers = [
        "aborted",
        "rejected",
        "cancelled",
        "failed",
        "timed out",
        "timeout",
        "queued",
    ]
    return any(marker in msg for marker in retry_markers)


def extract_track_info_from_path(filepath: str) -> tuple[str, str]:
    """
    Extract artist and title from a Soulseek file path.
    Tries common patterns like 'Artist/Album/## - Title.ext'
    """
    # Get just the filename
    filename = filepath.split("\\")[-1]
    # Remove extension
    name = re.sub(r'\.[^.]+$', '', filename)
    # Remove track number prefix like "01 - " or "01. "
    name = re.sub(r'^\d+[\s.\-]+', '', name)

    # Try to extract artist from path
    parts = filepath.replace("\\", "/").split("/")
    artist = "Unknown"

    # Look for artist in path (usually 2-3 levels up from file)
    for i, part in enumerate(parts):
        # Skip common folder names
        if part.lower() in ["music", "main", "albums", "singles", "anthologies", "instrumental", "@@*"]:
            continue
        if part.startswith("@@"):
            continue
        if re.match(r'^\[\d{4}\]', part):  # Album folder like [1976] Arrival
            continue
        if re.match(r'^cd\d*$', part.lower()):  # CD1, CD2, etc.
            continue
        # First real folder name is likely the artist
        if i > 0 and not part.startswith("["):
            artist = part
            break

    return artist, name


def search_slskd(query: str, timeout_secs: int = 12) -> list[dict]:
    """
    Search slskd and return normalized results.
    Returns list of dicts with: id, title, artist, quality, score, source, slskd_* fields
    """
    token = get_slskd_token()
    if not token:
        return []

    results = []

    try:
        headers = {"Authorization": f"Bearer {token}"}

        with httpx.Client(timeout=30) as client:
            # Start search
            search_response = client.post(
                f"{SLSKD_URL}/api/v0/searches",
                headers=headers,
                json={"searchText": query}
            )

            if search_response.status_code != 200:
                print(f"slskd search failed: {search_response.status_code}")
                return []

            search_data = search_response.json()
            search_id = search_data["id"]
            print(f"slskd: Search started, ID: {search_id}")

            # Poll for results - wait for completion or timeout
            # Don't break early on file count as responses may not be ready
            start_time = time.time()
            last_file_count = 0
            final_status = None
            while time.time() - start_time < timeout_secs:
                time.sleep(1)

                status_response = client.get(
                    f"{SLSKD_URL}/api/v0/searches/{search_id}",
                    headers=headers
                )

                if status_response.status_code == 200:
                    final_status = status_response.json()
                    file_count = final_status.get("fileCount", 0)
                    if file_count != last_file_count:
                        print(f"slskd: Polling... {file_count} files, {final_status.get('responseCount', 0)} responses")
                        last_file_count = file_count
                    if final_status.get("isComplete"):
                        print(f"slskd: Search complete. {file_count} files total")
                        break

            if final_status:
                print(f"slskd: Final status - {final_status.get('fileCount', 0)} files, {final_status.get('responseCount', 0)} responses")

            # Small delay to allow responses to be fully indexed
            time.sleep(1)

            # Get responses with a short retry window in case indexing lags
            responses = []
            responses_deadline = time.time() + min(5, timeout_secs)
            while time.time() < responses_deadline:
                responses_response = client.get(
                    f"{SLSKD_URL}/api/v0/searches/{search_id}/responses",
                    headers=headers
                )
                if responses_response.status_code == 200:
                    responses = responses_response.json()
                    if responses:
                        break
                time.sleep(0.5)

            print(f"slskd: Got {len(responses)} user responses")

            # Process results - pick best file from each user
            seen_tracks = set()
            skipped_locked = 0
            skipped_quality = 0
            skipped_no_slot = 0

            for response in responses:
                username = response.get("username", "")
                has_free_slot = response.get("hasFreeUploadSlot", False)
                upload_speed = response.get("uploadSpeed", 0)

                if SLSKD_REQUIRE_FREE_SLOT and not has_free_slot:
                    skipped_no_slot += 1
                    continue

                files = response.get("files") or response.get("fileInfos") or response.get("fileInfo") or []
                for file_info in files:
                    if file_info.get("isLocked", False):
                        skipped_locked += 1
                        continue

                    filepath = file_info.get("filename", "")
                    quality_label, quality_score = parse_slskd_quality(file_info)

                    # Skip low quality
                    if quality_score < 50:
                        skipped_quality += 1
                        continue

                    artist, title = extract_track_info_from_path(filepath)

                    # Dedupe by artist+title+quality
                    track_key = f"{artist.lower()}|{title.lower()}|{quality_label}"
                    if track_key in seen_tracks:
                        continue
                    seen_tracks.add(track_key)

                    # Boost score for free slots and fast uploaders
                    adjusted_score = quality_score
                    if has_free_slot:
                        adjusted_score += 10
                    if upload_speed > 1000000:  # > 1MB/s
                        adjusted_score += 5

                    results.append({
                        "id": f"slskd_{uuid.uuid4().hex[:8]}",
                        "title": title,
                        "artist": artist,
                        "channel": username,  # Show username as "channel"
                        "quality": quality_label,
                        "quality_score": adjusted_score,
                        "source": "soulseek",
                        "duration": str(file_info.get("length", 0)),
                        "size": file_info.get("size", 0),
                        "slskd_username": username,
                        "slskd_filename": filepath,
                    })

            print(
                "slskd: Skipped "
                f"{skipped_locked} locked, {skipped_quality} low quality, "
                f"{skipped_no_slot} no free slot, kept {len(results)}"
            )

            # Clean up search
            try:
                client.delete(f"{SLSKD_URL}/api/v0/searches/{search_id}", headers=headers)
            except Exception:
                pass

    except Exception as e:
        print(f"slskd search error: {e}")

    # Sort by quality score (descending)
    results.sort(key=lambda x: x["quality_score"], reverse=True)

    return results[:20]  # Return top 20


def download_from_slskd(username: str, filename: str, dest_dir: Path, timeout_secs: int = 600) -> Optional[Path]:
    """
    Download a file from Soulseek via slskd.
    Returns the path to the downloaded file, or None on failure.

    Uses SLSKD_DOWNLOADS_PATH if set; otherwise falls back to common download locations.
    slskd typically organises downloads as: {downloads_path}/{username}/{filename}
    """
    token = get_slskd_token()
    if not token:
        raise Exception("slskd authentication failed")

    headers = {"Authorization": f"Bearer {token}"}

    # Extract just the filename from the full path
    target_norm = normalize_slskd_path(filename)
    source_filename = Path(target_norm).name

    slskd_download_dirs = []
    if SLSKD_DOWNLOADS_PATH:
        slskd_download_dirs.append(Path(SLSKD_DOWNLOADS_PATH))
    slskd_download_dirs.extend([
        Path("/slskd/downloads"),
        Path("/app/downloads"),
        Path("/downloads"),
    ])
    seen_dirs = set()
    slskd_download_dirs = [
        d for d in slskd_download_dirs
        if not (str(d) in seen_dirs or seen_dirs.add(str(d)))
    ]

    try:
        with httpx.Client(timeout=30) as client:
            # Enqueue the download
            enqueue_response = client.post(
                f"{SLSKD_URL}/api/v0/transfers/downloads/{username}",
                headers=headers,
                json=[{"filename": filename}]
            )

            if enqueue_response.status_code not in [200, 201]:
                raise Exception(f"Failed to enqueue download: {enqueue_response.status_code}")

            print(f"slskd: Enqueued download of '{source_filename}' from {username}")

            # Poll for download completion
            start_time = time.time()
            download_complete = False
            downloaded_path = None
            abort_count = 0
            max_abort_requeues = 3  # Re-queue up to 3 times on abort before giving up
            last_state = ""

            while time.time() - start_time < timeout_secs:
                time.sleep(5)

                # Get download status for this user
                status_response = client.get(
                    f"{SLSKD_URL}/api/v0/transfers/downloads/{username}",
                    headers=headers
                )

                if status_response.status_code != 200:
                    continue

                downloads_data = status_response.json()

                # slskd returns { "directories": [...], "files": [...] } structure
                # Each directory has "files" array with the actual transfer info
                files_to_check = []

                if isinstance(downloads_data, dict):
                    # New API format: { directories: [...] }
                    for directory in downloads_data.get("directories", []):
                        files_to_check.extend(directory.get("files", []))
                elif isinstance(downloads_data, list):
                    # Old API format: direct list of files
                    files_to_check = downloads_data

                # Find our file in the downloads
                file_found = False
                for dl in files_to_check:
                    dl_filename = dl.get("filename", "")
                    dl_norm = normalize_slskd_path(dl_filename)
                    dl_base = Path(dl_norm).name
                    if dl_norm == target_norm or dl_base == source_filename or dl_norm.endswith(f"/{source_filename}"):
                        file_found = True
                        state = dl.get("state", "")
                        progress = dl.get("percentComplete", 0)

                        # Only log state changes to reduce noise
                        if state != last_state:
                            print(f"slskd: Download state: {state} ({progress}%)")
                            last_state = state

                        state_lower = state.lower()

                        # Terminal failure states - these won't recover
                        if any(s in state_lower for s in ("failed", "cancelled", "rejected", "errored")):
                            raise Exception(f"Download failed: {state}")

                        # Aborted is often transient - try re-queuing
                        if "aborted" in state_lower:
                            abort_count += 1
                            if abort_count > max_abort_requeues:
                                raise Exception(f"Download aborted {abort_count} times, giving up")

                            print(f"slskd: Download aborted, re-queuing (attempt {abort_count}/{max_abort_requeues})...")
                            time.sleep(2)  # Brief pause before re-queue

                            # Re-enqueue the download
                            requeue_response = client.post(
                                f"{SLSKD_URL}/api/v0/transfers/downloads/{username}",
                                headers=headers,
                                json=[{"filename": filename}]
                            )
                            if requeue_response.status_code not in [200, 201]:
                                print(f"slskd: Re-queue failed with status {requeue_response.status_code}")
                            else:
                                print(f"slskd: Re-queued successfully")

                            last_state = ""  # Reset to log new state
                            break  # Continue polling

                        # Success states
                        if state_lower.startswith("completed") or state_lower == "succeeded":
                            # Make sure it's actually completed successfully, not "CompletedWithError"
                            if "error" not in state_lower:
                                download_complete = True
                                downloaded_path = get_slskd_local_path(dl) or dl_filename
                            else:
                                raise Exception(f"Download completed with error: {state}")
                            break

                if download_complete:
                    break

                # If file disappeared from the queue entirely, it might have been
                # removed or the user went offline - try re-queuing once
                if not file_found and last_state and "queue" not in last_state.lower():
                    print(f"slskd: File no longer in transfer queue, attempting re-queue...")
                    requeue_response = client.post(
                        f"{SLSKD_URL}/api/v0/transfers/downloads/{username}",
                        headers=headers,
                        json=[{"filename": filename}]
                    )
                    if requeue_response.status_code in [200, 201]:
                        print(f"slskd: Re-queued successfully")
                    last_state = ""

            if not download_complete:
                raise Exception(f"Download timed out after {timeout_secs}s")

            # File should now be in slskd's downloads folder
            # slskd typically organises as: {downloads_path}/{username}/{filename}
            candidate_paths = []
            if downloaded_path:
                normalized_path = normalize_slskd_path(downloaded_path)
                dl_path = Path(normalized_path)
                if dl_path.is_absolute():
                    candidate_paths.append(dl_path)
                else:
                    for slskd_dir in slskd_download_dirs:
                        candidate_paths.append(slskd_dir / dl_path)
                        candidate_paths.append(slskd_dir / username / dl_path)

            for slskd_dir in slskd_download_dirs:
                candidate_paths.append(slskd_dir / username / source_filename)

            for potential_path in candidate_paths:
                if potential_path.exists():
                    import shutil
                    dest_path = dest_dir / source_filename
                    shutil.copy2(potential_path, dest_path)
                    print(f"slskd: Copied {potential_path} to {dest_path}")
                    return dest_path

            # If not found, search recursively in the username folder
            for slskd_dir in slskd_download_dirs:
                user_dir = slskd_dir / username
                if user_dir.exists():
                    for found_file in user_dir.rglob(source_filename):
                        if found_file.is_file():
                            import shutil
                            dest_path = dest_dir / source_filename
                            shutil.copy2(found_file, dest_path)
                            print(f"slskd: Found and copied {found_file} to {dest_path}")
                            return dest_path

            # List what's actually there for debugging
            for slskd_dir in slskd_download_dirs:
                if slskd_dir.exists():
                    print(f"slskd: Downloads directory contents ({slskd_dir}):")
                    for item in slskd_dir.iterdir():
                        print(f"  - {item.name}/")
                        if item.is_dir():
                            for subitem in list(item.iterdir())[:5]:
                                print(f"      {subitem.name}")
                else:
                    print(f"slskd: Downloads directory not found: {slskd_dir}")

            raise Exception(
                "Downloaded file not found at expected location. "
                "Check that the slskd downloads path is mounted into MusicGrabber."
            )

    except Exception as e:
        print(f"slskd download error: {e}")
        raise

def apply_metadata_to_file(file_path: Path, artist: str, title: str, album: str = "Singles", year: str = None):
    """Apply metadata to audio file using mutagen (supports multiple formats)"""
    try:
        suffix = file_path.suffix.lower()

        if suffix == '.flac':
            audio = FLAC(str(file_path))
            audio["ARTIST"] = artist
            audio["TITLE"] = title
            audio["ALBUM"] = album
            if year:
                audio["DATE"] = year
            audio.save()

        elif suffix == '.mp3':
            from mutagen.easyid3 import EasyID3
            from mutagen.mp3 import MP3
            try:
                audio = EasyID3(str(file_path))
            except Exception:
                # If no ID3 tag exists, create one
                mp3 = MP3(str(file_path))
                mp3.add_tags()
                mp3.save()
                audio = EasyID3(str(file_path))
            audio["artist"] = artist
            audio["title"] = title
            audio["album"] = album
            if year:
                audio["date"] = year
            audio.save()

        elif suffix in ['.m4a', '.mp4']:
            from mutagen.mp4 import MP4
            audio = MP4(str(file_path))
            audio["\xa9ART"] = [artist]
            audio["\xa9nam"] = [title]
            audio["\xa9alb"] = [album]
            if year:
                audio["\xa9day"] = [year]
            audio.save()

        elif suffix in ['.ogg', '.opus']:
            from mutagen.oggopus import OggOpus
            from mutagen.oggvorbis import OggVorbis
            try:
                if suffix == '.opus':
                    audio = OggOpus(str(file_path))
                else:
                    audio = OggVorbis(str(file_path))
                audio["ARTIST"] = artist
                audio["TITLE"] = title
                audio["ALBUM"] = album
                if year:
                    audio["DATE"] = year
                audio.save()
            except Exception:
                pass  # Some ogg variants may not be supported

        # For .webm and other unsupported formats, skip metadata (yt-dlp handles it)

    except Exception:
        # If metadata application fails, continue anyway
        pass

def check_duplicate(artist: str, title: str) -> Optional[Path]:
    """Check if a track already exists in the library (any audio format)"""
    try:
        artist_dir = SINGLES_DIR / sanitize_filename(artist)
        if not artist_dir.exists():
            return None

        sanitized_title = sanitize_filename(title)

        # Check for exact filename match in any supported format
        for ext in ['.flac', '.opus', '.m4a', '.webm', '.mp3', '.ogg']:
            expected_file = artist_dir / f"{sanitized_title}{ext}"
            if expected_file.exists():
                return expected_file

        # Check for similar files (case-insensitive) in any audio format
        title_lower = sanitized_title.lower()
        for ext in ['*.flac', '*.opus', '*.m4a', '*.webm', '*.mp3', '*.ogg']:
            for file in artist_dir.glob(ext):
                if file.stem.lower() == title_lower:
                    return file

        return None
    except Exception:
        return None

@app.get("/", response_class=HTMLResponse)
def root():
    """Serve the main UI"""
    return FileResponse("/app/static/index.html")

@app.get("/api/config")
def get_config():
    """Expose server defaults for the UI"""
    return {"default_convert_to_flac": DEFAULT_CONVERT_TO_FLAC}

@app.get("/api/preview/{video_id}")
def get_preview_url(video_id: str):
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

def search_youtube(query: str, limit: int) -> list[dict]:
    """Search YouTube and return normalized results"""
    try:
        fetch_limit = max(limit * 3, 30)

        cmd = [
            "yt-dlp",
            "--dump-json",
            "--flat-playlist",
            "--no-warnings",
            f"ytsearch{fetch_limit}:{query}",
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode != 0:
            return []

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
                quality_score = score_search_result(title, channel)

                results.append({
                    "video_id": data.get("id", ""),
                    "title": title,
                    "channel": channel,
                    "duration": parse_duration(data.get("duration", 0) or 0) if not is_playlist else "",
                    "thumbnail": data.get("thumbnail", f"https://i.ytimg.com/vi/{data.get('id')}/mqdefault.jpg"),
                    "is_playlist": is_playlist,
                    "video_count": video_count,
                    "source": "youtube",
                    "quality": None,
                    "quality_score": quality_score,
                    "slskd_username": None,
                    "slskd_filename": None,
                })
            except json.JSONDecodeError:
                continue

        results.sort(key=lambda x: x["quality_score"], reverse=True)
        return results[:limit]

    except Exception as e:
        print(f"YouTube search error: {e}")
        return []


@app.post("/api/search")
def search(request: SearchRequest):
    """Search YouTube for music (fast, no slskd delay)"""
    try:
        yt_results = search_youtube(request.query, request.limit)

        final_results = []
        for item in yt_results[:request.limit]:
            final_results.append(SearchResult(
                video_id=item["video_id"],
                title=item["title"],
                channel=item["channel"],
                duration=item["duration"],
                thumbnail=item["thumbnail"],
                is_playlist=item["is_playlist"],
                video_count=item["video_count"],
                source=item["source"],
                quality=item["quality"],
                quality_score=item["quality_score"],
                slskd_username=item["slskd_username"],
                slskd_filename=item["slskd_filename"],
            ))

        return {"results": final_results, "slskd_enabled": slskd_enabled()}

    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Search timed out")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/search/slskd")
def search_slskd_endpoint(request: SearchRequest):
    """Search Soulseek via slskd (slower, called separately)"""
    if not slskd_enabled():
        return {"results": [], "slskd_enabled": False}

    try:
        print(f"Searching slskd for: {request.query}")
        slskd_results = search_slskd(request.query, timeout_secs=12)
        print(f"slskd returned {len(slskd_results)} results")

        final_results = []
        for r in slskd_results[:request.limit]:
            final_results.append(SearchResult(
                video_id=r["id"],
                title=r["title"],
                channel=r["channel"],
                duration=parse_duration(int(r["duration"])) if r["duration"].isdigit() else r["duration"],
                thumbnail="",
                is_playlist=False,
                video_count=None,
                source="soulseek",
                quality=r["quality"],
                quality_score=r["quality_score"],
                slskd_username=r["slskd_username"],
                slskd_filename=r["slskd_filename"],
            ))

        return {"results": final_results, "slskd_enabled": True}

    except Exception as e:
        print(f"slskd search error: {e}")
        return {"results": [], "slskd_enabled": True, "error": str(e)}


@app.post("/api/download")
def download(request: DownloadRequest, background_tasks: BackgroundTasks):
    """Queue a download job"""
    job_id = str(uuid.uuid4())[:8]

    # Extract artist/title if not provided
    artist = request.artist
    title = request.title

    if not artist:
        # We'll extract from the full video info during download
        pass

    # Create job record
    conn = get_db()
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

    # Queue the download based on source
    if request.download_type == "playlist":
        background_tasks.add_task(process_playlist_download, job_id, request.video_id, title, request.convert_to_flac)
    elif request.source == "soulseek" and request.slskd_username and request.slskd_filename:
        background_tasks.add_task(
            process_slskd_download,
            job_id,
            request.slskd_username,
            request.slskd_filename,
            artist or "",
            title,
            request.convert_to_flac
        )
    else:
        background_tasks.add_task(process_download, job_id, request.video_id, request.convert_to_flac)

    return {"job_id": job_id, "status": "queued"}


def process_playlist_download(job_id: str, playlist_id: str, playlist_name: str, convert_to_flac: bool = True):
    """Process a playlist download job"""
    conn = get_db()

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

                # Download with best audio quality
                output_template = str(artist_dir / f"{sanitize_filename(title)}.%(ext)s")

                download_cmd = [
                    "yt-dlp",
                    "-x",
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

                # Only convert to FLAC if requested
                if convert_to_flac:
                    download_cmd.insert(2, "--audio-format")
                    download_cmd.insert(3, "flac")

                download_result = subprocess.run(
                    download_cmd,
                    capture_output=True,
                    text=True,
                    timeout=300
                )

                if download_result.returncode == 0:
                    # Find the downloaded file (extension depends on convert_to_flac setting)
                    audio_file = None
                    sanitized_title = sanitize_filename(title)
                    for ext in ['.flac', '.opus', '.m4a', '.webm', '.mp3', '.ogg']:
                        candidate = artist_dir / f"{sanitized_title}{ext}"
                        if candidate.exists():
                            audio_file = candidate
                            break

                    if audio_file:
                        # Try to enrich metadata with MusicBrainz
                        mb_metadata = lookup_musicbrainz(artist, title)
                        if mb_metadata:
                            # Use MusicBrainz metadata
                            apply_metadata_to_file(
                                audio_file,
                                mb_metadata.get("artist", artist),
                                mb_metadata.get("title", title),
                                mb_metadata.get("album", "Singles"),
                                mb_metadata.get("year")
                            )
                        else:
                            # Use cleaned YouTube metadata
                            apply_metadata_to_file(audio_file, artist, title, "Singles")

                        downloaded_files.append(str(audio_file.relative_to(SINGLES_DIR)))

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
            trigger_navidrome_scan()
        if JELLYFIN_URL and JELLYFIN_API_KEY:
            trigger_jellyfin_scan()

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


def process_slskd_download(job_id: str, username: str, filename: str, artist: str, title: str, convert_to_flac: bool = True):
    """Process a Soulseek download job via slskd"""
    conn = get_db()

    try:
        # Update status to downloading
        conn.execute("UPDATE jobs SET status = ? WHERE id = ?", ("downloading", job_id))
        conn.commit()

        # If artist/title not provided, extract from filename
        if not artist or not title:
            artist, title = extract_track_info_from_path(filename)

        # Update job with extracted info
        conn.execute(
            "UPDATE jobs SET title = ?, artist = ? WHERE id = ?",
            (title, artist, job_id)
        )
        conn.commit()

        # Check for duplicates
        existing_file = check_duplicate(artist, title)
        if existing_file:
            conn.execute(
                "UPDATE jobs SET status = ?, completed_at = ?, error = ? WHERE id = ?",
                ("completed", datetime.now().isoformat(), f"Already exists: {existing_file.name}", job_id)
            )
            conn.commit()
            return

        # Create artist directory under Singles
        artist_dir = SINGLES_DIR / sanitize_filename(artist)
        artist_dir.mkdir(parents=True, exist_ok=True)

        # Download from slskd with retries on common queue/abort failures
        downloaded_file = None
        attempts = 0
        tried_candidates = set()
        candidate_queue = [(username, filename)]
        last_error = None

        while candidate_queue:
            cand_username, cand_filename = candidate_queue.pop(0)
            if (cand_username, cand_filename) in tried_candidates:
                continue
            tried_candidates.add((cand_username, cand_filename))

            try:
                downloaded_file = download_from_slskd(cand_username, cand_filename, artist_dir)
                break
            except Exception as e:
                last_error = str(e)
                print(f"slskd download attempt failed: {last_error}")
                if attempts >= SLSKD_MAX_RETRIES or not should_retry_slskd_error(last_error):
                    break
                attempts += 1

                # Refresh candidates from a new search if we don't have any left
                if not candidate_queue:
                    retry_query = f"{artist} {title}".strip()
                    retry_results = search_slskd(retry_query, timeout_secs=12)
                    for r in retry_results:
                        candidate = (r.get("slskd_username", ""), r.get("slskd_filename", ""))
                        if candidate[0] and candidate[1] and candidate not in tried_candidates:
                            candidate_queue.append(candidate)

        if not downloaded_file:
            raise Exception(last_error or "Soulseek download failed")

        if not downloaded_file or not downloaded_file.exists():
            raise Exception("Download completed but file not found")

        # Rename to our standard naming
        sanitized_title = sanitize_filename(title)
        source_ext = downloaded_file.suffix.lower()

        # Determine final filename
        if convert_to_flac and source_ext != '.flac':
            # Convert to FLAC
            final_file = artist_dir / f"{sanitized_title}.flac"
            convert_cmd = [
                "ffmpeg", "-y", "-i", str(downloaded_file),
                "-c:a", "flac", str(final_file)
            ]
            result = subprocess.run(convert_cmd, capture_output=True, timeout=120)
            if result.returncode == 0:
                downloaded_file.unlink()  # Remove original
            else:
                # Conversion failed, keep original with new name
                final_file = artist_dir / f"{sanitized_title}{source_ext}"
                downloaded_file.rename(final_file)
        else:
            # Keep original format
            final_file = artist_dir / f"{sanitized_title}{source_ext}"
            if downloaded_file != final_file:
                downloaded_file.rename(final_file)

        # Apply metadata
        mb_metadata = lookup_musicbrainz(artist, title)
        if mb_metadata:
            apply_metadata_to_file(
                final_file,
                mb_metadata.get("artist", artist),
                mb_metadata.get("title", title),
                mb_metadata.get("album", "Singles"),
                mb_metadata.get("year")
            )
        else:
            apply_metadata_to_file(final_file, artist, title, "Singles")

        # Fetch and save lyrics
        lyrics = fetch_lyrics(artist, title)
        if lyrics:
            save_lyrics_file(final_file, lyrics)
            print(f"Saved lyrics for {artist} - {title}")
        else:
            print(f"No lyrics found for {artist} - {title}")

        # Trigger Navidrome rescan if configured
        if NAVIDROME_URL and NAVIDROME_USER and NAVIDROME_PASS:
            trigger_navidrome_scan()
        if JELLYFIN_URL and JELLYFIN_API_KEY:
            trigger_jellyfin_scan()

        # Update job status
        conn.execute(
            "UPDATE jobs SET status = ?, completed_at = ? WHERE id = ?",
            ("completed", datetime.now().isoformat(), job_id)
        )
        conn.commit()

        print(f"slskd: Successfully downloaded {artist} - {title}")

    except Exception as e:
        print(f"slskd download failed: {e}")
        conn.execute(
            "UPDATE jobs SET status = ?, error = ?, completed_at = ? WHERE id = ?",
            ("failed", str(e), datetime.now().isoformat(), job_id)
        )
        conn.commit()

    finally:
        conn.close()


def process_download(job_id: str, video_id: str, convert_to_flac: bool = True):
    """Process a download job"""
    conn = get_db()
    
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

        # Download with best audio quality
        output_template = str(artist_dir / f"{sanitize_filename(title)}.%(ext)s")

        download_cmd = [
            "yt-dlp",
            "-x",  # Extract audio
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

        # Only convert to FLAC if requested
        if convert_to_flac:
            download_cmd.insert(2, "--audio-format")
            download_cmd.insert(3, "flac")
        
        download_result = subprocess.run(
            download_cmd, 
            capture_output=True, 
            text=True, 
            timeout=300
        )
        
        if download_result.returncode != 0:
            raise Exception(f"Download failed: {download_result.stderr}")

        # Find the downloaded file (extension depends on convert_to_flac setting)
        audio_file = None
        sanitized_title = sanitize_filename(title)
        for ext in ['.flac', '.opus', '.m4a', '.webm', '.mp3', '.ogg']:
            candidate = artist_dir / f"{sanitized_title}{ext}"
            if candidate.exists():
                audio_file = candidate
                break

        if audio_file:
            # Try to enrich metadata with MusicBrainz
            mb_metadata = lookup_musicbrainz(artist, title)
            if mb_metadata:
                # Use MusicBrainz metadata
                apply_metadata_to_file(
                    audio_file,
                    mb_metadata.get("artist", artist),
                    mb_metadata.get("title", title),
                    mb_metadata.get("album", "Singles"),
                    mb_metadata.get("year")
                )
            else:
                # Use cleaned YouTube metadata
                apply_metadata_to_file(audio_file, artist, title, "Singles")

            # Fetch and save lyrics
            lyrics = fetch_lyrics(artist, title)
            if lyrics:
                save_lyrics_file(audio_file, lyrics)
                print(f"Saved lyrics for {artist} - {title}")
            else:
                print(f"No lyrics found for {artist} - {title}")

        # Trigger Navidrome rescan if configured
        if NAVIDROME_URL and NAVIDROME_USER and NAVIDROME_PASS:
            trigger_navidrome_scan()
        if JELLYFIN_URL and JELLYFIN_API_KEY:
            trigger_jellyfin_scan()

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


def trigger_navidrome_scan():
    """Trigger a Navidrome library scan via API"""
    try:
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

        with httpx.Client(timeout=10) as client:
            client.get(
                f"{NAVIDROME_URL}/rest/startScan",
                params=params
            )
    except Exception:
        pass  # Non-critical, scan will happen on schedule anyway


def trigger_jellyfin_scan():
    """Trigger a Jellyfin library scan via API"""
    try:
        with httpx.Client(timeout=10) as client:
            client.post(
                f"{JELLYFIN_URL}/Library/Refresh",
                headers={"X-Emby-Token": JELLYFIN_API_KEY}
            )
    except Exception:
        pass  # Non-critical, scan will happen on schedule anyway


def create_bulk_playlist(bulk_import_id: str, playlist_name: str, expected_count: int):
    """Create an M3U playlist from a bulk import after all downloads complete

    Waits for all jobs with the matching playlist_name to complete, then generates the M3U file.
    """
    # Wait for all downloads to complete (with timeout)
    max_wait_time = 3600  # 1 hour max
    check_interval = 10  # Check every 10 seconds
    waited = 0

    while waited < max_wait_time:
        conn = get_db()
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

        time.sleep(check_interval)
        waited += check_interval

    # Gather all successfully downloaded files
    conn = get_db()
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

        # Construct expected file path (any supported format)
        artist_dir = SINGLES_DIR / sanitize_filename(artist)
        audio_file = None
        for ext in ['.flac', '.opus', '.m4a', '.webm', '.mp3', '.ogg']:
            candidate = artist_dir / f"{sanitize_filename(title)}{ext}"
            if candidate.exists():
                audio_file = candidate
                break

        if audio_file:
            # Store relative path from Singles directory
            rel_path = audio_file.relative_to(SINGLES_DIR)
            playlist_files.append(str(rel_path))

    if playlist_files:
        # Create M3U file
        m3u_path = SINGLES_DIR / f"{sanitize_filename(playlist_name)}.m3u"
        with open(m3u_path, 'w', encoding='utf-8') as f:
            f.write("#EXTM3U\n")
            for file_path in playlist_files:
                f.write(f"{file_path}\n")


@app.get("/api/jobs")
def get_jobs(limit: int = 20):
    """Get recent jobs"""
    conn = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(
        "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?",
        (limit,)
    )
    jobs = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return {"jobs": jobs}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    """Get a specific job"""
    conn = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Job not found")

    return dict(row)


@app.post("/api/jobs/{job_id}/retry")
def retry_job(job_id: str, background_tasks: BackgroundTasks):
    """Retry a failed job"""
    conn = get_db()
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
def cleanup_jobs(status: Optional[str] = None):
    """Delete completed or failed jobs

    Args:
        status: Optional filter - 'completed', 'failed', or None for both
    """
    conn = get_db()

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


def process_bulk_import_worker(import_id: str):
    """Background worker to process bulk import tracks one by one

    Handles rate limiting with exponential backoff:
    - 1 second delay between searches
    - On 429: wait 30s, then 60s, then 120s
    - Tracks progress in database for resilience
    """
    global _bulk_import_worker_running

    conn = get_db()
    conn.row_factory = sqlite3.Row

    # Get import details
    cursor = conn.execute("SELECT * FROM bulk_imports WHERE id = ?", (import_id,))
    import_row = cursor.fetchone()
    if not import_row:
        conn.close()
        return

    convert_to_flac = bool(import_row["convert_to_flac"])
    create_playlist = bool(import_row["create_playlist"])
    playlist_name = import_row["playlist_name"]

    # Update status to processing
    conn.execute("UPDATE bulk_imports SET status = 'processing' WHERE id = ?", (import_id,))
    conn.commit()

    # Rate limiting state
    base_delay = 1.0  # 1 second between searches
    backoff_delays = [30, 60, 120, 300]  # Exponential backoff on 429
    current_backoff_index = 0
    consecutive_successes = 0

    try:
        while True:
            # Get next pending track
            cursor = conn.execute(
                "SELECT * FROM bulk_import_tracks WHERE import_id = ? AND status = 'pending' ORDER BY line_num LIMIT 1",
                (import_id,)
            )
            track = cursor.fetchone()

            if not track:
                # No more pending tracks - we're done
                break

            track_id = track["id"]
            artist = track["artist"]
            song = track["song"]
            line_num = track["line_num"]

            # Mark track as searching
            conn.execute("UPDATE bulk_import_tracks SET status = 'searching' WHERE id = ?", (track_id,))
            conn.commit()

            # Search for the song
            try:
                search_query = f"{artist} {song}"
                cmd = [
                    "yt-dlp",
                    "--dump-json",
                    "--flat-playlist",
                    "--no-warnings",
                    f"ytsearch10:{search_query}",
                ]

                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

                # Check for rate limiting (429 in stderr)
                if "429" in result.stderr or "Too Many Requests" in result.stderr:
                    # Rate limited - apply backoff
                    delay = backoff_delays[min(current_backoff_index, len(backoff_delays) - 1)]
                    current_backoff_index += 1
                    consecutive_successes = 0

                    # Update import with rate limit info
                    rate_limited_until = datetime.now().isoformat()
                    conn.execute(
                        "UPDATE bulk_imports SET rate_limited_until = ? WHERE id = ?",
                        (rate_limited_until, import_id)
                    )
                    conn.execute("UPDATE bulk_import_tracks SET status = 'pending' WHERE id = ?", (track_id,))
                    conn.commit()

                    time.sleep(delay)
                    continue

                # Success - reset backoff
                consecutive_successes += 1
                if consecutive_successes >= 5:
                    current_backoff_index = max(0, current_backoff_index - 1)
                    consecutive_successes = 0

                # Clear rate limit flag
                conn.execute("UPDATE bulk_imports SET rate_limited_until = NULL WHERE id = ?", (import_id,))

                if result.returncode != 0 or not result.stdout.strip():
                    # Search failed
                    conn.execute(
                        "UPDATE bulk_import_tracks SET status = 'failed', error = ? WHERE id = ?",
                        ("No results found", track_id)
                    )
                    conn.execute(
                        "UPDATE bulk_imports SET searched = searched + 1, failed = failed + 1 WHERE id = ?",
                        (import_id,)
                    )
                    conn.commit()
                    time.sleep(base_delay)
                    continue

                # Parse results and find best match
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
                    conn.execute(
                        "UPDATE bulk_import_tracks SET status = 'failed', error = ? WHERE id = ?",
                        ("No valid results", track_id)
                    )
                    conn.execute(
                        "UPDATE bulk_imports SET searched = searched + 1, failed = failed + 1 WHERE id = ?",
                        (import_id,)
                    )
                    conn.commit()
                    time.sleep(base_delay)
                    continue

                # Sort by score and pick best
                search_results.sort(key=lambda x: x["score"], reverse=True)
                best_match = search_results[0]
                video_id = best_match["video_id"]

                # Create download job
                job_id = str(uuid.uuid4())[:8]

                if create_playlist:
                    conn.execute(
                        "INSERT INTO jobs (id, video_id, title, artist, status, download_type, playlist_name) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (job_id, video_id, song, artist, "queued", "single", import_id)
                    )
                else:
                    conn.execute(
                        "INSERT INTO jobs (id, video_id, title, artist, status, download_type) VALUES (?, ?, ?, ?, ?, ?)",
                        (job_id, video_id, song, artist, "queued", "single")
                    )

                # Update track as queued
                conn.execute(
                    "UPDATE bulk_import_tracks SET status = 'queued', job_id = ?, video_id = ? WHERE id = ?",
                    (job_id, video_id, track_id)
                )
                conn.execute(
                    "UPDATE bulk_imports SET searched = searched + 1, queued = queued + 1 WHERE id = ?",
                    (import_id,)
                )
                conn.commit()

                # Start the download in a thread
                download_thread = threading.Thread(
                    target=process_download,
                    args=(job_id, video_id, convert_to_flac)
                )
                download_thread.start()

            except subprocess.TimeoutExpired:
                conn.execute(
                    "UPDATE bulk_import_tracks SET status = 'failed', error = ? WHERE id = ?",
                    ("Search timeout", track_id)
                )
                conn.execute(
                    "UPDATE bulk_imports SET searched = searched + 1, failed = failed + 1 WHERE id = ?",
                    (import_id,)
                )
                conn.commit()

            except Exception as e:
                conn.execute(
                    "UPDATE bulk_import_tracks SET status = 'failed', error = ? WHERE id = ?",
                    (str(e)[:200], track_id)
                )
                conn.execute(
                    "UPDATE bulk_imports SET searched = searched + 1, failed = failed + 1 WHERE id = ?",
                    (import_id,)
                )
                conn.commit()

            # Standard delay between searches
            time.sleep(base_delay)

        # All tracks processed - mark import as complete
        conn.execute(
            "UPDATE bulk_imports SET status = 'completed', completed_at = CURRENT_TIMESTAMP WHERE id = ?",
            (import_id,)
        )
        conn.commit()

        # Create playlist if requested
        if create_playlist:
            cursor = conn.execute("SELECT queued FROM bulk_imports WHERE id = ?", (import_id,))
            row = cursor.fetchone()
            if row and row["queued"] > 0:
                playlist_thread = threading.Thread(
                    target=create_bulk_playlist,
                    args=(import_id, playlist_name or f"Playlist {import_id}", row["queued"])
                )
                playlist_thread.start()

    except Exception as e:
        conn.execute(
            "UPDATE bulk_imports SET status = 'error', error = ? WHERE id = ?",
            (str(e)[:500], import_id)
        )
        conn.commit()

    finally:
        conn.close()
        with _bulk_import_lock:
            _bulk_import_worker_running = False


@app.post("/api/bulk-import-async")
def bulk_import_async(request: AsyncBulkImportRequest):
    """Start an async bulk import job

    Returns immediately with import_id. Use /api/bulk-import/{id}/status to poll progress.
    Downloads start as soon as tracks are found, while searching continues in background.
    """
    global _bulk_import_worker_running

    lines = request.songs.strip().split('\n')
    import_id = str(uuid.uuid4())[:8]

    # Parse and validate all lines first
    tracks_to_import = []
    for line_num, line in enumerate(lines, 1):
        original_line = line
        line = clean_bulk_import_line(line)

        if not line:
            continue

        if len(line) > 200:
            continue

        # Try to parse "Artist - Song" format
        match = re.match(r'^(.+?)\s*[-–—]\s*(.+)$', line)
        if not match:
            continue

        artist, song = match.groups()
        artist = artist.strip()
        song = song.strip()

        if not artist or not song:
            continue

        tracks_to_import.append({
            "line_num": line_num,
            "artist": artist,
            "song": song
        })

    if not tracks_to_import:
        raise HTTPException(status_code=400, detail="No valid tracks found in input")

    # Create bulk import record
    conn = get_db()
    conn.execute(
        """INSERT INTO bulk_imports
           (id, status, total_tracks, create_playlist, playlist_name, convert_to_flac)
           VALUES (?, 'pending', ?, ?, ?, ?)""",
        (import_id, len(tracks_to_import), int(request.create_playlist),
         request.playlist_name, int(request.convert_to_flac))
    )

    # Insert all tracks
    for track in tracks_to_import:
        conn.execute(
            "INSERT INTO bulk_import_tracks (import_id, line_num, artist, song, status) VALUES (?, ?, ?, ?, 'pending')",
            (import_id, track["line_num"], track["artist"], track["song"])
        )

    conn.commit()
    conn.close()

    # Start background worker
    with _bulk_import_lock:
        _bulk_import_worker_running = True

    worker_thread = threading.Thread(target=process_bulk_import_worker, args=(import_id,))
    worker_thread.daemon = True
    worker_thread.start()

    return {
        "import_id": import_id,
        "total_tracks": len(tracks_to_import),
        "status": "pending"
    }


@app.get("/api/bulk-import/{import_id}/status")
def get_bulk_import_status(import_id: str):
    """Get status of a bulk import job

    Returns progress info for polling UI updates.
    """
    conn = get_db()
    conn.row_factory = sqlite3.Row

    cursor = conn.execute("SELECT * FROM bulk_imports WHERE id = ?", (import_id,))
    import_row = cursor.fetchone()

    if not import_row:
        conn.close()
        raise HTTPException(status_code=404, detail="Import not found")

    # Get recent track statuses for display - show most recently processed first
    # Prioritise queued/failed over pending, then by line_num descending within processed
    cursor = conn.execute(
        """SELECT artist, song, status, error FROM bulk_import_tracks
           WHERE import_id = ?
           ORDER BY
               CASE status
                   WHEN 'queued' THEN 0
                   WHEN 'failed' THEN 0
                   WHEN 'searching' THEN 1
                   ELSE 2
               END,
               line_num DESC
           LIMIT 10""",
        (import_id,)
    )
    recent_tracks = [dict(row) for row in cursor.fetchall()]

    # Count download statuses by joining bulk_import_tracks with jobs
    cursor = conn.execute(
        """SELECT
               SUM(CASE WHEN j.status = 'completed' THEN 1 ELSE 0 END) as completed,
               SUM(CASE WHEN j.status = 'failed' THEN 1 ELSE 0 END) as download_failed,
               SUM(CASE WHEN j.status IN ('queued', 'downloading') THEN 1 ELSE 0 END) as still_queued
           FROM bulk_import_tracks t
           JOIN jobs j ON t.job_id = j.id
           WHERE t.import_id = ?""",
        (import_id,)
    )
    row = cursor.fetchone()
    completed_count = row[0] or 0
    download_failed_count = row[1] or 0
    still_queued_count = row[2] or 0

    conn.close()

    # "queued" from bulk_imports = tracks that were successfully searched
    # "still_queued" = tracks waiting to download (not yet completed or failed)
    # "failed" from bulk_imports = search failures
    # download_failed_count = download failures (separate from search failures)
    total_failed = import_row["failed"] + download_failed_count

    return {
        "import_id": import_id,
        "status": import_row["status"],
        "total_tracks": import_row["total_tracks"],
        "searched": import_row["searched"],
        "queued": still_queued_count,
        "completed": completed_count,
        "failed": total_failed,
        "skipped": import_row["skipped"],
        "rate_limited": import_row["rate_limited_until"] is not None,
        "error": import_row["error"],
        "recent_tracks": recent_tracks,
        "complete": import_row["status"] in ("completed", "error")
    }


@app.get("/api/bulk-imports")
def list_bulk_imports(limit: int = 10):
    """List recent bulk imports"""
    conn = get_db()
    conn.row_factory = sqlite3.Row

    cursor = conn.execute(
        "SELECT * FROM bulk_imports ORDER BY created_at DESC LIMIT ?",
        (limit,)
    )
    imports = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return {"imports": imports}


@app.post("/api/spotify-playlist")
def fetch_spotify_playlist(request: SpotifyPlaylistRequest):
    """Fetch track list from a public Spotify playlist or album URL

    Uses Spotify's embed endpoint which contains track data in a parseable format.
    Returns tracks in "Artist - Song" format ready for bulk import.
    """
    # Validate and extract ID from URL - support both playlists and albums
    playlist_match = re.match(r'https?://open\.spotify\.com/playlist/([a-zA-Z0-9]+)', request.url)
    album_match = re.match(r'https?://open\.spotify\.com/album/([a-zA-Z0-9]+)', request.url)

    if playlist_match:
        spotify_id = playlist_match.group(1)
        spotify_type = "playlist"
    elif album_match:
        spotify_id = album_match.group(1)
        spotify_type = "album"
    else:
        raise HTTPException(status_code=400, detail="Invalid Spotify URL. Expected playlist or album URL.")

    # Fetch the embed page - this contains track data unlike the main page
    try:
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            response = client.get(
                f"https://open.spotify.com/embed/{spotify_type}/{spotify_id}",
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                }
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(status_code=404, detail=f"{spotify_type.title()} not found or is private")
        raise HTTPException(status_code=502, detail=f"Failed to fetch {spotify_type}: {e}")
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Failed to connect to Spotify: {e}")

    html_content = response.text

    # Extract name - first "title" match is usually the playlist/album name
    playlist_name = f"Spotify {spotify_type.title()}"
    title_matches = re.findall(r'"title":"([^"]+)"', html_content)
    if title_matches:
        playlist_name = title_matches[0]

    # Extract tracks using title/subtitle pattern
    # The embed page has tracks as alternating "title":"SONG","subtitle":"ARTIST" pairs
    # We need to find these pairs and combine them
    tracks = []

    # Find all title and subtitle values
    titles = re.findall(r'"title":"([^"]+)"', html_content)
    subtitles = re.findall(r'"subtitle":"([^"]+)"', html_content)

    # Skip the first title (playlist name) and first subtitle (usually "Spotify")
    if len(titles) > 1 and len(subtitles) > 1:
        # The titles and subtitles should align: titles[1] is first track, subtitles[1] is its artist
        track_titles = titles[1:]  # Skip playlist name
        track_artists = subtitles[1:]  # Skip "Spotify"

        # Pair them up
        for title, artist in zip(track_titles, track_artists):
            # Decode unicode escapes like \u0026 -> & using json.loads (safest method)
            try:
                title = json.loads(f'"{title}"')
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass  # Keep original if decode fails
            try:
                artist = json.loads(f'"{artist}"')
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass  # Keep original if decode fails
            tracks.append(f"{artist} - {title}")

    if not tracks:
        raise HTTPException(
            status_code=422,
            detail=f"Could not extract tracks from {spotify_type}. It may be empty or Spotify's page structure may have changed."
        )

    return {
        "tracks": tracks,
        "playlist_name": playlist_name,
        "count": len(tracks)
    }


@app.post("/api/bulk-import")
def bulk_import(request: BulkImportRequest, background_tasks: BackgroundTasks):
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
            conn = get_db()

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
            background_tasks.add_task(process_download, job_id, video_id, request.convert_to_flac)

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
