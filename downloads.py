"""
MusicGrabber - Download Processing

Single track, playlist, and Soulseek download handlers.
Library scan triggers and M3U playlist generation.
"""

import hashlib
import json
import sqlite3
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path

import httpx

from constants import (
    COOKIES_FILE, MUSIC_DIR, SINGLES_DIR,
    TIMEOUT_YTDLP_INFO, TIMEOUT_YTDLP_DOWNLOAD, TIMEOUT_YTDLP_PLAYLIST,
    TIMEOUT_FFMPEG_CONVERT, TIMEOUT_HTTP_REQUEST,
    YTDLP_403_MAX_RETRIES, YTDLP_403_RETRY_DELAY,
    SLSKD_MAX_RETRIES, TIMEOUT_SLSKD_SEARCH,
    PLAYLIST_WAIT_MAX, PLAYLIST_WAIT_INTERVAL,
)
from db import db_conn
from metadata import lookup_musicbrainz, fetch_lyrics, save_lyrics_file, apply_metadata_to_file
from notifications import send_notification
from settings import get_setting
from slskd import (
    download_from_slskd, extract_track_info_from_path,
    search_slskd, should_retry_slskd_error,
)
from utils import (
    sanitize_filename,
    extract_artist_title,
    check_duplicate,
    is_valid_youtube_id,
    set_file_permissions,
)
from youtube import (
    _ytdlp_base_args, _is_ytdlp_403, _strip_cookies_args,
    _should_retry_without_cookies, _sleep_if_botted, _note_bot_block, _note_cookie_failure,
)


def trigger_navidrome_scan():
    """Trigger a Navidrome library scan via API"""
    navidrome_url = get_setting("navidrome_url")
    navidrome_user = get_setting("navidrome_user")
    navidrome_pass = get_setting("navidrome_pass")

    if not (navidrome_url and navidrome_user and navidrome_pass):
        return

    try:
        # Navidrome uses subsonic API
        salt = uuid.uuid4().hex[:8]
        # Subsonic API requires md5(password + salt)
        token = hashlib.md5(f"{navidrome_pass}{salt}".encode()).hexdigest()

        params = {
            "u": navidrome_user,
            "t": token,
            "s": salt,
            "v": "1.16.1",
            "c": "music-grabber",
            "f": "json"
        }

        with httpx.Client(timeout=TIMEOUT_HTTP_REQUEST) as client:
            client.get(
                f"{navidrome_url}/rest/startScan",
                params=params
            )
    except Exception:
        pass  # Non-critical, scan will happen on schedule anyway


def trigger_jellyfin_scan():
    """Trigger a Jellyfin library scan via API"""
    jellyfin_url = get_setting("jellyfin_url")
    jellyfin_api_key = get_setting("jellyfin_api_key")

    if not (jellyfin_url and jellyfin_api_key):
        return

    try:
        with httpx.Client(timeout=TIMEOUT_HTTP_REQUEST) as client:
            client.post(
                f"{jellyfin_url}/Library/Refresh",
                headers={"X-Emby-Token": jellyfin_api_key}
            )
    except Exception:
        pass  # Non-critical, scan will happen on schedule anyway


def _build_ytdlp_download_cmd(video_id: str, output_template: str, convert_to_flac: bool) -> list[str]:
    """Build yt-dlp args for audio extraction, metadata, and thumbnail embedding."""
    flac_args = ["--audio-format", "flac"] if convert_to_flac else []
    return [
        "yt-dlp",
        *_ytdlp_base_args(),
        "-f", "bestaudio/best",
        "-x",
        *flac_args,
        "--audio-quality", "0",
        "--embed-metadata",
        "--embed-thumbnail",
        "--convert-thumbnails", "jpg",
        "--ppa", "ffmpeg:-c:v mjpeg -vf crop=\"'if(gt(ih,iw),iw,ih)':'if(gt(iw,ih),ih,iw)'\"",
        "--add-metadata",
        "--parse-metadata", "%(artist,channel,uploader)s:%(meta_artist)s",
        "--parse-metadata", "%(track,title)s:%(meta_title)s",
        "-o", output_template,
        "--no-warnings",
        f"https://www.youtube.com/watch?v={video_id}"
    ]


