import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_sanitize_playlist_name_uses_url_fallback_when_name_sanitizes_empty():
    from utils import sanitize_playlist_name

    assert (
        sanitize_playlist_name("///", "https://monochrome.tf/playlist/0dfc3b10-fbdb-4419-bf54-11b90051fa6c")
        == "0dfc3b10-fbdb-4419-bf54-11b90051fa6c"
    )


def test_sanitize_playlist_name_keeps_extended_characters_and_removes_path_separators():
    from utils import sanitize_playlist_name

    assert sanitize_playlist_name("Beyoncé / Café ☕") == "Beyoncé Café ☕"


def test_sanitize_playlist_name_never_returns_empty():
    from utils import sanitize_playlist_name

    assert sanitize_playlist_name("///") == "Playlist"
