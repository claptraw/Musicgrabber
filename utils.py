"""
MusicGrabber - Common Utilities

Filename sanitisation, title cleaning, track hashing, duplicate detection.
"""

import hashlib
import os
import re
import secrets
import threading
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, unquote, urlparse

import shutil

from constants import (
    AUDIO_EXTENSIONS, MAX_FILENAME_LENGTH,
)
from settings import get_singles_dir, get_albums_dir, get_download_dir, get_playlists_dir, get_trash_dir, get_setting


def sanitize_filename(name: str) -> str:
    """Remove/replace characters that are problematic in filenames"""
    name = re.sub(r'[<>:"/\\|?*]', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name[:MAX_FILENAME_LENGTH]


def _fallback_name_from_url(value: str) -> str:
    """Return a short label from a playlist/import URL for filesystem fallback."""
    try:
        parsed = urlparse(value)
    except Exception:
        return value
    if not parsed.scheme or not parsed.netloc:
        return value

    query = parse_qs(parsed.query)
    for key in ("list", "id"):
        if query.get(key):
            return unquote(query[key][0])

    path_parts = [unquote(p) for p in parsed.path.split("/") if p]
    if path_parts:
        return path_parts[-1]
    return parsed.netloc.removeprefix("www.")


def sanitize_playlist_name(name: str | None, fallback: str | None = None) -> str:
    """Return a non-empty filesystem-safe playlist/M3U stem.

    `fallback` can be an import URL, playlist ID, or other stable label. It is
    only used when the user/upstream name sanitizes to empty.
    """
    safe = sanitize_filename(name or "")
    if safe:
        return safe

    fallback_label = _fallback_name_from_url(fallback or "")
    safe = sanitize_filename(fallback_label)
    return safe or "Playlist"


def is_valid_youtube_id(video_id: str) -> bool:
    """Basic validation for YouTube video/playlist IDs."""
    return bool(re.match(r'^[A-Za-z0-9_-]+$', video_id or ""))


def clean_title(title: str) -> str:
    """Clean up YouTube title by removing common suffixes and annotations"""
    # Remove common bracketed annotations (lyrics, remaster, official, etc.)
    title = re.sub(
        r'\s*[\(\[][^\)\]]*(?:official|lyrics?|lyric|audio|h[dq]|remaster|music\s*video)[^\)\]]*[\)\]]',
        '',
        title,
        flags=re.IGNORECASE
    )
    # Remove standalone "Official (Music) Video" text
    title = re.sub(r'\s*official\s*(music\s*)?video', '', title, flags=re.IGNORECASE)

    # Remove trailing dash-separated suffixes: "- Official Audio", "- Official Music Video", etc.
    title = re.sub(
        r'\s+[-–—]\s+(?:official\s+)?(?:music\s+)?(?:audio|video|lyric\s+video)\s*$',
        '',
        title,
        flags=re.IGNORECASE
    )

    # Remove trailing pipe-separated promo/session suffixes.
    # Example: "A Couple Minutes | A COLORS SHOW" -> "A Couple Minutes"
    title = re.sub(
        r'\s*\|\s*(?:a\s+colors?\s+show|colors?\s+show|'
        r'(?:official\s+)?(?:music\s+)?(?:audio|video)|'
        r'lyric(?:\s+video|s)?|visuali[sz]er|live\s+session|session)\s*$',
        '',
        title,
        flags=re.IGNORECASE,
    )

    # Strip any trailing dangling separators left after cleanup (e.g. "Title -")
    title = re.sub(r'\s+[-–—]\s*$', '', title)

    return title.strip()


def _normalise_duplicate_stem(stem: str) -> str:
    """Normalise a filename stem for duplicate comparisons."""
    s = clean_title(stem or "")
    s = s.replace("’", "'").replace("‘", "'").replace("`", "'")
    s = re.sub(r"\s*[\(\[].*?[\)\]]", "", s)
    s = re.sub(r"[^a-z0-9]+", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def normalise_track_for_hash(artist: str, title: str) -> str:
    """Normalise artist/title for consistent hashing across playlist checks.

    Examples:
    "Daft Punk feat. Pharrell Williams | Get Lucky (Radio Edit)"
        -> "daft punk | get lucky"
    "SZA - Kill Bill [Official Lyric Video]"
        -> "sza | kill bill"
    """
    text = f"{artist}|{title}".lower()
    # Remove feat./ft./featuring and everything after
    text = re.sub(r'\s*(feat\.?|ft\.?|featuring)\s+.*?\|', '|', text)
    text = re.sub(r'\s*(feat\.?|ft\.?|featuring)\s+.*$', '', text)
    # Remove common suffixes in parens/brackets
    text = re.sub(r'\s*[\(\[].*?[\)\]]', '', text)
    # Remove punctuation except pipe separator
    text = re.sub(r'[^\w\s|]', '', text)
    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def hash_track(artist: str, title: str) -> str:
    """Generate hash for track identification in watched playlists"""
    normalised = normalise_track_for_hash(artist, title)
    return hashlib.sha256(normalised.encode()).hexdigest()[:16]


def extract_artist_title(full_title: str, channel: str) -> tuple[str, str]:
    """Try to extract artist and title from YouTube video title"""
    # Guard against None  -  some YouTube videos have no channel/uploader in their metadata
    full_title = full_title or "Unknown Title"
    channel = channel or "Unknown Artist"

    # Some uploaders prefix titles with a label or channel tag: "[UKF release] Artist - Title"
    # or "(Monstercat) Artist - Title". Strip the prefix and try the clean remainder first,
    # but only when what's left starts with a real word (no leading punctuation), so we don't
    # accidentally eat bracketed artist names like "[IVY], A Little Sound - Can't Love Me".
    _prefix_stripped = re.sub(r'^\s*[\(\[][^\)\]]+[\)\]]\s*', '', full_title)
    titles_to_try = [full_title]
    if _prefix_stripped and _prefix_stripped != full_title and re.match(r'^[A-Za-z0-9]', _prefix_stripped):
        titles_to_try = [_prefix_stripped, full_title]

    # Common patterns: "Artist -- Title", "Artist - Title", "Artist  -  Title", "Artist | Title"
    # Require spaces around hyphens to avoid splitting compound words like "T-4"
    patterns = [
        r'^(.+?)\s+--\s+(.+)$',
        r'^(.+?)\s+[-–—]\s+(.+)$',
        r'^(.+?)\s*\|\s*(.+)$',
    ]

    for try_title in titles_to_try:
        for pattern in patterns:
            match = re.match(pattern, try_title)
            if match:
                artist, title = match.groups()
                cleaned_title = clean_title(title)
                # If suffix stripping nukes the whole title (e.g. "... - Official Video"),
                # treat this split as invalid and try other patterns/fallback.
                if cleaned_title:
                    return artist.strip(), cleaned_title

    # Fallback: use channel as artist, full title as title
    # Remove common channel suffixes like "VEVO", "Official", "- Topic"
    artist = re.sub(r'\s*[-–—]\s*Topic$', '', channel, flags=re.IGNORECASE)
    artist = re.sub(r'\s*(VEVO|Official|Music)$', '', artist, flags=re.IGNORECASE)
    # Strip common label/distributor prefixes used in channel names:
    # "Premiere Eczko" → "Eczko", "Monstercat Silk" → "Silk", etc.
    artist = re.sub(r'^(?:Premiere|Monstercat|NCS|UKF|Proximity|Majestic|Trap Nation|Bass Nation)\s+', '', artist, flags=re.IGNORECASE)
    fallback_title = clean_title(full_title)
    if not fallback_title:
        fallback_title = full_title.strip() or "Unknown Title"
    return artist.strip() or "Unknown Artist", fallback_title


def _find_audio_match_in_dir(directory: Path, stems: list[str]) -> Optional[Path]:
    """Return the first matching audio file inside one directory."""
    if not directory.exists():
        return None

    for stem in stems:
        for ext in AUDIO_EXTENSIONS:
            expected_file = directory / f"{stem}{ext}"
            if expected_file.exists():
                return expected_file

    stem_lowers = {s.lower() for s in stems if s}
    if stem_lowers:
        for ext in AUDIO_EXTENSIONS:
            for file in directory.glob(f"*{ext}"):
                if file.stem.lower() in stem_lowers:
                    return file

    norm_targets = {_normalise_duplicate_stem(s) for s in stems if s}
    if norm_targets:
        for ext in AUDIO_EXTENSIONS:
            for file in directory.glob(f"*{ext}"):
                if _normalise_duplicate_stem(file.stem) in norm_targets:
                    return file

    return None


def check_duplicate(artist: str, title: str, user_id: str | None = None) -> Optional[Path]:
    """Check if a track already exists anywhere in the local library.

    Searches Singles in both flat and artist-subfolder layouts, then scans
    playlist folders too so a track already downloaded for one playlist can be
    reused by another without being treated as missing.
    """
    try:
        sanitized_title = sanitize_filename(title)
        sanitized_artist = sanitize_filename(artist or "")
        artist_title_stem = f"{sanitized_artist} - {sanitized_title}" if sanitized_artist else sanitized_title
        stems = [s for s in (sanitized_title, artist_title_stem) if s]

        checks = [
            get_download_dir(artist, user_id=user_id),
            get_singles_dir(user_id=user_id) / sanitize_filename(artist),
            get_singles_dir(user_id=user_id),
        ]
        seen = set()
        for directory in checks:
            d_str = str(directory)
            if d_str in seen:
                continue
            seen.add(d_str)
            match = _find_audio_match_in_dir(directory, stems)
            if match:
                return match

        # Auto-album routing moves singles into Artist/Album/ subfolders.
        # Scan one level deeper so re-downloads find the routed copy.
        artist_dirs_to_scan = []
        singles_artist = get_singles_dir(user_id=user_id) / sanitize_filename(artist or "")
        if singles_artist.exists():
            artist_dirs_to_scan.append(singles_artist)
        albums_artist = get_albums_dir(user_id=user_id) / sanitize_filename(artist or "")
        if albums_artist.exists():
            artist_dirs_to_scan.append(albums_artist)
        for artist_dir in artist_dirs_to_scan:
            try:
                for subdir in artist_dir.iterdir():
                    if not subdir.is_dir():
                        continue
                    d_str = str(subdir)
                    if d_str in seen:
                        continue
                    seen.add(d_str)
                    match = _find_audio_match_in_dir(subdir, stems)
                    if match:
                        return match
            except OSError:
                pass

        playlists_dir = get_playlists_dir(user_id=user_id)
        if playlists_dir and playlists_dir.exists():
            for playlist_dir in sorted(playlists_dir.iterdir(), key=lambda p: p.name.casefold()):
                if not playlist_dir.is_dir():
                    continue
                d_str = str(playlist_dir)
                if d_str in seen:
                    continue
                seen.add(d_str)
                match = _find_audio_match_in_dir(playlist_dir, stems)
                if match:
                    return match

        return None
    except Exception:
        return None


def move_to_trash(file_path: Path, user_id: str | None = None) -> Path | None:
    """Move a file to the trash directory, preserving its relative path.

    Returns the trash path on success, None on failure. Also moves the
    matching .lrc sidecar if one exists.
    """
    try:
        music_dir = Path(get_setting("music_dir", str(Path(os.getenv("MUSIC_DIR", "/music"))), user_id=user_id))
        trash_dir = get_trash_dir(user_id=user_id)
        rel = file_path.relative_to(music_dir)
        dest = trash_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(file_path), str(dest))
        set_file_permissions(dest)

        # Drag the lyrics along for the ride
        lrc = file_path.with_suffix(".lrc")
        if lrc.exists():
            lrc_dest = dest.with_suffix(".lrc")
            shutil.move(str(lrc), str(lrc_dest))
            set_file_permissions(lrc_dest)

        # Tidy up empty parent directories back to music_dir
        parent = file_path.parent
        while parent != music_dir and str(parent).startswith(str(music_dir)):
            try:
                if parent.exists() and not any(parent.iterdir()):
                    parent.rmdir()
            except OSError:
                break
            parent = parent.parent

        return dest
    except Exception as e:
        print(f"[trash] Failed to move {file_path} to trash: {e}")
        return None


def restore_from_trash(trash_path: Path, user_id: str | None = None) -> Path | None:
    """Restore a file from trash back to its original library location.

    Returns the restored path on success, None on failure.
    """
    try:
        trash_dir = get_trash_dir(user_id=user_id)
        music_dir = Path(get_setting("music_dir", str(Path(os.getenv("MUSIC_DIR", "/music"))), user_id=user_id))
        rel = trash_path.relative_to(trash_dir)
        dest = music_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(trash_path), str(dest))
        set_file_permissions(dest)

        # Restore lyrics sidecar too
        lrc_trash = trash_path.with_suffix(".lrc")
        if lrc_trash.exists():
            lrc_dest = dest.with_suffix(".lrc")
            shutil.move(str(lrc_trash), str(lrc_dest))
            set_file_permissions(lrc_dest)

        # Tidy up empty trash subdirectories
        parent = trash_path.parent
        while parent != trash_dir and str(parent).startswith(str(trash_dir)):
            try:
                if parent.exists() and not any(parent.iterdir()):
                    parent.rmdir()
            except OSError:
                break
            parent = parent.parent

        return dest
    except Exception as e:
        print(f"[trash] Failed to restore {trash_path}: {e}")
        return None


