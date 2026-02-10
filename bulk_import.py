"""
MusicGrabber - Bulk Import Logic

Line cleaning, import job creation, and background worker.
"""

import re
import sqlite3
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Optional

from constants import (
    BULK_IMPORT_SEARCH_DELAY, BULK_IMPORT_BACKOFF_DELAYS,
    BULK_IMPORT_BACKOFF_RESET_AFTER, TIMEOUT_YTDLP_SEARCH,
)
from db import db_conn, get_blacklisted_video_ids
from downloads import process_download, create_bulk_playlist
from notifications import send_notification
from utils import hash_track, spawn_daemon_thread
from youtube import _ytdlp_base_args, parse_youtube_search_results

# Limits concurrent downloads spawned by bulk imports to avoid overwhelming
# YouTube with simultaneous requests and starving the DB connection pool.
_download_pool = ThreadPoolExecutor(max_workers=3)


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

    # Normalise multiple spaces/tabs to single space
    line = re.sub(r'\s+', ' ', line)

    return line.strip()


def start_bulk_import_for_tracks(
    tracks: list[tuple[str, str]],
    convert_to_flac: bool,
    watch_playlist_id: Optional[str] = None,
) -> str:
    """Create a bulk import job from a list of (artist, title) tuples."""
    import_id = str(uuid.uuid4())[:8]

    with db_conn() as conn:
        conn.execute(
            """INSERT INTO bulk_imports
               (id, status, total_tracks, create_playlist, playlist_name, convert_to_flac, watch_playlist_id)
               VALUES (?, 'pending', ?, 0, NULL, ?, ?)""",
            (import_id, len(tracks), int(convert_to_flac), watch_playlist_id)
        )

        for line_num, (artist, song) in enumerate(tracks, 1):
            conn.execute(
                "INSERT INTO bulk_import_tracks (import_id, line_num, artist, song, status) VALUES (?, ?, ?, ?, 'pending')",
                (import_id, line_num, artist, song)
            )

        conn.commit()

    spawn_daemon_thread(process_bulk_import_worker, import_id)

    return import_id