def _update_job(job_id: str, **fields) -> None:
    """Update job fields in the database."""
    if not fields:
        return
    columns = ", ".join(f"{key} = ?" for key in fields)
    values = list(fields.values())
    with db_conn() as conn:
        conn.execute(f"UPDATE jobs SET {columns} WHERE id = ?", (*values, job_id))
        conn.commit()


def _mark_watched_track_downloaded(job_id: str) -> None:
    with db_conn() as conn:
        conn.execute(
            "UPDATE watched_playlist_tracks SET downloaded_at = datetime('now') WHERE job_id = ?",
            (job_id,)
        )
        conn.commit()


def _run_ytdlp_with_retries(
    download_cmd: list[str],
    timeout_secs: int,
    has_cookies: bool
) -> tuple[subprocess.CompletedProcess | None, bool]:
    """Run yt-dlp with retry/backoff and optional cookie fallback."""
    download_result = None
    download_timed_out = False

    for attempt in range(1 + YTDLP_403_MAX_RETRIES):
        _sleep_if_botted()
        try:
            download_result = subprocess.run(
                download_cmd,
                capture_output=True,
                text=True,
                timeout=timeout_secs
            )
        except subprocess.TimeoutExpired:
            download_timed_out = True
            break

        if download_result.returncode == 0:
            break

        if _is_ytdlp_403(download_result.stderr) and attempt < YTDLP_403_MAX_RETRIES:
            print(f"YouTube 403 for {download_cmd[-1]}, retrying (attempt {attempt + 1})")
            time.sleep(YTDLP_403_RETRY_DELAY * (attempt + 1))
        else:
            break

    if download_timed_out or (download_result and _should_retry_without_cookies(download_result.stderr)):
        _note_bot_block()
        if has_cookies and download_result and _should_retry_without_cookies(download_result.stderr):
            _note_cookie_failure()

    if (download_timed_out or (download_result and download_result.returncode != 0)) and has_cookies:
        if download_timed_out or _should_retry_without_cookies(download_result.stderr):
            download_cmd_no_cookies = _strip_cookies_args(download_cmd)
            try:
                download_result = subprocess.run(
                    download_cmd_no_cookies,
                    capture_output=True,
                    text=True,
                    timeout=timeout_secs
                )
                download_timed_out = False
            except subprocess.TimeoutExpired:
                download_timed_out = True

    return download_result, download_timed_out


def create_bulk_playlist(bulk_import_id: str, playlist_name: str, expected_count: int):
    """Create an M3U playlist from a bulk import after all downloads complete

    Waits for all jobs with the matching playlist_name to complete, then generates the M3U file.
    """
    # Wait for all downloads to complete (with timeout)
    max_wait_time = PLAYLIST_WAIT_MAX
    check_interval = PLAYLIST_WAIT_INTERVAL
    waited = 0

    while waited < max_wait_time:
        with db_conn() as conn:
            cursor = conn.execute(
                "SELECT COUNT(*) as total, SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed FROM jobs WHERE playlist_name = ?",
                (bulk_import_id,)
            )
            row = cursor.fetchone()

        total, completed = row
        completed = completed or 0

        # All downloads complete
        if completed >= expected_count or total == completed:
            break

        time.sleep(check_interval)
        waited += check_interval

    # Gather all successfully downloaded files
    with db_conn() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT artist, title FROM jobs WHERE playlist_name = ? AND status = 'completed' AND error IS NULL ORDER BY created_at",
            (bulk_import_id,)
        )
        jobs = [dict(row) for row in cursor.fetchall()]

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
        set_file_permissions(m3u_path)


