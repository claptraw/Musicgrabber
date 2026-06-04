"""Track upgrades  -  Phase 1: the library scan.

Lidarr-style "hold out for a better copy" feature. This module owns the cheap,
network-free half: walk the library, work out each file's quality tier, and flag
the ones sitting below the quality the user already downloads at. The actual
searching and file-swapping (Phase 2) lives elsewhere; the scan never touches a
file or hits the network.

Ground rules (see docs/upgrades-design.md):
- The filesystem is the source of truth. We ffprobe... well, mutagen-probe... the
  real files every scan, so a file the user upgraded themselves self-corrects.
- Only files carrying our SOURCE tag are eligible. No tag = the user's own file =
  hands off. The tag is written at download time by metadata.apply_metadata_to_file.
- Target quality is derived from the existing download/convert settings, never a
  separate knob, so there is no contradiction and no re-upgrade loop.
- Albums are deliberately out of scope; we only walk Singles + playlist folders.
"""

import sqlite3
import threading
import time
from pathlib import Path

import mutagen

from constants import (
    UPGRADE_SCAN_INTERVAL_HOURS,
    UPGRADE_SEARCH_TTL_SECONDS,
    UPGRADE_MATCH_FLOOR,
)
from db import db_conn
from settings import (
    get_setting_bool,
    get_setting_int,
    get_setting,
    get_singles_dir,
    get_playlists_dir,
)
from utils import spawn_daemon_thread

# Quality tiers, higher == better. Lossless is one flat tier on purpose: a FLAC
# is a FLAC, we never "upgrade" one lossless wrapper to another.
TIER_LOSSY_128 = 1
TIER_LOSSY_192 = 2
TIER_LOSSY_256 = 3
TIER_LOSSY_320 = 4
TIER_LOSSLESS = 5

_LOSSLESS_CODECS = {"flac", "alac", "wav", "wave", "pcm", "ape", "wavpack", "tak"}

# Extensions we bother scanning. webm is skipped: yt-dlp owns its tags and we
# can't reliably stamp/read a SOURCE on it anyway.
_AUDIO_EXTS = {".flac", ".mp3", ".m4a", ".mp4", ".opus", ".ogg", ".oga", ".wav", ".aac"}

# Rough VBR-quality-preset -> effective average kbps. Only the common LAME
# presets matter; anything exotic falls back to a conservative middle value.
_VBR_KBPS = {"v0": 245, "v1": 225, "v2": 190, "v3": 175, "v4": 165, "v5": 150}


def _kbps_to_tier(kbps: int) -> int:
    """Bucket an effective average bitrate into a lossy tier."""
    if kbps >= 300:
        return TIER_LOSSY_320
    if kbps >= 240:
        return TIER_LOSSY_256
    if kbps >= 170:
        return TIER_LOSSY_192
    return TIER_LOSSY_128


def tier_of(codec: str | None, bitrate_kbps: int) -> int:
    """Quality tier for an actual file given its codec and average bitrate."""
    if codec and codec.lower() in _LOSSLESS_CODECS:
        return TIER_LOSSLESS
    return _kbps_to_tier(bitrate_kbps or 0)


def _digits_to_int(s: str) -> int:
    """Largest number in a string, else 0. Largest (not first) so codec-name digits
    don't win: 'FLAC (from MP3 320kbps)' -> 320, not the 3 in 'MP3'."""
    import re
    nums = [int(n) for n in re.findall(r"\d+", s or "")]
    return max(nums) if nums else 0


def effective_tier(codec: str | None, bitrate_kbps: int, source_quality: str | None) -> int:
    """True quality tier, seeing through a lossless container around a lossy source.

    A YouTube grab converted to FLAC is a FLAC *file* but 130kbps *audio*. We stamp the
    honest origin into SOURCE_QUALITY at download time (e.g. "FLAC (from OPUS 130kbps)"),
    so a "(from ...)" marker means tier by the lossy origin, not the container. This is
    what lets the most common real upgrade (lossy-sourced -> proper lossless) be detected.
    """
    sq = source_quality or ""
    is_lossless_container = bool(codec and codec.lower() in _LOSSLESS_CODECS)

    if is_lossless_container and "from" in sq.lower():
        origin_kbps = _digits_to_int(sq)
        return _kbps_to_tier(origin_kbps) if origin_kbps else TIER_LOSSY_192

    if is_lossless_container:
        return TIER_LOSSLESS

    # Lossy: trust the real bitrate, but mutagen reports 0 for some codecs (Opus),
    # so fall back to the kbps recorded in SOURCE_QUALITY.
    kbps = bitrate_kbps or _digits_to_int(sq)
    return _kbps_to_tier(kbps)


