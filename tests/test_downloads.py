"""
Download flow tests.

Fast tests: queue jobs, verify shape and fields, clean up immediately (no waiting).
Slow tests: actually wait for completion and check outcomes including file placement.

Run fast only:  pytest -m "not slow"
Run all:        pytest -m slow (or --slow via run_tests.sh)

---------------------------------------------------------------------
Clarification on download_type values:
  "single"   (default) -- one video_id, land in Singles/ (or Playlists/ if
             use_playlists_dir=True + playlist_name set).
  "playlist" -- treats video_id as a YouTube playlist ID and fans out via
             process_playlist_download. Not used in these tests.
---------------------------------------------------------------------
"""

import pathlib
import time
from contextlib import contextmanager

import pytest


# Reliable, short public video -- "Me at the zoo", 18 seconds.
# AcoustID fingerprinting is skipped for <30s files, so MB won't match.
_SHORT_YT_ID   = "jNQXAC9IVRw"
_SHORT_YT_ARTIST = "jawed"
_SHORT_YT_TITLE  = "Me at the zoo"

# A track MusicBrainz reliably identifies via AcoustID fingerprint.
# "Thriller" by Michael Jackson -- known album, predictable routing.
# NOTE: these tests take several minutes each because the full track downloads.
_THRILLER_YT_ID  = "sOnqjkJTMaA"
_THRILLER_ARTIST = "Michael Jackson"
_THRILLER_TITLE  = "Thriller"

