"""Completed Queue rows are reconciled against the exact file delivered."""

import os
import queue
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db


@pytest.fixture
def fresh_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "final-path.db")
    monkeypatch.setattr(db, "_DB_POOL_SIZE", 4)
    monkeypatch.setattr(db, "_db_pool", queue.LifoQueue(maxsize=4))
    monkeypatch.setattr(db, "_db_pool_created", 0)
    monkeypatch.setattr(db, "_db_pool_lock", threading.Lock())
    db.init_db()
    return db


def _add_completed(job_id, *, final_path=None, override_dir=None):
    with db.db_conn() as conn:
        conn.execute(
            """INSERT INTO jobs
               (id, video_id, artist, title, status, completed_at, final_path,
                override_dir, album_name, album_track_title)
               VALUES (?, 'video', 'Knife Party', 'Ghost Train', 'completed',
                       datetime('now'), ?, ?, 'Lost Souls', 'Ghost Train')""",
            (job_id, final_path, override_dir),
        )
        conn.commit()


def _job(job_id):
    with db.db_conn() as conn:
        return conn.execute(
            "SELECT file_deleted, final_path FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()


def _add_watched_playlist_track(job_id, final_path):
    with db.db_conn() as conn:
        conn.execute(
            """INSERT INTO watched_playlists
               (id, url, name, platform)
               VALUES ('playlist-1', 'https://example.test/list', 'Incoming', 'spotify')"""
        )
        conn.execute(
            """INSERT INTO watched_playlist_tracks
               (playlist_id, track_hash, artist, title, downloaded_at,
                resolved_path, job_id)
               VALUES ('playlist-1', 'knife-party-ghost-train', 'Knife Party',
                       'Ghost Train', datetime('now'), ?, ?)""",
            (final_path, job_id),
        )
        conn.commit()


def _watched_track():
    with db.db_conn() as conn:
        return conn.execute(
            """SELECT downloaded_at, resolved_path
               FROM watched_playlist_tracks
               WHERE playlist_id = 'playlist-1'"""
        ).fetchone()


def test_existing_exact_final_path_keeps_job_completed(fresh_db, tmp_path):
    delivered = tmp_path / "Albums" / "Knife Party" / "Ghost Train.flac"
    delivered.parent.mkdir(parents=True)
    delivered.write_bytes(b"audio")
    _add_completed("exact", final_path=str(delivered))

    assert db.reconcile_deleted_library_files() == (0, 0)
    assert _job("exact") == (0, str(delivered))


def test_missing_exact_path_is_not_replaced_by_a_lookalike(fresh_db, monkeypatch, tmp_path):
    missing = tmp_path / "Albums" / "Knife Party" / "Ghost Train.flac"
    _add_completed("gone", final_path=str(missing))
    import utils
    monkeypatch.setattr(
        utils,
        "check_duplicate",
        lambda *_a, **_k: tmp_path / "Singles" / "Ghost Train.mp3",
    )

    assert db.reconcile_deleted_library_files() == (1, 0)
    assert _job("gone") == (1, str(missing))


def test_missing_handoff_file_preserves_watched_completion(fresh_db, monkeypatch, tmp_path):
    missing = tmp_path / "Incoming" / "Knife Party" / "Ghost Train.flac"
    _add_completed("handed-off", final_path=str(missing))
    _add_watched_playlist_track("handed-off", str(missing))
    monkeypatch.setenv("PRESERVE_WATCHED_DOWNLOAD_HISTORY", "true")

    assert db.reconcile_deleted_library_files() == (1, 0)
    assert _job("handed-off") == (1, str(missing))
    downloaded_at, resolved_path = _watched_track()
    assert downloaded_at is not None
    assert resolved_path is None


def test_missing_watched_file_still_unlinks_by_default(fresh_db, tmp_path):
    missing = tmp_path / "Singles" / "Knife Party" / "Ghost Train.flac"
    _add_completed("deleted", final_path=str(missing))
    _add_watched_playlist_track("deleted", str(missing))

    assert db.reconcile_deleted_library_files() == (1, 1)
    downloaded_at, resolved_path = _watched_track()
    assert downloaded_at is None
    assert resolved_path is None


def test_legacy_album_job_backfills_safe_album_match(fresh_db, monkeypatch, tmp_path):
    delivered = tmp_path / "Albums" / "Knife Party" / "Lost Souls EP" / "01 Ghost Train.flac"
    delivered.parent.mkdir(parents=True)
    delivered.write_bytes(b"audio")
    _add_completed("legacy", override_dir=str(delivered.parent))
    import albums
    monkeypatch.setattr(albums, "find_existing_album_track", lambda *_a, **_k: delivered)

    assert db.reconcile_deleted_library_files() == (0, 0)
    assert _job("legacy") == (0, str(delivered))
