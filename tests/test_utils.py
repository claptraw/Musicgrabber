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


def test_cap_filename_stem_keeps_short_names_untouched():
    from utils import cap_filename_stem

    stem = "Daft Punk - Get Lucky"
    assert cap_filename_stem(stem) == stem


def test_cap_filename_stem_fits_within_name_max_with_extension():
    # The real-world crasher: a 30-artist remix that combined to 257 bytes and
    # blew up with [Errno 36] mid-download, stalling the rest of the playlist.
    from utils import cap_filename_stem, sanitize_filename
    from constants import MAX_FILENAME_BYTES

    artist = ("Krept & Konan, Abra Cadabra, BackRoad Gee, French Montana, Clavish, "
              "Beenie Man, Pa Salieu, Bandokay, Double Lz, DoRoad")
    title = ("Dat Way (Remix) [feat. Abra Cadabra, Backroad Gee, French Montana, "
             "Clavish, Beenie Man, Pa Salieu, Bandokay, Double Lz, K-Trap & DoRoad]")
    raw = f"{sanitize_filename(artist)} - {sanitize_filename(title)}"
    assert len(raw.encode("utf-8")) > MAX_FILENAME_BYTES  # would have crashed

    capped = cap_filename_stem(raw)
    # Stem + any extension we use, plus yt-dlp's worst-case temp suffix, must fit.
    assert len((capped + ".f399.webm.part").encode("utf-8")) <= MAX_FILENAME_BYTES


def test_truncate_to_bytes_never_splits_a_multibyte_glyph():
    from utils import truncate_to_bytes

    # Each emoji is 4 UTF-8 bytes; truncating to 10 must drop to 8 (2 glyphs),
    # never leave a mangled half-character.
    out = truncate_to_bytes("🎵🎵🎵", 10)
    assert out == "🎵🎵"
    out.encode("utf-8")  # must be valid UTF-8, would raise otherwise


def test_sanitize_filename_caps_multibyte_to_byte_budget():
    from utils import sanitize_filename
    from constants import MAX_FILENAME_BYTES

    # 200 CJK chars is under the char cap but 600 bytes, well over NAME_MAX.
    out = sanitize_filename("あ" * 200)
    assert len(out.encode("utf-8")) <= MAX_FILENAME_BYTES
