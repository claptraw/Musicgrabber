"""
MusicGrabber - Download Processing

Single track, playlist, and Soulseek download handlers.
Library scan triggers and M3U playlist generation.
"""

import base64
import json
import os
import re
import sqlite3
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

from constants import (
    AUDIO_EXTENSIONS,
    MUSIC_DIR,
    MONOCHROME_API_URL, MONOCHROME_COVER_BASE, TIMEOUT_MONOCHROME_API,
    TIMEOUT_YTDLP_INFO, TIMEOUT_YTDLP_SEARCH, TIMEOUT_YTDLP_DOWNLOAD, TIMEOUT_YTDLP_PLAYLIST,
    TIMEOUT_FFMPEG_CONVERT, TIMEOUT_HTTP_REQUEST,
    YTDLP_403_MAX_RETRIES, YTDLP_403_RETRY_DELAY,
    SLSKD_MAX_RETRIES, TIMEOUT_SLSKD_SEARCH,
    PLAYLIST_WAIT_MAX, PLAYLIST_WAIT_INTERVAL,
    YOUTUBE_SEARCH_MULTIPLIER, YOUTUBE_SEARCH_MIN_FETCH,
    MAX_AUDIO_START_OFFSET_SECS,
    MB_DURATION_TOLERANCE,
)
from db import db_conn
from metadata import lookup_metadata, fetch_lyrics, save_lyrics_file, apply_metadata_to_file
from notifications import send_notification
from settings import get_setting, get_setting_bool, get_setting_int, get_singles_dir, get_download_dir, get_playlists_dir
from slskd import (
    download_from_slskd, extract_track_info_from_path,
    search_slskd, should_retry_slskd_error,
)
from utils import (
    sanitize_filename,
    extract_artist_title,
    check_duplicate,
    is_valid_youtube_id,
    set_file_permissions,
    subsonic_auth_params,
)
from youtube import (
    _ytdlp_base_args, _is_ytdlp_403, _strip_cookies_args,
    _should_retry_without_cookies, _sleep_if_botted, _note_bot_block, _note_cookie_failure,
    parse_youtube_search_results,
)


_AUDIO_RECHECK_MAX_ATTEMPTS = 2
_AUDIO_RESEARCH_MAX_ALTERNATES = 2


def _default_metadata_source(source: str) -> str:
    """Metadata fallback label when no AcoustID/MusicBrainz match is available."""
    source_name = (source or "youtube").lower()
    if source_name == "soundcloud":
        return "soundcloud_guessed"
    if source_name == "monochrome":
        return "monochrome_guessed"
    if source_name == "soulseek":
        return "soulseek_guessed"
    return "youtube_guessed"


def _safe_sanitized_title(title: str, fallback: str) -> str:
    """Return a filesystem-safe non-empty title for output templates/lookup."""
    cleaned = sanitize_filename(title or "")
    if cleaned:
        return cleaned
    fallback_cleaned = sanitize_filename(fallback or "")
    return fallback_cleaned or "Unknown Title"


def _output_stem(artist: str, title: str, fallback: str) -> str:
    """Return the output filename stem for a track.

    In flat (no-artist-subfolder) mode: 'Artist - Title'
    In organised mode: 'Title'

    The artist prefix in flat mode saves you from a directory full of files
    called 'Track 1.flac' with no idea who they belong to.
    """
    safe_title = _safe_sanitized_title(title, fallback)
    if not get_setting_bool("organise_by_artist", True):
        safe_artist = sanitize_filename(artist or "Unknown Artist")
        return f"{safe_artist} - {safe_title}"
    return safe_title


def _playlist_stem(artist: str, title: str, fallback: str) -> str:
    """Return 'Artist - Title' filename stem for tracks inside a playlist folder.

    Always flat  -  no organise_by_artist logic needed since the playlist folder
    itself provides the organisational context.
    """
    safe_artist = sanitize_filename(artist or "Unknown Artist")
    safe_title = _safe_sanitized_title(title, fallback)
    return f"{safe_artist} - {safe_title}"


def _find_downloaded_audio_or_raise(artist_dir: Path, sanitized_title: str) -> Path:
    """Find downloaded audio file by expected base name, or raise with useful context."""
    for ext in AUDIO_EXTENSIONS:
        candidate = artist_dir / f"{sanitized_title}{ext}"
        if candidate.exists():
            return candidate

    seen_files = []
    try:
        seen_files = [p.name for p in artist_dir.iterdir() if p.is_file()][:8]
    except OSError:
        pass
    raise Exception(
        f"Download completed but expected '{sanitized_title}' audio file not found in {artist_dir}. "
        f"Found files: {', '.join(seen_files) if seen_files else 'none'}"
    )