_TERMINAL = ("completed", "failed", "completed_with_errors")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _wait_for_job(api, base_url, job_id, timeout=120):
    """Poll until the job reaches a terminal state. Returns the final job dict."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(5)
        r = api.get(f"{base_url}/api/jobs/{job_id}", timeout=10)
        assert r.status_code == 200
        job = r.json()
        if job.get("status") in _TERMINAL:
            return job
    raise TimeoutError(f"Job {job_id} did not complete within {timeout}s")


def _queue_download(api, base_url, **kwargs):
    """POST /api/download with the given payload. Returns (job_id, response_json)."""
    payload = {
        "video_id": _SHORT_YT_ID,
        "title": _SHORT_YT_TITLE,
        "artist": _SHORT_YT_ARTIST,
        "source": "youtube",
        "convert_to_flac": True,
        **kwargs,
    }
    r = api.post(f"{base_url}/api/download", json=payload, timeout=15)
    assert r.status_code == 200, f"POST /api/download failed: {r.text}"
    data = r.json()
    job_id = data.get("job_id") or data.get("id")
    assert job_id, f"No job_id in response: {data}"
    return job_id, data


def _cleanup(api, base_url, job_id=None):
    if job_id:
        api.delete(f"{base_url}/api/jobs/{job_id}/file", timeout=10)
    api.delete(f"{base_url}/api/jobs/cleanup", timeout=10)


@contextmanager
def _temp_settings(api, base_url, **overrides):
    """Temporarily patch settings, restoring the originals on exit."""
    original = api.get(f"{base_url}/api/settings", timeout=10).json()
    api.put(f"{base_url}/api/settings", json=overrides, timeout=10)
    try:
        yield original
    finally:
        restore = {k: original.get(k) for k in overrides}
        api.put(f"{base_url}/api/settings", json=restore, timeout=10)


def _assert_completed(job):
    assert job["status"] in ("completed", "completed_with_errors"), \
        f"Download failed or timed out: {job.get('error')}"


def _mb_skip_if_no_override(job, artist, title):
    """Skip (not fail) when MB returned no album so routing never triggered."""
    if not job.get("override_dir"):
        pytest.skip(
            f"MusicBrainz didn't return album metadata for '{artist} - {title}'; "
            "routing can't be verified (download itself completed OK)"
        )


def _import_downloads_or_skip():
    try:
        import downloads
    except ModuleNotFoundError as exc:
        pytest.skip(f"downloads module dependencies unavailable: {exc.name}")
    return downloads


@pytest.mark.parametrize("stderr, expected", [
    # The exact shape that flaked a release build: YouTube's thumbnail CDN
    # didn't serve the .webp, so the convert/embed step died even though the
    # audio downloaded fine. Must be recoverable, not fatal.
    ("ERROR: [Errno 2] No such file or directory: '/music/Singles/jawed/Me at the zoo.webp'", True),
    ("ERROR: Unable to embed thumbnail in the output file", True),
    ("ERROR: [Errno 2] No such file or directory: cover.jpg", True),
    # Genuine audio failures must NOT be swallowed as recoverable thumbnail blips.
    ("ERROR: unable to download video data: HTTP Error 403: Forbidden", False),
    ("ERROR: Postprocessing: Conversion failed!", False),
    ("", False),
])
def test_thumbnail_postprocess_failure_detection(stderr, expected):
    downloads = _import_downloads_or_skip()
    assert downloads._is_thumbnail_postprocess_failure(stderr) is expected


def test_auto_route_single_uses_album_artist_for_folder(tmp_path, monkeypatch):
    downloads = _import_downloads_or_skip()

    monkeypatch.setattr(downloads, "_update_job", lambda *args, **kwargs: None)
    monkeypatch.setattr(downloads, "set_file_permissions", lambda *args, **kwargs: None)
    monkeypatch.setattr(downloads, "get_singles_dir", lambda user_id=None: tmp_path / "Singles")
    monkeypatch.setattr(downloads, "get_albums_dir", lambda user_id=None: tmp_path / "Albums")
    monkeypatch.setattr(
        downloads,
        "get_setting_bool",
        lambda key, default=False, user_id=None: {
            "auto_album_singles": True,
            "auto_album_singles_use_albums_dir": False,
            "organise_by_artist": False,
        }.get(key, default),
    )

    source_dir = tmp_path / "Singles" / "Luis Fonsi, Daddy Yankee, Justin Bieber"
    source_dir.mkdir(parents=True)
    audio_file = source_dir / "Despacito (Remix).flac"
    audio_file.write_bytes(b"not real audio")

    routed = downloads._auto_route_single_to_album(
        audio_file,
        "Luis Fonsi, Daddy Yankee, Justin Bieber",
        "Despacito (Remix)",
        {"album": "Vida", "album_artist": "Luis Fonsi"},
        "job-id",
        None,
    )

    assert routed == tmp_path / "Singles" / "Luis Fonsi" / "Vida" / "Luis Fonsi - Despacito (Remix).flac"
    assert routed.exists()


def test_auto_route_playlist_uses_album_artist_for_folder_and_flat_filename(tmp_path, monkeypatch):
    downloads = _import_downloads_or_skip()

    monkeypatch.setattr(downloads, "_update_job", lambda *args, **kwargs: None)
    monkeypatch.setattr(downloads, "set_file_permissions", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        downloads,
        "get_setting_bool",
        lambda key, default=False, user_id=None: {
            "auto_album_singles": True,
            "organise_by_artist": False,
        }.get(key, default),
    )

    playlist_dir = tmp_path / "Playlists" / "Test"
    playlist_dir.mkdir(parents=True)
    audio_file = playlist_dir / "Luis Fonsi, Daddy Yankee, Justin Bieber - Despacito (Remix).flac"
    audio_file.write_bytes(b"not real audio")

    routed, did_route = downloads._auto_route_playlist_to_album(
        audio_file,
        "Luis Fonsi, Daddy Yankee, Justin Bieber",
        "Despacito (Remix)",
        {"album": "Vida", "album_artist": "Luis Fonsi"},
        "job-id",
        playlist_dir,
        None,
    )

    assert did_route is True
    assert routed == playlist_dir / "Luis Fonsi" / "Vida" / "Luis Fonsi - Despacito (Remix).flac"
    assert routed.exists()


# ---------------------------------------------------------------------------
# Regression guards for "Add to playlist" / watched-playlist M3U writes.
# These cover two paper-cut bugs we shipped 2.8.15 to fix; if either of these
# tests fails, the playlist .m3u file on disk is silently not being updated.
# ---------------------------------------------------------------------------

def test_append_to_physical_m3u_writes_to_playlists_dir_when_configured(tmp_path, monkeypatch):
    """Happy path: playlists_subdir is set, M3U lands in Playlists/."""
    downloads = _import_downloads_or_skip()

    playlists_dir = tmp_path / "Playlists"
    playlists_dir.mkdir()
    monkeypatch.setattr(downloads, "get_playlists_dir", lambda user_id=None: playlists_dir)
    monkeypatch.setattr(downloads, "get_singles_dir", lambda user_id=None: tmp_path / "Singles")

    audio_file = playlists_dir / "Rock Mix" / "Some Track.flac"
    audio_file.parent.mkdir(parents=True)
    audio_file.write_bytes(b"not real audio")

    downloads._append_to_physical_m3u(audio_file, "Rock Mix", use_playlists_dir=True)

    m3u = playlists_dir / "Rock Mix.m3u"
    assert m3u.exists(), "Expected Playlists/Rock Mix.m3u to be created"
    contents = m3u.read_text(encoding="utf-8")
    assert "#EXTM3U" in contents
    # Relative path inside the playlist folder for portability.
    assert "Rock Mix/Some Track.flac" in contents


def test_append_to_physical_m3u_falls_back_to_singles_when_playlists_dir_unset(tmp_path, monkeypatch):
    """Bug 2 regression guard: without a Playlists folder configured, the
    M3U should still be written (to Singles), not silently dropped."""
    downloads = _import_downloads_or_skip()

    singles_dir = tmp_path / "Singles"
    singles_dir.mkdir()
    # Simulate playlists_subdir unset: get_playlists_dir returns None.
    monkeypatch.setattr(downloads, "get_playlists_dir", lambda user_id=None: None)
    monkeypatch.setattr(downloads, "get_singles_dir", lambda user_id=None: singles_dir)

    audio_file = singles_dir / "Jawed" / "Me at the zoo.flac"
    audio_file.parent.mkdir(parents=True)
    audio_file.write_bytes(b"not real audio")

    downloads._append_to_physical_m3u(audio_file, "My Faves", use_playlists_dir=True)

    m3u = singles_dir / "My Faves.m3u"
    assert m3u.exists(), "Expected Singles/My Faves.m3u as fallback when Playlists dir is unset"
    contents = m3u.read_text(encoding="utf-8")
    assert "#EXTM3U" in contents
    assert str(audio_file) in contents


def test_append_to_physical_m3u_does_not_duplicate_entries(tmp_path, monkeypatch):
    downloads = _import_downloads_or_skip()

    playlists_dir = tmp_path / "Playlists"
    playlists_dir.mkdir()
    monkeypatch.setattr(downloads, "get_playlists_dir", lambda user_id=None: playlists_dir)
    monkeypatch.setattr(downloads, "get_singles_dir", lambda user_id=None: tmp_path / "Singles")

    audio_file = playlists_dir / "Rock Mix" / "Track.flac"
    audio_file.parent.mkdir(parents=True)
    audio_file.write_bytes(b"not real audio")

    downloads._append_to_physical_m3u(audio_file, "Rock Mix", use_playlists_dir=True)
    downloads._append_to_physical_m3u(audio_file, "Rock Mix", use_playlists_dir=True)

    m3u = playlists_dir / "Rock Mix.m3u"
    body = m3u.read_text(encoding="utf-8")
    assert body.count("Rock Mix/Track.flac") == 1, f"Expected one entry, got: {body!r}"


def test_append_to_physical_m3u_noop_without_playlist_name(tmp_path, monkeypatch):
    downloads = _import_downloads_or_skip()

    singles_dir = tmp_path / "Singles"
    singles_dir.mkdir()
    monkeypatch.setattr(downloads, "get_playlists_dir", lambda user_id=None: None)
    monkeypatch.setattr(downloads, "get_singles_dir", lambda user_id=None: singles_dir)

    audio_file = singles_dir / "Jawed" / "Me at the zoo.flac"
    audio_file.parent.mkdir(parents=True)
    audio_file.write_bytes(b"not real audio")

    downloads._append_to_physical_m3u(audio_file, "", use_playlists_dir=True)
    downloads._append_to_physical_m3u(audio_file, None, use_playlists_dir=True)

    # Nothing should land on disk when no playlist is named.
    assert not any(p.suffix == ".m3u" for p in singles_dir.glob("**/*"))


def test_append_to_physical_m3u_falls_back_when_name_sanitizes_empty(tmp_path, monkeypatch):
    downloads = _import_downloads_or_skip()

    singles_dir = tmp_path / "Singles"
    singles_dir.mkdir()
    monkeypatch.setattr(downloads, "get_playlists_dir", lambda user_id=None: None)
    monkeypatch.setattr(downloads, "get_singles_dir", lambda user_id=None: singles_dir)

    audio_file = singles_dir / "Artist" / "Track.flac"
    audio_file.parent.mkdir(parents=True)
    audio_file.write_bytes(b"not real audio")

    downloads._append_to_physical_m3u(audio_file, "///", use_playlists_dir=False)

    m3u = singles_dir / "Playlist.m3u"
    assert m3u.exists(), "Expected empty-after-sanitizing playlist names to fall back"


def _setup_watched_playlist_db(monkeypatch, downloads):
    """Wire downloads.db_conn to an in-memory sqlite with the minimal schema
    needed for _mark_watched_track_downloaded's watched-playlist lookup.

    Returns the live connection so the test can seed and assert on it.
    """
    import sqlite3
    from contextlib import contextmanager

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE jobs (
            id TEXT PRIMARY KEY,
            artist TEXT,
            title TEXT,
            status TEXT,
            error TEXT,
            skip_mismatch_check INTEGER DEFAULT 0
        );
        CREATE TABLE watched_playlists (
            id TEXT PRIMARY KEY,
            name TEXT,
            make_m3u INTEGER DEFAULT 1,
            use_playlists_dir INTEGER DEFAULT 0,
            sync_mode TEXT DEFAULT 'append',
            custom_subdir TEXT,
            user_id TEXT
        );
        CREATE TABLE watched_playlist_tracks (
            playlist_id TEXT,
            track_hash TEXT,
            artist TEXT,
            title TEXT,
            downloaded_at TIMESTAMP,
            resolved_path TEXT,
            job_id TEXT,
            PRIMARY KEY (playlist_id, track_hash)
        );
        CREATE TABLE bulk_imports (
            id TEXT PRIMARY KEY,
            watch_playlist_id TEXT
        );
        CREATE TABLE bulk_import_tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            import_id TEXT,
            job_id TEXT
        );
    """)

    @contextmanager
    def fake_db_conn():
        yield conn

    monkeypatch.setattr(downloads, "db_conn", fake_db_conn)
    return conn