def process_bulk_import_worker(import_id: str):
    """Background worker to process bulk import tracks one by one

    Handles rate limiting with exponential backoff:
    - 1 second delay between searches
    - On 429: wait 30s, then 60s, then 120s
    - After N consecutive successes, step the backoff down by one
    - Tracks progress in database for resilience
    """
    # Load import details
    with db_conn() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute("SELECT * FROM bulk_imports WHERE id = ?", (import_id,))
        import_row = cursor.fetchone()
        if not import_row:
            return

        convert_to_flac = bool(import_row["convert_to_flac"])
        create_playlist = bool(import_row["create_playlist"])
        playlist_name = import_row["playlist_name"]
        watch_playlist_id = import_row["watch_playlist_id"]

        conn.execute("UPDATE bulk_imports SET status = 'processing' WHERE id = ?", (import_id,))
        conn.commit()

    # Load blacklisted video IDs once for the whole import run
    blocked_ids = get_blacklisted_video_ids()

    # Rate limiting state
    base_delay = BULK_IMPORT_SEARCH_DELAY
    backoff_delays = BULK_IMPORT_BACKOFF_DELAYS
    current_backoff_index = 0
    consecutive_successes = 0

    try:
        while True:
            # Get next pending track
            with db_conn() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT * FROM bulk_import_tracks WHERE import_id = ? AND status = 'pending' ORDER BY line_num LIMIT 1",
                    (import_id,)
                )
                track = cursor.fetchone()
                # Materialise before releasing connection
                track = dict(track) if track else None

            if not track:
                break

            track_id = track["id"]
            artist = track["artist"]
            song = track["song"]

            with db_conn() as conn:
                conn.execute("UPDATE bulk_import_tracks SET status = 'searching' WHERE id = ?", (track_id,))
                conn.commit()

            # Search for the song
            try:
                search_query = f"{artist} {song}"
                cmd = [
                    "yt-dlp",
                    *_ytdlp_base_args(),
                    "--dump-json",
                    "--flat-playlist",
                    "--no-warnings",
                    f"ytsearch10:{search_query}",
                ]

                result = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_YTDLP_SEARCH)

                # Check for rate limiting (429 in stderr)
                if "429" in result.stderr or "Too Many Requests" in result.stderr:
                    delay = backoff_delays[min(current_backoff_index, len(backoff_delays) - 1)]
                    current_backoff_index += 1
                    consecutive_successes = 0

                    with db_conn() as conn:
                        rate_limited_until = datetime.now(timezone.utc).isoformat()
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
                if consecutive_successes >= BULK_IMPORT_BACKOFF_RESET_AFTER:
                    current_backoff_index = max(0, current_backoff_index - 1)
                    consecutive_successes = 0

                with db_conn() as conn:
                    conn.execute("UPDATE bulk_imports SET rate_limited_until = NULL WHERE id = ?", (import_id,))
                    conn.commit()

                if result.returncode != 0 or not result.stdout.strip():
                    with db_conn() as conn:
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

                # Parse results and find best match (excluding blacklisted videos)
                search_results = parse_youtube_search_results(result.stdout, query=f"{artist} - {song}")
                search_results = [r for r in search_results
                                  if r.get("video_id") and not r.get("is_playlist")
                                  and r.get("video_id") not in blocked_ids]

                if not search_results:
                    with db_conn() as conn:
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
                search_results.sort(key=lambda x: x["quality_score"], reverse=True)
                best_match = search_results[0]
                video_id = best_match["video_id"]

                # Create download job and update tracking
                job_id = str(uuid.uuid4())[:8]

                with db_conn() as conn:
                    if create_playlist:
                        conn.execute(
                            "INSERT INTO jobs (id, video_id, title, artist, status, download_type, playlist_name, source, convert_to_flac) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (job_id, video_id, song, artist, "queued", "single", import_id, "youtube", int(convert_to_flac))
                        )
                    else:
                        conn.execute(
                            "INSERT INTO jobs (id, video_id, title, artist, status, download_type, source, convert_to_flac) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            (job_id, video_id, song, artist, "queued", "single", "youtube", int(convert_to_flac))
                        )

                    conn.execute(
                        "UPDATE bulk_import_tracks SET status = 'queued', job_id = ?, video_id = ? WHERE id = ?",
                        (job_id, video_id, track_id)
                    )
                    if watch_playlist_id:
                        track_hash = hash_track(artist, song)
                        conn.execute(
                            "UPDATE watched_playlist_tracks SET job_id = ? WHERE playlist_id = ? AND track_hash = ?",
                            (job_id, watch_playlist_id, track_hash)
                        )
                    conn.execute(
                        "UPDATE bulk_imports SET searched = searched + 1, queued = queued + 1 WHERE id = ?",
                        (import_id,)
                    )
                    conn.commit()

                # Submit download to bounded pool (max 3 concurrent)
                _download_pool.submit(process_download, job_id, video_id, convert_to_flac)

            except subprocess.TimeoutExpired:
                with db_conn() as conn:
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
                with db_conn() as conn:
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
        with db_conn() as conn:
            conn.row_factory = sqlite3.Row
            conn.execute(
                "UPDATE bulk_imports SET status = 'completed', completed_at = CURRENT_TIMESTAMP WHERE id = ?",
                (import_id,)
            )
            conn.commit()

            # Get final counts for notification
            cursor = conn.execute(
                "SELECT total_tracks, queued, failed, skipped FROM bulk_imports WHERE id = ?",
                (import_id,)
            )
            final_row = cursor.fetchone()
            final_queued = final_row["queued"] if final_row else 0
            final_failed = final_row["failed"] if final_row else 0
            final_skipped = final_row["skipped"] if final_row else 0
            final_total = final_row["total_tracks"] if final_row else 0

        # Send notification for bulk import
        bulk_status = "completed_with_errors" if final_failed > 0 else "completed"
        send_notification(
            notification_type="bulk",
            title=playlist_name or f"Bulk import {import_id}",
            status=bulk_status,
            track_count=final_total,
            failed_count=final_failed,
            skipped_count=final_skipped
        )

        # Create playlist if requested
        if create_playlist and final_queued > 0:
            spawn_daemon_thread(
                create_bulk_playlist,
                import_id,
                playlist_name or f"Playlist {import_id}",
                final_queued
            )

    except Exception as e:
        with db_conn() as conn:
            conn.execute(
                "UPDATE bulk_imports SET status = 'error', error = ? WHERE id = ?",
                (str(e)[:500], import_id)
            )
            conn.commit()

        # Send notification for bulk import failure
        send_notification(
            notification_type="error",
            title=playlist_name or f"Bulk import {import_id}",
            status="failed",
            error=str(e)
        )