def _setting_to_kbps(value: str) -> int | None:
    """Map a bitrate setting value to effective kbps. None means lossless."""
    if not value:
        return None
    v = value.strip().lower().rstrip("k")
    if v in ("lossless", "0", ""):
        return None
    if v in _VBR_KBPS:
        return _VBR_KBPS[v]
    try:
        return int(v)
    except ValueError:
        return None


def target_tier(user_id: str | None = None) -> int:
    """Derive the upgrade target tier from the user's download/convert settings.

    No separate setting: this IS "the quality I already download at". Convert-to-FLAC
    or a lossless format means lossless; otherwise the chosen codec's bitrate setting
    decides the lossy tier.
    """
    if get_setting_bool("default_convert_to_flac", True, user_id=user_id):
        return TIER_LOSSLESS

    fmt = (get_setting("audio_format", "flac", user_id=user_id) or "flac").strip().lower()
    if fmt in ("flac", "wav", "alac"):
        # alac is lossless; alac_bitrate is effectively always "lossless"
        if fmt != "alac":
            return TIER_LOSSLESS
        if _setting_to_kbps(get_setting("alac_bitrate", "lossless", user_id=user_id)) is None:
            return TIER_LOSSLESS

    if fmt == "mp3":
        kbps = _setting_to_kbps(get_setting("mp3_bitrate", "v2", user_id=user_id))
    elif fmt == "opus":
        kbps = _setting_to_kbps(get_setting("opus_bitrate", "320k", user_id=user_id))
    else:
        # aac / m4a-lossy and anything else: assume a sensible high lossy target
        kbps = 256

    if kbps is None:
        return TIER_LOSSLESS
    return _kbps_to_tier(kbps)


def _decode_tag_value(value):
    """Pull a plain string out of whatever shape a tag value arrives in."""
    if value is None:
        return None
    if isinstance(value, list):
        if not value:
            return None
        value = value[0]
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", "ignore")
    return str(value)


def probe_file(path: Path) -> dict | None:
    """Read codec, bitrate, duration, SOURCE tag and artist/title in one mutagen open.

    Returns None when the file isn't readable audio. Pure-Python, no subprocess, so
    it stays cheap across a library of thousands of files.
    """
    try:
        audio = mutagen.File(str(path))
    except Exception:
        return None
    if audio is None:
        return None

    info = getattr(audio, "info", None)
    tags = audio.tags

    # Codec: lean on the mutagen class, with MP4 needing a codec sniff for ALAC vs AAC.
    cls = type(audio).__name__.lower()
    codec = None
    if "flac" in cls:
        codec = "flac"
    elif "mp3" in cls or "easymp3" in cls:
        codec = "mp3"
    elif "opus" in cls:
        codec = "opus"
    elif "vorbis" in cls or "oggvorbis" in cls:
        codec = "vorbis"
    elif "wave" in cls or cls == "wav":
        codec = "wav"
    elif "mp4" in cls or "m4a" in cls:
        mp4_codec = (getattr(info, "codec", "") or "").lower()
        codec = "alac" if "alac" in mp4_codec else "aac"
    else:
        codec = cls or None

    bitrate_kbps = int((getattr(info, "bitrate", 0) or 0) / 1000)
    duration = float(getattr(info, "length", 0) or 0)

    source = source_quality = title = artist = None
    if tags is not None:
        source = _read_tag(tags, "SOURCE", "----:com.musicgrabber:SOURCE")
        source_quality = _read_tag(tags, "SOURCE_QUALITY", "----:com.musicgrabber:SOURCE_QUALITY")
        title = _read_tag(tags, "TITLE", "\xa9nam", id3="TIT2")
        artist = _read_tag(tags, "ARTIST", "\xa9ART", id3="TPE1")

    return {
        "codec": codec,
        "bitrate_kbps": bitrate_kbps,
        "duration": duration,
        "source": source,
        "source_quality": source_quality,
        "artist": artist,
        "title": title,
    }


def _read_tag(tags, vorbis_key: str, mp4_key: str, id3: str | None = None):
    """Read a tag value across Vorbis / ID3-TXXX / MP4-freeform layouts."""
    # Vorbis comments (FLAC, Ogg, Opus) and MP4 freeform both behave dict-like.
    for key in (vorbis_key, vorbis_key.lower(), mp4_key):
        try:
            v = tags.get(key)
        except Exception:
            v = None
        if v:
            return _decode_tag_value(v)
    # ID3 (MP3): standard frame or our custom TXXX
    try:
        if id3:
            frame = tags.get(id3)
            if frame is not None:
                return _decode_tag_value(getattr(frame, "text", None))
        frames = tags.getall(f"TXXX:{vorbis_key}")
        if frames:
            return _decode_tag_value(getattr(frames[0], "text", None))
    except Exception:
        pass
    return None