def _validate_audio_integrity(file_path: Path) -> tuple[bool, str, float]:
    """Validate that a downloaded audio file is decodable and non-empty.

    Uses ffprobe to ensure at least one audio stream exists, duration is > 0,
    and the start offset is not suspiciously large (preview segment indicator).

    Returns (ok, reason, actual_duration_secs). Duration is 0.0 on failure.
    """
    if not file_path.exists():
        return False, "File not found after download", 0.0
    try:
        if file_path.stat().st_size <= 0:
            return False, "Downloaded file is empty", 0.0
    except OSError as e:
        return False, f"Unable to stat file: {e}", 0.0

    try:
        probe = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration,size,start_time:stream=codec_type,codec_name,duration",
                "-of", "json",
                str(file_path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception as e:
        return False, f"ffprobe execution failed: {e}", 0.0

    if probe.returncode != 0:
        stderr = (probe.stderr or "").strip()
        return False, f"ffprobe failed: {stderr or 'unknown ffprobe error'}", 0.0

    try:
        info = json.loads(probe.stdout or "{}")
    except json.JSONDecodeError:
        return False, "ffprobe returned invalid JSON", 0.0

    streams = info.get("streams") or []
    audio_streams = [s for s in streams if (s.get("codec_type") == "audio" or s.get("codec_name"))]
    if not audio_streams:
        return False, "No audio stream found", 0.0

    fmt = info.get("format") or {}
    duration_raw = fmt.get("duration")
    if duration_raw in (None, "", "N/A"):
        duration_raw = audio_streams[0].get("duration")
    try:
        duration = float(duration_raw or 0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 0:
        return False, "Audio duration is zero or unreadable", 0.0

    # A start_time well above zero means this is a preview segment, not a full track.
    # Normal encoder delay is a few milliseconds; anything above the threshold is a red flag.
    start_raw = fmt.get("start_time")
    if start_raw not in (None, "", "N/A"):
        try:
            start_time = float(start_raw)
            if start_time > MAX_AUDIO_START_OFFSET_SECS:
                return False, f"Audio start offset is {start_time:.1f}s - likely a preview segment, not a full track", 0.0
        except (TypeError, ValueError):
            pass  # Unparseable start_time: give it the benefit of the doubt

    return True, "", duration


def _check_duration_against_mb(actual_secs: float, mb_metadata: Optional[dict], artist: str, title: str) -> tuple[bool, str]:
    """Compare the downloaded file's duration against the MusicBrainz expected duration.

    Returns (ok, reason). ok=True means the duration is within MB_DURATION_TOLERANCE
    of the expected value, or MB didn't return a duration (in which case we can't check).
    This is a no-op when MusicBrainz is disabled, since lookup_metadata returns None.
    """
    if not mb_metadata:
        return True, ""
    expected = mb_metadata.get("expected_duration_secs")
    if not expected or expected <= 0:
        return True, ""
    low = expected * (1 - MB_DURATION_TOLERANCE)
    high = expected * (1 + MB_DURATION_TOLERANCE)
    if low <= actual_secs <= high:
        return True, ""
    return (
        False,
        f"Duration mismatch for {artist} - {title}: got {actual_secs:.0f}s, "
        f"MusicBrainz expects {expected:.0f}s "
        f"(tolerance ±{MB_DURATION_TOLERANCE*100:.0f}%, allowed {low:.0f}s-{high:.0f}s)"
    )


def _note_blacklist_entry(
    *,
    source: str,
    reason: str,
    note: str,
    job_id: str | None = None,
    video_id: str | None = None,
    uploader: str | None = None,
) -> None:
    """Write/update a blacklist entry for known-bad files/sources."""
    src = (source or "").strip().lower() or "youtube"
    vid = (video_id or "").strip() or None
    upl = (uploader or "").strip() or None
    try:
        with db_conn() as conn:
            if vid:
                existing = conn.execute(
                    "SELECT id FROM blacklist WHERE video_id = ? AND source = ?",
                    (vid, src),
                ).fetchone()
                if existing:
                    conn.execute(
                        "UPDATE blacklist SET reason = ?, note = ?, job_id = COALESCE(?, job_id) WHERE id = ?",
                        (reason, note, job_id, existing[0]),
                    )
                else:
                    conn.execute(
                        "INSERT INTO blacklist (video_id, uploader, source, reason, note, job_id) VALUES (?, ?, ?, ?, ?, ?)",
                        (vid, upl, src, reason, note, job_id),
                    )
            elif upl:
                existing = conn.execute(
                    "SELECT id FROM blacklist WHERE lower(uploader) = ? AND source = ? AND (video_id IS NULL OR video_id = '')",
                    (upl.lower(), src),
                ).fetchone()
                if existing:
                    conn.execute(
                        "UPDATE blacklist SET reason = ?, note = ?, job_id = COALESCE(?, job_id) WHERE id = ?",
                        (reason, note, job_id, existing[0]),
                    )
                else:
                    conn.execute(
                        "INSERT INTO blacklist (uploader, source, reason, note, job_id) VALUES (?, ?, ?, ?, ?)",
                        (upl, src, reason, note, job_id),
                    )
            conn.commit()
    except Exception as e:
        print(f"Blacklist write skipped: {e}")


def _find_alternate_search_candidate(query: str, attempted_ids: set[str]) -> dict | None:
    """Search across sources and return the best untried candidate."""
    if not query.strip():
        return None
    try:
        from search import search_all

        for cand in search_all(query, limit=12):
            cand_id = (cand.get("video_id") or "").strip()
            if not cand_id or cand_id in attempted_ids:
                continue
            return cand
    except Exception as e:
        print(f"Alternate candidate search failed: {e}")
    return None


def trigger_navidrome_scan():
    """Trigger a Navidrome library scan via API"""
    navidrome_url = get_setting("navidrome_url")
    navidrome_user = get_setting("navidrome_user")
    navidrome_pass = get_setting("navidrome_pass")

    if not (navidrome_url and navidrome_user and navidrome_pass):
        return

    try:
        params = subsonic_auth_params(navidrome_user, navidrome_pass)

        with httpx.Client(timeout=TIMEOUT_HTTP_REQUEST) as client:
            client.get(
                f"{navidrome_url}/rest/startScan",
                params=params
            )
    except Exception:
        pass  # Non-critical, scan will happen on schedule anyway


def _display_path(p: Path) -> str:
    """Return a human-readable path for queue messages.

    Uses 'Artist/filename.flac' format so Navidrome paths like
    'Artist/Album/01-01 - Title.flac' show the artist rather than
    just the bare filename (which is useless for numbered tracks).
    """
    if p.parent and p.parent.name:
        return f"{p.parent.name}/{p.name}"
    return p.name


def check_navidrome_duplicate(artist: str, title: str) -> Optional[Path]:
    """Check if a track already exists in Navidrome via the Subsonic search2 API.

    Only runs when Navidrome is configured and navidrome_dupe_check is enabled.
    Returns the Path to the file on disk if found (from Navidrome's 'path' field),
    or None if not found / check is disabled / Navidrome is unreachable.
    Silently swallows all errors  -  this is a best-effort check, not a blocker.
    """
    from settings import get_setting_bool
    if not get_setting_bool("navidrome_dupe_check", True):
        return None

    navidrome_url = get_setting("navidrome_url")
    navidrome_user = get_setting("navidrome_user")
    navidrome_pass = get_setting("navidrome_pass")

    if not (navidrome_url and navidrome_user and navidrome_pass):
        return None

    try:
        params = subsonic_auth_params(navidrome_user, navidrome_pass)
        query = f"{artist} {title}".strip() if artist else title
        params.update({
            "query": query,
            "artistCount": 0,
            "albumCount": 0,
            # Common titles (Numb, Back In Black, etc.) need a wider net.
            "songCount": 100,
        })

        with httpx.Client(timeout=TIMEOUT_HTTP_REQUEST) as client:
            response = client.get(f"{navidrome_url.rstrip('/')}/rest/search2", params=params)

        if response.status_code != 200:
            return None

        data = response.json().get("subsonic-response", {})
        if data.get("status") != "ok":
            return None

        songs = data.get("searchResult2", {}).get("song", [])

        # Normalise punctuation so "Guns N' Roses" == "Guns N' Roses", etc.
        # Collapse all apostrophe/quote variants and strip non-alphanumeric noise.
        _punct_re = re.compile(r"[''`´\u2018\u2019\u201b\u02bc]")

        def _norm(s: str) -> str:
            return _punct_re.sub("'", (s or "").strip()).casefold()

        title_norm = _norm(title)
        artist_norm = _norm(artist or "")

        # Strip trailing version qualifiers (Remaster, Live, Radio Edit, etc.) from a title
        # so "Everytime (Remastered)" and "Everytime" are treated as the same song,
        # but "Everytime [Remix]" is kept distinct  -  remixes are different recordings.
        _version_re = re.compile(
            r'[\s\(\[]+(?:remaster(?:ed)?|remastered \d{4}|\d{4} remaster|'
            r'radio edit|single (?:version|edit)|album (?:version|edit)|'
            r'original (?:version|mix)|mono|stereo|explicit|clean)([\s\)\]]+|$)',
            re.IGNORECASE
        )

        def _base_title(t: str) -> str:
            t = _version_re.sub("", t).strip()
            # Strip common promo/session suffixes so
            # "A Couple Minutes | A COLORS SHOW" matches "A Couple Minutes".
            t = re.sub(
                r'(?:\s*\|\s*|\s+)(?:a\s+colors?\s+show|colors?\s+show|'
                r'(?:official\s+)?(?:music\s+)?(?:video|audio)|'
                r'lyric(?:\s+video|s)?|visuali[sz]er|live\s+session|session)\s*$',
                '',
                t,
                flags=re.IGNORECASE,
            )
            return t.strip().casefold()

        title_base = _base_title(title)

        # Albums whose names contain these keywords are covers/tributes/karaoke  - 
        # definitely not the same recording even if artist and title match.
        _covers_re = re.compile(
            r'\b(?:cover[s]?|tribute|karaoke|piano version|instrumental version|'
            r'made famous|in the style of|as made|acoustic version)\b',
            re.IGNORECASE
        )

        for song in songs:
            song_raw_title = (song.get("title") or "").strip()
            song_title_norm = _norm(song_raw_title)
            song_title_base = _base_title(song_raw_title)
            song_artist_norm = _norm(song.get("artist") or "")
            # albumArtist is the reliable "who actually recorded this" field  -
            # track artist on covers albums is often the original artist.
            song_album_artist_norm = _norm(song.get("albumArtist") or song.get("artist") or "")
            song_album = (song.get("album") or "")

            # Reject covers/tribute/karaoke albums outright
            if _covers_re.search(song_album):
                continue

            # Track artist OR album artist matching is enough  -  requiring both breaks
            # tracks on compilations where albumArtist is "Various Artists".
            artist_match = not artist_norm or (
                song_artist_norm == artist_norm or song_album_artist_norm == artist_norm
            )

            # Exact title match always wins; also match if both titles share the same base
            # (e.g. we want "Everytime" and Navidrome has "Everytime (Remastered 2004)").
            # Remix/version titles in the search query must match exactly  -  they are distinct recordings.
            title_match = (song_title_norm == title_norm) or (song_title_base == title_base and title_base == title_norm)

            if title_match and artist_match:
                raw_path = song.get("path") or ""
                if raw_path.startswith("/"):
                    # Real path mode is on  -  absolute path we can actually use
                    return Path(raw_path)
                # Synthetic path (e.g. "Artist/Album/01-Track.mp3")  -  tells us the track
                # exists but is useless as a filesystem reference. Return a sentinel that is
                # truthy (so callers block re-downloads) but won't pass .exists() (so M3U
                # builders skip it rather than writing garbage into the playlist).
                return Path(title)

        return None

    except Exception:
        return None  # Never let a dupe check failure block a download


def trigger_jellyfin_scan():
    """Trigger a Jellyfin library scan via API"""
    jellyfin_url = get_setting("jellyfin_url")
    jellyfin_api_key = get_setting("jellyfin_api_key")

    if not (jellyfin_url and jellyfin_api_key):
        return

    try:
        with httpx.Client(timeout=TIMEOUT_HTTP_REQUEST) as client:
            client.post(
                f"{jellyfin_url}/Library/Refresh",
                headers={"X-Emby-Token": jellyfin_api_key}
            )
    except Exception:
        pass  # Non-critical, scan will happen on schedule anyway


def probe_audio_quality(
    file_path: Path,
    source_info: tuple[str, int] | None = None,
) -> tuple[str | None, int]:
    """Use ffprobe to extract audio quality info.

    Returns (human_readable_string, bitrate_kbps). For lossless formats like
    FLAC the bitrate is reported as 0 (lossless always passes quality gates).

    source_info is an optional (codec_label, bitrate_kbps) tuple describing
    the original format before conversion. When the final file is FLAC but
    the source was lossy, the display string honestly notes the conversion
    (e.g. "FLAC (from MP3 128kbps)") and the returned bitrate is the SOURCE
    bitrate so the quality gate can reject lipstick-on-a-pig transcodes.
    """
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-select_streams", "a:0",
             "-show_entries", "stream=codec_name,bit_rate,sample_rate,bits_per_raw_sample",
             "-of", "json", str(file_path)],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            return None, 0
        info = json.loads(result.stdout)
        stream = info.get("streams", [{}])[0]
        codec = (stream.get("codec_name") or "").upper()
        sample_rate = int(stream.get("sample_rate") or 0)
        bit_rate = int(stream.get("bit_rate") or 0)
        bit_depth = int(stream.get("bits_per_raw_sample") or 0)
        bitrate_kbps = bit_rate // 1000

        sample_khz = f"{sample_rate / 1000:.1f}kHz".replace(".0kHz", "kHz") if sample_rate else ""

        if codec == "FLAC":
            # Check if this FLAC was converted from a lossy source
            if source_info:
                src_codec, src_bitrate = source_info
                lossless_codecs = {"FLAC", "ALAC", "WAV", "PCM_S16LE", "PCM_S24LE"}
                if src_codec and src_codec.upper() not in lossless_codecs:
                    src_kbps = f" {src_bitrate}kbps" if src_bitrate else ""
                    label = f"FLAC (from {src_codec}{src_kbps})"
                    return label, src_bitrate  # Source bitrate for quality gate

            # Genuinely lossless
            parts = ["FLAC", sample_khz]
            if bit_depth:
                parts.append(f"{bit_depth}bit")
            return " ".join(p for p in parts if p), 0  # Lossless  -  always passes
        else:
            kbps = f"{bitrate_kbps}kbps" if bitrate_kbps else ""
            label = " ".join(p for p in [codec, kbps] if p) or None
            return label, bitrate_kbps
    except Exception:
        return None, 0


def _extract_source_format_from_info(info: dict) -> tuple[str, int]:
    """Extract the source audio codec and bitrate from yt-dlp info JSON.

    Returns (codec_label, bitrate_kbps). The top-level 'acodec' and 'abr'
    fields describe what yt-dlp actually selected to download, before any
    post-processing conversion.
    """
    acodec = (info.get("acodec") or "").strip().lower()
    abr = info.get("abr")  # Already in kbps (float or None)

    codec_map = {
        "mp3": "MP3", "aac": "AAC", "opus": "OPUS", "vorbis": "VORBIS",
        "flac": "FLAC", "alac": "ALAC", "pcm_s16le": "WAV", "pcm_s24le": "WAV",
        "mp4a.40.2": "AAC", "mp4a.40.5": "AAC",
    }

    codec_label = codec_map.get(acodec, acodec.upper() if acodec else "")
    bitrate_kbps = int(abr) if abr else 0

    return codec_label, bitrate_kbps


def _build_ytdlp_download_cmd(
    video_id: str,
    output_template: str,
    convert_to_flac: bool,
    source_url: str = None,
    use_cookies: bool = True,
) -> list[str]:
    """Build yt-dlp args for audio extraction, metadata, and thumbnail embedding.

    source_url overrides the default YouTube URL (used for SoundCloud etc.).
    use_cookies=False skips cookie/player-client args (not needed for SoundCloud).
    """
    if convert_to_flac:
        fmt = get_setting("audio_format", "flac")
        fmt = fmt if fmt in ("flac", "opus", "mp3") else "flac"
        format_args = ["--audio-format", fmt]
    else:
        fmt = None
        format_args = []  # Keep original format from source
    # MP3 VBR ~192k (LAME -V 2)  -  good trade-off between size and quality.
    # For FLAC/Opus, quality 0 = best (lossless / highest bitrate).
    audio_quality = "2" if fmt == "mp3" else "0"
    base_args = _ytdlp_base_args() if use_cookies else []
    url = source_url or f"https://www.youtube.com/watch?v={video_id}"
    return [
        "yt-dlp",
        *base_args,
        "-f", "bestaudio/best",
        "-x",
        *format_args,
        "--audio-quality", audio_quality,
        "--embed-metadata",
        "--embed-thumbnail",
        "--convert-thumbnails", "jpg",
        "--ppa", "ffmpeg:-c:v mjpeg -vf crop=\"'if(gt(ih,iw),iw,ih)':'if(gt(iw,ih),ih,iw)'\"",
        "--add-metadata",
        "--parse-metadata", "%(artist,channel,uploader)s:%(meta_artist)s",
        "--parse-metadata", "%(track,title)s:%(meta_title)s",
        "-o", output_template,
        "--no-warnings",
        url,
    ]


def _update_job(job_id: str, **fields) -> None:
    """Update job fields in the database."""
    if not fields:
        return
    columns = ", ".join(f"{key} = ?" for key in fields)
    values = list(fields.values())
    with db_conn() as conn:
        conn.execute(f"UPDATE jobs SET {columns} WHERE id = ?", (*values, job_id))
        conn.commit()


def _normalise_watched_match_text(text: str) -> str:
    """Normalise artist/title text for strict watched-track match checks."""
    t = (text or "").lower()
    t = t.replace("\u2019", "’").replace("\u2018", "’").replace("`", "’")
    # Strip bracketed clauses: (feat. X), [feat. X], etc.
    t = re.sub(r"\s*[\(\[].*?[\)\]]", "", t)
    # Strip inline feat./ft./featuring clauses not in brackets, e.g. "Track feat. Artist"
    t = re.sub(r"\s+(?:feat|ft|featuring)\.?\s+.*$", "", t)
    # Strip trailing junk keywords and everything after them
    t = re.sub(
        r"\b(remaster(?:ed)?|radio edit|album version|single version|single edit|live|explicit|clean|"
        r"official|(?:music\s+)?(?:video|audio)|lyric(?:\s+video|s)?|visuali[sz]er|"
        r"a\s+colors?\s+show|colors?\s+show)\b.*$",
        "",
        t,
    )
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


_REMIX_INDICATOR_WORDS = frozenset({
    "remix", "mix", "edit", "version", "bootleg", "rework", "flip", "refix",
})

_ARTIST_NOISE_WORDS = frozenset({"feat", "ft", "featuring", "vs", "x", "and", "the"})


def _artist_words(artist_norm: str) -> set:
    """Split a normalised artist string into a set of significant words."""
    return {w for w in artist_norm.split() if w not in _ARTIST_NOISE_WORDS and len(w) > 1}


def _has_remix_suffix(extra: str) -> bool:
    """Return True if the extra words on a longer title end in a remix indicator."""
    words = extra.strip().split()
    return bool(words) and words[-1] in _REMIX_INDICATOR_WORDS


def _watched_track_matches_expected(expected_artist: str, expected_title: str, actual_artist: str, actual_title: str) -> bool:
    """Return True when downloaded metadata matches watched-track expectation.

    Handles several real-world drift patterns:
    - Remaster/version suffix drift (e.g. 'The Chain 2004 Remaster' vs 'The Chain')
    - Remix suffix drift: Spotify stores 'Track - Remixer Remix' as the title (dash
      becomes a space after normalisation), while YouTube brackets get stripped, leaving
      just 'Track'. We accept the expected title as a prefix of got_title when the extra
      words end in a remix indicator AND the remix direction is from expected (Spotify).
    - Artist field remix additions: Spotify appends the remixer to the artist list
      ('KH, Four Tet, MPH'), YouTube only tags primary artists ('KH, Four Tet').
    - Collaborator separator differences: comma vs feat. vs x vs & all normalise.
    - Artist order swaps: handled by set-based word comparison.
    """
    exp_artist = _normalise_watched_match_text(expected_artist)
    exp_title = _normalise_watched_match_text(expected_title)
    got_artist = _normalise_watched_match_text(actual_artist)
    got_title = _normalise_watched_match_text(actual_title)

    def _strip_version_suffix(t: str) -> str:
        t = re.sub(r"\b\d{4}\s*remaster(?:ed)?\b", "", t)
        t = re.sub(r"\bremaster(?:ed)?\s*\d{4}\b", "", t)
        t = re.sub(r"\b(remaster(?:ed)?|radio edit|single edit|single version|album version|live)\b$", "", t)
        t = re.sub(r"\s+", " ", t).strip()
        parts = t.split()
        if len(parts) > 1 and re.fullmatch(r"\d{4}", parts[-1]):
            t = " ".join(parts[:-1]).strip()
        return t

    if not exp_title or not got_title:
        return False

    et, gt = _strip_version_suffix(exp_title), _strip_version_suffix(got_title)
    title_ok = (et == gt)

    if not title_ok:
        # Remix-suffix prefix match: Spotify title has 'Track - Remixer Remix' which
        # after normalisation becomes 'track remixer remix'. YouTube strips the brackets
        # so the got_title is just 'track'. Accept when exp_title starts with got_title
        # and the extra exp_title words end in a remix indicator. We only allow this in
        # the expected->got direction (not got->expected) to avoid accepting a downloaded
        # remix as a match for a plain original.
        if et.startswith(gt) and et[len(gt):len(gt)+1] in (" ", ""):
            extra = et[len(gt):].strip()
            if extra and _has_remix_suffix(extra):
                title_ok = True

    if not title_ok:
        return False
    if not exp_artist:
        return True

    # Artist matching: Spotify often appends the remixer to the artist list and/or
    # uses different collaborator separators (comma vs feat. vs x vs &).
    # Strategy: treat the smaller set of significant artist words as a subset of the larger.
    # A remixer added to the Spotify artist field makes exp_artist a superset of got_artist,
    # which passes the subset check. Order differences are naturally handled by set ops.
    exp_words = _artist_words(exp_artist)
    got_words = _artist_words(got_artist)
    if not got_words:
        return False
    return got_words.issubset(exp_words) or exp_words.issubset(got_words)


def _mark_watched_track_downloaded(job_id: str) -> bool:
    """Mark a watched playlist track as downloaded and rebuild the M3U if enabled.

    Returns False when the final downloaded metadata does not match the expected
    watched track for this job.
    """
    with db_conn() as conn:
        conn.row_factory = sqlite3.Row
        link = conn.execute(
            """SELECT wpt.playlist_id, wpt.artist AS expected_artist, wpt.title AS expected_title,
                      j.artist AS actual_artist, j.title AS actual_title
               FROM watched_playlist_tracks wpt
               LEFT JOIN jobs j ON j.id = wpt.job_id
               WHERE wpt.job_id = ?
               LIMIT 1""",
            (job_id,),
        ).fetchone()
        if not link:
            return True

        if not _watched_track_matches_expected(
            link["expected_artist"] or "",
            link["expected_title"] or "",
            link["actual_artist"] or "",
            link["actual_title"] or "",
        ):
            msg = (
                f"Watched track mismatch: expected '{link['expected_artist']} - {link['expected_title']}', "
                f"got '{link['actual_artist'] or 'Unknown'} - {link['actual_title'] or 'Unknown'}'"
            )
            print(msg)
            old = conn.execute("SELECT error FROM jobs WHERE id = ?", (job_id,)).fetchone()
            old_error = (old[0] or "").strip() if old else ""
            merged_error = f"{old_error} | {msg}" if old_error else msg
            conn.execute(
                "UPDATE jobs SET status = 'completed_with_errors', error = ? WHERE id = ?",
                (merged_error, job_id),
            )
            conn.commit()
            return False

        conn.execute(
            "UPDATE watched_playlist_tracks SET downloaded_at = datetime('now') WHERE job_id = ?",
            (job_id,)
        )
        conn.commit()

        # Rebuild the M3U immediately if this job belongs to a watched playlist
        # so the file grows track-by-track rather than waiting for the next full refresh
        row = conn.execute(
            """SELECT wp.id, wp.name, wp.make_m3u, wp.use_playlists_dir, wp.sync_mode
               FROM watched_playlists wp
               JOIN bulk_imports bi ON bi.watch_playlist_id = wp.id
               JOIN bulk_import_tracks bt ON bt.import_id = bi.id AND bt.job_id = ?
               WHERE wp.make_m3u = 1
               LIMIT 1""",
            (job_id,)
        ).fetchone()

    if row:
        rebuild_watched_playlist_m3u(
            row["id"], row["name"],
            use_playlists_dir=bool(row["use_playlists_dir"]),
            sync_mode=row["sync_mode"] or "append",
        )
    return True


def _cleanup_temp_files(artist_dir: Path, sanitized_title: str) -> int:
    """Remove yt-dlp .temp.* leftover files for a given track. Returns count removed."""
    removed = 0
    for temp_file in artist_dir.glob(f"{sanitized_title}.temp.*"):
        try:
            temp_file.unlink()
            removed += 1
            print(f"Cleaned up temp file: {temp_file.name}")
        except OSError:
            pass
    return removed


def _relocate_for_normalised_artist(audio_file: Path, old_artist: str, new_artist: str) -> Path:
    """Move a downloaded file to the correct artist directory after MusicBrainz normalisation.

    Because MusicBrainz actually knows how to spell, unlike half the uploaders on YouTube.
    Returns the new file path (or the original if no move was needed).
    """
    # Flat directory mode doesn't use artist names  -  nothing to shuffle
    if not get_setting_bool("organise_by_artist", True):
        return audio_file

    new_dir = get_download_dir(new_artist)
    old_dir = audio_file.parent

    if new_dir == old_dir:
        return audio_file

    new_dir.mkdir(parents=True, exist_ok=True)
    new_path = new_dir / audio_file.name

    # Don't trample an existing file  -  paranoia beats regret
    if new_path.exists():
        print(f"Artist normalisation: target already exists, skipping move: {new_path}")
        return audio_file

    audio_file.rename(new_path)
    print(f"Artist normalised: {old_dir.name}/{audio_file.name} -> {new_dir.name}/{audio_file.name}")
    set_file_permissions(new_path)

    # Relocate any lyrics file that tagged along
    old_lrc = audio_file.with_suffix(".lrc")
    if old_lrc.exists():
        new_lrc = new_path.with_suffix(".lrc")
        old_lrc.rename(new_lrc)
        set_file_permissions(new_lrc)

    # Tidy up the old directory if it's now gathering dust
    try:
        if old_dir.exists() and not any(old_dir.iterdir()):
            old_dir.rmdir()
            print(f"Removed empty artist directory: {old_dir.name}")
    except OSError:
        pass

    return new_path


def _is_permission_error(stderr: str) -> bool:
    """Check if yt-dlp failed due to a permission denied error on rename."""
    return "Permission denied" in stderr and ".temp." in stderr


def _summarise_ytdlp_stderr(stderr: str) -> str:
    """Return a short, user-safe failure reason from yt-dlp stderr."""
    lower = (stderr or "").lower()
    if not lower.strip():
        return "provider returned an unknown error"
    if "sign in to confirm your age" in lower or "age-restricted" in lower:
        return "age-restricted content requires valid account cookies"
    if "private video" in lower:
        return "video is private"
    if "video unavailable" in lower:
        return "video is unavailable or region-restricted"
    if "http error 429" in lower or "too many requests" in lower:
        return "rate-limited by YouTube"
    if _is_ytdlp_403(stderr):
        return "request blocked (403)"
    if "unable to extract" in lower or "failed to extract" in lower:
        return "provider metadata extraction failed"
    if "unable to download webpage" in lower or "timed out" in lower:
        return "provider/network timeout"
    if "requested format is not available" in lower:
        return "format manifest unavailable for this request"
    return "provider rejected the request"


def _format_info_lookup_error(source_label: str, stderr: str, has_cookies: bool) -> str:
    """Build a concise, provider-specific info lookup error message."""
    src = (source_label or "youtube").lower()
    reason = _summarise_ytdlp_stderr(stderr)
    if src == "youtube":
        if _is_ytdlp_403(stderr):
            if has_cookies:
                return "YouTube info lookup blocked (403). Cookies may be stale, re-export in Settings."
            return "YouTube info lookup blocked (403). Add fresh cookies in Settings or retry later."
        return f"YouTube info lookup failed: {reason}"
    if src == "soundcloud":
        return f"SoundCloud info lookup failed: {reason}"
    return f"Source info lookup failed: {reason}"


def _search_youtube_for_monochrome_fallback(query: str, limit: int = 5) -> tuple[list[dict], str]:
    """Search YouTube for Monochrome fallback with cookie-aware diagnostics."""
    fetch_limit = max(limit * YOUTUBE_SEARCH_MULTIPLIER, YOUTUBE_SEARCH_MIN_FETCH)
    cmd = [
        "yt-dlp",
        *_ytdlp_base_args(),
        "--dump-json",
        "--flat-playlist",
        "--no-warnings",
        f"ytsearch{fetch_limit}:{query}",
    ]
    used_cookies = "--cookies" in cmd

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_YTDLP_SEARCH)
    except subprocess.TimeoutExpired:
        return [], "YouTube fallback search timed out"

    if result.returncode == 0:
        results = parse_youtube_search_results(result.stdout, query=query)
        results.sort(key=lambda x: x["quality_score"], reverse=True)
        if results:
            return results[:limit], ""
        return [], "YouTube fallback search returned no parseable results"

    reason = _summarise_ytdlp_stderr(result.stderr)
    if _should_retry_without_cookies(result.stderr):
        _note_bot_block()

    if not used_cookies:
        return [], f"YouTube fallback search failed: {reason}"

    # One explicit cookieless retry: this often recovers from stale/premium cookies.
    cmd_no_cookies = _strip_cookies_args(cmd)
    try:
        result_no_cookies = subprocess.run(
            cmd_no_cookies, capture_output=True, text=True, timeout=TIMEOUT_YTDLP_SEARCH
        )
    except subprocess.TimeoutExpired:
        return [], f"YouTube fallback search failed: {reason}. Cookieless retry timed out."

    if result_no_cookies.returncode == 0:
        results = parse_youtube_search_results(result_no_cookies.stdout, query=query)
        results.sort(key=lambda x: x["quality_score"], reverse=True)
        if results:
            print("Monochrome fallback: cookieless YouTube search succeeded, cookies appear stale")
            _note_cookie_failure()
            return results[:limit], ""
        return [], f"YouTube fallback search failed: {reason}. Cookieless retry returned no parseable results."

    reason_no_cookies = _summarise_ytdlp_stderr(result_no_cookies.stderr)
    if _should_retry_without_cookies(result_no_cookies.stderr):
        _note_bot_block()
    return [], f"YouTube fallback search failed: {reason}. Cookieless retry failed: {reason_no_cookies}."


