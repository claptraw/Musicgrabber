"""
MusicGrabber - Database Layer

SQLite connection management, schema creation, and job monitoring.
"""

import sqlite3
from contextlib import contextmanager
import queue
import threading
import time
from datetime import datetime

from constants import DB_PATH, STALE_JOB_TIMEOUT, STALE_JOB_CHECK_INTERVAL


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


_DB_POOL_SIZE = 5
_db_pool: "queue.LifoQueue[sqlite3.Connection]" = queue.LifoQueue(maxsize=_DB_POOL_SIZE)


def _get_pooled_conn() -> sqlite3.Connection:
    try:
        return _db_pool.get_nowait()
    except queue.Empty:
        return get_db()


def _return_pooled_conn(conn: sqlite3.Connection) -> None:
    try:
        _db_pool.put_nowait(conn)
    except queue.Full:
        conn.close()


@contextmanager
def db_conn() -> sqlite3.Connection:
    conn = _get_pooled_conn()
    try:
        yield conn
        if conn.in_transaction:
            try:
                conn.rollback()
            except sqlite3.Error:
                pass
    except Exception:
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        raise
    finally:
        conn.row_factory = None
        _return_pooled_conn(conn)


def init_db():
    with db_conn() as conn:
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
            failed_tracks INTEGER DEFAULT 0,
            skipped_tracks INTEGER DEFAULT 0,
            m3u_path TEXT,
            source TEXT DEFAULT 'youtube',
            slskd_username TEXT,
            slskd_filename TEXT,
            convert_to_flac INTEGER DEFAULT 1,
            source_url TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP
        )
    """)

        try:
            conn.execute("ALTER TABLE jobs ADD COLUMN source TEXT DEFAULT 'youtube'")
        except sqlite3.OperationalError:
            pass  # Column already exists
        try:
            conn.execute("ALTER TABLE jobs ADD COLUMN slskd_username TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE jobs ADD COLUMN slskd_filename TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE jobs ADD COLUMN convert_to_flac INTEGER DEFAULT 1")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE jobs ADD COLUMN source_url TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE jobs ADD COLUMN failed_tracks INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE jobs ADD COLUMN skipped_tracks INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass

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
            watch_playlist_id TEXT,
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

        try:
            conn.execute("ALTER TABLE bulk_imports ADD COLUMN watch_playlist_id TEXT")
        except sqlite3.OperationalError:
            pass

        # Watched playlists - playlists to monitor for new tracks
        conn.execute("""
        CREATE TABLE IF NOT EXISTS watched_playlists (
            id TEXT PRIMARY KEY,
            url TEXT NOT NULL UNIQUE,
            name TEXT,
            platform TEXT NOT NULL,
            refresh_interval_hours INTEGER DEFAULT 24,
            last_checked TIMESTAMP,
            last_track_count INTEGER DEFAULT 0,
            enabled INTEGER DEFAULT 1,
            convert_to_flac INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

        # Tracks seen in watched playlists (for detecting new additions)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS watched_playlist_tracks (
            playlist_id TEXT NOT NULL,
            track_hash TEXT NOT NULL,
            artist TEXT,
            title TEXT,
            first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            downloaded_at TIMESTAMP,
            job_id TEXT,
            PRIMARY KEY (playlist_id, track_hash),
            FOREIGN KEY (playlist_id) REFERENCES watched_playlists(id) ON DELETE CASCADE
        )
    """)

        conn.execute("CREATE INDEX IF NOT EXISTS idx_watched_tracks_playlist ON watched_playlist_tracks(playlist_id)")

        # Settings table - stores configuration that can be edited via UI
        conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

        conn.commit()


def cleanup_stale_jobs():
    """Mark any downloading/queued jobs older than STALE_JOB_TIMEOUT as failed.
    Handles cases where the background task crashed or the container restarted."""
    with db_conn() as conn:
        cutoff = datetime.now().timestamp() - STALE_JOB_TIMEOUT
        cursor = conn.execute(
            """UPDATE jobs SET status = 'failed', error = 'Timed out (no progress)',
               completed_at = ?
               WHERE status IN ('downloading', 'queued')
               AND created_at < ?""",
            (datetime.now().isoformat(), datetime.fromtimestamp(cutoff).isoformat())
        )
        if cursor.rowcount > 0:
            print(f"Cleaned up {cursor.rowcount} stale job(s)")
        conn.commit()


def _stale_job_monitor():
    """Background thread that periodically checks for stale jobs."""
    while True:
        time.sleep(STALE_JOB_CHECK_INTERVAL)
        try:
            cleanup_stale_jobs()
        except Exception as e:
            print(f"Stale job monitor error: {e}")


def start_stale_job_monitor():
    """Run stale job cleanup at startup and start periodic monitor."""
    cleanup_stale_jobs()
    _stale_monitor_thread = threading.Thread(target=_stale_job_monitor, daemon=True)
    _stale_monitor_thread.start()