def _norm_user(user_id: str | None) -> str:
    return user_id or ""


def _iter_audio_files(directory: Path):
    if not directory or not directory.exists():
        return
    for p in directory.rglob("*"):
        try:
            if p.is_file() and p.suffix.lower() in _AUDIO_EXTS:
                yield p
        except OSError:
            continue


def scan_user_library(user_id: str | None = None) -> dict:
    """Scan one user's eligible library and refresh their upgrade_candidates rows.

    Returns a small summary dict. Network-free, never modifies audio files.
    """
    if not get_setting_bool("enable_track_upgrades", False, user_id=user_id):
        return {"scanned": 0, "eligible": 0, "below_target": 0, "skipped_unchanged": 0}

    uid = _norm_user(user_id)
    tgt = target_tier(user_id)

    dirs = [get_singles_dir(user_id)]
    pl = get_playlists_dir(user_id)
    if pl:
        dirs.append(pl)

    # Existing cache: path -> (mtime, dismissed, dismissed_mtime)
    with db_conn() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT path, mtime, dismissed, dismissed_mtime FROM upgrade_candidates WHERE user_id = ?",
            (uid,),
        ).fetchall()
    cache = {r["path"]: r for r in rows}

    seen: set[str] = set()
    scanned = eligible = below = skipped = 0

    for d in dirs:
        for path in _iter_audio_files(d):
            scanned += 1
            spath = str(path)
            try:
                st = path.stat()
            except OSError:
                continue
            mtime = st.st_mtime
            cached = cache.get(spath)

            if cached is not None and cached["mtime"] == mtime:
                # Unchanged file: keep the probe result, but the target may have moved
                # and dismissals lapse only on file change, so just refresh below_target.
                seen.add(spath)
                skipped += 1
                eligible += 1
                with db_conn() as conn:
                    conn.row_factory = sqlite3.Row
                    file_tier = conn.execute(
                        "SELECT file_tier FROM upgrade_candidates WHERE user_id=? AND path=?",
                        (uid, spath),
                    ).fetchone()["file_tier"]
                    is_below = 1 if (file_tier is not None and file_tier < tgt) else 0
                    below += is_below
                    conn.execute(
                        "UPDATE upgrade_candidates SET target_tier=?, below_target=?, "
                        "last_scanned=CURRENT_TIMESTAMP WHERE user_id=? AND path=?",
                        (tgt, is_below, uid, spath),
                    )
                    conn.commit()
                continue

            info = probe_file(path)
            if info is None or not info.get("source"):
                # Not our file (or unreadable). Drop any stale row and move on.
                if cached is not None:
                    with db_conn() as conn:
                        conn.execute(
                            "DELETE FROM upgrade_candidates WHERE user_id=? AND path=?",
                            (uid, spath),
                        )
                        conn.commit()
                continue

            eligible += 1
            seen.add(spath)
            file_tier = effective_tier(info["codec"], info["bitrate_kbps"], info.get("source_quality"))
            is_below = 1 if file_tier < tgt else 0
            below += is_below

            # A changed file lapses any prior dismissal (filesystem-truth).
            dismissed = 0
            dismissed_mtime = None
            if cached is not None and cached["dismissed"] and cached["dismissed_mtime"] == mtime:
                dismissed = 1
                dismissed_mtime = mtime

            with db_conn() as conn:
                conn.execute(
                    """
                    INSERT INTO upgrade_candidates
                        (user_id, path, mtime, file_size, codec, bitrate_kbps, duration,
                         file_tier, target_tier, below_target, source, artist, title,
                         dismissed, dismissed_mtime, last_scanned)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
                    ON CONFLICT(user_id, path) DO UPDATE SET
                        mtime=excluded.mtime, file_size=excluded.file_size,
                        codec=excluded.codec, bitrate_kbps=excluded.bitrate_kbps,
                        duration=excluded.duration, file_tier=excluded.file_tier,
                        target_tier=excluded.target_tier, below_target=excluded.below_target,
                        source=excluded.source, artist=excluded.artist, title=excluded.title,
                        dismissed=excluded.dismissed, dismissed_mtime=excluded.dismissed_mtime,
                        last_scanned=CURRENT_TIMESTAMP
                    """,
                    (uid, spath, mtime, st.st_size, info["codec"], info["bitrate_kbps"],
                     info["duration"], file_tier, tgt, is_below, info["source"],
                     info["artist"], info["title"], dismissed, dismissed_mtime),
                )
                conn.commit()

    # Prune rows for files that have vanished or lost eligibility since last scan.
    stale = [p for p in cache if p not in seen]
    if stale:
        with db_conn() as conn:
            conn.executemany(
                "DELETE FROM upgrade_candidates WHERE user_id=? AND path=?",
                [(uid, p) for p in stale],
            )
            conn.commit()

    return {"scanned": scanned, "eligible": eligible, "below_target": below, "skipped_unchanged": skipped}