def test_mark_watched_track_downloaded_rebuilds_m3u_via_missing_tracks_retry(monkeypatch):
    """Bug 1 regression guard: when a job was queued via the missing-tracks
    retry endpoint, there is no bulk_imports row, only a watched_playlist_tracks
    row. The post-download M3U rebuild must still find the playlist and fire."""
    downloads = _import_downloads_or_skip()

    conn = _setup_watched_playlist_db(monkeypatch, downloads)

    conn.execute(
        "INSERT INTO jobs (id, artist, title, status, skip_mismatch_check) VALUES (?, ?, ?, ?, ?)",
        ("job-1", "Jawed", "Me at the zoo", "downloading", 1),
    )
    conn.execute(
        "INSERT INTO watched_playlists (id, name, make_m3u, use_playlists_dir, sync_mode, user_id) "
        "VALUES (?, ?, 1, 1, 'append', NULL)",
        ("pl-1", "Rock Mix"),
    )
    conn.execute(
        "INSERT INTO watched_playlist_tracks (playlist_id, track_hash, artist, title, job_id) "
        "VALUES (?, ?, ?, ?, ?)",
        ("pl-1", "hash-1", "Jawed", "Me at the zoo", "job-1"),
    )
    # Deliberately NO bulk_imports / bulk_import_tracks rows: this mirrors the
    # state created by /api/watched-playlists/{id}/queue-track-candidate.
    conn.commit()

    calls = []
    monkeypatch.setattr(
        downloads, "rebuild_watched_playlist_m3u",
        lambda *args, **kwargs: calls.append((args, kwargs)) or None,
    )

    result = downloads._mark_watched_track_downloaded("job-1", resolved_path=None, skip_mismatch=True)

    assert result is True
    assert len(calls) == 1, (
        "Expected rebuild_watched_playlist_m3u to fire for the missing-tracks "
        "retry path; if this fails, the watched playlist .m3u is going stale "
        "after every missing-track retry (see bug fixed in v2.8.15)."
    )
    args, kwargs = calls[0]
    assert args[0] == "pl-1"
    assert args[1] == "Rock Mix"


