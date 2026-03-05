"""
MusicGrabber - Database Layer

SQLite connection management, schema creation, and job monitoring.
"""

import sqlite3
from contextlib import contextmanager
import queue
import threading
import time
from constants import (
    DB_PATH,
    STALE_JOB_TIMEOUT,
    STALE_JOB_CHECK_INTERVAL,
    LIBRARY_RECONCILE_INTERVAL,
    SEARCH_LOG_RETENTION_DAYS,
    WATCHED_REFRESH_STALE_SECONDS,
)


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
            file_deleted INTEGER DEFAULT 0,
            metadata_source TEXT,
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
        try:
            conn.execute("ALTER TABLE jobs ADD COLUMN search_query TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE jobs ADD COLUMN search_token TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE jobs ADD COLUMN audio_quality TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE jobs ADD COLUMN file_deleted INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE jobs ADD COLUMN metadata_source TEXT")
        except sqlite3.OperationalError:
            pass
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_search_token ON jobs(search_token)")

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
        try:
            conn.execute("ALTER TABLE bulk_imports ADD COLUMN watch_artist_id TEXT")
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
            refresh_state TEXT DEFAULT 'idle',
            refresh_stage TEXT,
            refresh_started_at TIMESTAMP,
            refresh_completed_at TIMESTAMP,
            refresh_error TEXT,
            refresh_import_id TEXT,
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

        # Search history logs for stats
        conn.execute("""
        CREATE TABLE IF NOT EXISTS search_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT NOT NULL,
            artist TEXT,
            result_count INTEGER DEFAULT 0,
            source TEXT DEFAULT 'youtube',
            search_token TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
        try:
            conn.execute("ALTER TABLE search_logs ADD COLUMN search_token TEXT")
        except sqlite3.OperationalError:
            pass
        conn.execute(
            "UPDATE search_logs SET search_token = lower(hex(randomblob(16))) "
            "WHERE search_token IS NULL OR search_token = ''"
        )

        conn.execute("CREATE INDEX IF NOT EXISTS idx_search_logs_created_at ON search_logs(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_search_logs_artist ON search_logs(artist)")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_search_logs_search_token "
            "ON search_logs(search_token) WHERE search_token IS NOT NULL"
        )

        # Settings table - stores configuration that can be edited via UI
        conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

        # Blacklist  -  reported bad tracks and blocked uploaders
        conn.execute("""
        CREATE TABLE IF NOT EXISTS blacklist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id TEXT,
            uploader TEXT,
            source TEXT,
            reason TEXT,
            note TEXT,
            job_id TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_blacklist_video ON blacklist(video_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_blacklist_uploader ON blacklist(uploader, source)")

        # Migration: add uploader column to jobs (raw channel/uploader name)
        try:
            conn.execute("ALTER TABLE jobs ADD COLUMN uploader TEXT")
        except sqlite3.OperationalError:
            pass

        # Migration: add make_m3u to watched_playlists
        try:
            conn.execute("ALTER TABLE watched_playlists ADD COLUMN make_m3u INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass

        # Migration: add use_playlists_dir to watched_playlists and bulk_imports
        try:
            conn.execute("ALTER TABLE watched_playlists ADD COLUMN use_playlists_dir INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE bulk_imports ADD COLUMN use_playlists_dir INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass

        # Migration: sync_mode for watched playlists (append = grow forever, mirror = track removals)
        try:
            conn.execute("ALTER TABLE watched_playlists ADD COLUMN sync_mode TEXT DEFAULT 'append'")
        except sqlite3.OperationalError:
            pass

        # Migration: removed_at for tracked playlist tracks (set when a track vanishes from upstream)
        try:
            conn.execute("ALTER TABLE watched_playlist_tracks ADD COLUMN removed_at TIMESTAMP")
        except sqlite3.OperationalError:
            pass

        # Migration: stale_navidrome_paths - count of dead Navidrome entries found during last M3U rebuild
        try:
            conn.execute("ALTER TABLE watched_playlists ADD COLUMN stale_navidrome_paths INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE watched_playlists ADD COLUMN refresh_state TEXT DEFAULT 'idle'")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE watched_playlists ADD COLUMN refresh_stage TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE watched_playlists ADD COLUMN refresh_started_at TIMESTAMP")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE watched_playlists ADD COLUMN refresh_completed_at TIMESTAMP")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE watched_playlists ADD COLUMN refresh_error TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE watched_playlists ADD COLUMN refresh_import_id TEXT")
        except sqlite3.OperationalError:
            pass

        # Watched artists - artists to monitor for new singles via MusicBrainz
        conn.execute("""
        CREATE TABLE IF NOT EXISTS watched_artists (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            mbid TEXT NOT NULL UNIQUE,
            from_date TEXT NOT NULL,
            refresh_interval_hours INTEGER DEFAULT 24,
            last_checked TIMESTAMP,
            last_track_count INTEGER DEFAULT 0,
            enabled INTEGER DEFAULT 1,
            convert_to_flac INTEGER DEFAULT 1,
            refresh_state TEXT DEFAULT 'idle',
            refresh_stage TEXT,
            refresh_started_at TIMESTAMP,
            refresh_completed_at TIMESTAMP,
            refresh_error TEXT,
            refresh_import_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS watched_artist_tracks (
            artist_id TEXT NOT NULL,
            track_hash TEXT NOT NULL,
            artist TEXT,
            title TEXT,
            release_date TEXT,
            release_mbid TEXT,
            first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            downloaded_at TIMESTAMP,
            job_id TEXT,
            resolved_path TEXT,
            PRIMARY KEY (artist_id, track_hash),
            FOREIGN KEY (artist_id) REFERENCES watched_artists(id) ON DELETE CASCADE
        )
    """)

        conn.execute("CREATE INDEX IF NOT EXISTS idx_watched_artist_tracks_artist ON watched_artist_tracks(artist_id)")

        # Migration: resolved_path - actual on-disk path saved at download time.
        # Sidesteps artist/title lookup mismatches caused by romanisation or
        # metadata normalisation (e.g. Spotify sends '山下達郎', file lands as 'Tatsuro Yamashita').
        try:
            conn.execute("ALTER TABLE watched_playlist_tracks ADD COLUMN resolved_path TEXT")
        except sqlite3.OperationalError:
            pass

        # Multi-user support tables
        conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            is_active INTEGER DEFAULT 1,
            force_password_change INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS download_tokens (
            token TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            job_id TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            used_at TIMESTAMP,
            expires_at TIMESTAMP NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, key),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """)

        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_download_tokens_expires ON download_tokens(expires_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_download_tokens_user ON download_tokens(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_download_tokens_job ON download_tokens(job_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_user_settings_user ON user_settings(user_id)")

        # Multi-user: add user_id to all domain tables
        try:
            conn.execute("ALTER TABLE jobs ADD COLUMN user_id TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE bulk_imports ADD COLUMN user_id TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE watched_playlists ADD COLUMN user_id TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE watched_artists ADD COLUMN user_id TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE blacklist ADD COLUMN user_id TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE search_logs ADD COLUMN user_id TEXT")
        except sqlite3.OperationalError:
            pass

        # --- DB version tracking ---
        # Version is stored in settings as 'db_version' (integer string).
        # Increment when table recreations or other irreversible migrations run.
        db_version_row = conn.execute(
            "SELECT value FROM settings WHERE key = 'db_version'"
        ).fetchone()
        db_version = int(db_version_row[0]) if db_version_row else 0

        # v1: Relax unique constraints on watched_playlists and watched_artists so
        # multiple users can independently watch the same URL / artist.
        # Also scope the search_logs unique index to (user_id, search_token).
        # SQLite can't drop constraints, so we recreate the affected tables.
        if db_version < 1:
            # watched_playlists: url UNIQUE → (user_id, url) UNIQUE
            conn.execute("""
            CREATE TABLE IF NOT EXISTS watched_playlists_new (
                id TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                name TEXT,
                platform TEXT NOT NULL,
                refresh_interval_hours INTEGER DEFAULT 24,
                last_checked TIMESTAMP,
                last_track_count INTEGER DEFAULT 0,
                enabled INTEGER DEFAULT 1,
                convert_to_flac INTEGER DEFAULT 1,
                make_m3u INTEGER DEFAULT 0,
                use_playlists_dir INTEGER DEFAULT 0,
                sync_mode TEXT DEFAULT 'append',
                stale_navidrome_paths INTEGER DEFAULT 0,
                refresh_state TEXT DEFAULT 'idle',
                refresh_stage TEXT,
                refresh_started_at TIMESTAMP,
                refresh_completed_at TIMESTAMP,
                refresh_error TEXT,
                refresh_import_id TEXT,
                user_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, url)
            )
            """)
            conn.execute("""
            INSERT OR IGNORE INTO watched_playlists_new
            SELECT id, url, name, platform, refresh_interval_hours, last_checked,
                   last_track_count, enabled, convert_to_flac,
                   COALESCE(make_m3u, 0),
                   COALESCE(use_playlists_dir, 0),
                   COALESCE(sync_mode, 'append'),
                   COALESCE(stale_navidrome_paths, 0),
                   COALESCE(refresh_state, 'idle'),
                   refresh_stage, refresh_started_at, refresh_completed_at,
                   refresh_error, refresh_import_id,
                   user_id, created_at
            FROM watched_playlists
            """)
            conn.execute("DROP TABLE watched_playlists")
            conn.execute("ALTER TABLE watched_playlists_new RENAME TO watched_playlists")

            # watched_artists: mbid UNIQUE → (user_id, mbid) UNIQUE
            conn.execute("""
            CREATE TABLE IF NOT EXISTS watched_artists_new (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                mbid TEXT NOT NULL,
                from_date TEXT NOT NULL,
                refresh_interval_hours INTEGER DEFAULT 24,
                last_checked TIMESTAMP,
                last_track_count INTEGER DEFAULT 0,
                enabled INTEGER DEFAULT 1,
                convert_to_flac INTEGER DEFAULT 1,
                refresh_state TEXT DEFAULT 'idle',
                refresh_stage TEXT,
                refresh_started_at TIMESTAMP,
                refresh_completed_at TIMESTAMP,
                refresh_error TEXT,
                refresh_import_id TEXT,
                user_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, mbid)
            )
            """)
            conn.execute("""
            INSERT OR IGNORE INTO watched_artists_new
            SELECT id, name, mbid, from_date, refresh_interval_hours, last_checked,
                   last_track_count, enabled, convert_to_flac,
                   COALESCE(refresh_state, 'idle'),
                   refresh_stage, refresh_started_at, refresh_completed_at,
                   refresh_error, refresh_import_id,
                   user_id, created_at
            FROM watched_artists
            """)
            conn.execute("DROP TABLE watched_artists")
            conn.execute("ALTER TABLE watched_artists_new RENAME TO watched_artists")

            # search_logs: drop the global unique index on search_token;
            # uniqueness is now enforced per (user_id, search_token).
            conn.execute("DROP INDEX IF EXISTS idx_search_logs_search_token")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_search_logs_user_token "
                "ON search_logs(user_id, search_token) WHERE search_token IS NOT NULL"
            )

            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('db_version', '1')"
            )
            print("DB migrated to version 1: multi-user unique constraints applied")

        conn.commit()


def cleanup_old_search_logs(retention_days: int = SEARCH_LOG_RETENTION_DAYS) -> int:
    """Delete search log rows older than retention window. Returns deleted row count."""
    with db_conn() as conn:
        cursor = conn.execute(
            "DELETE FROM search_logs WHERE created_at < datetime('now', '-' || ? || ' days')",
            (int(retention_days),)
        )
        deleted = cursor.rowcount
        conn.commit()
        return deleted


def cleanup_stale_jobs():
    """Mark any downloading/queued jobs older than STALE_JOB_TIMEOUT as failed.
    Handles cases where the background task crashed or the container restarted."""
    with db_conn() as conn:
        cursor = conn.execute(
            """UPDATE jobs SET status = 'failed', error = 'Timed out (no progress)',
               completed_at = datetime('now')
               WHERE status IN ('downloading', 'queued')
               AND created_at < datetime('now', ? || ' seconds')""",
            (str(-STALE_JOB_TIMEOUT),)
        )
        if cursor.rowcount > 0:
            print(f"Cleaned up {cursor.rowcount} stale job(s)")
        conn.commit()


def cleanup_stale_watched_refreshes():
    """Mark stuck watched playlist/artist refresh states as failed."""
    stale_arg = (str(WATCHED_REFRESH_STALE_SECONDS),)
    stale_sql = (
        "SET refresh_state = 'error', refresh_stage = 'failed',"
        " refresh_error = 'Refresh timed out (process interrupted)',"
        " refresh_completed_at = datetime('now')"
        " WHERE refresh_state = 'running'"
        " AND refresh_started_at IS NOT NULL"
        " AND refresh_started_at < datetime('now', '-' || ? || ' seconds')"
    )
    with db_conn() as conn:
        p = conn.execute(f"UPDATE watched_playlists {stale_sql}", stale_arg)
        a = conn.execute(f"UPDATE watched_artists {stale_sql}", stale_arg)
        total = p.rowcount + a.rowcount
        if total > 0:
            print(f"Cleared {total} stale watched refresh state(s)")
        conn.commit()


def reconcile_deleted_library_files(batch_size: int = 500) -> tuple[int, int]:
    """Mark completed jobs as deleted when their files no longer exist.

    This keeps `file_deleted` and watched playlist track state in sync even when
    files are removed or renamed directly on disk (outside MusicGrabber APIs).

    Returns (jobs_marked_deleted, watched_rows_unlinked).
    """
    # Local import avoids circular import: utils -> settings -> db
    from utils import check_duplicate

    with db_conn() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT id, artist, title
               FROM jobs
               WHERE status IN ('completed', 'completed_with_errors')
                 AND COALESCE(file_deleted, 0) = 0
                 AND artist IS NOT NULL AND artist != ''
                 AND title IS NOT NULL AND title != ''
               ORDER BY completed_at DESC, created_at DESC
               LIMIT ?""",
            (int(batch_size),)
        ).fetchall()

    if not rows:
        return 0, 0

    # Grab any stored resolved_paths in one shot so we can check those first.
    # check_duplicate only walks Singles; playlist-folder tracks live elsewhere.
    job_ids = [r["id"] for r in rows]
    with db_conn() as conn:
        conn.row_factory = sqlite3.Row
        placeholders = ",".join("?" * len(job_ids))
        rp_rows = conn.execute(
            f"SELECT job_id, resolved_path FROM watched_playlist_tracks WHERE job_id IN ({placeholders}) AND resolved_path IS NOT NULL",
            job_ids,
        ).fetchall()
    resolved_paths: dict[str, str] = {r["job_id"]: r["resolved_path"] for r in rp_rows}

    stale_ids: list[str] = []
    for row in rows:
        job_id = row["id"]
        # Prefer the stored resolved_path (covers playlist folders).
        rp = resolved_paths.get(job_id)
        if rp:
            from pathlib import Path as _Path
            if _Path(rp).is_absolute() and _Path(rp).exists():
                continue  # File is right where we left it
        # Fall back to walking Singles layout.
        if not check_duplicate(row["artist"], row["title"]):
            stale_ids.append(job_id)

    if not stale_ids:
        return 0, 0

    with db_conn() as conn:
        conn.executemany(
            "UPDATE jobs SET file_deleted = 1 WHERE id = ?",
            [(jid,) for jid in stale_ids],
        )
        watched_rows = 0
        for jid in stale_ids:
            cursor = conn.execute(
                """UPDATE watched_playlist_tracks
                   SET downloaded_at = NULL,
                       resolved_path = NULL
                   WHERE job_id = ?
                     AND downloaded_at IS NOT NULL""",
                (jid,),
            )
            watched_rows += cursor.rowcount
            cursor = conn.execute(
                """UPDATE watched_artist_tracks
                   SET downloaded_at = NULL,
                       resolved_path = NULL
                   WHERE job_id = ?
                     AND downloaded_at IS NOT NULL""",
                (jid,),
            )
            watched_rows += cursor.rowcount
        conn.commit()

    print(
        f"Library reconcile: marked {len(stale_ids)} job(s) as deleted, "
        f"unlinked {watched_rows} watched track row(s)"
    )
    return len(stale_ids), watched_rows