def get_candidates(user_id: str | None = None, below_only: bool = True,
                   include_dismissed: bool = False) -> list[dict]:
    """Return the cached scan rows for a user, newest scan first."""
    uid = _norm_user(user_id)
    clauses = ["user_id = ?"]
    params: list = [uid]
    if below_only:
        clauses.append("below_target = 1")
    if not include_dismissed:
        clauses.append("dismissed = 0")
    where = " AND ".join(clauses)
    with db_conn() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT path, codec, bitrate_kbps, duration, file_tier, target_tier, "
            f"below_target, source, artist, title, dismissed, upgrade_state, last_scanned "
            f"FROM upgrade_candidates WHERE {where} ORDER BY last_scanned DESC",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


# =============================================================================
# Phase 2a: per-candidate upgrade search (on demand, user-driven, read-only)
# =============================================================================

def _estimate_result_tier(result: dict) -> tuple[int, bool]:
    """Best-effort (tier, verified) for a search result before downloading.

    verified == we know the quality up front (Monochrome). slskd and the lossy web
    sources are estimates, confirmed only by the Phase 2b download-and-compare.
    """
    source = (result.get("source") or "").lower()
    quality = result.get("quality") or ""
    q = quality.upper()
    if source == "monochrome":
        if "LOSSLESS" in q:  # HI_RES_LOSSLESS or LOSSLESS
            return TIER_LOSSLESS, True
        return TIER_LOSSY_320, True  # HIGH
    if source == "soulseek":
        if any(t in q for t in ("FLAC", "WAV", "ALAC")):
            return TIER_LOSSLESS, False
        digits = "".join(ch for ch in quality if ch.isdigit())
        if digits:
            return _kbps_to_tier(int(digits)), False
        return TIER_LOSSY_320, False
    if source in ("youtube", "soundcloud"):
        return TIER_LOSSY_192, False  # lossy, rarely an upgrade; estimate low
    if source == "freemp3cloud":
        # HQ-tagged results carry a "320kbps" quality label, the rest are 128.
        digits = "".join(ch for ch in quality if ch.isdigit())
        return (_kbps_to_tier(int(digits)) if digits else TIER_LOSSY_320), False
    return TIER_LOSSY_320, False  # mp3phoenix / zvu4no etc, estimate, unverified


def _candidate_public(row) -> dict:
    """Shape a candidate row for the API/UI."""
    d = dict(row)
    d["filename"] = Path(d["path"]).name
    return d