def test_mark_watched_track_downloaded_rebuilds_m3u_via_bulk_import(monkeypatch):
    """Sister test to the missing-tracks one: the bulk-import refresh path
    (the original code path) must also continue to work."""
    downloads = _import_downloads_or_skip()

    conn = _setup_watched_playlist_db(monkeypatch, downloads)

    conn.execute(
        "INSERT INTO jobs (id, artist, title, status, skip_mismatch_check) VALUES (?, ?, ?, ?, ?)",
        ("job-2", "Jawed", "Me at the zoo", "downloading", 1),
    )
    conn.execute(
        "INSERT INTO watched_playlists (id, name, make_m3u, use_playlists_dir, sync_mode, user_id) "
        "VALUES (?, ?, 1, 1, 'append', NULL)",
        ("pl-2", "Pop Mix"),
    )
    conn.execute(
        "INSERT INTO watched_playlist_tracks (playlist_id, track_hash, artist, title, job_id) "
        "VALUES (?, ?, ?, ?, ?)",
        ("pl-2", "hash-2", "Jawed", "Me at the zoo", "job-2"),
    )
    conn.execute("INSERT INTO bulk_imports (id, watch_playlist_id) VALUES (?, ?)", ("imp-1", "pl-2"))
    conn.execute(
        "INSERT INTO bulk_import_tracks (import_id, job_id) VALUES (?, ?)",
        ("imp-1", "job-2"),
    )
    conn.commit()

    calls = []
    monkeypatch.setattr(
        downloads, "rebuild_watched_playlist_m3u",
        lambda *args, **kwargs: calls.append((args, kwargs)) or None,
    )

    result = downloads._mark_watched_track_downloaded("job-2", resolved_path=None, skip_mismatch=True)

    assert result is True
    assert len(calls) == 1
    args, _ = calls[0]
    assert args[0] == "pl-2"
    assert args[1] == "Pop Mix"