def process_playlist_download(job_id: str, playlist_id: str, playlist_name: str, convert_to_flac: bool = True):
    """Process a playlist download job"""
    try:
        _update_job(job_id, status="downloading")

        # Get playlist information and extract all video IDs
        info_cmd = [
            "yt-dlp",
            *_ytdlp_base_args(),
            "--dump-json",
            "--flat-playlist",
            "--no-warnings",
            f"https://www.youtube.com/playlist?list={playlist_id}"
        ]

        info_result = subprocess.run(info_cmd, capture_output=True, text=True, timeout=TIMEOUT_YTDLP_PLAYLIST)
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

        _update_job(job_id, total_tracks=len(videos))

        # Download each video in the playlist
        downloaded_files = []
        completed_tracks = 0
        failed_tracks = 0
        skipped_tracks = 0
        has_cookies = COOKIES_FILE.exists() and COOKIES_FILE.stat().st_size > 0

        for _, video in enumerate(videos, 1):
            track_label = video.get("title", "Unknown")
            try:
                video_id = video["id"]

                # Get detailed video info
                detail_cmd = [
                    "yt-dlp",
                    *_ytdlp_base_args(),
                    "--dump-json",
                    "--no-warnings",
                    f"https://www.youtube.com/watch?v={video_id}"
                ]

                detail_result = subprocess.run(detail_cmd, capture_output=True, text=True, timeout=TIMEOUT_YTDLP_INFO)
                if detail_result.returncode != 0:
                    failed_tracks += 1
                    continue

                info = json.loads(detail_result.stdout)
                full_title = info.get("title", "Unknown")
                channel = info.get("channel", info.get("uploader", "Unknown"))
                artist, title = extract_artist_title(full_title, channel)

                # Check for duplicates
                existing_file = check_duplicate(artist, title)
                if existing_file:
                    skipped_tracks += 1
                    continue

                # Create artist directory under Singles
                artist_dir = SINGLES_DIR / sanitize_filename(artist)
                artist_dir.mkdir(parents=True, exist_ok=True)

                # Download with best audio quality
                output_template = str(artist_dir / f"{sanitize_filename(title)}.%(ext)s")
                download_cmd = _build_ytdlp_download_cmd(video_id, output_template, convert_to_flac)

                download_result, download_timed_out = _run_ytdlp_with_retries(
                    download_cmd,
                    TIMEOUT_YTDLP_DOWNLOAD,
                    has_cookies
                )

                if download_timed_out or not download_result or download_result.returncode != 0:
                    failed_tracks += 1
                    continue

                # Find the downloaded file (extension depends on convert_to_flac setting)
                audio_file = None
                sanitized_title = sanitize_filename(title)
                for ext in ['.flac', '.opus', '.m4a', '.webm', '.mp3', '.ogg']:
                    candidate = artist_dir / f"{sanitized_title}{ext}"
                    if candidate.exists():
                        audio_file = candidate
                        break

                if not audio_file:
                    failed_tracks += 1
                    continue

                # Try to enrich metadata with MusicBrainz
                mb_metadata = lookup_musicbrainz(artist, title)
                if mb_metadata:
                    apply_metadata_to_file(
                        audio_file,
                        mb_metadata.get("artist", artist),
                        mb_metadata.get("title", title),
                        mb_metadata.get("album", "Singles"),
                        mb_metadata.get("year")
                    )
                else:
                    apply_metadata_to_file(audio_file, artist, title, "Singles")

                # Fetch and save lyrics
                lyrics = fetch_lyrics(artist, title)
                if lyrics:
                    save_lyrics_file(audio_file, lyrics)

                downloaded_files.append(str(audio_file.relative_to(SINGLES_DIR)))
                completed_tracks += 1

            except Exception as track_error:
                # Track this individual failure and continue
                print(f"Playlist track failed: {track_label} - {track_error}")
                failed_tracks += 1
            finally:
                _update_job(
                    job_id,
                    completed_tracks=completed_tracks,
                    failed_tracks=failed_tracks,
                    skipped_tracks=skipped_tracks
                )

        # Generate M3U playlist file
        if downloaded_files:
            m3u_path = SINGLES_DIR / f"{sanitize_filename(playlist_name)}.m3u"
            with open(m3u_path, 'w', encoding='utf-8') as f:
                f.write("#EXTM3U\n")
                for file_path in downloaded_files:
                    f.write(f"{file_path}\n")
            set_file_permissions(m3u_path)

            _update_job(job_id, m3u_path=str(m3u_path.relative_to(MUSIC_DIR)))

        # Trigger library rescans if configured
        trigger_navidrome_scan()
        trigger_jellyfin_scan()

        # Update job status based on results
        final_status = "completed"
        error_message = None

        if failed_tracks:
            final_status = "completed_with_errors"
            error_message = f"{failed_tracks} track(s) failed"
            if skipped_tracks:
                error_message += f", {skipped_tracks} skipped (duplicates)"

        if final_status == "completed":
            error_message = None

        _update_job(
            job_id,
            status=final_status,
            error=error_message,
            completed_at=datetime.now().isoformat()
        )

        # Send notification for playlist
        send_notification(
            notification_type="playlist",
            title=playlist_name,
            playlist_name=playlist_name,
            source="youtube",
            status=final_status,
            error=error_message,
            track_count=len(videos),
            failed_count=failed_tracks,
            skipped_count=skipped_tracks
        )

    except Exception as e:
        _update_job(job_id, status="failed", error=str(e), completed_at=datetime.now().isoformat())

        # Send notification for playlist failure
        send_notification(
            notification_type="error",
            title=playlist_name,
            playlist_name=playlist_name,
            source="youtube",
            status="failed",
            error=str(e)
        )