def search_candidate(user_id: str | None, candidate_id: int, force: bool = False) -> dict | None:
    """Find the best better-than-current copy for one candidate; cache it (TTL).

    Returns the refreshed candidate dict, or None if the id doesn't exist. Hits the
    network via search_all, so callers should rate-limit (the UI does, one row at a time).
    """
    uid = _norm_user(user_id)
    with db_conn() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM upgrade_candidates WHERE user_id=? AND id=?", (uid, candidate_id)
        ).fetchone()
    if row is None:
        return None

    # Serve cached result while it's fresh.
    if not force and row["found_searched"] and row["found_at"]:
        try:
            if time.time() - float(row["found_at"]) < UPGRADE_SEARCH_TTL_SECONDS:
                return _candidate_public(row)
        except (TypeError, ValueError):
            pass

    artist = row["artist"] or ""
    title = row["title"] or ""
    if not (artist or title):
        try:
            from utils import extract_artist_title
            a, t = extract_artist_title(Path(row["path"]).stem, "")
            artist, title = a or "", t or ""
        except Exception:
            pass
    query = f"{artist} - {title}".strip(" -") or Path(row["path"]).stem
    file_tier = row["file_tier"] or 0
    expected_dur = row["duration"] or None

    best = None
    try:
        from search import search_all
        from matching import compute_match_confidence
        include_sk = get_setting_bool("source_soulseek_enabled", False, user_id=user_id)
        results, _ = search_all(query, 8, include_soulseek=include_sk)
    except Exception as e:
        print(f"Upgrade search failed for candidate {candidate_id}: {e}")
        results = []

    for r in results:
        tier, verified = _estimate_result_tier(r)
        if tier <= file_tier:
            continue  # not actually better than what's on disk
        cand_title = r.get("title") or ""
        cand_artist = r.get("channel") or r.get("artist") or ""
        try:
            cand_dur = float(r.get("duration") or 0) or None
        except (TypeError, ValueError):
            cand_dur = None
        conf, _ = compute_match_confidence(
            artist or None, title or None, cand_title, cand_artist,
            candidate_duration_s=cand_dur, expected_duration_s=expected_dur, query=query,
        )
        if conf < UPGRADE_MATCH_FLOOR:
            continue
        score = r.get("quality_score", 0) or 0
        if best is None or (tier, score) > (best["tier"], best["result"].get("quality_score", 0) or 0):
            best = {"tier": tier, "verified": verified, "confidence": conf, "result": r}

    with db_conn() as conn:
        if best:
            r = best["result"]
            conn.execute(
                """UPDATE upgrade_candidates SET found_searched=1, found_at=?,
                   found_source=?, found_quality=?, found_tier=?, found_confidence=?, found_verified=?,
                   found_video_id=?, found_source_url=?, found_slskd_username=?,
                   found_slskd_filename=?, found_slskd_size=?
                   WHERE user_id=? AND id=?""",
                (time.time(), r.get("source"), r.get("quality"), best["tier"],
                 round(best["confidence"], 3), 1 if best["verified"] else 0,
                 r.get("video_id"), r.get("source_url"), r.get("slskd_username"),
                 r.get("slskd_filename"), r.get("slskd_size"), uid, candidate_id),
            )
        else:
            conn.execute(
                """UPDATE upgrade_candidates SET found_searched=1, found_at=?,
                   found_source=NULL, found_quality=NULL, found_tier=NULL, found_confidence=NULL,
                   found_verified=0, found_video_id=NULL, found_source_url=NULL,
                   found_slskd_username=NULL, found_slskd_filename=NULL, found_slskd_size=NULL
                   WHERE user_id=? AND id=?""",
                (time.time(), uid, candidate_id),
            )
        conn.commit()
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM upgrade_candidates WHERE user_id=? AND id=?", (uid, candidate_id)
        ).fetchone()
    return _candidate_public(row)


def get_candidates_page(user_id: str | None, page: int = 1, per_page: int = 10,
                        include_dismissed: bool = False) -> dict:
    """A stable, paginated slice of below-target candidates for the Watched Upgrades view."""
    uid = _norm_user(user_id)
    page = max(1, int(page or 1))
    per_page = max(1, min(50, int(per_page or 10)))
    clauses = ["user_id=?", "below_target=1"]
    params: list = [uid]
    if not include_dismissed:
        clauses.append("dismissed=0")
    where = " AND ".join(clauses)
    with db_conn() as conn:
        conn.row_factory = sqlite3.Row
        total = conn.execute(
            f"SELECT COUNT(*) AS c FROM upgrade_candidates WHERE {where}", params
        ).fetchone()["c"]
        rows = conn.execute(
            f"SELECT * FROM upgrade_candidates WHERE {where} "
            f"ORDER BY artist COLLATE NOCASE, title COLLATE NOCASE, path LIMIT ? OFFSET ?",
            params + [per_page, (page - 1) * per_page],
        ).fetchall()
    pages = (total + per_page - 1) // per_page if total else 0
    return {"items": [_candidate_public(r) for r in rows], "total": total,
            "page": page, "per_page": per_page, "pages": pages}


def dismiss_candidate(user_id: str | None, candidate_id: int) -> bool:
    """Dismiss a candidate. Keyed to current mtime, so a later file change re-surfaces it."""
    uid = _norm_user(user_id)
    with db_conn() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT mtime FROM upgrade_candidates WHERE user_id=? AND id=?", (uid, candidate_id)
        ).fetchone()
        if row is None:
            return False
        conn.execute(
            "UPDATE upgrade_candidates SET dismissed=1, dismissed_mtime=? WHERE user_id=? AND id=?",
            (row["mtime"], uid, candidate_id),
        )
        conn.commit()
    return True


# =============================================================================
# Phase 2b: download + verify + swap (the file-touching half)
# =============================================================================

# Same-recording gates. Deliberately strict: we are replacing the user's file.
UPGRADE_DURATION_TOLERANCE_S = 5.0      # plus a 5% allowance for long tracks
UPGRADE_SWAP_MATCH_FLOOR = 0.72         # higher than the search floor
UPGRADE_FP_SIMILARITY_FLOOR = 0.65      # Chromaprint bit-similarity; ~0.5 is noise


def _quarantine_dir() -> Path:
    from constants import DB_PATH
    d = DB_PATH.parent / ".upgrade_quarantine"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _fpcalc_raw(path: Path) -> tuple[float, list[int]] | None:
    """(duration, raw Chromaprint fingerprint ints) for a file, or None."""
    import json
    import subprocess
    try:
        out = subprocess.run(
            ["fpcalc", "-raw", "-json", str(path)],
            capture_output=True, text=True, timeout=60,
        )
        if out.returncode != 0:
            return None
        data = json.loads(out.stdout)
        return float(data.get("duration") or 0), list(data.get("fingerprint") or [])
    except Exception:
        return None


