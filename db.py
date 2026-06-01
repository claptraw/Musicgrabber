"""
MusicGrabber - Database Layer

SQLite connection management, schema creation, and job monitoring.
"""

import json
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
    MONOCHROME_HIFI_API_URL,
    MONOCHROME_QOBUZ_PROXY_URL,
)


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


_DB_POOL_SIZE = 8
_db_pool: "queue.LifoQueue[sqlite3.Connection]" = queue.LifoQueue(maxsize=_DB_POOL_SIZE)


def _get_pooled_conn() -> sqlite3.Connection:
    try:
        # Try to grab an idle connection first.
        return _db_pool.get_nowait()
    except queue.Empty:
        pass
    # Pool is exhausted — either wait for one to come back (up to 15s) or
    # open a fresh connection as a safety valve. Under heavy concurrent load
    # (bulk import + multiple download threads) spawning unlimited connections
    # causes them to queue up and fight over the single WAL write lock, which
    # is what produces "database is locked" crashes. Blocking here serialises
    # checkout instead of flooding sqlite with competing writers.
    try:
        return _db_pool.get(timeout=15)
    except queue.Empty:
        # Genuinely exhausted after 15s — open a new one rather than hang forever.
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
            slskd_size INTEGER,
            convert_to_flac INTEGER DEFAULT 1,
            source_url TEXT,
            file_deleted INTEGER DEFAULT 0,
            metadata_source TEXT,
            override_dir TEXT,
            album_release_mbid TEXT,
            album_name TEXT,
            album_track_title TEXT,
            album_track_number INTEGER,
            album_track_total INTEGER,
            skip_mismatch_check INTEGER DEFAULT 0,
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
            conn.execute("ALTER TABLE jobs ADD COLUMN slskd_size INTEGER")
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
        try:
            conn.execute("ALTER TABLE jobs ADD COLUMN override_dir TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE jobs ADD COLUMN album_release_mbid TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE jobs ADD COLUMN album_name TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE jobs ADD COLUMN album_track_title TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE jobs ADD COLUMN album_track_number INTEGER")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE jobs ADD COLUMN album_track_total INTEGER")
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
            use_playlists_dir INTEGER DEFAULT 0,
            watch_artist_id TEXT,
            user_id TEXT,
            preferred_sources TEXT DEFAULT 'all',
            priority_source TEXT,
            override_dir TEXT,
            album_release_mbid TEXT,
            album_total_tracks INTEGER,
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
        try:
            conn.execute("ALTER TABLE bulk_imports ADD COLUMN album_total_tracks INTEGER")
        except sqlite3.OperationalError:
            pass

        # Watched playlists - playlists to monitor for new tracks
        conn.execute("""
        CREATE TABLE IF NOT EXISTS watched_playlists (
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
            preferred_sources TEXT DEFAULT 'all',
            priority_source TEXT,
            lb_username TEXT,
            refresh_state TEXT DEFAULT 'idle',
            refresh_stage TEXT,
            refresh_started_at TIMESTAMP,
            refresh_completed_at TIMESTAMP,
            refresh_error TEXT,
            refresh_import_id TEXT,
            gone_strikes INTEGER DEFAULT 0,
            auto_paused INTEGER DEFAULT 0,
            pause_reason TEXT,
            user_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, url)
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

        # Track upgrades  -  the Phase 1 scan caches one row per eligible (our-tagged)
        # file. The filesystem stays the source of truth; this is just a cache keyed by
        # (user_id, path) and refreshed on every scan. mtime lets re-scans skip
        # unchanged files. dismissed_mtime records the file mtime at the moment the user
        # dismissed it, so a later edit to the file lapses the dismissal automatically.
        conn.execute("""
        CREATE TABLE IF NOT EXISTS upgrade_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL DEFAULT '',
            path TEXT NOT NULL,
            mtime REAL,
            file_size INTEGER,
            codec TEXT,
            bitrate_kbps INTEGER,
            duration REAL,
            file_tier INTEGER,
            target_tier INTEGER,
            below_target INTEGER DEFAULT 0,
            source TEXT,
            artist TEXT,
            title TEXT,
            dismissed INTEGER DEFAULT 0,
            dismissed_mtime REAL,
            upgrade_state TEXT DEFAULT 'none',
            last_scanned TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, path)
        )
    """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_upgrade_below ON upgrade_candidates(user_id, below_target, dismissed)")

        # Phase 2: per-candidate search cache. Populated on demand when the user views the
        # Watched Upgrades page; found_at gives the TTL so revisits don't re-hammer sources.
        # found_searched distinguishes "searched, nothing better" from "not searched yet".
        for _col, _decl in [
            ("found_at", "TIMESTAMP"),
            ("found_searched", "INTEGER DEFAULT 0"),
            ("found_source", "TEXT"),
            ("found_quality", "TEXT"),
            ("found_tier", "INTEGER"),
            ("found_confidence", "REAL"),
            ("found_verified", "INTEGER DEFAULT 0"),
            ("found_video_id", "TEXT"),
            ("found_source_url", "TEXT"),
            ("found_slskd_username", "TEXT"),
            ("found_slskd_filename", "TEXT"),
            ("found_slskd_size", "INTEGER"),
        ]:
            try:
                conn.execute(f"ALTER TABLE upgrade_candidates ADD COLUMN {_col} {_decl}")
            except sqlite3.OperationalError:
                pass

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
        try:
            # ListenBrainz "Created for You" playlists rotate weekly — store the username so we can
            # re-resolve the current week's UUID when the pinned one goes stale.
            conn.execute("ALTER TABLE watched_playlists ADD COLUMN lb_username TEXT")
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

        # Migration: track position within the source playlist for correct M3U ordering
        try:
            conn.execute("ALTER TABLE watched_playlist_tracks ADD COLUMN position INTEGER")
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
        try:
            conn.execute("ALTER TABLE watched_playlists ADD COLUMN preferred_sources TEXT DEFAULT 'all'")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE bulk_imports ADD COLUMN preferred_sources TEXT DEFAULT 'all'")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE watched_playlists ADD COLUMN priority_source TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE bulk_imports ADD COLUMN priority_source TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE bulk_imports ADD COLUMN override_dir TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE bulk_imports ADD COLUMN album_release_mbid TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE watched_playlists ADD COLUMN custom_subdir TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE bulk_imports ADD COLUMN custom_subdir TEXT")
        except sqlite3.OperationalError:
            pass

        try:
            conn.execute("ALTER TABLE jobs ADD COLUMN skip_mismatch_check INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE jobs ADD COLUMN progress_stage TEXT")
        except sqlite3.OperationalError:
            pass

        # Watched track match mismatches  -  persistent audit log so we can spot
        # normalisation gaps without relying on Docker log retention.
        conn.execute("""
        CREATE TABLE IF NOT EXISTS watched_match_mismatches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT,
            playlist_id TEXT,
            expected_artist TEXT,
            expected_title TEXT,
            actual_artist TEXT,
            actual_title TEXT,
            exp_normalised TEXT,
            got_normalised TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mismatches_created ON watched_match_mismatches(created_at)"
        )

        # Search decisions  -  records why the scorer picked a particular candidate
        # over its rivals for automated downloads (bulk import, watched playlists).
        # Only populated for automated searches; manual picks don't need justification.
        conn.execute("""
        CREATE TABLE IF NOT EXISTS search_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT,
            query TEXT,
            decision_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_search_decisions_job ON search_decisions(job_id)"
        )

        # Album track locks  -  records which artist/title combinations are actively
        # being processed for album downloads.  Persists across failures so retries
        # still bypass dupe check.  Cleared only on success or by the stale monitor.
        conn.execute("""
        CREATE TABLE IF NOT EXISTS album_track_locks (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            release_mbid TEXT,
            album_name   TEXT NOT NULL,
            album_artist TEXT NOT NULL,
            track_title  TEXT NOT NULL COLLATE NOCASE,
            status       TEXT NOT NULL DEFAULT 'pending',
            job_id       TEXT,
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP
        )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_atl_track "
            "ON album_track_locks (album_artist, track_title)"
        )
        # Without this unique index the ON CONFLICT(release_mbid, track_title)
        # clause in upsert_album_track_lock() has nothing to conflict against,
        # and SQLite refuses the statement entirely. NULL release_mbids are still
        # allowed to repeat (SQLite treats NULLs as distinct in unique indexes),
        # which is exactly what we want for the folder-only routing branch.
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_atl_release_track "
            "ON album_track_locks (release_mbid, track_title)"
        )

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
                preferred_sources TEXT DEFAULT 'all',
                lb_username TEXT,
                custom_subdir TEXT,
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
            # Older DBs may not have custom_subdir yet (the ALTER above adds it),
            # but we still SELECT it explicitly so the column survives the copy.
            # If somehow it's missing on the source table, fall back to NULL.
            try:
                conn.execute("""
                INSERT OR IGNORE INTO watched_playlists_new
                SELECT id, url, name, platform, refresh_interval_hours, last_checked,
                       last_track_count, enabled, convert_to_flac,
                       COALESCE(make_m3u, 0),
                       COALESCE(use_playlists_dir, 0),
                       COALESCE(sync_mode, 'append'),
                       COALESCE(stale_navidrome_paths, 0),
                       COALESCE(preferred_sources, 'all'),
                       lb_username,
                       custom_subdir,
                       COALESCE(refresh_state, 'idle'),
                       refresh_stage, refresh_started_at, refresh_completed_at,
                       refresh_error, refresh_import_id,
                       user_id, created_at
                FROM watched_playlists
                """)
            except sqlite3.OperationalError:
                # Source table lacks custom_subdir for some reason; copy without it.
                conn.execute("""
                INSERT OR IGNORE INTO watched_playlists_new
                SELECT id, url, name, platform, refresh_interval_hours, last_checked,
                       last_track_count, enabled, convert_to_flac,
                       COALESCE(make_m3u, 0),
                       COALESCE(use_playlists_dir, 0),
                       COALESCE(sync_mode, 'append'),
                       COALESCE(stale_navidrome_paths, 0),
                       COALESCE(preferred_sources, 'all'),
                       lb_username,
                       NULL,
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

        # v2: Seed Monochrome URL settings for users upgrading across the removal/re-addition
        # gap. Anyone with an empty or missing value gets the current defaults; anyone who
        # deliberately changed them keeps their value (UPDATE ... WHERE value = '' only).
        if db_version < 2:
            for key, default in (
                ("monochrome_hifi_api_url", MONOCHROME_HIFI_API_URL),
                ("monochrome_qobuz_proxy_url", MONOCHROME_QOBUZ_PROXY_URL),
            ):
                conn.execute("""
                    INSERT INTO settings (key, value, updated_at)
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(key) DO UPDATE SET value = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE value = '' OR value IS NULL
                """, (key, default, default))
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('db_version', '2')"
            )
            print("DB migrated to version 2: Monochrome URL defaults seeded")

        # v3: The old qobuz.kennyy.com.br proxy went dark. The new official proxy
        # (qdl-api.monochrome.tf) speaks the exact same API, so anyone still pointed
        # at the dead host gets transparently migrated. Custom self-hosted URLs are
        # untouched, because the WHERE clause only matches the specific dead host.
        if db_version < 3:
            conn.execute("""
                UPDATE settings
                SET value = 'https://qdl-api.monochrome.tf',
                    updated_at = CURRENT_TIMESTAMP
                WHERE key = 'monochrome_qobuz_proxy_url'
                  AND value = 'https://qobuz.kennyy.com.br'
            """)
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('db_version', '3')"
            )
            print("DB migrated to version 3: stale kennyy Qobuz proxy URL retired")

        # v4: The eu-central Render node was suspended by its owner, so hifi-api lookups
        # via that hostname now return 503. The apex api.monochrome.tf is CDN-routed and
        # picks a healthy node automatically; switch anyone still pinned to the dead one.
        if db_version < 4:
            conn.execute("""
                UPDATE settings
                SET value = 'https://api.monochrome.tf',
                    updated_at = CURRENT_TIMESTAMP
                WHERE key = 'monochrome_hifi_api_url'
                  AND value = 'https://eu-central.monochrome.tf'
            """)
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('db_version', '4')"
            )
            print("DB migrated to version 4: stale eu-central hifi-api URL retired")

        # v5: The apex hifi-api endpoint also moved to a Render suspension page
        # in May 2026. Monochrome's live frontend still advertises the Samidy
        # instance, and it returns Tidal search metadata for the same API shape.
        if db_version < 5:
            conn.execute("""
                UPDATE settings
                SET value = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE key = 'monochrome_hifi_api_url'
                  AND value IN ('https://api.monochrome.tf', 'https://eu-central.monochrome.tf')
            """, (MONOCHROME_HIFI_API_URL,))
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('db_version', '5')"
            )
            print("DB migrated to version 5: suspended Monochrome hifi-api URL retired")

        # v6: Rebuild every user's hifi-api URL list from scratch. Most of the
        # known public instances are now dead (api/eu-central suspended, entire
        # qqdl.site cluster down). Rather than matching exact strings we just
        # strip confirmed-dead entries from whatever the user had, then prepend
        # the current live defaults. Custom self-hosted URLs survive if they're
        # not on the dead list.
        if db_version < 6:
            import re as _re
            _DEAD_HIFI_URLS = {
                "https://api.monochrome.tf",
                "https://eu-central.monochrome.tf",
                "https://hifi.geeked.wtf",
                "https://maus.qqdl.site",
                "https://vogel.qqdl.site",
                "https://katze.qqdl.site",
                "https://hund.qqdl.site",
                "https://wolf.qqdl.site",
                "https://tidal.kinoplus.online",
                "https://mono.scavengerfurs.net",
            }
            _NEW_LIVE = ["https://us-west.monochrome.tf", "https://monochrome-api.samidy.com"]

            row = conn.execute(
                "SELECT value FROM settings WHERE key = 'monochrome_hifi_api_url'"
            ).fetchone()
            current = (row[0] if row else "") or ""

            existing = []
            seen_e = set()
            for part in _re.split(r"[\s,]+", current):
                url = part.strip().rstrip("/")
                if not url or not _re.match(r"https?://", url, _re.I) or url in seen_e:
                    continue
                seen_e.add(url)
                existing.append(url)

            # Surviving custom URLs: not dead, not already in the new live defaults
            custom = [u for u in existing if u not in _DEAD_HIFI_URLS and u not in _NEW_LIVE]
            new_value = ",".join(_NEW_LIVE + custom)

            conn.execute("""
                INSERT INTO settings (key, value, updated_at)
                VALUES ('monochrome_hifi_api_url', ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                               updated_at = excluded.updated_at
            """, (new_value,))
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('db_version', '6')"
            )
            print(f"DB migrated to version 6: Monochrome hifi-api URLs rebuilt → {new_value}")

        # v7: The qdl-api.monochrome.tf Qobuz proxy expired its Qobuz credentials
        # (returns HTTP 400 wrapping a Qobuz 401). Two community proxies that speak
        # the same API are live: kennyy.com.br and mono.scavengerfurs.net.
        # We prepend these to whatever the user had, keeping any custom self-hosted
        # URLs, and strip the now-dead qdl-api entry from front-runner position.
        if db_version < 7:
            import re as _re7
            _DEAD_QOBUZ_URLS = {"https://qdl-api.monochrome.tf"}
            _NEW_QOBUZ_LIVE = [
                "https://qobuz.kennyy.com.br",
                "https://mono.scavengerfurs.net",
                "https://qdl-api.monochrome.tf",  # keep at the back, may recover
            ]

            row7 = conn.execute(
                "SELECT value FROM settings WHERE key = 'monochrome_qobuz_proxy_url'"
            ).fetchone()
            current7 = (row7[0] if row7 else "") or ""

            existing7 = []
            seen7: set[str] = set()
            for part in _re7.split(r"[\s,]+", current7):
                url = part.strip().rstrip("/")
                if not url or not _re7.match(r"https?://", url, _re7.I) or url in seen7:
                    continue
                seen7.add(url)
                existing7.append(url)

            # Custom URLs: anything that isn't one of our known public proxies
            custom7 = [u for u in existing7 if u not in {
                "https://qdl-api.monochrome.tf",
                "https://qobuz.kennyy.com.br",
                "https://mono.scavengerfurs.net",
            }]
            new_qobuz_value = ",".join(_NEW_QOBUZ_LIVE + custom7)

            conn.execute("""
                INSERT INTO settings (key, value, updated_at)
                VALUES ('monochrome_qobuz_proxy_url', ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                               updated_at = excluded.updated_at
            """, (new_qobuz_value,))
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('db_version', '7')"
            )
            print(f"DB migrated to version 7: Qobuz proxy list rebuilt → {new_qobuz_value}")

        # v8: Track when a watched playlist keeps coming back "not found" so we can
        # auto-pause vanished playlists with a note instead of retrying forever.
        if db_version < 8:
            for _col8, _ddl8 in (
                ("gone_strikes", "ALTER TABLE watched_playlists ADD COLUMN gone_strikes INTEGER DEFAULT 0"),
                ("auto_paused", "ALTER TABLE watched_playlists ADD COLUMN auto_paused INTEGER DEFAULT 0"),
                ("pause_reason", "ALTER TABLE watched_playlists ADD COLUMN pause_reason TEXT"),
            ):
                try:
                    conn.execute(_ddl8)
                except sqlite3.OperationalError:
                    pass  # Column already present (fresh DB built from the new schema)
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('db_version', '8')"
            )
            print("DB migrated to version 8: watched playlists gain gone-strike auto-pause tracking")

        # Defensive backstop: early dev builds of v1 silently dropped custom_subdir
        # when recreating watched_playlists. Re-add it for any DB that already
        # passed through that mangled migration. Harmless if the column is present.
        try:
            conn.execute("ALTER TABLE watched_playlists ADD COLUMN custom_subdir TEXT")
        except sqlite3.OperationalError:
            pass

        # Same shape as the custom_subdir backstop: the v1 table recreate predates
        # priority_source, so a fresh DB initialised in one go loses the column.
        # Idempotent; harmless on DBs that already have it.
        try:
            conn.execute("ALTER TABLE watched_playlists ADD COLUMN priority_source TEXT")
        except sqlite3.OperationalError:
            pass

        conn.commit()


def upsert_album_track_lock(
    release_mbid: str | None,
    album_name: str,
    album_artist: str,
    track_title: str,
    job_id: str,
) -> None:
    """Insert or update an album track lock to mark a track as in-flight.

    Safe to call multiple times for the same track; subsequent calls just update
    the job_id and bump status to 'downloading'.
    """
    with db_conn() as conn:
        if release_mbid:
            conn.execute(
                """INSERT INTO album_track_locks
                   (release_mbid, album_name, album_artist, track_title, status, job_id)
                   VALUES (?, ?, ?, ?, 'downloading', ?)
                   ON CONFLICT(release_mbid, track_title)
                   DO UPDATE SET status = 'downloading', job_id = excluded.job_id""",
                (release_mbid, album_name, album_artist, track_title, job_id),
            )
        else:
            # No MBID — folder-only routing.  Check for an existing pending row first
            # to avoid stacking up duplicates from rapid retries.
            existing = conn.execute(
                "SELECT id FROM album_track_locks "
                "WHERE release_mbid IS NULL AND album_name = ? AND track_title = ?",
                (album_name, track_title),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE album_track_locks SET status = 'downloading', job_id = ? "
                    "WHERE id = ?",
                    (job_id, existing[0]),
                )
            else:
                conn.execute(
                    """INSERT INTO album_track_locks
                       (release_mbid, album_name, album_artist, track_title, status, job_id)
                       VALUES (NULL, ?, ?, ?, 'downloading', ?)""",
                    (album_name, album_artist, track_title, job_id),
                )
        conn.commit()


def complete_album_track_lock(
    release_mbid: str | None,
    track_title: str,
    album_name: str,
) -> None:
    """Mark an album track lock as completed once the file is safely on disk."""
    with db_conn() as conn:
        if release_mbid:
            conn.execute(
                "UPDATE album_track_locks SET status = 'completed', completed_at = datetime('now') "
                "WHERE release_mbid = ? AND track_title = ?",
                (release_mbid, track_title),
            )
        else:
            conn.execute(
                "UPDATE album_track_locks SET status = 'completed', completed_at = datetime('now') "
                "WHERE release_mbid IS NULL AND album_name = ? AND track_title = ?",
                (album_name, track_title),
            )
        conn.commit()


def get_album_track_lock(
    release_mbid: str | None,
    track_title: str | None,
    album_name: str | None,
) -> dict | None:
    """Return the lock row for an album track, or None if no active lock exists."""
    if not track_title:
        return None
    with db_conn() as conn:
        conn.row_factory = sqlite3.Row
        if release_mbid:
            row = conn.execute(
                "SELECT * FROM album_track_locks WHERE release_mbid = ? AND track_title = ?",
                (release_mbid, track_title),
            ).fetchone()
        elif album_name:
            row = conn.execute(
                "SELECT * FROM album_track_locks "
                "WHERE release_mbid IS NULL AND album_name = ? AND track_title = ?",
                (album_name, track_title),
            ).fetchone()
        else:
            return None
    return dict(row) if row else None


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
               progress_stage = NULL, completed_at = datetime('now')
               WHERE status IN ('downloading', 'queued')
               AND created_at < datetime('now', ? || ' seconds')""",
            (str(-STALE_JOB_TIMEOUT),)
        )
        if cursor.rowcount > 0:
            print(f"Cleaned up {cursor.rowcount} stale job(s)")

        # Evict album track locks that never reached completed status  -  these are
        # orphans from crashed workers or abandoned imports.  24-hour threshold gives
        # plenty of headroom for slow downloads without leaving ghosts forever.
        lock_cursor = conn.execute(
            "DELETE FROM album_track_locks "
            "WHERE status != 'completed' AND created_at < datetime('now', '-24 hours')"
        )
        if lock_cursor.rowcount > 0:
            print(f"Evicted {lock_cursor.rowcount} stale album track lock(s)")

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

def log_match_mismatch(
    job_id: str,
    playlist_id: str,
    expected_artist: str,
    expected_title: str,
    actual_artist: str,
    actual_title: str,
    exp_normalised: str,
    got_normalised: str,
) -> None:
    """Persist a watched-track mismatch so it survives container restarts."""
    try:
        with db_conn() as conn:
            conn.execute(
                """INSERT INTO watched_match_mismatches
                   (job_id, playlist_id, expected_artist, expected_title,
                    actual_artist, actual_title, exp_normalised, got_normalised)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (job_id, playlist_id, expected_artist, expected_title,
                 actual_artist, actual_title, exp_normalised, got_normalised),
            )
            conn.commit()
    except Exception as e:
        print(f"Failed to log mismatch: {e}")


def save_search_decision(
    job_id: str,
    query: str,
    selected: dict,
    runners_up: list[dict],
) -> None:
    """Persist the scoring rationale for an automated search decision.

    Stores the winning candidate and top runners-up so users can later
    understand why the scorer picked one result over another, without
    having to tail Docker logs like some sort of animal.
    """
    try:
        blob = json.dumps({
            "selected": selected,
            "runners_up": runners_up[:3],
        })
        with db_conn() as conn:
            conn.execute(
                """INSERT INTO search_decisions (job_id, query, decision_json)
                   VALUES (?, ?, ?)""",
                (job_id, query, blob),
            )
            conn.commit()
    except Exception as e:
        print(f"Failed to save search decision: {e}")


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