def process_slskd_download(job_id: str, username: str, filename: str, artist: str, title: str, convert_to_flac: bool = True):
    """Process a Soulseek download job via slskd"""
    try:
        _update_job(job_id, status="downloading")

        # If artist/title not provided, extract from filename
        if not artist or not title:
            artist, title = extract_track_info_from_path(filename)

        # Update job with extracted info
        _update_job(job_id, title=title, artist=artist)

        # Check for duplicates
        existing_file = check_duplicate(artist, title)
        if existing_file:
            _update_job(
                job_id,
                status="completed",
                completed_at=datetime.now().isoformat(),
                error=f"Already exists: {existing_file.name}"
            )
            _mark_watched_track_downloaded(job_id)
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
                    retry_results = search_slskd(retry_query, timeout_secs=TIMEOUT_SLSKD_SEARCH)
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
            result = subprocess.run(convert_cmd, capture_output=True, timeout=TIMEOUT_FFMPEG_CONVERT)
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

        # Set permissions for NAS/SMB compatibility
        set_file_permissions(final_file)

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

        # Trigger library rescans if configured
        trigger_navidrome_scan()
        trigger_jellyfin_scan()

        # Update job status
        _update_job(job_id, status="completed", error=None, completed_at=datetime.now().isoformat())
        _mark_watched_track_downloaded(job_id)

        print(f"slskd: Successfully downloaded {artist} - {title}")

        # Send notification for Soulseek single
        send_notification(
            notification_type="single",
            title=title,
            artist=artist,
            source="soulseek",
            status="completed"
        )

    except Exception as e:
        print(f"slskd download failed: {e}")
        _update_job(job_id, status="failed", error=str(e), completed_at=datetime.now().isoformat())

        # Send notification for Soulseek failure
        send_notification(
            notification_type="error",
            title=title,
            artist=artist,
            source="soulseek",
            status="failed",
            error=str(e)
        )