def _fingerprint_similarity(fa: list[int], fb: list[int]) -> float:
    """Best bit-similarity (0..1) of two raw fingerprints over a small offset search.

    Same recording at different qualities scores ~0.85+; unrelated tracks sit near the
    0.5 noise floor, so the floor cleanly separates them. Capped in length for speed.
    """
    if not fa or not fb:
        return 0.0
    cap = 600
    fa, fb = fa[:cap], fb[:cap]
    best = 0.0
    for off in range(-25, 26):
        x = fa[off:] if off >= 0 else fa
        y = fb if off >= 0 else fb[-off:]
        n = min(len(x), len(y))
        if n < 40:
            continue
        errors = 0
        for i in range(n):
            errors += bin(x[i] ^ y[i]).count("1")
        sim = 1.0 - errors / (32.0 * n)
        if sim > best:
            best = sim
    return best


def _download_candidate_to_staging(user_id: str | None, row, staging_dir: Path) -> Path | None:
    """Download the proposed upgrade into a staging dir using the normal pipeline.

    Reuses process_download / process_slskd_download with override_dir=staging so we get
    all the source-specific handling and quality probing for free, then we take the file
    out of staging ourselves. The transient job row is removed to keep the Queue clean.
    """
    import uuid as _uuid
    from downloads import process_download, process_slskd_download

    source = row["found_source"]
    video_id = row["found_video_id"] or ""
    source_url = row["found_source_url"]
    artist = row["artist"] or ""
    title = row["title"] or Path(row["path"]).stem
    convert = get_setting_bool("default_convert_to_flac", True, user_id=user_id)
    job_id = str(_uuid.uuid4())[:8]
    staging_dir.mkdir(parents=True, exist_ok=True)

    with db_conn() as conn:
        conn.execute(
            """INSERT INTO jobs
               (id, video_id, title, artist, status, download_type, source,
                slskd_username, slskd_filename, slskd_size, convert_to_flac, source_url,
                user_id, override_dir)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (job_id, video_id, title, artist, "queued", "upgrade", source,
             row["found_slskd_username"], row["found_slskd_filename"], row["found_slskd_size"],
             int(convert), source_url, user_id, str(staging_dir)),
        )
        conn.commit()

    try:
        if source == "soulseek":
            process_slskd_download(
                job_id, row["found_slskd_username"], row["found_slskd_filename"],
                artist, title, convert, user_id=user_id,
                override_dir=str(staging_dir), slskd_size=row["found_slskd_size"],
            )
        else:
            process_download(
                job_id, video_id, convert, source_url=source_url,
                user_id=user_id, override_dir=str(staging_dir), skip_dupe_check=True,
            )
    except Exception as e:
        print(f"Upgrade download error (candidate {row['id']}): {e}")

    with db_conn() as conn:
        conn.row_factory = sqlite3.Row
        j = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
        status = j["status"] if j else None
        conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        conn.commit()

    files = [p for p in staging_dir.rglob("*")
             if p.is_file() and p.suffix.lower() in _AUDIO_EXTS]
    if status == "completed" and files:
        return max(files, key=lambda p: p.stat().st_mtime)
    return None


def _fix_m3u_references(user_id: str | None, old_path: Path, new_path: Path) -> None:
    """Best-effort: rewrite playlist .m3u entries when an upgrade changed the file's name."""
    if old_path.name == new_path.name:
        return
    try:
        roots = []
        pl = get_playlists_dir(user_id)
        if pl:
            roots.append(pl)
        roots.append(get_singles_dir(user_id).parent)  # music root
        seen = set()
        for root in roots:
            if not root or not root.exists():
                continue
            for m3u in root.rglob("*.m3u"):
                if m3u in seen:
                    continue
                seen.add(m3u)
                try:
                    text = m3u.read_text(encoding="utf-8", errors="ignore")
                    if old_path.name in text:
                        m3u.write_text(text.replace(old_path.name, new_path.name), encoding="utf-8")
                except OSError:
                    continue
    except Exception:
        pass


def perform_upgrade(user_id: str | None, candidate_id: int, force: bool = False) -> dict:
    """Download the proposed copy, verify it, and (only if safe) swap it in.

    Returns {"status": "upgraded"|"rejected"|"error", "reason": ...}. On anything short of
    a clean pass the original file is left exactly as it was; the download is binned.

    force=True skips the same-recording gates (better/duration/match/fingerprint) for when
    the user deliberately wants the proposed copy regardless, e.g. swapping a long YouTube
    edit for the studio cut. The download still has to be valid audio, and the old file
    still goes to quarantine, so a forced mistake is always recoverable.
    """
    uid = _norm_user(user_id)
    with db_conn() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM upgrade_candidates WHERE user_id=? AND id=?", (uid, candidate_id)
        ).fetchone()
    if row is None:
        return {"status": "error", "reason": "Candidate not found"}
    if not row["found_source"]:
        return {"status": "error", "reason": "No proposed upgrade to apply"}

    current = Path(row["path"])
    if not current.exists():
        return {"status": "error", "reason": "Original file no longer exists"}

    import tempfile
    staging = Path(tempfile.mkdtemp(prefix=f"upg_{candidate_id}_", dir=str(_quarantine_dir().parent)))
    try:
        staged = _download_candidate_to_staging(user_id, row, staging)
        if staged is None:
            _set_upgrade_state(uid, candidate_id, "rejected")
            return {"status": "rejected", "reason": "Download failed or produced no file"}

        info = probe_file(staged)
        if info is None:
            return {"status": "rejected", "reason": "Downloaded file was not readable audio"}

        # 1) Genuinely better than what's on disk (compares the file as it landed).
        staged_tier = effective_tier(info["codec"], info["bitrate_kbps"], info.get("source_quality"))
        if not force and staged_tier <= (row["file_tier"] or 0):
            return {"status": "rejected",
                    "reason": f"Not actually better ({info.get('source_quality') or info['codec']})"}

        # 2) Same recording: duration, then title/artist confidence, then fingerprint.
        # All of these are skipped on a forced upgrade (the user is overriding identity).
        if not force:
            cur_dur = float(row["duration"] or 0)
            new_dur = float(info["duration"] or 0)
            tol = max(UPGRADE_DURATION_TOLERANCE_S, cur_dur * 0.05)
            if cur_dur and new_dur and abs(cur_dur - new_dur) > tol:
                return {"status": "rejected",
                        "reason": f"Duration mismatch ({cur_dur:.0f}s vs {new_dur:.0f}s)"}

            try:
                from matching import compute_match_confidence
                conf, _ = compute_match_confidence(
                    row["artist"] or None, row["title"] or None,
                    info.get("title") or "", info.get("artist") or "",
                    candidate_duration_s=new_dur or None, expected_duration_s=cur_dur or None,
                )
            except Exception:
                conf = 1.0  # matching unavailable; lean on duration + fingerprint
            if conf < UPGRADE_SWAP_MATCH_FLOOR:
                return {"status": "rejected", "reason": f"Title/artist match too weak ({conf:.0%})"}

            fp_cur = _fpcalc_raw(current)
            fp_new = _fpcalc_raw(staged)
            if fp_cur and fp_new:
                sim = _fingerprint_similarity(fp_cur[1], fp_new[1])
                if sim < UPGRADE_FP_SIMILARITY_FLOOR:
                    return {"status": "rejected",
                            "reason": f"Fingerprint says different recording ({sim:.0%})"}
            # If fpcalc is unavailable we don't hard-fail; duration + confidence already passed.

        # 3) All gates passed. Quarantine the old file, swap the new one into its place.
        new_path = current.with_suffix(staged.suffix)
        import shutil
        from datetime import datetime
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        quarantined = _quarantine_dir() / f"{stamp}__{current.name}"
        shutil.move(str(current), str(quarantined))
        try:
            shutil.move(str(staged), str(new_path))
        except Exception as e:
            # Swap failed mid-flight: restore the original so we never lose the file.
            shutil.move(str(quarantined), str(current))
            return {"status": "error", "reason": f"Swap failed, original restored: {e}"}

        # Keep the library entry's identity; the download already stamped SOURCE/SOURCE_QUALITY.
        try:
            from metadata import apply_metadata_to_file
            apply_metadata_to_file(new_path, row["artist"] or "", row["title"] or new_path.stem)
        except Exception:
            pass

        _fix_m3u_references(user_id, current, new_path)
        try:
            from downloads import trigger_navidrome_scan
            trigger_navidrome_scan(user_id=user_id)
        except Exception:
            pass

        # Refresh the candidate row to reflect the upgraded file (now at/above target).
        new_info = probe_file(new_path) or info
        new_tier = effective_tier(new_info["codec"], new_info["bitrate_kbps"], new_info.get("source_quality"))
        try:
            st = new_path.stat()
            with db_conn() as conn:
                conn.execute(
                    """UPDATE upgrade_candidates SET path=?, mtime=?, file_size=?, codec=?,
                       bitrate_kbps=?, duration=?, file_tier=?, below_target=?, source=?,
                       upgrade_state='upgraded', found_searched=0, found_source=NULL,
                       found_quality=NULL, found_tier=NULL, found_confidence=NULL,
                       found_verified=0, found_video_id=NULL, found_source_url=NULL,
                       found_slskd_username=NULL, found_slskd_filename=NULL, found_slskd_size=NULL
                       WHERE user_id=? AND id=?""",
                    (str(new_path), st.st_mtime, st.st_size, new_info["codec"],
                     new_info["bitrate_kbps"], new_info["duration"], new_tier,
                     1 if new_tier < (row["target_tier"] or TIER_LOSSLESS) else 0,
                     new_info.get("source") or row["source"], uid, candidate_id),
                )
                conn.commit()
        except Exception as e:
            print(f"Upgrade row refresh failed (candidate {candidate_id}): {e}")

        return {"status": "upgraded", "from": row["found_quality"] or "",
                "path": str(new_path), "quarantined": str(quarantined)}
    finally:
        try:
            import shutil
            shutil.rmtree(staging, ignore_errors=True)
        except Exception:
            pass