# ---------------------------------------------------------------------------
# Fast: shape / creation tests
# ---------------------------------------------------------------------------

def test_queue_single_download_returns_job_id(api, base_url):
    job_id, _ = _queue_download(api, base_url)
    assert job_id
    _cleanup(api, base_url, job_id)


def test_queue_single_download_job_is_retrievable(api, base_url):
    job_id, _ = _queue_download(api, base_url)
    r = api.get(f"{base_url}/api/jobs/{job_id}", timeout=10)
    assert r.status_code == 200
    assert r.json()["id"] == job_id
    _cleanup(api, base_url, job_id)


def test_queue_single_download_initial_status(api, base_url):
    job_id, _ = _queue_download(api, base_url)
    status = api.get(f"{base_url}/api/jobs/{job_id}", timeout=10).json().get("status")
    assert status in (*_TERMINAL, "queued", "downloading"), \
        f"Unexpected initial status: {status}"
    _cleanup(api, base_url, job_id)


def test_queue_playlist_routed_download_accepted(api, base_url):
    """use_playlists_dir + playlist_name on a single-type download is a valid request.

    playlist_name is not persisted to the DB for single downloads (only passed in-memory
    to the worker), so we just verify the request is accepted and the job is created.
    """
    job_id, _ = _queue_download(api, base_url, playlist_name="Test Playlist", use_playlists_dir=True)
    assert api.get(f"{base_url}/api/jobs/{job_id}", timeout=10).json()["id"] == job_id
    _cleanup(api, base_url, job_id)


def test_download_missing_video_id_rejected(api, base_url):
    r = api.post(f"{base_url}/api/download", json={
        "title": "Test", "artist": "Test", "source": "youtube", "convert_to_flac": True
    }, timeout=10)
    assert r.status_code in (400, 422), f"Expected 400/422, got {r.status_code}"


def test_download_job_shape(api, base_url):
    """Every expected field should be present on a freshly queued job."""
    expected_keys = [
        "id", "video_id", "title", "artist", "status",
        "error", "download_type", "source", "convert_to_flac",
    ]
    job_id, _ = _queue_download(api, base_url)
    job = api.get(f"{base_url}/api/jobs/{job_id}", timeout=10).json()
    for key in expected_keys:
        assert key in job, f"Job response missing key: {key}"
    _cleanup(api, base_url, job_id)


# ---------------------------------------------------------------------------
# Slow: setting combination tests
#
# Each test verifies one combination of settings and checks the outcome via
# the job's `override_dir` field (set by the album-routing code when a file
# moves) or just completion status.
#
# Notation used in test names:
#   single  = download_type default, lands in Singles/
#   pl      = use_playlists_dir=True, lands in Playlists/Name/
#   album   = auto_album_singles=True
#   albums  = auto_album_singles_use_albums_dir=True (route to Albums/ not Singles/)
#   trackno = include_track_number_in_filename=True
#
# Tests using _SHORT_YT_ID: MB won't match (18s, fingerprint skipped), so
#   album routing never fires. Used to verify routing is OFF.
# Tests using _THRILLER_YT_ID: MB should match and return album info.
#   These are several minutes each. Skipped gracefully if MB doesn't match.
# ---------------------------------------------------------------------------