def _run_ytdlp_with_retries(
    download_cmd: list[str],
    timeout_secs: int,
    has_cookies: bool
) -> tuple[subprocess.CompletedProcess | None, bool]:
    """Run yt-dlp with retry/backoff and optional cookie fallback."""
    download_result = None
    download_timed_out = False

    for attempt in range(1 + YTDLP_403_MAX_RETRIES):
        _sleep_if_botted()
        try:
            download_result = subprocess.run(
                download_cmd,
                capture_output=True,
                text=True,
                timeout=timeout_secs
            )
        except subprocess.TimeoutExpired:
            download_timed_out = True
            break

        if download_result.returncode == 0:
            break

        if _is_ytdlp_403(download_result.stderr) and attempt < YTDLP_403_MAX_RETRIES:
            print(f"YouTube 403 for {download_cmd[-1]}, retrying (attempt {attempt + 1})")
            time.sleep(YTDLP_403_RETRY_DELAY * (attempt + 1))
        else:
            break

    cookies_may_be_at_fault = has_cookies and download_result and _should_retry_without_cookies(download_result.stderr)

    if download_timed_out or (download_result and _should_retry_without_cookies(download_result.stderr)):
        _note_bot_block()

    if (download_timed_out or (download_result and download_result.returncode != 0)) and has_cookies:
        if download_timed_out or _should_retry_without_cookies(download_result.stderr):
            # Retry without cookies  -  if this succeeds, it confirms cookies were the problem.
            # If it also fails, the video itself is blocked (geo-lock, ContentID, etc.) and
            # cookies were innocent bystanders  -  don't penalise them.
            download_cmd_no_cookies = _strip_cookies_args(download_cmd)
            try:
                download_result = subprocess.run(
                    download_cmd_no_cookies,
                    capture_output=True,
                    text=True,
                    timeout=timeout_secs
                )
                download_timed_out = False
                if download_result.returncode == 0 and cookies_may_be_at_fault:
                    # Cookieless worked  -  so cookies were actively causing the 403.
                    # Disable them for a while so they don't break other downloads too.
                    print("Cookie-related 403 confirmed (cookieless retry succeeded)  -  disabling cookies temporarily")
                    _note_cookie_failure()
            except subprocess.TimeoutExpired:
                download_timed_out = True

    return download_result, download_timed_out


