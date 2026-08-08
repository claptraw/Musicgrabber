"""
Unit tests for album_urls.parse_album_url and resolve_album_url.

Pure unit tests: the Spotify embed fetch and Apple Music fetch are both
monkeypatched, and no real MusicBrainz lookups happen either (matching is
stubbed at the album_urls.match_album_to_musicbrainz seam). No network calls.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _import_album_urls_or_skip():
    try:
        import album_urls
    except ModuleNotFoundError as exc:
        pytest.skip(f"album_urls module dependencies unavailable: {exc.name}")
    return album_urls


# ---------------------------------------------------------------------------
# parse_album_url: recognition
# ---------------------------------------------------------------------------

def test_spotify_album_url_is_recognised(monkeypatch):
    album_urls = _import_album_urls_or_skip()
    import watched_playlists
    monkeypatch.setattr(
        watched_playlists, "_fetch_spotify_playlist_embed",
        lambda url, **kw: {
            "playlist_name": "Discovery",
            "tracks": ["Daft Punk - One More Time", "Daft Punk - Aerodynamic"],
            "count": 2,
        },
    )

    parsed = album_urls.parse_album_url("https://open.spotify.com/album/2noRn2Aes5aoNVsU6iWThc")
    assert parsed == {
        "service": "spotify",
        "id": "2noRn2Aes5aoNVsU6iWThc",
        "artist": "Daft Punk",
        "album": "Discovery",
    }


def test_spotify_playlist_url_is_not_recognised_as_an_album():
    album_urls = _import_album_urls_or_skip()
    assert album_urls.parse_album_url("https://open.spotify.com/playlist/37i9dQZEVXbMwmF30ppw50") is None


def test_spotify_track_url_is_not_recognised_as_an_album():
    album_urls = _import_album_urls_or_skip()
    assert album_urls.parse_album_url("https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC") is None


def test_apple_music_album_url_is_recognised(monkeypatch):
    album_urls = _import_album_urls_or_skip()
    monkeypatch.setattr(
        album_urls, "fetch_apple_music_playlist",
        lambda url, **kw: {
            "playlist_name": "OK Computer",
            "tracks": ["Radiohead - Airbag", "Radiohead - Paranoid Android"],
            "count": 2,
        },
    )

    parsed = album_urls.parse_album_url("https://music.apple.com/gb/album/ok-computer/1097864980")
    assert parsed == {
        "service": "apple",
        "id": "1097864980",
        "artist": "Radiohead",
        "album": "OK Computer",
    }


def test_apple_music_album_url_with_track_deeplink_still_recognised(monkeypatch):
    album_urls = _import_album_urls_or_skip()
    monkeypatch.setattr(
        album_urls, "fetch_apple_music_playlist",
        lambda url, **kw: {"playlist_name": "OK Computer", "tracks": ["Radiohead - Airbag"], "count": 1},
    )
    parsed = album_urls.parse_album_url("https://music.apple.com/gb/album/ok-computer/1097864980?i=1097865010")
    assert parsed["service"] == "apple"
    assert parsed["id"] == "1097864980"


def test_apple_music_playlist_url_is_not_recognised_as_an_album():
    album_urls = _import_album_urls_or_skip()
    assert album_urls.parse_album_url("https://music.apple.com/gb/playlist/todays-hits/pl.abc123") is None


def test_apple_music_song_url_is_not_recognised_as_an_album():
    album_urls = _import_album_urls_or_skip()
    assert album_urls.parse_album_url("https://music.apple.com/gb/song/airbag/1097865010") is None


@pytest.mark.parametrize("url", [
    "https://music.youtube.com/playlist?list=OLAK5uy_kmPseHwrPCbC1DsXhwQjLdcpQjZzDNKOU",
    "https://www.youtube.com/playlist?list=OLAK5uy_kmPseHwrPCbC1DsXhwQjLdcpQjZzDNKOU",
    "https://music.amazon.co.uk/albums/B00136RSDG",
    "https://www.beatport.com/release/ok-computer/12345",
    "https://monochrome.tf/album/12345",
    "not a url at all",
    "",
    None,
])
def test_out_of_scope_and_junk_urls_are_not_recognised(url):
    album_urls = _import_album_urls_or_skip()
    assert album_urls.parse_album_url(url) is None


def test_spotify_album_scrape_failure_still_recognises_the_url(monkeypatch):
    """A dead link or service hiccup shouldn't make a genuine album url look
    unrecognised; it just comes back with no artist/album scraped."""
    album_urls = _import_album_urls_or_skip()
    import watched_playlists

    def _boom(url, **kw):
        raise RuntimeError("Spotify had a moment")

    monkeypatch.setattr(watched_playlists, "_fetch_spotify_playlist_embed", _boom)

    parsed = album_urls.parse_album_url("https://open.spotify.com/album/2noRn2Aes5aoNVsU6iWThc")
    assert parsed == {"service": "spotify", "id": "2noRn2Aes5aoNVsU6iWThc", "artist": None, "album": None}


# ---------------------------------------------------------------------------
# resolve_album_url
# ---------------------------------------------------------------------------

def test_resolve_album_url_not_recognised():
    album_urls = _import_album_urls_or_skip()
    result = album_urls.resolve_album_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert result["recognised"] is False
    assert result["service"] is None
    assert result["confident"] is False
    assert result["match"] is None


def test_resolve_album_url_confident_match_passes_straight_through(monkeypatch):
    album_urls = _import_album_urls_or_skip()
    monkeypatch.setattr(
        album_urls, "parse_album_url",
        lambda url: {"service": "spotify", "id": "abc123", "artist": "Daft Punk", "album": "Discovery"},
    )
    monkeypatch.setattr(
        album_urls, "match_album_to_musicbrainz",
        lambda artist, album: {
            "match": {"release_group_mbid": "rg-1", "release_mbid": "rel-1", "title": album, "artist": artist},
            "confidence": 0.95, "confident": True, "candidates": [],
        },
    )

    result = album_urls.resolve_album_url("https://open.spotify.com/album/abc123")
    assert result["recognised"] is True
    assert result["service"] == "spotify"
    assert result["confident"] is True
    assert result["match"]["release_group_mbid"] == "rg-1"


def test_resolve_album_url_weak_match_asks_for_confirmation(monkeypatch):
    album_urls = _import_album_urls_or_skip()
    monkeypatch.setattr(
        album_urls, "parse_album_url",
        lambda url: {"service": "apple", "id": "999", "artist": "Some Artist", "album": "Some Album"},
    )
    monkeypatch.setattr(
        album_urls, "match_album_to_musicbrainz",
        lambda artist, album: {
            "match": {"release_group_mbid": "rg-2", "release_mbid": None, "title": "Close But Not Quite", "artist": artist},
            "confidence": 0.4, "confident": False,
            "candidates": [{"release_group_mbid": "rg-2", "title": "Close But Not Quite"}],
        },
    )

    result = album_urls.resolve_album_url("https://music.apple.com/gb/album/some-album/999")
    assert result["recognised"] is True
    assert result["confident"] is False
    assert result["candidates"]


def test_resolve_album_url_scrape_failure_skips_matching(monkeypatch):
    """No album title scraped -> nothing to match against, and we must not
    even try (an empty-string album search is a waste of a MusicBrainz call)."""
    album_urls = _import_album_urls_or_skip()
    monkeypatch.setattr(
        album_urls, "parse_album_url",
        lambda url: {"service": "spotify", "id": "abc123", "artist": None, "album": None},
    )
    called = []
    monkeypatch.setattr(
        album_urls, "match_album_to_musicbrainz",
        lambda artist, album: called.append((artist, album)),
    )

    result = album_urls.resolve_album_url("https://open.spotify.com/album/abc123")
    assert result["recognised"] is True
    assert result["match"] is None
    assert result["confident"] is False
    assert called == []
