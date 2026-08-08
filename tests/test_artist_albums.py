"""Watched-artist album following ("automatically add new albums").

The one rule that matters: switching the toggle on must never trigger a
back-catalogue avalanche. Enabling seeds the current album list as already-seen
without queueing anything; only albums that turn up on a later refresh count as
new. MusicBrainz is fully mocked here, no network involved.

Real schema via db.init_db() against a throwaway database; no server, no
network. Mirrors the style of test_watched_counts.py.
"""

import os
import queue
import sqlite3
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db


@pytest.fixture
def fresh_db(monkeypatch, tmp_path):
    """A real MusicGrabber schema on a throwaway file, with a cold pool."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "artist_albums.db")
    monkeypatch.setattr(db, "_db_pool", queue.LifoQueue(maxsize=db._DB_POOL_SIZE))
    monkeypatch.setattr(db, "_db_pool_created", 0)
    monkeypatch.setattr(db, "_db_pool_lock", threading.Lock())
    db.init_db()
    return db.db_conn


def _seed_artist(db_conn, artist_id, name, mbid, auto_add_albums=0):
    """Insert a bare-bones watched_artists row, same shape the app would create."""
    with db_conn() as conn:
        conn.execute(
            """INSERT INTO watched_artists (id, name, mbid, from_date, auto_add_albums)
               VALUES (?, ?, ?, '2000-01-01', ?)""",
            (artist_id, name, mbid, auto_add_albums),
        )
        conn.commit()


def _album_rows(db_conn, artist_id="a1"):
    with db_conn() as conn:
        conn.row_factory = sqlite3.Row
        return {
            r["release_mbid"]: dict(r)
            for r in conn.execute(
                "SELECT * FROM watched_artist_albums WHERE artist_id = ?", (artist_id,)
            ).fetchall()
        }


def test_enabling_toggle_seeds_without_queueing(fresh_db, monkeypatch):
    """seed_artist_albums must record every current album as 'seen' and queue nothing."""
    import watched_artists as wa

    _seed_artist(fresh_db, "a1", "Radiohead", "mbid-1")
    monkeypatch.setattr(wa, "fetch_artist_albums", lambda mbid: [
        {"title": "OK Computer", "year": "1997", "release_mbid": "rel-1"},
        {"title": "Kid A", "year": "2000", "release_mbid": "rel-2"},
    ])
    monkeypatch.setattr(
        wa.albums, "queue_album_download",
        lambda *a, **kw: pytest.fail("seeding must never queue"),
    )

    seeded = wa.seed_artist_albums("a1", "mbid-1")

    assert seeded == 2
    rows = _album_rows(fresh_db)
    assert set(rows) == {"rel-1", "rel-2"}
    assert all(r["status"] == "seen" for r in rows.values())
    assert all(r["queued_at"] is None for r in rows.values())


def test_new_release_group_gets_queued_on_refresh(fresh_db, monkeypatch):
    """An album MusicBrainz didn't have at seed time is genuinely new and gets queued."""
    import watched_artists as wa

    _seed_artist(fresh_db, "a1", "Radiohead", "mbid-1", auto_add_albums=1)
    with fresh_db() as conn:
        conn.execute(
            """INSERT INTO watched_artist_albums
               (artist_id, release_group_mbid, release_mbid, title, year, status)
               VALUES ('a1', 'rel-1', 'rel-1', 'OK Computer', '1997', 'seen')"""
        )
        conn.commit()

    monkeypatch.setattr(wa, "fetch_artist_albums", lambda mbid: [
        {"title": "OK Computer", "year": "1997", "release_mbid": "rel-1"},
        {"title": "Kid A", "year": "2000", "release_mbid": "rel-2"},  # new arrival
    ])
    queue_calls = []

    def fake_queue(artist, title, release_mbid, **kw):
        queue_calls.append(release_mbid)
        return {"import_id": "imp-42"}

    monkeypatch.setattr(wa.albums, "queue_album_download", fake_queue)

    artist = {"mbid": "mbid-1", "name": "Radiohead", "convert_audio": 1}
    with fresh_db() as conn:
        result = wa._refresh_artist_albums(conn, artist, "a1", None)

    assert result == {"new_albums": 1, "queued": 1, "failed": 0}
    assert queue_calls == ["rel-2"]  # only the new one, the seen one is left alone

    rows = _album_rows(fresh_db)
    assert rows["rel-2"]["status"] == "queued"
    assert rows["rel-2"]["import_id"] == "imp-42"
    assert rows["rel-1"]["status"] == "seen"  # untouched


def test_previously_seen_release_group_not_queued_twice(fresh_db, monkeypatch):
    """Once an album has been diffed (seeded, queued, or failed), a later refresh leaves it alone."""
    import watched_artists as wa

    _seed_artist(fresh_db, "a1", "Radiohead", "mbid-1", auto_add_albums=1)
    monkeypatch.setattr(wa, "fetch_artist_albums", lambda mbid: [
        {"title": "Kid A", "year": "2000", "release_mbid": "rel-2"},
    ])
    queue_calls = []

    def fake_queue(artist, title, release_mbid, **kw):
        queue_calls.append(release_mbid)
        return {"import_id": "imp-1"}

    monkeypatch.setattr(wa.albums, "queue_album_download", fake_queue)
    artist = {"mbid": "mbid-1", "name": "Radiohead", "convert_audio": 1}

    with fresh_db() as conn:
        first = wa._refresh_artist_albums(conn, artist, "a1", None)
    with fresh_db() as conn:
        second = wa._refresh_artist_albums(conn, artist, "a1", None)

    assert first == {"new_albums": 1, "queued": 1, "failed": 0}
    assert second == {"new_albums": 0, "queued": 0, "failed": 0}
    assert queue_calls == ["rel-2"]  # queued exactly once across both refreshes


