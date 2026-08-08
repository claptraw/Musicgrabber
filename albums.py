"""
MusicGrabber - Album Download Pipeline

Everything involved in turning a MusicBrainz release into a queued bulk import:
existing/missing track comparison, the .albuminfo sidecar, the in-flight
duplicate guard, and handing the missing tracks to the bulk importer.

This used to live inline in app.py's /api/albums/download route. It moved here
so watched_artists.py (artist album-following) can call the same pipeline
without app.py importing watched_artists.py importing app.py in a circle that
would make Python very cross indeed.
"""

import contextlib
import json
import os
import re
import tempfile
import unicodedata
from pathlib import Path

from constants import AUDIO_EXTENSIONS
from db import db_conn
from downloads import rebuild_album_m3u
from metadata import fetch_album_tracks
from settings import get_albums_dir, get_setting_bool
from utils import clean_title, sanitize_filename, sanitize_playlist_name, set_file_permissions


class AlbumTracklistUnavailable(ValueError):
    """Raised when MusicBrainz has no tracklist for the requested release.

    A plain exception rather than an HTTPException, since this module has no
    business knowing about FastAPI. The route translates it back into the same
    404 it always returned.
    """


def _album_track_stem(artist: str, title: str, user_id: str | None = None) -> str:
    """Build the expected filename stem for an album track in override_dir mode."""
    safe_title = sanitize_filename(title or "") or "Unknown Title"
    if not get_setting_bool("organise_by_artist", True, user_id=user_id):
        safe_artist = sanitize_filename(artist or "Unknown Artist")
        return f"{safe_artist} - {safe_title}"
    return safe_title