def check_trash_duplicate(artist: str, title: str, user_id: str | None = None) -> Path | None:
    """Check if a trashed copy of this track exists and can be restored.

    Scans .trash/ mirroring the same directory structure as the library.
    Returns the trash path if found, None otherwise.
    """
    try:
        trash_dir = get_trash_dir(user_id=user_id)
        if not trash_dir.exists():
            return None

        sanitized_title = sanitize_filename(title)
        sanitized_artist = sanitize_filename(artist or "")
        artist_title_stem = f"{sanitized_artist} - {sanitized_title}" if sanitized_artist else sanitized_title
        stems = [s for s in (sanitized_title, artist_title_stem) if s]

        # Walk every directory under .trash/ looking for a match
        for dirpath, _dirnames, _filenames in os.walk(trash_dir):
            match = _find_audio_match_in_dir(Path(dirpath), stems)
            if match:
                return match

        return None
    except Exception:
        return None


def set_file_permissions(file_path: Path):
    """Set file permissions for NAS/SMB compatibility.

    Files get 0o666 or 0o777 depending on the setting.
    Directories need the execute bit to be traversable, so they always get 0o777.
    Also fixes permissions on parent directories up to (but not including) the
    music root, because mkdir() inherits the container's umask, which leaves
    artist/album folders at 0o755 and upsets NAS shares.
    """
    mode_str = get_setting("file_permissions", "666")
    file_mode = 0o777 if mode_str == "777" else 0o666
    # Directories always need execute bits or nobody can cd into them
    dir_mode = 0o777

    try:
        if file_path.is_file():
            os.chmod(file_path, file_mode)
        elif file_path.is_dir():
            os.chmod(file_path, dir_mode)
    except OSError:
        pass

    # Walk up and fix parent directories that mkdir may have created with
    # restrictive umask permissions. Stop at the music root so we don't
    # go stomping on system directories.
    try:
        music_root = Path(get_setting("music_dir", str(Path(os.getenv("MUSIC_DIR", "/music")))))
        parent = file_path.parent if file_path.is_file() else file_path
        while parent != music_root and str(parent).startswith(str(music_root)):
            os.chmod(parent, dir_mode)
            parent = parent.parent
    except OSError:
        pass  # Best effort; we might not own every directory in the chain


def subsonic_auth_params(username: str, password: str) -> dict:
    """Build Subsonic API authentication parameters (for Navidrome)."""
    salt = secrets.token_hex(8)
    token = hashlib.md5(f"{password}{salt}".encode()).hexdigest()
    return {
        "u": username,
        "t": token,
        "s": salt,
        "v": "1.16.1",
        "c": "MusicGrabber",
        "f": "json",
    }


def spawn_daemon_thread(target, *args, **kwargs) -> None:
    """Start a daemon thread for background work."""
    thread = threading.Thread(target=target, args=args, kwargs=kwargs, daemon=True)
    thread.start()