def test_a_new_earliest_pressing_does_not_make_a_known_album_look_new(fresh_db, monkeypatch):
    """The release-group is the album's identity, not whichever pressing
    MusicBrainz currently thinks came first.

    fetch_artist_albums returns one row per release-group and picks the
    earliest release to represent it. That representative is not stable: add a
    freshly catalogued 1997 promo pressing and the release_mbid changes even
    though it is unmistakably the same album. Keying on release_mbid would
    therefore re-queue an entire album on the strength of a MusicBrainz
    housekeeping edit, which is a rotten way to find out your NAS is full.
    """
    import watched_artists as wa

    _seed_artist(fresh_db, "a1", "Radiohead", "mbid-1", auto_add_albums=1)
    queue_calls = []

    def fake_queue(artist, title, release_mbid, **kw):
        queue_calls.append(release_mbid)
        return {"import_id": "imp-1"}

    monkeypatch.setattr(wa.albums, "queue_album_download", fake_queue)
    artist = {"mbid": "mbid-1", "name": "Radiohead", "convert_audio": 1}

    monkeypatch.setattr(wa, "fetch_artist_albums", lambda mbid: [
        {"title": "OK Computer", "year": "1997", "release_mbid": "rel-original",
         "release_group_mbid": "rg-okc"},
    ])
    with fresh_db() as conn:
        first = wa._refresh_artist_albums(conn, artist, "a1", None)

    # Same release-group, different representative release.
    monkeypatch.setattr(wa, "fetch_artist_albums", lambda mbid: [
        {"title": "OK Computer", "year": "1997", "release_mbid": "rel-newly-found-promo",
         "release_group_mbid": "rg-okc"},
    ])
    with fresh_db() as conn:
        second = wa._refresh_artist_albums(conn, artist, "a1", None)

    assert first == {"new_albums": 1, "queued": 1, "failed": 0}
    assert second == {"new_albums": 0, "queued": 0, "failed": 0}
    assert queue_calls == ["rel-original"], "an album should be queued once, not once per pressing"


def test_one_album_failing_does_not_abort_the_rest(fresh_db, monkeypatch):
    """A blown-up queue attempt for one album must not stop the others, or the refresh."""
    import watched_artists as wa

    _seed_artist(fresh_db, "a1", "Radiohead", "mbid-1", auto_add_albums=1)
    monkeypatch.setattr(wa, "fetch_artist_albums", lambda mbid: [
        {"title": "Broken Album", "year": "1999", "release_mbid": "rel-bad"},
        {"title": "Kid A", "year": "2000", "release_mbid": "rel-2"},
    ])

    def fake_queue(artist, title, release_mbid, **kw):
        if release_mbid == "rel-bad":
            raise RuntimeError("MusicBrainz had a wobble")
        return {"import_id": "imp-2"}

    monkeypatch.setattr(wa.albums, "queue_album_download", fake_queue)
    artist = {"mbid": "mbid-1", "name": "Radiohead", "convert_audio": 1}

    with fresh_db() as conn:
        result = wa._refresh_artist_albums(conn, artist, "a1", None)

    assert result == {"new_albums": 2, "queued": 1, "failed": 1}
    rows = _album_rows(fresh_db)
    assert rows["rel-bad"]["status"] == "failed"
    assert rows["rel-2"]["status"] == "queued"


def test_refresh_watched_artist_wires_in_album_refresh(fresh_db, monkeypatch):
    """End-to-end through refresh_watched_artist: auto_add_albums=1 pulls the album
    diff into the normal singles refresh cycle and reports it in the result."""
    import watched_artists as wa

    _seed_artist(fresh_db, "a1", "Radiohead", "mbid-1", auto_add_albums=1)
    monkeypatch.setattr(wa, "fetch_artist_singles", lambda mbid: [])
    monkeypatch.setattr(wa, "fetch_artist_albums", lambda mbid: [
        {"title": "Kid A", "year": "2000", "release_mbid": "rel-2"},
    ])
    monkeypatch.setattr(wa, "start_bulk_import_for_tracks", lambda *a, **kw: "import-singles")
    monkeypatch.setattr(wa.albums, "queue_album_download", lambda *a, **kw: {"import_id": "imp-99"})

    result = wa.refresh_watched_artist("a1")

    assert result["new_albums"] == 1
    assert result["albums_queued"] == 1
    assert result["albums_failed"] == 0

    with fresh_db() as conn:
        row = conn.execute("SELECT refresh_state FROM watched_artists WHERE id = 'a1'").fetchone()
    assert row[0] == "idle"  # refresh completed cleanly, not stuck 'running' or 'error'


def test_refresh_skips_album_diff_when_toggle_is_off(fresh_db, monkeypatch):
    """Artists without auto_add_albums must never touch the album pipeline."""
    import watched_artists as wa

    _seed_artist(fresh_db, "a1", "Radiohead", "mbid-1", auto_add_albums=0)
    monkeypatch.setattr(wa, "fetch_artist_singles", lambda mbid: [])

    def fail_if_called(mbid):
        pytest.fail("fetch_artist_albums must not be called when auto_add_albums is off")

    monkeypatch.setattr(wa, "fetch_artist_albums", fail_if_called)
    monkeypatch.setattr(wa, "start_bulk_import_for_tracks", lambda *a, **kw: "import-singles")

    result = wa.refresh_watched_artist("a1")

    assert result["new_albums"] == 0
    assert result["albums_queued"] == 0
    assert _album_rows(fresh_db) == {}