def _stale_job_monitor():
    """Background thread that periodically checks for stale jobs."""
    last_reconcile = 0.0
    while True:
        time.sleep(STALE_JOB_CHECK_INTERVAL)
        try:
            cleanup_stale_jobs()
            cleanup_stale_watched_refreshes()
            if LIBRARY_RECONCILE_INTERVAL > 0:
                now = time.time()
                if now - last_reconcile >= LIBRARY_RECONCILE_INTERVAL:
                    reconcile_deleted_library_files()
                    last_reconcile = now
            cleanup_old_search_logs(SEARCH_LOG_RETENTION_DAYS)
            # Bin any sessions that have outstayed their welcome
            from auth import cleanup_expired_download_tokens, cleanup_expired_sessions
            cleanup_expired_sessions()
            cleanup_expired_download_tokens()
            # While we're here, evict any expired YouTube cookies so they don't
            # silently rot in settings causing mysterious 403s
            from youtube import clear_expired_cookies
            clear_expired_cookies()
        except Exception as e:
            print(f"Stale job monitor error: {e}")


def start_stale_job_monitor():
    """Run stale job cleanup at startup and start periodic monitor."""
    cleanup_stale_jobs()
    cleanup_stale_watched_refreshes()
    reconcile_deleted_library_files()
    from auth import cleanup_expired_download_tokens, cleanup_expired_sessions
    cleanup_expired_sessions()
    cleanup_expired_download_tokens()
    _stale_monitor_thread = threading.Thread(target=_stale_job_monitor, daemon=True)
    _stale_monitor_thread.start()


# ---------------------------------------------------------------------------
# Blacklist helpers  -  kept close to the DB layer for easy reuse
# ---------------------------------------------------------------------------

def get_blacklisted_video_ids() -> set[str]:
    """Return all blacklisted video IDs (any source)."""
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT video_id FROM blacklist WHERE video_id IS NOT NULL AND video_id != ''"
        ).fetchall()
    return {r[0] for r in rows}


def get_blacklisted_uploaders(source: str) -> set[str]:
    """Return lowercased uploader names blacklisted for a given source."""
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT lower(uploader) FROM blacklist "
            "WHERE uploader IS NOT NULL AND uploader != '' AND source = ?",
            (source,)
        ).fetchall()
    return {r[0] for r in rows}


def is_video_blacklisted(video_id: str) -> bool:
    """Quick check for a single video ID."""
    if not video_id:
        return False
    with db_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM blacklist WHERE video_id = ? LIMIT 1",
            (video_id,)
        ).fetchone()
    return row is not None