def _normalise_album_match_text(text: str) -> str:
    """Normalise track titles for album-track matching."""
    t = clean_title(text or "")
    t = t.replace("’", "'").replace("‘", "'").replace("`", "'")
    t = unicodedata.normalize("NFKD", t)
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = re.sub(r"\b(?:feat\.?|ft\.?|featuring)\b.*$", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*[\(\[].*?[\)\]]", "", t)
    t = re.sub(r"[^a-z0-9]+", " ", t.lower())
    return re.sub(r"\s+", " ", t).strip()


def album_on_disk(artist: str, album_title: str, user_id: str | None = None) -> bool:
    """Cheap already-have-it check: does the album folder exist with any audio in it?

    Unlike album_track_status, this never touches MusicBrainz. Used by list views
    (e.g. GET /api/watched-artists/{id}/albums) where fetching every release's
    tracklist just to render a badge would hammer MB for nothing.
    """
    album_dir = get_albums_dir(user_id=user_id) / sanitize_filename(artist) / sanitize_filename(album_title)
    if not album_dir.exists():
        return False
    return any(p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS for p in album_dir.iterdir())


def album_track_status(artist: str, album_title: str, tracks: list[dict], user_id: str | None = None) -> dict:
    """Return existing/missing status for tracklist against the target album directory."""
    album_dir = get_albums_dir(user_id=user_id) / sanitize_filename(artist) / sanitize_filename(album_title)
    audio_files = [p for p in album_dir.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS] if album_dir.exists() else []
    audio_stems = [p.stem for p in audio_files]

    track_status = []
    for t in tracks:
        title = (t.get("title") or "").strip()
        stem = _album_track_stem(artist, title, user_id=user_id)
        exists_exact = any((album_dir / f"{stem}{ext}").exists() for ext in AUDIO_EXTENSIONS)
        exists_fuzzy = False
        if not exists_exact:
            norm_title = _normalise_album_match_text(title)
            if norm_title:
                for file_stem in audio_stems:
                    norm_stem = _normalise_album_match_text(file_stem)
                    if not norm_stem:
                        continue
                    if norm_stem == norm_title or norm_stem.endswith(f" {norm_title}"):
                        exists_fuzzy = True
                        break
        exists = bool(exists_exact or exists_fuzzy)
        track_status.append({
            "position": t.get("position"),
            "title": title,
            "isrc": t.get("isrc"),
            "exists": exists,
        })

    existing_tracks = [t for t in track_status if t["exists"]]
    missing_tracks = [t for t in track_status if not t["exists"]]
    m3u_files = sorted([p.name for p in album_dir.glob("*.m3u") if p.is_file()], key=str.casefold) if album_dir.exists() else []
    return {
        "album_dir": album_dir,
        "tracks": track_status,
        "existing_tracks": existing_tracks,
        "missing_tracks": missing_tracks,
        "m3u_files": m3u_files,
    }


def queue_album_download(
    artist: str,
    album_title: str,
    release_mbid: str,
    make_m3u: bool = False,
    m3u_name: str = "",
    convert_audio: bool | None = None,
    user_id: str | None = None,
) -> dict:
    """Queue a full album for download.

    Fetches the tracklist from MusicBrainz, creates a bulk import job routed to
    Albums/Artist/Album/ instead of the normal Singles layout, and queues only
    the tracks not already present. Returns the same dict shape the
    /api/albums/download route has always returned (import_id, track_count,
    queued_count, existing_count, missing_count, album_dir, plus optional
    warning/m3u/already_queued fields).

    Raises AlbumTracklistUnavailable if MusicBrainz has no tracklist for the
    release.
    """
    tracks = fetch_album_tracks(release_mbid)
    if not tracks:
        raise AlbumTracklistUnavailable("Could not fetch tracklist from MusicBrainz")

    status = album_track_status(artist, album_title, tracks, user_id=user_id)
    album_dir = status["album_dir"]
    missing_tracks = status["missing_tracks"]
    album_dir.mkdir(parents=True, exist_ok=True)

    # Write a .albuminfo sidecar so the picker can restore MBID context without a DB.
    # Atomic write (mkstemp + rename) so a crash mid-write leaves nothing corrupt.
    albuminfo_path = album_dir / ".albuminfo"
    if not albuminfo_path.exists():
        tmp_fd, tmp_path = tempfile.mkstemp(dir=album_dir, suffix=".albuminfo.tmp")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"artist": artist, "album": album_title, "release_mbid": release_mbid}, indent=2))
            Path(tmp_path).rename(albuminfo_path)
            set_file_permissions(albuminfo_path)
        except Exception:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)

    # Refuse to queue if an in-flight import is already targeting this album directory.
    # Catches double-clicks and impatient re-submissions before any files have landed.
    with db_conn() as conn:
        inflight = conn.execute(
            """SELECT id FROM bulk_imports
               WHERE override_dir = ?
                 AND status IN ('pending', 'processing')
                 AND completed_at IS NULL
               LIMIT 1""",
            (str(album_dir),),
        ).fetchone()
    if inflight:
        return {
            "import_id": inflight[0],
            "track_count": len(tracks),
            "queued_count": len(missing_tracks),
            "existing_count": len(status["existing_tracks"]),
            "missing_count": len(missing_tracks),
            "album_dir": str(album_dir),
            "warning": "Download already in progress for this album.",
            "already_queued": True,
        }

    # Queue only missing tracks; already-present tracks are left as-is.
    # ISRC list runs parallel to track_pairs (same list, same order) so the bulk
    # importer can try the exact studio recording before falling back to free text.
    track_pairs = [(artist, t["title"]) for t in missing_tracks]
    track_isrcs = [t.get("isrc") for t in missing_tracks]

    if not track_pairs:
        updated_m3u = None
        if make_m3u:
            updated_m3u = rebuild_album_m3u(album_dir, m3u_name or f"{artist} - {album_title}")
        return {
            "import_id": None,
            "track_count": len(tracks),
            "queued_count": 0,
            "existing_count": len(status["existing_tracks"]),
            "missing_count": 0,
            "album_dir": str(album_dir),
            "m3u_updated": bool(updated_m3u),
            "m3u_path": str(updated_m3u) if updated_m3u else None,
            "warning": "Album already exists on disk. Nothing queued." + (" Existing M3U updated." if updated_m3u else ""),
        }

    from bulk_import import start_bulk_import_for_tracks
    import_id = start_bulk_import_for_tracks(
        tracks=track_pairs,
        track_isrcs=track_isrcs,
        convert_audio=convert_audio,
        user_id=user_id,
        override_dir=str(album_dir),
        album_release_mbid=release_mbid,
        album_total_tracks=len(tracks),
    )

    # If M3U requested, store the album details so create_bulk_playlist can pick it up.
    # We repurpose the existing create_playlist + playlist_name mechanism.
    if make_m3u:
        playlist_label = m3u_name or f"{artist} - {album_title}"
        if playlist_label.lower().endswith(".m3u"):
            playlist_label = playlist_label[:-4]
        playlist_label = sanitize_playlist_name(playlist_label, f"{artist} - {album_title}")
        with db_conn() as conn:
            conn.execute(
                "UPDATE bulk_imports SET create_playlist = 1, playlist_name = ? WHERE id = ?",
                (playlist_label, import_id)
            )
            conn.commit()

    return {
        "import_id": import_id,
        "track_count": len(tracks),
        "queued_count": len(track_pairs),
        "existing_count": len(status["existing_tracks"]),
        "missing_count": len(missing_tracks),
        "album_dir": str(album_dir),
        "warning": f"{len(status['existing_tracks'])} track(s) already existed; queued {len(track_pairs)} missing track(s).",
    }