@pytest.mark.slow
def test_single_baseline_completes(api, base_url):
    """Single download with all routing settings off -- the happy path."""
    with _temp_settings(api, base_url,
                        auto_album_singles=False,
                        include_track_number_in_filename=False):
        job_id, _ = _queue_download(api, base_url, convert_to_flac=False)
        job = _wait_for_job(api, base_url, job_id)
    _assert_completed(job)
    assert job.get("override_dir") is None, \
        f"Expected no routing with all settings off, got override_dir={job['override_dir']}"
    _cleanup(api, base_url, job_id)


@pytest.mark.slow
def test_single_album_routing_off_no_override_dir(api, base_url):
    """override_dir stays None when auto_album_singles is explicitly off."""
    with _temp_settings(api, base_url, auto_album_singles=False):
        job_id, _ = _queue_download(api, base_url)
        job = _wait_for_job(api, base_url, job_id)
    _assert_completed(job)
    assert job.get("override_dir") is None, \
        f"override_dir should be None with routing off, got: {job['override_dir']}"
    _cleanup(api, base_url, job_id)


@pytest.mark.slow
def test_single_album_routing_on_sets_override_dir(api, base_url):
    """With album routing on, a track MB recognises lands in Singles/Artist/Album/.

    override_dir is set by _auto_route_single_to_album and must not be inside
    the Albums directory (that's a separate setting).
    """
    with _temp_settings(api, base_url,
                        auto_album_singles=True,
                        auto_album_singles_use_albums_dir=False):
        job_id, _ = _queue_download(api, base_url,
                                    video_id=_THRILLER_YT_ID,
                                    title=_THRILLER_TITLE,
                                    artist=_THRILLER_ARTIST)
        job = _wait_for_job(api, base_url, job_id, timeout=300)

    _assert_completed(job)
    _mb_skip_if_no_override(job, _THRILLER_ARTIST, _THRILLER_TITLE)

    od = pathlib.Path(job["override_dir"])
    settings = api.get(f"{base_url}/api/settings", timeout=10).json()
    singles_subdir = settings.get("singles_subdir") or "Singles"
    assert singles_subdir in od.parts, \
        f"Expected override_dir inside Singles dir ({singles_subdir}), got: {od}"
    albums_subdir = settings.get("albums_subdir") or "Albums"
    assert albums_subdir not in od.parts, \
        f"override_dir should NOT be in Albums dir with use_albums_dir=False, got: {od}"
    _cleanup(api, base_url, job_id)


@pytest.mark.slow
def test_single_album_routing_on_albums_dir(api, base_url):
    """With auto_album_singles_use_albums_dir on, the track lands in Albums/Artist/Album/."""
    with _temp_settings(api, base_url,
                        auto_album_singles=True,
                        auto_album_singles_use_albums_dir=True):
        job_id, _ = _queue_download(api, base_url,
                                    video_id=_THRILLER_YT_ID,
                                    title=_THRILLER_TITLE,
                                    artist=_THRILLER_ARTIST)
        job = _wait_for_job(api, base_url, job_id, timeout=300)

    _assert_completed(job)
    _mb_skip_if_no_override(job, _THRILLER_ARTIST, _THRILLER_TITLE)

    od = pathlib.Path(job["override_dir"])
    settings = api.get(f"{base_url}/api/settings", timeout=10).json()
    albums_subdir = settings.get("albums_subdir") or "Albums"
    assert albums_subdir in od.parts, \
        f"Expected override_dir inside Albums dir ({albums_subdir}), got: {od}"
    _cleanup(api, base_url, job_id)


@pytest.mark.slow
def test_single_track_number_completes(api, base_url):
    """Track number in filename enabled -- single download should still complete."""
    with _temp_settings(api, base_url, include_track_number_in_filename=True):
        job_id, _ = _queue_download(api, base_url, convert_to_flac=False)
        job = _wait_for_job(api, base_url, job_id)
    _assert_completed(job)
    _cleanup(api, base_url, job_id)