def _set_upgrade_state(uid: str, candidate_id: int, state: str) -> None:
    with db_conn() as conn:
        conn.execute(
            "UPDATE upgrade_candidates SET upgrade_state=? WHERE user_id=? AND id=?",
            (state, uid, candidate_id),
        )
        conn.commit()


def perform_upgrade_all(user_id: str | None) -> dict:
    """Best-effort upgrade of every below-target candidate that has a proposal."""
    uid = _norm_user(user_id)
    with db_conn() as conn:
        conn.row_factory = sqlite3.Row
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM upgrade_candidates WHERE user_id=? AND below_target=1 "
            "AND dismissed=0 AND found_source IS NOT NULL", (uid,)
        ).fetchall()]
    upgraded = rejected = errored = 0
    for cid in ids:
        try:
            res = perform_upgrade(user_id, cid)
        except Exception:
            errored += 1
            continue
        st = res.get("status")
        upgraded += st == "upgraded"
        rejected += st == "rejected"
        errored += st == "error"
    return {"total": len(ids), "upgraded": upgraded, "rejected": rejected, "errored": errored}


def _eligible_user_ids() -> list[str | None]:
    """Active non-peon users to scan, plus the global/single-user pass (None)."""
    try:
        with db_conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id FROM users WHERE is_active = 1 AND role != 'peon'"
            ).fetchall()
        ids = [r["id"] for r in rows]
    except Exception:
        ids = []
    # None covers single-user mode (no per-user dirs) and the global settings fallback.
    return ids if ids else [None]


