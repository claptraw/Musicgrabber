"""
MusicGrabber - Watched Artists

Monitors MusicBrainz for new singles from followed artists and auto-downloads them.
Mirrors the watched playlists pattern: lock, fetch, diff, queue, done.
"""

import random
import sqlite3
import time

from constants import WATCHED_PLAYLIST_CHECK_HOURS, WATCHED_REFRESH_STALE_SECONDS
from db import db_conn
from bulk_import import start_bulk_import_for_tracks
from metadata import fetch_artist_singles
from utils import hash_track, spawn_daemon_thread, check_duplicate


_scheduler_running = False


def refresh_watched_artist(artist_id: str) -> dict:
    """Fetch latest singles from MusicBrainz and queue any new ones for download.

    Returns a dict with refresh results including new_tracks count.
    Uses the same atomic lock pattern as watched playlists so concurrent
    refreshes can't stomp on each other.
    """
    with db_conn() as conn:
        conn.row_factory = sqlite3.Row

        artist = conn.execute(
            "SELECT * FROM watched_artists WHERE id = ?", (artist_id,)
        ).fetchone()

        if not artist:
            return {"error": "Artist not found", "artist_id": artist_id}

        artist = dict(artist)

        # Acquire an atomic per-artist refresh lock.
        lock_cursor = conn.execute(
            """UPDATE watched_artists
               SET refresh_state = 'running',
                   refresh_stage = 'starting',
                   refresh_started_at = datetime('now'),
                   refresh_completed_at = NULL,
                   refresh_error = NULL,
                   refresh_import_id = NULL
               WHERE id = ?
               AND (
                   refresh_state IS NULL
                   OR refresh_state != 'running'
                   OR refresh_started_at IS NULL
                   OR refresh_started_at < datetime('now', '-' || ? || ' seconds')
               )""",
            (artist_id, str(WATCHED_REFRESH_STALE_SECONDS))
        )
        conn.commit()

        if lock_cursor.rowcount == 0:
            running_state = conn.execute(
                "SELECT refresh_stage, refresh_started_at FROM watched_artists WHERE id = ?",
                (artist_id,)
            ).fetchone()
            return {
                "artist_id": artist_id,
                "name": artist["name"],
                "already_running": True,
                "message": "Refresh already in progress",
                "refresh_stage": running_state["refresh_stage"] if running_state else None,
                "refresh_started_at": running_state["refresh_started_at"] if running_state else None,
            }

        def set_refresh_stage(stage: str) -> None:
            conn.execute(
                """UPDATE watched_artists
                   SET refresh_state = 'running',
                       refresh_stage = ?,
                       refresh_error = NULL
                   WHERE id = ?""",
                (stage, artist_id)
            )
            conn.commit()

        def finish_refresh_success(import_id: str | None) -> None:
            conn.execute(
                """UPDATE watched_artists
                   SET refresh_state = 'idle',
                       refresh_stage = 'done',
                       refresh_error = NULL,
                       refresh_import_id = ?,
                       refresh_completed_at = datetime('now')
                   WHERE id = ?""",
                (import_id, artist_id)
            )
            conn.commit()

        def finish_refresh_error(error_msg: str) -> None:
            conn.execute(
                """UPDATE watched_artists
                   SET refresh_state = 'error',
                       refresh_stage = 'failed',
                       refresh_error = ?,
                       refresh_import_id = NULL,
                       refresh_completed_at = datetime('now')
                   WHERE id = ?""",
                ((error_msg or "Refresh failed")[:800], artist_id)
            )
            conn.commit()

        try:
            # Fetch current singles from MusicBrainz
            set_refresh_stage("fetching")
            mb_tracks = fetch_artist_singles(artist["mbid"])

            # Deduplicate by hash within this release batch  -  MB can list the same
            # recording across multiple single releases (e.g. regional releases).
            seen_hashes: set[str] = set()
            unique_tracks: list[dict] = []
            for t in mb_tracks:
                h = hash_track(t["artist"] or artist["name"], t["title"])
                if h not in seen_hashes:
                    seen_hashes.add(h)
                    unique_tracks.append(t)

            # Load existing track state
            set_refresh_stage("diffing")
            track_rows = conn.execute(
                """SELECT wat.track_hash, wat.downloaded_at, wat.job_id,
                          wat.artist, wat.title, j.status as job_status
                   FROM watched_artist_tracks wat
                   LEFT JOIN jobs j ON wat.job_id = j.id
                   WHERE wat.artist_id = ?""",
                (artist_id,)
            ).fetchall()
            tracked = {row["track_hash"]: dict(row) for row in track_rows}

            from_date = artist.get("from_date") or ""
            tracks_to_import: list[tuple[str, str]] = []
            new_count = 0

            for t in unique_tracks:
                track_artist = t["artist"] or artist["name"]
                track_title = t["title"]
                release_date = t.get("release_date") or ""
                track_hash = hash_track(track_artist, track_title)
                existing = tracked.get(track_hash)

                if not existing:
                    # New track  -  check disk before inserting so pre-existing
                    # library files are recognised immediately rather than queued.
                    existing_file = check_duplicate(track_artist, track_title)
                    conn.execute(
                        """INSERT OR IGNORE INTO watched_artist_tracks
                           (artist_id, track_hash, artist, title, release_date, release_mbid,
                            downloaded_at, resolved_path)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (artist_id, track_hash, track_artist, track_title,
                         release_date, t.get("release_mbid") or "",
                         "now" if existing_file else None,
                         str(existing_file) if existing_file else None)
                    )
                    if existing_file:
                        continue  # Already on disk, nothing to queue
                    # Only queue if it's on or after the from_date
                    if not from_date or not release_date or release_date >= from_date:
                        tracks_to_import.append((track_artist, track_title))
                        new_count += 1
                    # else: seeded as already-known, will never be re-queued
                    continue

                # Already tracked  -  check if file went missing
                if existing.get("downloaded_at"):
                    if not check_duplicate(
                        existing.get("artist") or track_artist,
                        existing.get("title") or track_title
                    ):
                        # File has vanished  -  clear downloaded_at so it re-queues
                        conn.execute(
                            """UPDATE watched_artist_tracks
                               SET downloaded_at = NULL, resolved_path = NULL
                               WHERE artist_id = ? AND track_hash = ?""",
                            (artist_id, track_hash)
                        )
                        tracks_to_import.append((track_artist, track_title))
                    continue

                # Not downloaded  -  check disk in case the file arrived via another route
                existing_file = check_duplicate(track_artist, track_title)
                if existing_file:
                    conn.execute(
                        """UPDATE watched_artist_tracks
                           SET downloaded_at = datetime('now'), resolved_path = ?
                           WHERE artist_id = ? AND track_hash = ?""",
                        (str(existing_file), artist_id, track_hash)
                    )
                    continue

                # Not on disk  -  check job status
                job_status = existing.get("job_status")
                if job_status in ("queued", "downloading"):
                    continue  # In flight, don't double-queue
                # Failed, missing job, or no job  -  retry
                tracks_to_import.append((track_artist, track_title))

            conn.commit()

            # Queue new/missing tracks via bulk import
            import_id = None
            set_refresh_stage("queueing")
            if tracks_to_import:
                convert_to_flac = bool(artist.get("convert_to_flac", 1))
                import_id = start_bulk_import_for_tracks(
                    tracks_to_import,
                    convert_to_flac=convert_to_flac,
                    watch_artist_id=artist_id,
                )
                conn.execute(
                    "UPDATE watched_artists SET refresh_import_id = ? WHERE id = ?",
                    (import_id, artist_id)
                )
                conn.commit()

            # Update last_checked and track count
            total_tracked = len(tracked) + new_count
            conn.execute(
                """UPDATE watched_artists
                   SET last_checked = datetime('now'),
                       last_track_count = ?
                   WHERE id = ?""",
                (total_tracked, artist_id)
            )
            conn.commit()

            finish_refresh_success(import_id)

            return {
                "artist_id": artist_id,
                "name": artist["name"],
                "new_tracks": new_count,
                "queued": len(tracks_to_import),
                "total_tracked": total_tracked,
                "import_id": import_id,
            }

        except Exception as e:
            error_msg = str(e)
            print(f"Watched artist refresh error for {artist.get('name', artist_id)}: {error_msg}")
            finish_refresh_error(error_msg)
            return {
                "artist_id": artist_id,
                "name": artist.get("name", ""),
                "error": error_msg,
            }


def watched_artist_scheduler():
    """Background thread that periodically checks watched artists for new singles."""
    global _scheduler_running
    _scheduler_running = True

    print(f"Watched artist scheduler started (checking every {WATCHED_PLAYLIST_CHECK_HOURS} hours)")

    # Let the main scheduler go first
    time.sleep(15)
    print("Artist scheduler: Running initial check for overdue artists...")

    while _scheduler_running:
        try:
            print("Artist scheduler: Checking watched artists...")
            with db_conn() as conn:
                conn.row_factory = sqlite3.Row
                artists = conn.execute("""
                    SELECT id, name FROM watched_artists
                    WHERE enabled = 1
                    AND (last_checked IS NULL
                         OR datetime(last_checked, '+' || refresh_interval_hours || ' hours') < datetime('now'))
                """).fetchall()

            if artists:
                print(f"Artist scheduler: Found {len(artists)} artist(s) due for refresh")
                total_new = 0
                for a in artists:
                    result = refresh_watched_artist(a["id"])
                    total_new += result.get("new_tracks", 0)
                print(f"Artist scheduler: Checked {len(artists)} artist(s), {total_new} new track(s) found")
            else:
                print("Artist scheduler: No artists due for refresh")

        except Exception as e:
            print(f"Artist scheduler error: {e}")

        base_sleep_seconds = WATCHED_PLAYLIST_CHECK_HOURS * 3600
        jitter = random.uniform(0.95, 1.05)
        sleep_seconds = max(60, int(base_sleep_seconds * jitter))
        elapsed = 0
        while elapsed < sleep_seconds and _scheduler_running:
            time.sleep(60)
            elapsed += 60


def start_artist_scheduler():
    """Start the watched artist background scheduler if not already running."""
    global _scheduler_running

    if WATCHED_PLAYLIST_CHECK_HOURS <= 0:
        print("Watched artist scheduler disabled (WATCHED_PLAYLIST_CHECK_HOURS=0)")
        return

    if _scheduler_running:
        return

    spawn_daemon_thread(watched_artist_scheduler)
