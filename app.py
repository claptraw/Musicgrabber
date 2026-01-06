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

app = FastAPI(title="Music Grabber", version="1.0.0")

# Configuration from environment
MUSIC_DIR = Path(os.getenv("MUSIC_DIR", "/music"))
SINGLES_DIR = MUSIC_DIR / "Singles"
DB_PATH = Path(os.getenv("DB_PATH", "/data/music_grabber.db"))
NAVIDROME_URL = os.getenv("NAVIDROME_URL", "")
NAVIDROME_USER = os.getenv("NAVIDROME_USER", "")
NAVIDROME_PASS = os.getenv("NAVIDROME_PASS", "")

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


class SearchResult(BaseModel):
    video_id: str
    title: str
    channel: str
    duration: str
    thumbnail: str


def parse_duration(seconds: float) -> str:
    """Convert seconds to MM:SS or HH:MM:SS format"""
    seconds = int(seconds)
    if seconds < 3600:
        return f"{seconds // 60}:{seconds % 60:02d}"
    return f"{seconds // 3600}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"


def sanitize_filename(name: str) -> str:
    """Remove/replace characters that are problematic in filenames"""
    # Remove or replace problematic characters
    name = re.sub(r'[<>:"/\\|?*]', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name[:200]  # Limit length


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
            # Clean up common suffixes
            title = re.sub(r'\s*\(Official.*?\)', '', title, flags=re.IGNORECASE)
            title = re.sub(r'\s*\[Official.*?\]', '', title, flags=re.IGNORECASE)
            title = re.sub(r'\s*Official\s*(Music\s*)?Video', '', title, flags=re.IGNORECASE)
            title = re.sub(r'\s*\(Lyrics?\)', '', title, flags=re.IGNORECASE)
            title = re.sub(r'\s*\[Lyrics?\]', '', title, flags=re.IGNORECASE)
            return artist.strip(), title.strip()
    
    # Fallback: use channel as artist, full title as title
    title = re.sub(r'\s*\(Official.*?\)', '', full_title, flags=re.IGNORECASE)
    title = re.sub(r'\s*\[Official.*?\]', '', title, flags=re.IGNORECASE)
    # Remove common channel suffixes like "VEVO", "Official"
    artist = re.sub(r'\s*(VEVO|Official|Music)$', '', channel, flags=re.IGNORECASE)
    return artist.strip(), title.strip()


@app.get("/", response_class=HTMLResponse)
async def root():
    """Serve the main UI"""
    return FileResponse("/app/static/index.html")


@app.post("/api/search")
async def search(request: SearchRequest):
    """Search YouTube for music"""
    try:
        # Use yt-dlp to search YouTube Music first, fallback to regular YouTube
        cmd = [
            "yt-dlp",
            "--dump-json",
            "--flat-playlist",
            "--no-warnings",
            f"ytsearch{request.limit}:{request.query}",
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
                results.append(SearchResult(
                    video_id=data.get("id", ""),
                    title=data.get("title", "Unknown"),
                    channel=data.get("channel", data.get("uploader", "Unknown")),
                    duration=parse_duration(data.get("duration", 0) or 0),
                    thumbnail=data.get("thumbnail", f"https://i.ytimg.com/vi/{data.get('id')}/mqdefault.jpg"),
                ))
            except json.JSONDecodeError:
                continue
        
        return {"results": results}
    
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
    conn.execute(
        "INSERT INTO jobs (id, video_id, title, artist, status) VALUES (?, ?, ?, ?, ?)",
        (job_id, request.video_id, title, artist or "", "queued")
    )
    conn.commit()
    conn.close()
    
    # Queue the download
    background_tasks.add_task(process_download, job_id, request.video_id)
    
    return {"job_id": job_id, "status": "queued"}


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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