@pytest.mark.slow
def test_playlist_routed_single_album_routing_off(api, base_url):
    """Single track routed to Playlists/ with album routing off -- override_dir stays None.

    Requires playlists_subdir to be non-empty; test sets it temporarily if not.
    """
    with _temp_settings(api, base_url,
                        playlists_subdir="Playlists",
                        auto_album_singles=False):
        job_id, _ = _queue_download(api, base_url,
                                    playlist_name="Combo Test",
                                    use_playlists_dir=True,
                                    convert_to_flac=False)
        job = _wait_for_job(api, base_url, job_id)

    _assert_completed(job)
    assert job.get("override_dir") is None, \
        f"override_dir should be None with album routing off, got: {job['override_dir']}"
    _cleanup(api, base_url, job_id)


@pytest.mark.slow
def test_playlist_routed_single_album_routing_on(api, base_url):
    """Single track routed to Playlists/ with album routing on -- override_dir must be
    inside the playlist folder (Playlists/Name/Artist/Album/).

    This is the core test for the album-routing-inside-playlist fix.
    """
    with _temp_settings(api, base_url,
                        playlists_subdir="Playlists",
                        auto_album_singles=True,
                        auto_album_singles_use_albums_dir=False):
        job_id, _ = _queue_download(api, base_url,
                                    video_id=_THRILLER_YT_ID,
                                    title=_THRILLER_TITLE,
                                    artist=_THRILLER_ARTIST,
                                    playlist_name="Combo Test",
                                    use_playlists_dir=True)
        job = _wait_for_job(api, base_url, job_id, timeout=300)

    _assert_completed(job)
    _mb_skip_if_no_override(job, _THRILLER_ARTIST, _THRILLER_TITLE)

    od = pathlib.Path(job["override_dir"])
    assert "Combo Test" in od.parts, \
        f"override_dir should be inside the playlist folder, got: {od}"
    # Playlists/Combo Test/Artist/Album -- 4+ parts below filesystem root
    assert len(od.parts) >= 4, \
        f"override_dir too shallow for Artist/Album nesting inside playlist: {od}"
    # Must NOT be inside the Albums dir (that would be wrong for playlist routing)
    settings = api.get(f"{base_url}/api/settings", timeout=10).json()
    albums_subdir = settings.get("albums_subdir") or "Albums"
    assert albums_subdir not in od.parts, \
        f"Playlist-routed track must stay in Playlists/, not Albums/: {od}"
    _cleanup(api, base_url, job_id)


@pytest.mark.slow
def test_playlist_routed_single_track_number_completes(api, base_url):
    """Track number in filename + playlist routing -- should complete without error."""
    with _temp_settings(api, base_url,
                        playlists_subdir="Playlists",
                        include_track_number_in_filename=True,
                        auto_album_singles=False):
        job_id, _ = _queue_download(api, base_url,
                                    playlist_name="Combo Test",
                                    use_playlists_dir=True,
                                    convert_to_flac=False)
        job = _wait_for_job(api, base_url, job_id)
    _assert_completed(job)
    _cleanup(api, base_url, job_id)


@pytest.mark.slow
def test_playlist_routed_single_all_settings_on(api, base_url):
    """Album routing + track numbers + playlist folder all enabled simultaneously.

    override_dir should still land inside the playlist folder, not Albums/.
    Track number in filename doesn't affect override_dir but must not crash the pipeline.
    """
    with _temp_settings(api, base_url,
                        playlists_subdir="Playlists",
                        auto_album_singles=True,
                        auto_album_singles_use_albums_dir=False,
                        include_track_number_in_filename=True):
        job_id, _ = _queue_download(api, base_url,
                                    video_id=_THRILLER_YT_ID,
                                    title=_THRILLER_TITLE,
                                    artist=_THRILLER_ARTIST,
                                    playlist_name="Combo Test",
                                    use_playlists_dir=True)
        job = _wait_for_job(api, base_url, job_id, timeout=300)

    _assert_completed(job)
    _mb_skip_if_no_override(job, _THRILLER_ARTIST, _THRILLER_TITLE)

    od = pathlib.Path(job["override_dir"])
    assert "Combo Test" in od.parts, \
        f"override_dir should be inside the playlist folder with all settings on, got: {od}"
    _cleanup(api, base_url, job_id)
