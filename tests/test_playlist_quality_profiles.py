"""Per-playlist native FLAC policy tests; no provider network is used."""

import json
import os
import queue
import sys
import threading
from contextlib import contextmanager

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
import quality_profiles as qp
from models import WatchedPlaylistRequest


def _observed(bits, rate, codec="flac"):
    return {
        "codec": codec,
        "bits_per_sample": bits,
        "sample_rate_hz": rate,
    }


@pytest.mark.parametrize("rate", [44_100, 48_000, 88_200, 96_000, 192_000])
def test_hires_accepts_native_24_bit_at_source_sample_rate(monkeypatch, tmp_path, rate):
    monkeypatch.setattr(qp, "inspect_native_quality", lambda _path: _observed(24, rate))
    ok, reason, _ = qp.validate_native_quality(tmp_path / "track.flac", "hires", False)
    assert ok is True
    assert "native 24-bit" in reason


def test_cd_profile_is_exactly_16_bit_44100_hz(monkeypatch, tmp_path):
    monkeypatch.setattr(qp, "inspect_native_quality", lambda _path: _observed(16, 44_100))
    assert qp.validate_native_quality(tmp_path / "track.flac", "cd_16_44")[0] is True

    monkeypatch.setattr(qp, "inspect_native_quality", lambda _path: _observed(24, 96_000))
    ok, reason, _ = qp.validate_native_quality(tmp_path / "track.flac", "cd_16_44")
    assert ok is False
    assert "requires 16-bit/44100 Hz" in reason


def test_hires_cd_fallback_is_explicit(monkeypatch, tmp_path):
    monkeypatch.setattr(qp, "inspect_native_quality", lambda _path: _observed(16, 44_100))
    assert qp.validate_native_quality(tmp_path / "track.flac", "hires", False)[0] is False
    ok, reason, _ = qp.validate_native_quality(tmp_path / "track.flac", "hires", True)
    assert ok is True
    assert "fallback" in reason.lower()


def test_lossy_audio_never_satisfies_a_playlist_profile(monkeypatch, tmp_path):
    monkeypatch.setattr(qp, "inspect_native_quality", lambda _path: _observed(0, 44_100, "aac"))
    for profile in ("cd_16_44", "hires", "best"):
        assert qp.validate_native_quality(tmp_path / "track.m4a", profile, True)[0] is False


def test_qobuz_profile_format_ladders_are_lossless_only():
    assert qp.requested_qobuz_formats("cd_16_44", True) == [("CD_16_44", 7)]
    assert qp.requested_qobuz_formats("hires", False) == [("HI_RES", 27)]
    assert qp.requested_qobuz_formats("hires", True) == [
        ("HI_RES", 27),
        ("CD_16_44", 7),
    ]
    assert qp.requested_qobuz_formats("best", True) == [
        ("BEST_HI_RES", 27),
        ("BEST_CD", 7),
    ]


def test_playlist_request_rejects_unknown_profile():
    body = WatchedPlaylistRequest(url="https://open.spotify.com/playlist/example")
    assert body.quality_profile == "best"
    with pytest.raises(ValueError):
        WatchedPlaylistRequest(
            url="https://open.spotify.com/playlist/example",
            quality_profile="24_96",
        )


def _fresh_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "profiles.db")
    monkeypatch.setattr(db, "_db_pool", queue.LifoQueue(maxsize=db._DB_POOL_SIZE))
    monkeypatch.setattr(db, "_db_pool_created", 0)
    monkeypatch.setattr(db, "_db_pool_lock", threading.Lock())
    db.init_db()