def process_download(job_id: str, video_id: str, convert_to_flac: bool = True):
    """Process a download job"""
    try:
        if not is_valid_youtube_id(video_id):
            raise Exception("Invalid YouTube video ID")

        # Defaults in case extraction fails before artist/title are assigned
        artist = None
        title = video_id
        has_cookies = COOKIES_FILE.exists() and COOKIES_FILE.stat().st_size > 0

        # Update status to downloading
        _update_job(job_id, status="downloading")

        # First, get video info for proper metadata
        info_cmd = [
            "yt-dlp",
            *_ytdlp_base_args(),
            "--dump-json",
            "--no-warnings",
            f"https://www.youtube.com/watch?v={video_id}"
        ]

        info_result = subprocess.run(info_cmd, capture_output=True, text=True, timeout=TIMEOUT_YTDLP_INFO)
        if info_result.returncode != 0:
            if _is_ytdlp_403(info_result.stderr):
                if has_cookies:
                    _note_cookie_failure()
                hint = "Your cookies may have expired — try re-exporting them in Settings." if has_cookies else "Add browser cookies in Settings to authenticate."
                raise Exception(f"YouTube blocked this request (403). {hint}")
            raise Exception("Failed to get video info")

        info = json.loads(info_result.stdout)

        # Extract artist and title
        full_title = info.get("title", "Unknown")
        channel = info.get("channel", info.get("uploader", "Unknown"))
        artist, title = extract_artist_title(full_title, channel)

        # Update job with extracted info
        _update_job(job_id, title=title, artist=artist)

        # Check for duplicates
        existing_file = check_duplicate(artist, title)
        if existing_file:
            # Mark as completed without downloading
            _update_job(
                job_id,
                status="completed",
                completed_at=datetime.now().isoformat(),
                error=f"Already exists: {existing_file.name}"
            )
            _mark_watched_track_downloaded(job_id)
            return

        # Create artist directory under Singles
        artist_dir = SINGLES_DIR / sanitize_filename(artist)
        artist_dir.mkdir(parents=True, exist_ok=True)

        # Download with best audio quality
        output_template = str(artist_dir / f"{sanitize_filename(title)}.%(ext)s")
        download_cmd = _build_ytdlp_download_cmd(video_id, output_template, convert_to_flac)

        # Retry strategy: back off on suspected bot blocks; if cookies seem to cause 403s,
        # try again without cookies once to distinguish auth problems from general rate limits.
        download_result, download_timed_out = _run_ytdlp_with_retries(
            download_cmd,
            TIMEOUT_YTDLP_DOWNLOAD,
            has_cookies
        )

        if download_timed_out:
            raise Exception("Download timed out (no progress)")

        if not download_result or download_result.returncode != 0:
            stderr = download_result.stderr if download_result else ""
            error_msg = f"Download failed: {stderr}"
            if download_result and _is_ytdlp_403(stderr):
                if has_cookies:
                    error_msg = "YouTube blocked this download (403). Your cookies may have expired — try re-exporting them in Settings."
                else:
                    error_msg = "YouTube blocked this download (403). Add browser cookies in Settings to authenticate."
            raise Exception(error_msg)

        # Find the downloaded file (extension depends on convert_to_flac setting)
        audio_file = None
        sanitized_title = sanitize_filename(title)
        for ext in ['.flac', '.opus', '.m4a', '.webm', '.mp3', '.ogg']:
            candidate = artist_dir / f"{sanitized_title}{ext}"
            if candidate.exists():
                audio_file = candidate
                break

        if audio_file:
            # Set permissions for NAS/SMB compatibility
            set_file_permissions(audio_file)

            # Try to enrich metadata with MusicBrainz
            mb_metadata = lookup_musicbrainz(artist, title)
            if mb_metadata:
                apply_metadata_to_file(
                    audio_file,
                    mb_metadata.get("artist", artist),
                    mb_metadata.get("title", title),
                    mb_metadata.get("album", "Singles"),
                    mb_metadata.get("year")
                )
            else:
                apply_metadata_to_file(audio_file, artist, title, "Singles")

            # Fetch and save lyrics
            lyrics = fetch_lyrics(artist, title)
            if lyrics:
                save_lyrics_file(audio_file, lyrics)
                print(f"Saved lyrics for {artist} - {title}")
            else:
                print(f"No lyrics found for {artist} - {title}")

        # Trigger library rescans if configured
        trigger_navidrome_scan()
        trigger_jellyfin_scan()

        # Update job status
        _update_job(job_id, status="completed", error=None, completed_at=datetime.now().isoformat())
        _mark_watched_track_downloaded(job_id)

        # Send notification for single track
        send_notification(
            notification_type="single",
            title=title,
            artist=artist,
            source="youtube",
            status="completed"
        )

    except Exception as e:
        _update_job(job_id, status="failed", error=str(e), completed_at=datetime.now().isoformat())

        # Send notification for failure
        send_notification(
            notification_type="error",
            title=title,
            artist=artist,
            source="youtube",
            status="failed",
            error=str(e)
        )