def create_bulk_playlist(bulk_import_id: str, playlist_name: str, expected_count: int, use_playlists_dir: bool = False):
    """Create an M3U playlist from a bulk import after all downloads complete

    Waits for all jobs with the matching playlist_name to complete, then generates the M3U file.
    When use_playlists_dir is True and playlists_subdir is configured, the M3U and its
    relative track paths are written into the Playlists folder instead of Singles.
    """
    # Wait for all downloads to complete (with timeout)
    max_wait_time = PLAYLIST_WAIT_MAX
    check_interval = PLAYLIST_WAIT_INTERVAL
    waited = 0

    while waited < max_wait_time:
        with db_conn() as conn:
            cursor = conn.execute(
                "SELECT COUNT(*) as total, SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed FROM jobs WHERE playlist_name = ?",
                (bulk_import_id,)
            )
            row = cursor.fetchone()

        total, completed = row
        completed = completed or 0

        # All downloads complete
        if completed >= expected_count or total == completed:
            break

        time.sleep(check_interval)
        waited += check_interval

    # Gather all successfully downloaded files
    with db_conn() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT artist, title FROM jobs WHERE playlist_name = ? AND status = 'completed' AND error IS NULL ORDER BY created_at",
            (bulk_import_id,)
        )
        jobs = [dict(row) for row in cursor.fetchall()]

    if not jobs:
        return  # No successful downloads

    # Determine whether to write into the Playlists folder
    playlists_dir = get_playlists_dir() if use_playlists_dir else None
    safe_playlist = sanitize_filename(playlist_name)

    # Build M3U playlist
    playlist_files = []
    for job in jobs:
        artist = job.get("artist", "Unknown")
        title = job.get("title", "Unknown")

        if playlists_dir:
            # Tracks were downloaded into Playlists/PlaylistName/
            track_dir = playlists_dir / safe_playlist
            stem = _playlist_stem(artist, title, title)
            found = False
            for ext in ['.flac', '.opus', '.m4a', '.mp3', '.ogg', '.webm']:
                candidate = track_dir / f"{stem}{ext}"
                if candidate.exists():
                    # Path in M3U is relative to the M3U file (which sits one level up)
                    playlist_files.append(f"{safe_playlist}/{stem}{ext}")
                    found = True
                    break
            if not found:
                # Track already existed in Singles (duplicate skip)  -  include it from wherever it lives
                audio_file = check_duplicate(artist, title)
                if audio_file:
                    playlist_files.append(str(audio_file))
        else:
            audio_file = check_duplicate(artist, title)
            if audio_file:
                rel_path = audio_file.relative_to(get_singles_dir())
                playlist_files.append(str(rel_path))

    if playlist_files:
        if playlists_dir:
            m3u_path = playlists_dir / f"{safe_playlist}.m3u"
            playlists_dir.mkdir(parents=True, exist_ok=True)
        else:
            m3u_path = get_singles_dir() / f"{safe_playlist}.m3u"
        with open(m3u_path, 'w', encoding='utf-8') as f:
            f.write("#EXTM3U\n")
            for file_path in playlist_files:
                f.write(f"{file_path}\n")
        set_file_permissions(m3u_path)