_scheduler_running = False
_scheduler_lock = threading.Lock()


def run_scan_all() -> dict:
    """Scan every eligible user once. Safe to call from a route or the scheduler."""
    totals = {"users": 0, "scanned": 0, "eligible": 0, "below_target": 0}
    for uid in _eligible_user_ids():
        try:
            summary = scan_user_library(uid)
        except Exception as e:
            print(f"Upgrade scan error for user {uid}: {e}")
            continue
        totals["users"] += 1
        totals["scanned"] += summary["scanned"]
        totals["eligible"] += summary["eligible"]
        totals["below_target"] += summary["below_target"]
    return totals


def upgrade_scan_scheduler():
    """Background loop: periodically rescan eligible libraries. Cheap, no network."""
    print(f"Upgrade scan scheduler started (every {UPGRADE_SCAN_INTERVAL_HOURS}h)")
    time.sleep(45)  # let the heavier watched schedulers settle first
    while _scheduler_running:
        try:
            interval = get_setting_int("upgrade_scan_interval_hours", UPGRADE_SCAN_INTERVAL_HOURS)
            if interval <= 0:
                interval = UPGRADE_SCAN_INTERVAL_HOURS
            totals = run_scan_all()
            if totals["users"]:
                print(f"Upgrade scan: {totals['below_target']} below-target across "
                      f"{totals['eligible']} eligible files ({totals['users']} user(s))")
        except Exception as e:
            print(f"Upgrade scan scheduler error: {e}")
            interval = UPGRADE_SCAN_INTERVAL_HOURS

        sleep_seconds = max(3600, interval * 3600)
        elapsed = 0
        while elapsed < sleep_seconds and _scheduler_running:
            time.sleep(60)
            elapsed += 60


def start_upgrade_scheduler():
    """Start the background scan scheduler if not already running."""
    global _scheduler_running
    with _scheduler_lock:
        if _scheduler_running:
            return
        _scheduler_running = True
    spawn_daemon_thread(upgrade_scan_scheduler)