def test_schema_and_import_snapshot_preserve_playlist_policy(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    with db.db_conn() as conn:
        watched_columns = {row[1] for row in conn.execute("PRAGMA table_info(watched_playlists)")}
        import_columns = {row[1] for row in conn.execute("PRAGMA table_info(bulk_imports)")}
    assert {"quality_profile", "quality_fallback"} <= watched_columns
    assert {"quality_profile", "quality_fallback"} <= import_columns

    import bulk_import

    monkeypatch.setattr(bulk_import, "spawn_daemon_thread", lambda *_args, **_kwargs: None)
    import_id = bulk_import.start_bulk_import_for_tracks(
        [("Artist", "Track")],
        convert_audio=True,
        watch_playlist_id="playlist-quality-test",
        quality_profile="hires",
        quality_fallback=False,
    )
    with db.db_conn() as conn:
        import_row = conn.execute(
            "SELECT quality_profile, quality_fallback FROM bulk_imports WHERE id = ?",
            (import_id,),
        ).fetchone()
        target_row = conn.execute(
            """SELECT destination_json FROM acquisition_targets
               WHERE owner_type = 'watched_playlist'"""
        ).fetchone()
    assert tuple(import_row) == ("hires", 0)
    destination = json.loads(target_row[0])
    assert destination["quality_profile"] == "hires"
    assert destination["quality_fallback"] is False


def test_monochrome_resolver_requests_cd_format_7(monkeypatch):
    import monochrome

    calls = []
    monkeypatch.setattr("monochrome_browser.resolve_unified_stream_url", lambda *a, **k: None)
    monkeypatch.setattr(
        "qbdlx.resolve_qobuz_stream_url",
        lambda isrc, fmt: calls.append(fmt) or "https://qobuz.test/cd.flac",
    )
    url = monochrome._resolve_monochrome_stream_url(
        "monochrome://123?isrc=GBAYE9200070&quality=HIGH",
        quality_profile="cd_16_44",
    )
    assert url == "https://qobuz.test/cd.flac"
    assert calls == [7]


def test_monochrome_hires_strict_never_steps_down_to_cd(monkeypatch):
    import monochrome

    browser_qualities = []
    qobuz_formats = []
    monkeypatch.setattr(
        "monochrome_browser.resolve_unified_stream_url",
        lambda _isrc, quality, **_kwargs: browser_qualities.append(quality),
    )
    monkeypatch.setattr(
        "qbdlx.resolve_qobuz_stream_url",
        lambda _isrc, fmt: qobuz_formats.append(fmt),
    )
    monkeypatch.setattr(monochrome, "_deezer_isrc_rescue", lambda *_args: "")
    with pytest.raises(RuntimeError, match="no stream available"):
        monochrome._resolve_monochrome_stream_url(
            "monochrome://123?isrc=GBAYE9200070&quality=LOSSLESS",
            quality_profile="hires",
            allow_quality_fallback=False,
        )
    assert browser_qualities == ["HI_RES_LOSSLESS"]
    assert qobuz_formats == [27]


def test_monochrome_best_tries_hires_then_cd_without_lossy(monkeypatch):
    import monochrome

    qobuz_formats = []
    monkeypatch.setattr("monochrome_browser.resolve_unified_stream_url", lambda *a, **k: None)

    def qobuz(_isrc, fmt):
        qobuz_formats.append(fmt)
        return "https://qobuz.test/best.flac" if fmt == 7 else None

    monkeypatch.setattr("qbdlx.resolve_qobuz_stream_url", qobuz)
    url = monochrome._resolve_monochrome_stream_url(
        "monochrome://123?isrc=GBAYE9200070&quality=HIGH",
        quality_profile="best",
    )
    assert url == "https://qobuz.test/best.flac"
    assert qobuz_formats == [27, 7]


def test_manual_resolver_keeps_legacy_advertised_quality(monkeypatch):
    import monochrome

    qualities = []
    monkeypatch.setattr(
        "monochrome_browser.resolve_unified_stream_url",
        lambda _isrc, quality, **_kwargs: qualities.append(quality) or "https://legacy.test/audio",
    )
    monochrome._resolve_monochrome_stream_url(
        "monochrome://123?isrc=GBAYE9200070&quality=LOSSLESS"
    )
    assert qualities == ["LOSSLESS"]


def test_hires_download_checks_qobuz_before_accepting_cd_fallback(monkeypatch, tmp_path):
    import monochrome

    resolver_calls = []
    validation_calls = []
    monkeypatch.setattr(
        monochrome,
        "_resolve_monochrome_stream_url",
        lambda *_args, **kwargs: resolver_calls.append(kwargs) or "https://cdn.test/audio.flac",
    )
    monkeypatch.setattr(monochrome, "_download_monochrome_resource", lambda *_args: None)

    def validate(_path, profile, allow_fallback):
        validation_calls.append((profile, allow_fallback))
        # Browser delivered CD first; direct Qobuz then delivered native 24-bit.
        return (len(validation_calls) == 2, "observed quality", {})

    monkeypatch.setattr(monochrome, "validate_native_quality", validate)
    monochrome.download_monochrome_track(
        "monochrome://123?isrc=GBAYE9200070&quality=HI_RES_LOSSLESS",
        tmp_path / "track.flac",
        quality_profile="hires",
        allow_quality_fallback=True,
    )
    assert validation_calls == [("hires", False), ("hires", False)]
    assert [call["skip_browser"] for call in resolver_calls] == [False, True]
    assert all(call["allow_quality_fallback"] is False for call in resolver_calls)


def test_queue_job_reads_immutable_watched_playlist_quality_snapshot(monkeypatch):
    import downloads

    class Cursor:
        def fetchone(self):
            return ("watched_playlist", json.dumps({
                "quality_profile": "hires",
                "quality_fallback": False,
            }))

    class Connection:
        def execute(self, *_args, **_kwargs):
            return Cursor()

    @contextmanager
    def fake_db_conn():
        yield Connection()

    monkeypatch.setattr(downloads, "db_conn", fake_db_conn)
    assert downloads._job_playlist_quality_policy("job-1") == ("hires", False)