def rebuild_watched_playlist_m3u(playlist_id: str, playlist_name: str, use_playlists_dir: bool = False, sync_mode: str = "append") -> Path | None:
    """Rebuild the M3U file for a watched playlist from all tracks marked as downloaded.

    Walks every downloaded track in the playlist, resolves the file on disk, and
    writes (or overwrites) the M3U. Called after each refresh cycle so the playlist
    file grows in step with the library.

    In mirror mode, tracks that have been removed from the upstream playlist (removed_at IS NOT NULL)
    are excluded from the M3U. Audio files are never deleted  -  only the playlist file changes.

    Returns the M3U path on success, None if no files could be resolved.
    """
    def _normalise_m3u_match_text(text: str) -> str:
        """Loose normaliser for matching playlist rows to on-disk files.

        Strips punctuation and common metadata fluff (remaster/live/edit suffixes)
        so tiny naming drift does not cause silent M3U drops.
        """
        t = (text or "").lower()
        # Unify apostrophes/quotes so ASCII and curly forms compare cleanly.
        t = t.replace("’", "'").replace("‘", "'").replace("`", "'")
        # Remove bracketed descriptors.
        t = re.sub(r"\s*[\(\[].*?[\)\]]", "", t)
        # Remove common suffix noise.
        t = re.sub(r"\b(remaster(?:ed)?|radio edit|album version|live)\b.*$", "", t).strip()
        # Drop punctuation, keep alnum/space.
        t = re.sub(r"[^a-z0-9\s]", " ", t)
        return re.sub(r"\s+", " ", t).strip()

    def _candidate_pairs(row: sqlite3.Row) -> list[tuple[str, str]]:
        """Return artist/title pairs to try, preferring resolved job metadata."""
        pairs = []
        for a, t in (
            (row["job_artist"], row["job_title"]),
            (row["wpt_artist"], row["wpt_title"]),
        ):
            a = (a or "").strip()
            t = (t or "").strip()
            if not a or not t:
                continue
            if (a, t) not in pairs:
                pairs.append((a, t))
        return pairs

    def _resolve_from_playlist_folder(track_dir: Path, pairs: list[tuple[str, str]]) -> str | None:
        """Resolve a path for tracks expected in Playlists/<name>/.

        First try exact stems, then a loose match pass for metadata drift.
        """
        # Exact stem pass
        for artist, title in pairs:
            stem = _playlist_stem(artist, title, title)
            for ext in AUDIO_EXTENSIONS:
                candidate = track_dir / f"{stem}{ext}"
                if candidate.exists():
                    return f"{safe_playlist}/{candidate.name}"

        # Loose match pass
        parsed_files = []
        for ext in AUDIO_EXTENSIONS:
            parsed_files.extend(track_dir.glob(f"*{ext}"))

        for candidate in parsed_files:
            stem = candidate.stem
            if " - " not in stem:
                continue
            file_artist, file_title = stem.split(" - ", 1)
            file_artist_n = _normalise_m3u_match_text(file_artist)
            file_title_n = _normalise_m3u_match_text(file_title)
            for artist, title in pairs:
                artist_n = _normalise_m3u_match_text(artist)
                title_n = _normalise_m3u_match_text(title)
                artist_ok = (
                    artist_n and file_artist_n and
                    (artist_n in file_artist_n or file_artist_n in artist_n)
                )
                title_ok = (
                    title_n and file_title_n and
                    (title_n in file_title_n or file_title_n in title_n)
                )
                if artist_ok and title_ok:
                    return f"{safe_playlist}/{candidate.name}"
        return None

    with db_conn() as conn:
        conn.row_factory = sqlite3.Row
        # In mirror mode, exclude tracks that have been removed from the upstream playlist.
        # In append mode, keep everything ever downloaded regardless of upstream state.
        if sync_mode == "mirror":
            rows = conn.execute(
                """SELECT wpt.artist AS wpt_artist, wpt.title AS wpt_title,
                          j.artist AS job_artist, j.title AS job_title
                   FROM watched_playlist_tracks wpt
                   LEFT JOIN jobs j ON j.id = wpt.job_id
                   WHERE playlist_id = ? AND downloaded_at IS NOT NULL AND removed_at IS NULL
                   ORDER BY first_seen""",
                (playlist_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT wpt.artist AS wpt_artist, wpt.title AS wpt_title,
                          j.artist AS job_artist, j.title AS job_title
                   FROM watched_playlist_tracks wpt
                   LEFT JOIN jobs j ON j.id = wpt.job_id
                   WHERE playlist_id = ? AND downloaded_at IS NOT NULL
                   ORDER BY first_seen""",
                (playlist_id,)
            ).fetchall()

    playlists_dir = get_playlists_dir() if use_playlists_dir else None
    safe_playlist = sanitize_filename(playlist_name)

    playlist_files = []
    seen_paths = set()
    unresolved_rows: list[tuple[str, str, str, str]] = []
    synthetic_path_rows: list[tuple[str, str]] = []  # Navidrome knows about it but can't give a real path
    stale_navidrome_rows: list[tuple[str, str]] = []  # Navidrome path exists in DB but file is gone on disk

    def _is_real_path(p: Path) -> bool:
        """True when we have a path we can actually write into an M3U."""
        return p.is_absolute() or p.exists()

    def _is_navidrome_sentinel(p: Path | None) -> bool:
        """True when Navidrome found the track but returned a synthetic (relative) path.

        The sentinel is Path(title)  -  it's truthy so download blocking works,
        but it's not absolute and won't exist on disk. If we see this we know real
        path mode is off in Navidrome and the user needs to fix their settings.
        """
        return p is not None and not _is_real_path(p)

    for row in rows:
        pairs = _candidate_pairs(row)
        if not pairs:
            continue

        if playlists_dir:
            track_dir = playlists_dir / safe_playlist
            resolved = _resolve_from_playlist_folder(track_dir, pairs)
            if resolved and resolved not in seen_paths:
                seen_paths.add(resolved)
                playlist_files.append(resolved)
                continue

            # Track wasn't resolved inside the playlist folder  -  fall back through
            # duplicate checks using both job metadata and original watched metadata.
            existing = None
            navidrome_sentinel_hit = False
            for artist, title in pairs:
                local = check_duplicate(artist, title)
                if local and _is_real_path(local):
                    existing = local
                    break
                nav = check_navidrome_duplicate(artist, title)
                if nav and _is_real_path(nav):
                    existing = nav
                    break
                if _is_navidrome_sentinel(nav):
                    navidrome_sentinel_hit = True

            if existing:
                existing_path = str(existing)
                if existing_path not in seen_paths:
                    seen_paths.add(existing_path)
                    playlist_files.append(existing_path)
                    # Absolute path from Navidrome that doesn't exist on our filesystem = stale entry
                    if existing.is_absolute() and not existing.exists():
                        stale_navidrome_rows.append((row["wpt_artist"] or "", row["wpt_title"] or ""))
            elif navidrome_sentinel_hit:
                synthetic_path_rows.append((row["wpt_artist"] or "", row["wpt_title"] or ""))
            else:
                unresolved_rows.append((
                    row["wpt_artist"] or "",
                    row["wpt_title"] or "",
                    row["job_artist"] or "",
                    row["job_title"] or "",
                ))
        else:
            audio_file = None
            navidrome_sentinel_hit = False
            for artist, title in pairs:
                local = check_duplicate(artist, title)
                if local and _is_real_path(local):
                    audio_file = local
                    break
                nav = check_navidrome_duplicate(artist, title)
                if nav and _is_real_path(nav):
                    audio_file = nav
                    break
                if _is_navidrome_sentinel(nav):
                    navidrome_sentinel_hit = True

            if audio_file:
                try:
                    rel_path = audio_file.relative_to(get_singles_dir())
                    rel_path_str = str(rel_path)
                    if rel_path_str not in seen_paths:
                        seen_paths.add(rel_path_str)
                        playlist_files.append(rel_path_str)
                except ValueError:
                    abs_path = str(audio_file)  # Navidrome absolute path  -  use as-is
                    if abs_path not in seen_paths:
                        seen_paths.add(abs_path)
                        playlist_files.append(abs_path)
                        # Navidrome says it exists but our filesystem disagrees = stale DB entry
                        if not audio_file.exists():
                            stale_navidrome_rows.append((row["wpt_artist"] or "", row["wpt_title"] or ""))
            elif navidrome_sentinel_hit:
                synthetic_path_rows.append((row["wpt_artist"] or "", row["wpt_title"] or ""))
            else:
                unresolved_rows.append((
                    row["wpt_artist"] or "",
                    row["wpt_title"] or "",
                    row["job_artist"] or "",
                    row["job_title"] or "",
                ))

    if not playlist_files:
        return None

    if playlists_dir:
        m3u_path = playlists_dir / f"{safe_playlist}.m3u"
        playlists_dir.mkdir(parents=True, exist_ok=True)
    else:
        m3u_path = get_singles_dir() / f"{safe_playlist}.m3u"

    with open(m3u_path, 'w', encoding='utf-8') as f:
        f.write("#EXTM3U\n")
        for file_path in playlist_files:
            f.write(f"{file_path}\n")
    set_file_permissions(m3u_path)

    # Persist stale path count so the frontend can warn the user
    stale_count = len(stale_navidrome_rows)
    with db_conn() as conn:
        conn.execute(
            "UPDATE watched_playlists SET stale_navidrome_paths = ? WHERE id = ?",
            (stale_count, playlist_id)
        )
    if stale_count:
        print(
            f"WARNING: {stale_count} track(s) in playlist '{playlist_name}' have stale Navidrome entries "
            f"(file deleted from disk but still in Navidrome's database). These are written into the M3U "
            f"but won't play. Fix: Navidrome > Settings > Missing Files > Select All > Remove from Database, "
            f"then re-scan your library."
        )
        for w_artist, w_title in stale_navidrome_rows[:20]:
            print(f"  stale path: '{w_artist} - {w_title}'")

    if synthetic_path_rows:
        print(
            f"WARNING: Navidrome returned synthetic (fake) paths for {len(synthetic_path_rows)} track(s) "
            f"in playlist '{playlist_name}'. These tracks exist in Navidrome but cannot be added to the "
            f"M3U because real path mode is disabled. Enable it in Navidrome Settings > Players, or run "
            f"'Test Connection' in MusicGrabber Settings to fix this automatically."
        )
        for w_artist, w_title in synthetic_path_rows[:20]:
            print(f"  synthetic path: '{w_artist} - {w_title}'")
    if unresolved_rows:
        print(
            f"Watched playlist M3U unresolved tracks: {len(unresolved_rows)} "
            f"(playlist '{playlist_name}')"
        )
        for w_artist, w_title, j_artist, j_title in unresolved_rows[:20]:
            print(
                f"  unresolved: expected '{w_artist} - {w_title}' | "
                f"job '{j_artist or 'Unknown'} - {j_title or 'Unknown'}'"
            )
    print(f"Watched playlist M3U updated: {m3u_path.name} ({len(playlist_files)} tracks)")
    return m3u_path


def process_playlist_download(job_id: str, playlist_id: str, playlist_name: str, convert_to_flac: bool = True, use_playlists_dir: bool = True):
    """Process a playlist download job.

    When use_playlists_dir is True and playlists_subdir is configured, tracks are saved to
    Playlists/PlaylistName/ with 'Artist - Title' naming. Otherwise falls back to Singles.
    """
    try:
        _update_job(job_id, status="downloading")

        # Get playlist information and extract all video IDs
        info_cmd = [
            "yt-dlp",
            *_ytdlp_base_args(),
            "--dump-json",
            "--flat-playlist",
            "--no-warnings",
            f"https://www.youtube.com/playlist?list={playlist_id}"
        ]

        info_result = subprocess.run(info_cmd, capture_output=True, text=True, timeout=TIMEOUT_YTDLP_PLAYLIST)
        if info_result.returncode != 0:
            raise Exception("Failed to get playlist info")

        # Parse all videos from playlist
        videos = []
        for line in info_result.stdout.strip().split('\n'):
            if not line:
                continue
            try:
                data = json.loads(line)
                if data.get("id"):
                    videos.append({
                        "id": data["id"],
                        "title": data.get("title", "Unknown"),
                        "channel": data.get("channel", data.get("uploader", "Unknown"))
                    })
            except json.JSONDecodeError:
                continue

        if not videos:
            raise Exception("No videos found in playlist")

        _update_job(job_id, total_tracks=len(videos))

        # Resolve the download directory for this playlist
        playlists_dir = get_playlists_dir() if use_playlists_dir else None
        safe_playlist = sanitize_filename(playlist_name)
        if playlists_dir:
            playlist_track_dir = playlists_dir / safe_playlist
            playlist_track_dir.mkdir(parents=True, exist_ok=True)

        # Download each video in the playlist
        downloaded_files = []
        completed_tracks = 0
        failed_tracks = 0
        skipped_tracks = 0
        has_cookies = False

        for video in videos:
            track_label = video.get("title", "Unknown")
            try:
                video_id = video["id"]

                # Get detailed video info
                detail_cmd = [
                    "yt-dlp",
                    *_ytdlp_base_args(),
                    "--dump-json",
                    "--no-warnings",
                    f"https://www.youtube.com/watch?v={video_id}"
                ]
                has_cookies = "--cookies" in detail_cmd

                detail_result = subprocess.run(detail_cmd, capture_output=True, text=True, timeout=TIMEOUT_YTDLP_INFO)
                if detail_result.returncode != 0:
                    print(
                        f"Playlist track info lookup failed ({video_id}): "
                        f"{_summarise_ytdlp_stderr(detail_result.stderr)}"
                    )
                    failed_tracks += 1
                    continue

                info = json.loads(detail_result.stdout)
                full_title = info.get("title", "Unknown")
                channel = info.get("channel", info.get("uploader", "Unknown"))
                artist, title = extract_artist_title(full_title, channel)

                # Check for duplicates  -  still add to M3U even if we're not downloading
                existing_file = check_duplicate(artist, title)
                if existing_file:
                    skipped_tracks += 1
                    if playlists_dir:
                        stem = _playlist_stem(artist, title, video_id)
                        downloaded_files.append(f"{safe_playlist}/{stem}{existing_file.suffix}")
                    else:
                        downloaded_files.append(str(existing_file.relative_to(get_singles_dir())))
                    continue

                # Create download directory
                if playlists_dir:
                    artist_dir = playlist_track_dir
                    safe_title = _playlist_stem(artist, title, video_id)
                else:
                    artist_dir = get_download_dir(artist)
                    artist_dir.mkdir(parents=True, exist_ok=True)
                    safe_title = _output_stem(artist, title, video_id)
                output_template = str(artist_dir / f"{safe_title}.%(ext)s")
                download_cmd = _build_ytdlp_download_cmd(video_id, output_template, convert_to_flac)
                has_cookies = "--cookies" in download_cmd

                download_result, download_timed_out = _run_ytdlp_with_retries(
                    download_cmd,
                    TIMEOUT_YTDLP_DOWNLOAD,
                    has_cookies
                )

                if download_timed_out or not download_result or download_result.returncode != 0:
                    # Permission denied on temp file rename  -  clean up and retry once
                    stderr = download_result.stderr if download_result else ""
                    if not download_timed_out and download_result and _is_permission_error(stderr):
                        cleaned = _cleanup_temp_files(artist_dir, safe_title)
                        if cleaned:
                            print(f"Retrying playlist track after cleaning {cleaned} temp file(s)")
                            download_result, download_timed_out = _run_ytdlp_with_retries(
                                download_cmd, TIMEOUT_YTDLP_DOWNLOAD, has_cookies
                            )
                    if download_timed_out or not download_result or download_result.returncode != 0:
                        failed_tracks += 1
                        continue

                try:
                    audio_file = _find_downloaded_audio_or_raise(artist_dir, safe_title)
                except Exception as e:
                    print(f"Playlist track output lookup failed: {e}")
                    failed_tracks += 1
                    continue

                valid_audio, integrity_reason, actual_duration_secs = _validate_audio_integrity(audio_file)
                if not valid_audio:
                    audio_file.unlink(missing_ok=True)
                    print(
                        f"Playlist track integrity failed for {video_id}, retrying once: {integrity_reason}"
                    )
                    download_result, download_timed_out = _run_ytdlp_with_retries(
                        download_cmd,
                        TIMEOUT_YTDLP_DOWNLOAD,
                        has_cookies
                    )
                    if download_timed_out or not download_result or download_result.returncode != 0:
                        failed_tracks += 1
                        continue
                    try:
                        audio_file = _find_downloaded_audio_or_raise(artist_dir, safe_title)
                    except Exception:
                        failed_tracks += 1
                        continue
                    valid_audio, integrity_reason, actual_duration_secs = _validate_audio_integrity(audio_file)
                    if not valid_audio:
                        audio_file.unlink(missing_ok=True)
                        _note_blacklist_entry(
                            source="youtube",
                            reason="corrupt_audio",
                            note=f"Playlist track {video_id} failed integrity checks: {integrity_reason}",
                            job_id=job_id,
                            video_id=video_id,
                            uploader=channel,
                        )
                        failed_tracks += 1
                        continue

                # Set permissions for NAS/SMB compatibility
                set_file_permissions(audio_file)

                # Try to enrich metadata with AcoustID fingerprinting, then MusicBrainz
                mb_metadata = lookup_metadata(artist, title, audio_file)

                # Duration sanity check against MusicBrainz expected length
                dur_ok, dur_reason = _check_duration_against_mb(actual_duration_secs, mb_metadata, artist, title)
                if not dur_ok:
                    audio_file.unlink(missing_ok=True)
                    print(dur_reason)
                    failed_tracks += 1
                    continue

                if mb_metadata:
                    mb_artist = mb_metadata.get("artist", artist)
                    mb_title = mb_metadata.get("title", title)
                    apply_metadata_to_file(
                        audio_file, mb_artist, mb_title,
                        mb_metadata.get("album", ""),
                        mb_metadata.get("year")
                    )
                    # Use canonical artist/title from MusicBrainz
                    if mb_artist != artist:
                        # Playlist-routed files must stay in the playlist folder.
                        # Normalising artist names is fine for metadata, but moving them into
                        # Singles breaks playlist locality and confuses M3U expectations.
                        if not playlists_dir:
                            audio_file = _relocate_for_normalised_artist(audio_file, artist, mb_artist)
                        artist = mb_artist
                    if mb_title != title:
                        title = mb_title
                else:
                    apply_metadata_to_file(audio_file, artist, title)

                # Fetch and save lyrics
                lyrics = fetch_lyrics(artist, title)
                if lyrics:
                    save_lyrics_file(audio_file, lyrics)

                if playlists_dir:
                    downloaded_files.append(f"{safe_playlist}/{safe_title}{audio_file.suffix}")
                else:
                    downloaded_files.append(str(audio_file.relative_to(get_singles_dir())))
                completed_tracks += 1

            except Exception as track_error:
                # Track this individual failure and continue
                print(f"Playlist track failed: {track_label} - {track_error}")
                failed_tracks += 1
            finally:
                _update_job(
                    job_id,
                    completed_tracks=completed_tracks,
                    failed_tracks=failed_tracks,
                    skipped_tracks=skipped_tracks
                )

        # Generate M3U playlist file
        if downloaded_files:
            if playlists_dir:
                m3u_path = playlists_dir / f"{safe_playlist}.m3u"
            else:
                m3u_path = get_singles_dir() / f"{safe_playlist}.m3u"
            with open(m3u_path, 'w', encoding='utf-8') as f:
                f.write("#EXTM3U\n")
                for file_path in downloaded_files:
                    f.write(f"{file_path}\n")
            set_file_permissions(m3u_path)

            _update_job(job_id, m3u_path=str(m3u_path.relative_to(MUSIC_DIR)))

        # Trigger library rescans if configured
        trigger_navidrome_scan()
        trigger_jellyfin_scan()

        # Update job status based on results
        final_status = "completed"
        error_message = None

        if failed_tracks:
            final_status = "completed_with_errors"
            error_message = f"{failed_tracks} track(s) failed"
            if skipped_tracks:
                error_message += f", {skipped_tracks} skipped (duplicates)"

        if final_status == "completed":
            error_message = None

        _update_job(
            job_id,
            status=final_status,
            error=error_message,
            completed_at=datetime.now(timezone.utc).isoformat()
        )

        # Send notification for playlist
        send_notification(
            notification_type="playlist",
            title=playlist_name,
            playlist_name=playlist_name,
            source="youtube",
            status=final_status,
            error=error_message,
            track_count=len(videos),
            failed_count=failed_tracks,
            skipped_count=skipped_tracks
        )

    except Exception as e:
        print(f"Playlist download job failed ({job_id}, playlist={playlist_name}): {e}")
        _update_job(job_id, status="failed", error=str(e), completed_at=datetime.now(timezone.utc).isoformat())

        # Send notification for playlist failure
        send_notification(
            notification_type="error",
            title=playlist_name,
            playlist_name=playlist_name,
            source="youtube",
            status="failed",
            error=str(e)
        )



def process_slskd_download(job_id: str, username: str, filename: str, artist: str, title: str, convert_to_flac: bool = True):
    """Process a Soulseek download job via slskd"""
    try:
        _update_job(job_id, status="downloading")

        # If artist/title not provided, extract from filename
        if not artist or not title:
            artist, title = extract_track_info_from_path(filename)

        # Update job with extracted info (store slskd peer as uploader for blacklist)
        _update_job(job_id, title=title, artist=artist, uploader=username)

        # Check for duplicates (local filesystem, then Navidrome if configured)
        existing_file = check_duplicate(artist, title)
        if not existing_file:
            existing_file = check_navidrome_duplicate(artist, title)
        if existing_file:
            _update_job(
                job_id,
                status="completed",
                completed_at=datetime.now(timezone.utc).isoformat(),
                error=f"Already exists: {_display_path(existing_file)}"
            )
            _mark_watched_track_downloaded(job_id)
            return

        # Create download directory (with or without artist subfolder)
        artist_dir = get_download_dir(artist)
        artist_dir.mkdir(parents=True, exist_ok=True)

        # Download from slskd with retries on common queue/abort failures
        downloaded_file = None
        attempts = 0
        tried_candidates = set()
        candidate_queue = [(username, filename)]
        last_error = None

        while candidate_queue:
            cand_username, cand_filename = candidate_queue.pop(0)
            if (cand_username, cand_filename) in tried_candidates:
                continue
            tried_candidates.add((cand_username, cand_filename))

            try:
                downloaded_file = download_from_slskd(cand_username, cand_filename, artist_dir)
                if not downloaded_file or not downloaded_file.exists():
                    raise Exception("Download completed but file not found")

                # Validate raw Soulseek payload before spending time tagging/conversion.
                valid_raw, raw_reason, _raw_dur = _validate_audio_integrity(downloaded_file)
                if not valid_raw:
                    downloaded_file.unlink(missing_ok=True)
                    _note_blacklist_entry(
                        source="soulseek",
                        reason="corrupt_audio",
                        note=f"Rejected candidate '{cand_filename}' from {cand_username}: {raw_reason}",
                        job_id=job_id,
                        uploader=cand_username,
                    )
                    raise Exception(f"Invalid downloaded audio from {cand_username}: {raw_reason}")
                break
            except Exception as e:
                last_error = str(e)
                print(f"slskd download attempt failed: {last_error}")
                if attempts >= SLSKD_MAX_RETRIES or not should_retry_slskd_error(last_error):
                    break
                attempts += 1

                # Refresh candidates from a new search if we don't have any left
                if not candidate_queue:
                    retry_query = f"{artist} {title}".strip()
                    retry_results = search_slskd(retry_query, timeout_secs=TIMEOUT_SLSKD_SEARCH)
                    for r in retry_results:
                        candidate = (r.get("slskd_username", ""), r.get("slskd_filename", ""))
                        if candidate[0] and candidate[1] and candidate not in tried_candidates:
                            candidate_queue.append(candidate)

        if not downloaded_file:
            raise Exception(last_error or "Soulseek download failed")

        # Rename to our standard naming
        sanitized_title = _output_stem(artist, title, Path(filename).stem or job_id)
        source_ext = downloaded_file.suffix.lower()

        # Probe the source file BEFORE conversion so we know the real quality
        source_format_info = None
        if convert_to_flac and source_ext != '.flac':
            src_quality_str, src_bitrate = probe_audio_quality(downloaded_file)
            if src_quality_str:
                src_codec = src_quality_str.split()[0]
                source_format_info = (src_codec, src_bitrate)

        # Determine final filename
        audio_fmt = get_setting("audio_format", "flac") if convert_to_flac else None
        if audio_fmt not in ("flac", "opus", "mp3"):
            audio_fmt = "flac"

        target_ext = f".{audio_fmt}" if audio_fmt else source_ext
        needs_convert = convert_to_flac and source_ext != target_ext

        if needs_convert:
            # Convert to the target format
            if audio_fmt == "flac":
                ffmpeg_codec = "flac"
                extra_args = []
            elif audio_fmt == "opus":
                ffmpeg_codec = "libopus"
                extra_args = ["-b:a", "320k"]
            else:  # mp3  -  VBR ~192k
                ffmpeg_codec = "libmp3lame"
                extra_args = ["-q:a", "2"]
            final_file = artist_dir / f"{sanitized_title}{target_ext}"
            convert_cmd = ["ffmpeg", "-y", "-i", str(downloaded_file), "-c:a", ffmpeg_codec, *extra_args]
            convert_cmd.append(str(final_file))
            result = subprocess.run(convert_cmd, capture_output=True, timeout=TIMEOUT_FFMPEG_CONVERT)
            if result.returncode == 0:
                downloaded_file.unlink()
            else:
                # Conversion failed, keep original with new name
                final_file = artist_dir / f"{sanitized_title}{source_ext}"
                downloaded_file.rename(final_file)
        else:
            # Already in target format (or no conversion requested), just rename
            final_file = artist_dir / f"{sanitized_title}{source_ext}"
            if downloaded_file != final_file:
                downloaded_file.rename(final_file)

        # Set permissions for NAS/SMB compatibility
        set_file_permissions(final_file)

        # Probe audio quality (with source info so FLAC-from-lossy is reported honestly)
        audio_quality, bitrate_kbps = probe_audio_quality(final_file, source_info=source_format_info)
        min_bitrate = get_setting_int("min_audio_bitrate", 0)
        if min_bitrate and bitrate_kbps and bitrate_kbps < min_bitrate:
            final_file.unlink(missing_ok=True)
            raise Exception(f"Audio quality too low ({bitrate_kbps}kbps, minimum is {min_bitrate}kbps)")

        valid_audio, invalid_reason, actual_duration_secs = _validate_audio_integrity(final_file)
        if not valid_audio:
            final_file.unlink(missing_ok=True)
            _note_blacklist_entry(
                source="soulseek",
                reason="corrupt_audio",
                note=f"Final file failed integrity check: {invalid_reason}",
                job_id=job_id,
                uploader=username,
            )
            raise Exception(f"Soulseek audio integrity check failed: {invalid_reason}")

        # Apply metadata (AcoustID fingerprinting first, then text-based MusicBrainz fallback)
        metadata_source = _default_metadata_source("soulseek")
        mb_metadata = lookup_metadata(artist, title, final_file)

        # Duration sanity check against MusicBrainz expected length
        dur_ok, dur_reason = _check_duration_against_mb(actual_duration_secs, mb_metadata, artist, title)
        if not dur_ok:
            final_file.unlink(missing_ok=True)
            raise Exception(dur_reason)

        if mb_metadata:
            metadata_source = mb_metadata.get("metadata_source", metadata_source)
            mb_artist = mb_metadata.get("artist", artist)
            mb_title = mb_metadata.get("title", title)
            apply_metadata_to_file(
                final_file, mb_artist, mb_title,
                mb_metadata.get("album", ""),
                mb_metadata.get("year")
            )
            # Use canonical artist/title from MusicBrainz
            if mb_artist != artist:
                final_file = _relocate_for_normalised_artist(final_file, artist, mb_artist)
                artist = mb_artist
            if mb_title != title:
                title = mb_title
            _update_job(job_id, artist=artist, title=title)
        else:
            apply_metadata_to_file(final_file, artist, title)

        # Fetch and save lyrics
        lyrics = fetch_lyrics(artist, title)
        if lyrics:
            save_lyrics_file(final_file, lyrics)
            print(f"Saved lyrics for {artist} - {title}")
        else:
            print(f"No lyrics found for {artist} - {title}")

        # Trigger library rescans if configured
        trigger_navidrome_scan()
        trigger_jellyfin_scan()

        # Update job status
        _update_job(
            job_id,
            status="completed",
            error=None,
            audio_quality=audio_quality,
            metadata_source=metadata_source,
            completed_at=datetime.now(timezone.utc).isoformat()
        )
        _mark_watched_track_downloaded(job_id)

        print(f"slskd: Successfully downloaded {artist} - {title}")

        # Send notification for Soulseek single
        send_notification(
            notification_type="single",
            title=title,
            artist=artist,
            source="soulseek",
            status="completed"
        )

    except Exception as e:
        print(f"slskd download failed: {e}")
        _update_job(job_id, status="failed", error=str(e), completed_at=datetime.now(timezone.utc).isoformat())

        # Send notification for Soulseek failure
        send_notification(
            notification_type="error",
            title=title,
            artist=artist,
            source="soulseek",
            status="failed",
            error=str(e)
        )



def _monochrome_cover_url(cover_uuid: str) -> str:
    """Turn a Tidal cover UUID into a CDN thumbnail URL."""
    if not cover_uuid:
        return ""
    return f"{MONOCHROME_COVER_BASE}/{cover_uuid.replace('-', '/')}/640x640.jpg"


class _MonochromeAllTiers403(Exception):
    """Raised when every Monochrome quality tier returns 403 for a track.

    Caught by _process_monochrome_download to trigger a YouTube fallback
    rather than marking the job as outright failed.
    """


def _download_monochrome_direct(track_id: str, output_path: Path) -> None:
    """Download a FLAC directly from the Monochrome/Tidal API.

    No yt-dlp, no messing about  -  just a straight stream off the CDN.
    Tries HI_RES_LOSSLESS → LOSSLESS → HIGH → LOW in order; if all tiers 403, raises _MonochromeAllTiers403
    so the caller can fall back to YouTube rather than giving up entirely.
    """
    quality_attempts = ["HI_RES_LOSSLESS", "LOSSLESS", "HIGH", "LOW"]
    resp = None
    all_403 = True
    with httpx.Client(timeout=TIMEOUT_MONOCHROME_API) as client:
        for quality in quality_attempts:
            resp = client.get(
                f"{MONOCHROME_API_URL}/track/",
                params={"id": track_id, "quality": quality},
            )
            if resp.status_code != 403:
                all_403 = False
                break
            print(f"Monochrome: {quality} quality returned 403 for track {track_id}, trying next tier...")

    if all_403:
        raise _MonochromeAllTiers403(f"All Monochrome quality tiers returned 403 for track {track_id}")

    resp.raise_for_status()
    data = resp.json().get("data") or {}
    if not data.get("manifest"):
        raise Exception(f"No stream manifest returned for Monochrome track {track_id}")

    manifest_raw = base64.b64decode(data["manifest"])
    manifest_text = manifest_raw.decode("utf-8", errors="ignore").lstrip()

    # Legacy Monochrome manifest format: JSON blob with direct CDN URLs.
    if manifest_text.startswith("{"):
        manifest = json.loads(manifest_raw)
        encryption = manifest.get("encryptionType", "NONE")
        if encryption != "NONE":
            raise Exception(f"Monochrome track {track_id} is encrypted ({encryption})  -  cannot download")

        urls = manifest.get("urls") or []
        if not urls:
            raise Exception(f"Empty URL list in manifest for Monochrome track {track_id}")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with httpx.stream("GET", urls[0], timeout=120) as stream_resp:
            stream_resp.raise_for_status()
            with open(output_path, "wb") as f:
                for chunk in stream_resp.iter_bytes(chunk_size=8192):
                    f.write(chunk)
        return

    # Current Monochrome manifest format: DASH MPD XML.
    if manifest_text.startswith("<") and "<MPD" in manifest_text:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        mpd_tmp = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".mpd", delete=False) as tf:
                tf.write(manifest_raw)
                mpd_tmp = Path(tf.name)

            # Prefer stream copy first; if container/codec combo complains, fall back
            # to explicit FLAC encode for robustness.
            ffmpeg_copy_cmd = [
                "ffmpeg",
                "-y",
                "-v", "error",
                "-protocol_whitelist", "file,https,tls,tcp,http,crypto",
                "-i", str(mpd_tmp),
                "-map", "0:a:0",
                "-c:a", "copy",
                str(output_path),
            ]
            copy_result = subprocess.run(
                ffmpeg_copy_cmd,
                capture_output=True,
                text=True,
                timeout=TIMEOUT_FFMPEG_CONVERT,
            )
            if copy_result.returncode == 0 and output_path.exists():
                return

            ffmpeg_encode_cmd = [
                "ffmpeg",
                "-y",
                "-v", "error",
                "-protocol_whitelist", "file,https,tls,tcp,http,crypto",
                "-i", str(mpd_tmp),
                "-map", "0:a:0",
                "-c:a", "flac",
                str(output_path),
            ]
            encode_result = subprocess.run(
                ffmpeg_encode_cmd,
                capture_output=True,
                text=True,
                timeout=TIMEOUT_FFMPEG_CONVERT,
            )
            if encode_result.returncode != 0 or not output_path.exists():
                stderr = (encode_result.stderr or copy_result.stderr or "").strip()
                raise Exception(f"DASH manifest download failed: {stderr or 'unknown ffmpeg error'}")
            return
        finally:
            if mpd_tmp:
                mpd_tmp.unlink(missing_ok=True)

    raise Exception(
        f"Unsupported manifest format for Monochrome track {track_id} "
        f"(mime={data.get('manifestMimeType', 'unknown')})"
    )


def _embed_monochrome_cover(audio_file: Path, cover_uuid: str) -> None:
    """Download cover art from Tidal CDN and embed it in a FLAC file."""
    if not cover_uuid:
        return
    try:
        from mutagen.flac import FLAC, Picture

        cover_url = _monochrome_cover_url(cover_uuid)
        resp = httpx.get(cover_url, timeout=10)
        resp.raise_for_status()

        pic = Picture()
        pic.type = 3  # Cover (front)
        pic.mime = "image/jpeg"
        pic.data = resp.content

        audio = FLAC(str(audio_file))
        audio.clear_pictures()
        audio.add_picture(pic)
        audio.save()
    except Exception as e:
        # Non-critical  -  the track still plays fine without cover art
        print(f"Monochrome cover embed failed: {e}")


def _get_monochrome_track_info(track_id: str) -> dict | None:
    """Fetch track metadata from the Monochrome API info endpoint."""
    try:
        resp = httpx.get(
            f"{MONOCHROME_API_URL}/info/",
            params={"id": track_id},
            timeout=TIMEOUT_MONOCHROME_API,
        )
        resp.raise_for_status()
        return resp.json().get("data")
    except Exception as e:
        print(f"Monochrome track info lookup failed: {e}")
        return None


def _process_monochrome_download(job_id: str, track_id: str, convert_to_flac: bool = True,
                                  playlist_name: str = None, use_playlists_dir: bool = False):
    """Download a track directly from Monochrome/Tidal  -  no yt-dlp needed.

    The API gives us proper metadata (artist, album, ISRC) so we don't need
    to guess from dodgy YouTube titles. The audio is genuine lossless FLAC
    straight off the Tidal CDN.
    """
    source_label = "monochrome"
    artist = None
    title = track_id

    try:
        _update_job(job_id, status="downloading")

        # Get track metadata from the API  -  artist, title, album, the lot
        info = _get_monochrome_track_info(track_id)
        if not info:
            raise Exception(f"Failed to get track info for Monochrome track {track_id}")

        title = info.get("title", "Unknown")
        artist_obj = info.get("artist") or {}
        artist = artist_obj.get("name", "Unknown")
        album_obj = info.get("album") or {}
        album_title = album_obj.get("title", "Singles")
        cover_uuid = album_obj.get("cover", "")
        isrc = info.get("isrc", "")

        _update_job(job_id, title=title, artist=artist, uploader=artist)

        # Duplicate check  -  local filesystem first, then Navidrome if configured
        existing_file = check_duplicate(artist, title)
        if not existing_file:
            existing_file = check_navidrome_duplicate(artist, title)
        # For playlist routing, a synthetic Navidrome sentinel path is unusable.
        # Don't mark as "already exists" if we cannot actually append a path.
        if playlist_name and existing_file and not (existing_file.is_absolute() or existing_file.exists()):
            existing_file = None
        if existing_file and playlist_name:
            # Track already exists somewhere  -  add it to the target playlist and call it done.
            # Local paths guard with .exists(); absolute Navidrome real paths are trusted directly
            # (MusicGrabber may not share Navidrome's filesystem view, but the M3U consumer does).
            # The sentinel Path(title) is neither absolute nor locally present, so it's still skipped.
            source_label = "library" if existing_file.exists() else "Navidrome"
            error_label = f"Already exists in {source_label}: {_display_path(existing_file)} (added to playlist)"
            _update_job(
                job_id,
                status="completed",
                completed_at=datetime.now(timezone.utc).isoformat(),
                error=error_label
            )
            marked = _mark_watched_track_downloaded(job_id)
            if marked and (existing_file.is_absolute() or existing_file.exists()):
                _append_to_physical_m3u(existing_file, playlist_name, use_playlists_dir)
            return
        elif existing_file:
            _update_job(
                job_id,
                status="completed",
                completed_at=datetime.now(timezone.utc).isoformat(),
                error=f"Already exists: {_display_path(existing_file)}"
            )
            _mark_watched_track_downloaded(job_id)
            return

        # Create download directory  -  respect playlist routing if requested
        playlists_dir = get_playlists_dir() if (use_playlists_dir and playlist_name) else None
        if playlists_dir:
            artist_dir = playlists_dir / sanitize_filename(playlist_name)
            safe_title = _playlist_stem(artist, title, track_id)
        else:
            artist_dir = get_download_dir(artist)
            safe_title = _output_stem(artist, title, track_id)
        artist_dir.mkdir(parents=True, exist_ok=True)

        output_path = artist_dir / f"{safe_title}.flac"

        # Download + integrity recheck loop for occasionally truncated CDN responses.
        integrity_reason = ""
        actual_duration_secs = 0.0
        for attempt in range(1, _AUDIO_RECHECK_MAX_ATTEMPTS + 1):
            _download_monochrome_direct(track_id, output_path)
            valid_audio, integrity_reason, actual_duration_secs = _validate_audio_integrity(output_path)
            if valid_audio:
                break
            output_path.unlink(missing_ok=True)
            print(
                f"Monochrome integrity check failed for '{artist} - {title}' "
                f"(attempt {attempt}/{_AUDIO_RECHECK_MAX_ATTEMPTS}): {integrity_reason}"
            )
        else:
            _note_blacklist_entry(
                source="monochrome",
                reason="corrupt_audio",
                note=f"Track failed integrity check after retries: {integrity_reason}",
                job_id=job_id,
                video_id=track_id,
                uploader=artist,
            )
            raise Exception(f"Monochrome download failed integrity checks: {integrity_reason}")

        set_file_permissions(output_path)

        # Embed cover art from Tidal CDN
        _embed_monochrome_cover(output_path, cover_uuid)

        # Probe audio quality  -  this is genuine lossless, no transcode shenanigans
        audio_quality, bitrate_kbps = probe_audio_quality(output_path)
        min_bitrate = get_setting_int("min_audio_bitrate", 0)
        if min_bitrate and bitrate_kbps and bitrate_kbps < min_bitrate:
            output_path.unlink(missing_ok=True)
            raise Exception(f"Audio quality too low ({bitrate_kbps}kbps, minimum is {min_bitrate}kbps)")

        # Metadata enrichment  -  Tidal already gave us artist/title/album, which is authoritative.
        # We only use MusicBrainz to fill in the year (which Tidal doesn't provide).
        # We deliberately don't let MusicBrainz overwrite artist/title/album here  -  it has
        # a nasty habit of matching a live recording or remaster and silently making things worse.
        metadata_source = "monochrome_api"
        mb_metadata = lookup_metadata(artist, title, output_path)

        # Duration sanity check  -  Tidal should never serve the wrong track, but worth a nudge.
        dur_ok, dur_reason = _check_duration_against_mb(actual_duration_secs, mb_metadata, artist, title)
        if not dur_ok:
            output_path.unlink(missing_ok=True)
            raise Exception(dur_reason)

        year = mb_metadata.get("year") if mb_metadata else None
        apply_metadata_to_file(output_path, artist, title, album_title, year)

        # Lyrics
        lyrics = fetch_lyrics(artist, title)
        if lyrics:
            save_lyrics_file(output_path, lyrics)
            print(f"Saved lyrics for {artist} - {title}")
        else:
            print(f"No lyrics found for {artist} - {title}")

        # Library scans
        trigger_navidrome_scan()
        trigger_jellyfin_scan()

        # Done!
        _update_job(
            job_id,
            status="completed",
            error=None,
            audio_quality=audio_quality,
            metadata_source=metadata_source,
            completed_at=datetime.now(timezone.utc).isoformat()
        )
        marked = _mark_watched_track_downloaded(job_id)
        if marked:
            _append_to_physical_m3u(output_path, playlist_name, use_playlists_dir)

        print(f"Monochrome: Downloaded {artist} - {title} (lossless FLAC)")

        send_notification(
            notification_type="single",
            title=title,
            artist=artist,
            source=source_label,
            status="completed"
        )

    except _MonochromeAllTiers403:
        # Track is geo-restricted or unlicensed on every Tidal quality tier.
        # Fall back to YouTube rather than leaving the user with a failed job.
        print(f"Monochrome: all tiers 403 for '{artist} - {title}', falling back to YouTube search")
        try:
            query = f"{artist} - {title}" if artist and artist != "Unknown" else title
            _update_job(job_id, error="Monochrome all tiers returned 403, trying YouTube fallback")
            # Search YouTube directly  -  search_all merges sources by quality_score and Monochrome's
            # lossless bonuses would push YouTube results out of the top-N slice every time.
            yt_results, yt_reason = _search_youtube_for_monochrome_fallback(query, limit=5)
            if not yt_results:
                raise Exception(yt_reason or "No YouTube results found as fallback")
            best = yt_results[0]
            print(f"Monochrome fallback: using YouTube result '{best.get('title')}' ({best['video_id']})")
            yt_video_id = best["video_id"]
            _update_job(
                job_id,
                source="youtube",
                video_id=yt_video_id,
                source_url=best.get("source_url") or f"https://www.youtube.com/watch?v={yt_video_id}",
                error="Monochrome all tiers returned 403, switched to YouTube fallback",
            )
            # Re-use the same job, just route it through the YouTube path
            process_download(job_id, yt_video_id, convert_to_flac,
                             source_url=None, playlist_name=playlist_name,
                             use_playlists_dir=use_playlists_dir)
        except Exception as fallback_err:
            print(f"Monochrome YouTube fallback failed: {fallback_err}")
            _update_job(job_id, status="failed",
                        error=f"Monochrome: all tiers restricted. YouTube fallback also failed: {fallback_err}",
                        completed_at=datetime.now(timezone.utc).isoformat())
            send_notification(notification_type="error", title=title, artist=artist,
                              source=source_label, status="failed",
                              error="All Monochrome tiers restricted, YouTube fallback failed")

    except Exception as e:
        print(f"Monochrome download failed: {e}")
        _update_job(job_id, status="failed", error=str(e), completed_at=datetime.now(timezone.utc).isoformat())

        send_notification(
            notification_type="error",
            title=title,
            artist=artist,
            source=source_label,
            status="failed",
            error=str(e)
        )


def _append_to_physical_m3u(audio_file: Path, playlist_name: str, use_playlists_dir: bool) -> None:
    """Append a downloaded track's path to a physical .m3u file.

    Only runs when use_playlists_dir is True and playlist_name is set.
    Skips silently if the Playlists directory is not configured.
    Avoids duplicating entries that are already in the file.
    """
    if not (use_playlists_dir and playlist_name):
        return

    playlists_dir = get_playlists_dir()
    if not playlists_dir:
        return

    safe_playlist = sanitize_filename(playlist_name)
    m3u_path = playlists_dir / f"{safe_playlist}.m3u"
    track_dir = playlists_dir / safe_playlist
    # Keep paths inside the playlist folder relative (Rock Mix/Track.ext) so the M3U
    # remains portable and consistent after full rebuilds.
    if audio_file.is_absolute() and str(audio_file).startswith(str(track_dir) + "/"):
        relative_path = f"{safe_playlist}/{audio_file.name}"
    else:
        relative_path = str(audio_file)

    try:
        # Write header if new file; avoid duplicate entries if it already exists
        is_new = not m3u_path.exists()
        if not is_new:
            existing = m3u_path.read_text(encoding="utf-8")
            if relative_path in existing:
                return
        with m3u_path.open("a", encoding="utf-8") as f:
            if is_new:
                f.write("#EXTM3U\n")
            f.write(f"{relative_path}\n")
    except Exception as e:
        print(f"Warning: could not update {m3u_path}: {e}")


def process_download(job_id: str, video_id: str, convert_to_flac: bool = True, source_url: str = None,
                     playlist_name: str = None, use_playlists_dir: bool = False,
                     attempted_ids: set[str] | None = None, integrity_attempt: int = 1):
    """Process a download job.

    source_url overrides the default YouTube URL construction  -  used for
    SoundCloud and any future yt-dlp-supported source.
    Monochrome tracks bypass yt-dlp entirely and download via the API.
    playlist_name + use_playlists_dir route bulk import tracks into the Playlists folder.
    """
    is_soundcloud = source_url and "soundcloud.com" in source_url
    is_monochrome = source_url and "monochrome.tf" in source_url
    is_url_source = bool(source_url)

    attempted_ids = set(attempted_ids or [])
    attempted_ids.add(video_id)

    # Monochrome gets its own dedicated download path  -  no yt-dlp needed
    if is_monochrome:
        _process_monochrome_download(job_id, video_id, convert_to_flac, playlist_name, use_playlists_dir)
        return

    if is_soundcloud:
        source_label = "soundcloud"
    else:
        source_label = "youtube"
    target_url = source_url or f"https://www.youtube.com/watch?v={video_id}"

    try:
        if not is_url_source and not is_valid_youtube_id(video_id):
            raise Exception("Invalid YouTube video ID")

        # Defaults in case extraction fails before artist/title are assigned
        artist = None
        title = video_id
        has_cookies = False

        # Update status to downloading
        _update_job(job_id, status="downloading")

        # First, get video info for proper metadata
        base_args = _ytdlp_base_args() if not is_url_source else []
        info_cmd = [
            "yt-dlp",
            *base_args,
            "--dump-json",
            "--no-warnings",
            target_url,
        ]
        has_cookies = "--cookies" in info_cmd

        info_result = subprocess.run(info_cmd, capture_output=True, text=True, timeout=TIMEOUT_YTDLP_INFO)
        if info_result.returncode != 0:
            if not is_url_source and _is_ytdlp_403(info_result.stderr) and has_cookies:
                _note_cookie_failure()
            reason_msg = _format_info_lookup_error(source_label, info_result.stderr, has_cookies)
            print(f"Job {job_id} info lookup failed ({source_label}:{video_id}): {reason_msg}")
            raise Exception(reason_msg)

        info = json.loads(info_result.stdout)

        # Capture source audio format before yt-dlp converts it
        source_format_info = _extract_source_format_from_info(info) if convert_to_flac else None

        # Extract artist and title  -  SoundCloud uses 'uploader' for artist
        full_title = info.get("title", "Unknown")
        channel = info.get("uploader", info.get("channel", "Unknown")) if is_url_source else info.get("channel", info.get("uploader", "Unknown"))
        artist, title = extract_artist_title(full_title, channel)

        # Update job with extracted info (store raw uploader for blacklist reporting)
        _update_job(job_id, title=title, artist=artist, uploader=channel)

        # Duplicate check  -  local filesystem first, then Navidrome if configured
        existing_file = check_duplicate(artist, title)
        if not existing_file:
            existing_file = check_navidrome_duplicate(artist, title)
        # For playlist routing, a synthetic Navidrome sentinel path is unusable.
        # Don't mark as "already exists" if we cannot actually append a path.
        if playlist_name and existing_file and not (existing_file.is_absolute() or existing_file.exists()):
            existing_file = None
        if existing_file and playlist_name:
            # Track already exists somewhere  -  add it to the target playlist and call it done.
            # Local paths guard with .exists(); absolute Navidrome real paths are trusted directly
            # (MusicGrabber may not share Navidrome's filesystem view, but the M3U consumer does).
            # The sentinel Path(title) is neither absolute nor locally present, so it's still skipped.
            source_label = "library" if existing_file.exists() else "Navidrome"
            error_label = f"Already exists in {source_label}: {_display_path(existing_file)} (added to playlist)"
            _update_job(
                job_id,
                status="completed",
                completed_at=datetime.now(timezone.utc).isoformat(),
                error=error_label
            )
            marked = _mark_watched_track_downloaded(job_id)
            if marked and (existing_file.is_absolute() or existing_file.exists()):
                _append_to_physical_m3u(existing_file, playlist_name, use_playlists_dir)
            return
        elif existing_file:
            _update_job(
                job_id,
                status="completed",
                completed_at=datetime.now(timezone.utc).isoformat(),
                error=f"Already exists: {_display_path(existing_file)}"
            )
            _mark_watched_track_downloaded(job_id)
            return

        # Create download directory  -  either Playlists/Name/ or the standard Singles layout
        playlists_dir = get_playlists_dir() if (use_playlists_dir and playlist_name) else None
        if playlists_dir:
            artist_dir = playlists_dir / sanitize_filename(playlist_name)
            safe_title = _playlist_stem(artist, title, video_id)
        else:
            artist_dir = get_download_dir(artist)
            safe_title = _output_stem(artist, title, video_id)
        artist_dir.mkdir(parents=True, exist_ok=True)

        # Download with best audio quality
        output_template = str(artist_dir / f"{safe_title}.%(ext)s")
        download_cmd = _build_ytdlp_download_cmd(
            video_id, output_template, convert_to_flac,
            source_url=source_url,
            use_cookies=not is_url_source,
        )
        has_cookies = "--cookies" in download_cmd

        # Retry strategy: back off on suspected bot blocks; if cookies seem to cause 403s,
        # try again without cookies once to distinguish auth problems from general rate limits.
        download_result, download_timed_out = _run_ytdlp_with_retries(
            download_cmd,
            TIMEOUT_YTDLP_DOWNLOAD,
            has_cookies
        )

        if download_timed_out:
            raise Exception("Download timed out (no progress)")

        if not download_result or download_result.returncode != 0:
            stderr = download_result.stderr if download_result else ""

            # Permission denied on temp file rename  -  clean up and retry once
            if download_result and _is_permission_error(stderr):
                cleaned = _cleanup_temp_files(artist_dir, safe_title)
                if cleaned:
                    print(f"Retrying download after cleaning {cleaned} temp file(s)")
                    download_result, download_timed_out = _run_ytdlp_with_retries(
                        download_cmd, TIMEOUT_YTDLP_DOWNLOAD, has_cookies
                    )
                    if not download_timed_out and download_result and download_result.returncode == 0:
                        stderr = None  # Clear error  -  retry succeeded

            if stderr:
                error_msg = f"Download failed: {stderr}"
                if not is_url_source and download_result and _is_ytdlp_403(stderr):
                    if has_cookies:
                        error_msg = "YouTube blocked this download (403). Your cookies may have expired  -  try re-exporting them in Settings."
                    else:
                        error_msg = "YouTube blocked this download (403). Add browser cookies in Settings to authenticate."
                raise Exception(error_msg)

        audio_file = _find_downloaded_audio_or_raise(artist_dir, safe_title)

        # Integrity gate: if the file is corrupted/truncated, retry once then pivot.
        valid_audio, integrity_reason, actual_duration_secs = _validate_audio_integrity(audio_file)
        if not valid_audio:
            audio_file.unlink(missing_ok=True)
            print(
                f"Audio integrity check failed for {video_id} "
                f"(attempt {integrity_attempt}/{_AUDIO_RECHECK_MAX_ATTEMPTS}): {integrity_reason}"
            )
            if integrity_attempt < _AUDIO_RECHECK_MAX_ATTEMPTS:
                return process_download(
                    job_id,
                    video_id,
                    convert_to_flac,
                    source_url=source_url,
                    playlist_name=playlist_name,
                    use_playlists_dir=use_playlists_dir,
                    attempted_ids=attempted_ids,
                    integrity_attempt=integrity_attempt + 1,
                )

            _note_blacklist_entry(
                source=source_label,
                reason="corrupt_audio",
                note=f"File failed integrity check after retry: {integrity_reason}",
                job_id=job_id,
                video_id=video_id,
                uploader=channel,
            )

            if len(attempted_ids) < _AUDIO_RESEARCH_MAX_ALTERNATES + 1:
                query = f"{artist} - {title}".strip(" -")
                alternate = _find_alternate_search_candidate(query, attempted_ids)
                if alternate:
                    alt_id = alternate.get("video_id")
                    alt_source_url = alternate.get("source_url")
                    alt_source = alternate.get("source", "youtube")
                    print(
                        f"Retrying with alternate source after corruption: "
                        f"{alt_source} {alt_id}"
                    )
                    return process_download(
                        job_id,
                        alt_id,
                        convert_to_flac,
                        source_url=alt_source_url,
                        playlist_name=playlist_name,
                        use_playlists_dir=use_playlists_dir,
                        attempted_ids=attempted_ids,
                        integrity_attempt=1,
                    )

            raise Exception(
                f"Downloaded audio failed integrity checks and no alternate candidate succeeded: {integrity_reason}"
            )

        # Set permissions for NAS/SMB compatibility
        set_file_permissions(audio_file)

        # Probe audio quality (with source info so FLAC-from-lossy is reported honestly)
        audio_quality, bitrate_kbps = probe_audio_quality(audio_file, source_info=source_format_info)
        min_bitrate = get_setting_int("min_audio_bitrate", 0)
        if min_bitrate and bitrate_kbps and bitrate_kbps < min_bitrate:
            audio_file.unlink(missing_ok=True)
            raise Exception(f"Audio quality too low ({bitrate_kbps}kbps, minimum is {min_bitrate}kbps)")

        # Try to enrich metadata with AcoustID fingerprinting, then MusicBrainz
        metadata_source = _default_metadata_source(source_label)
        mb_metadata = lookup_metadata(artist, title, audio_file)

        # Duration sanity check: if MusicBrainz knows the expected length, verify we're within 10%.
        # Catches wrong tracks that passed the corruption check but are wildly the wrong length.
        dur_ok, dur_reason = _check_duration_against_mb(actual_duration_secs, mb_metadata, artist, title)
        if not dur_ok:
            audio_file.unlink(missing_ok=True)
            print(dur_reason)
            if len(attempted_ids) < _AUDIO_RESEARCH_MAX_ALTERNATES + 1:
                query = f"{artist} - {title}".strip(" -")
                alternate = _find_alternate_search_candidate(query, attempted_ids)
                if alternate:
                    alt_id = alternate.get("video_id")
                    alt_source_url = alternate.get("source_url")
                    print(f"Retrying with alternate source after duration mismatch: {alternate.get('source', 'youtube')} {alt_id}")
                    return process_download(
                        job_id,
                        alt_id,
                        convert_to_flac,
                        source_url=alt_source_url,
                        playlist_name=playlist_name,
                        use_playlists_dir=use_playlists_dir,
                        attempted_ids=attempted_ids,
                        integrity_attempt=1,
                    )
            raise Exception(dur_reason)

        if mb_metadata:
            metadata_source = mb_metadata.get("metadata_source", metadata_source)
            mb_artist = mb_metadata.get("artist", artist)
            mb_title = mb_metadata.get("title", title)
            apply_metadata_to_file(
                audio_file, mb_artist, mb_title,
                mb_metadata.get("album", ""),
                mb_metadata.get("year")
            )
            # Use the canonical artist/title from MusicBrainz everywhere
            if mb_artist != artist:
                # Playlist-routed files must stay in the playlist folder.
                if not playlists_dir:
                    audio_file = _relocate_for_normalised_artist(audio_file, artist, mb_artist)
                artist = mb_artist
            if mb_title != title:
                title = mb_title
            _update_job(job_id, artist=artist, title=title)
        else:
            apply_metadata_to_file(audio_file, artist, title)

        # Fetch and save lyrics
        lyrics = fetch_lyrics(artist, title)
        if lyrics:
            save_lyrics_file(audio_file, lyrics)
            print(f"Saved lyrics for {artist} - {title}")
        else:
            print(f"No lyrics found for {artist} - {title}")

        # Trigger library rescans if configured
        trigger_navidrome_scan()
        trigger_jellyfin_scan()

        # Update job status
        _update_job(
            job_id,
            status="completed",
            error=None,
            audio_quality=audio_quality,
            metadata_source=metadata_source,
            completed_at=datetime.now(timezone.utc).isoformat()
        )
        marked = _mark_watched_track_downloaded(job_id)
        if marked:
            _append_to_physical_m3u(audio_file, playlist_name, use_playlists_dir)

        # Send notification for single track
        send_notification(
            notification_type="single",
            title=title,
            artist=artist,
            source=source_label,
            status="completed"
        )

    except Exception as e:
        print(f"Download job failed ({job_id}, source={source_label}, id={video_id}): {e}")
        _update_job(job_id, status="failed", error=str(e), completed_at=datetime.now(timezone.utc).isoformat())

        # Send notification for failure
        send_notification(
            notification_type="error",
            title=title,
            artist=artist,
            source=source_label,
            status="failed",
            error=str(e)
        )
