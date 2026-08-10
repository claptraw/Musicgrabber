"""Album duplicate matching stays fuzzy inside Albums and nowhere else."""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import albums


def _album_root(monkeypatch, tmp_path: Path) -> Path:
    root = tmp_path / "Albums"
    root.mkdir()
    monkeypatch.setattr(albums, "get_albums_dir", lambda user_id=None: root)
    monkeypatch.setattr(
        albums,
        "get_setting_bool",
        lambda name, default=False, user_id=None: True,
    )
    return root


def test_equivalent_album_folder_and_numbered_track_are_reused(monkeypatch, tmp_path):
    root = _album_root(monkeypatch, tmp_path)
    existing_dir = root / "Knife Party" / "Lost Souls EP"
    existing_dir.mkdir(parents=True)
    existing_file = existing_dir / "01 - Ghost Train.flac"
    existing_file.write_bytes(b"audio")

    status = albums.album_track_status(
        "Knife Party",
        "Lost Souls",
        [{"position": "1", "title": "Ghost Train", "isrc": None}],
    )

    assert status["album_dir"] == existing_dir
    assert status["existing_tracks"][0]["title"] == "Ghost Train"
    assert albums.find_existing_album_track(
        "Knife Party", "Lost Souls", "Ghost Train"
    ) == existing_file


def test_punctuation_only_artist_and_album_differences_match(monkeypatch, tmp_path):
    root = _album_root(monkeypatch, tmp_path)
    existing_dir = root / "AC DC" / "Back-in Black"
    existing_dir.mkdir(parents=True)
    track = existing_dir / "Hells Bells.mp3"
    track.write_bytes(b"audio")

    assert albums.find_existing_album_track(
        "AC/DC", "Back in Black", "Hell's Bells"
    ) == track


def test_lookalike_single_outside_albums_is_never_considered(monkeypatch, tmp_path):
    _album_root(monkeypatch, tmp_path)
    unrelated = tmp_path / "Singles" / "Knife Party"
    unrelated.mkdir(parents=True)
    (unrelated / "Ghost Train.mp3").write_bytes(b"audio")

    assert albums.find_existing_album_track(
        "Knife Party", "Lost Souls", "Ghost Train"
    ) is None
    assert albums.album_on_disk("Knife Party", "Lost Souls") is False
