"""
MusicGrabber - Bulk Import Logic

Line cleaning, import job creation, and background worker.
"""

import re
import sqlite3
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from constants import BULK_IMPORT_SEARCH_DELAY, PRIORITY_SOURCE_BOOST
from db import db_conn, upsert_album_track_lock
from downloads import process_download, process_slskd_download, create_bulk_playlist
from notifications import send_notification
from search import search_all, log_ranked_results
from settings import get_setting_int
from utils import hash_track, spawn_daemon_thread

# Limits concurrent downloads spawned by bulk imports to avoid overwhelming
# YouTube with simultaneous requests and starving the DB connection pool.
_download_pool = None
_download_pool_size = 0


def _get_download_pool() -> ThreadPoolExecutor:
    """Return the download pool, recreating it if the configured size changed."""
    global _download_pool, _download_pool_size
    wanted = max(1, min(get_setting_int("max_concurrent_downloads", 3), 10))
    if _download_pool is None or wanted != _download_pool_size:
        if _download_pool is not None:
            _download_pool.shutdown(wait=False)
        _download_pool = ThreadPoolExecutor(max_workers=wanted)
        _download_pool_size = wanted
    return _download_pool


def _normalise_candidate_match_text(text: str) -> str:
    """Normalise text for loose artist matching in search candidates."""
    t = re.sub(r"[^a-z0-9]+", " ", (text or "").lower())
    return re.sub(r"\s+", " ", t).strip()


def _candidate_mentions_expected_artist(candidate: dict, expected_artist: str) -> bool:
    """Return True when candidate title/channel appears to include expected artist.

    For multi-artist credits like 'OGUZ, Nyctonian', passes if ANY of the
    comma-separated artists is mentioned. Requiring the full combined string
    rejects otherwise good results from sources that only show a primary artist.
    """
    if not expected_artist:
        return False

    title_norm = _normalise_candidate_match_text(candidate.get("title", ""))
    channel_norm = _normalise_candidate_match_text(candidate.get("channel", ""))
    combined = f"{title_norm} {channel_norm}".strip()

    # Split comma-separated multi-artist credits and check each one separately.
    # 'OGUZ, Nyctonian' becomes ['OGUZ', 'Nyctonian']; single-artist strings
    # become a one-element list, so the behaviour is identical for the common case.
    artists = [a.strip() for a in expected_artist.split(",") if a.strip()]
    for artist in artists:
        artist_norm = _normalise_candidate_match_text(artist)
        if not artist_norm:
            continue
        if artist_norm in combined:
            return True
        # Fallback: all significant tokens must appear for multi-word artist names.
        tokens = [t for t in artist_norm.split() if len(t) > 1]
        if len(tokens) > 1 and all(t in combined for t in tokens):
            return True

    return False


def _candidate_looks_like_cover(candidate: dict) -> bool:
    """Return True for obvious cover/tribute/karaoke style uploads."""
    combined = _normalise_candidate_match_text(
        f"{candidate.get('title', '')} {candidate.get('channel', '')}"
    )
    markers = (
        "cover",
        "tribute",
        "karaoke",
        "instrumental",
        "for piano",
        "piano version",
    )
    return any(marker in combined for marker in markers)


def _candidate_channel_matches_expected_artist(candidate: dict, expected_artist: str) -> bool:
    """Strict artist-channel match used for album-mode imports."""
    expected_norm = _normalise_candidate_match_text(expected_artist)
    if not expected_norm:
        return False

    channel_norm = _normalise_candidate_match_text(candidate.get("channel", ""))
    if not channel_norm:
        return False

    if channel_norm == expected_norm:
        return True

    allowed_suffixes = {"topic", "official", "music", "records", "channel"}
    if channel_norm.startswith(f"{expected_norm} "):
        suffix_tokens = channel_norm[len(expected_norm):].strip().split()
        if suffix_tokens and all(tok in allowed_suffixes for tok in suffix_tokens):
            return True

    compact_suffixes = ("vevo", "official")
    return any(channel_norm == f"{expected_norm}{suffix}" for suffix in compact_suffixes)


def apply_priority_source_boost(results: list, priority_source: Optional[str]) -> list:
    """Re-rank search results so the user-chosen source wins close calls.

    Adds PRIORITY_SOURCE_BOOST to the quality_score of any result whose
    source matches priority_source, then re-sorts descending. Mutates the
    list in place and also returns it for convenience.
    No-op when priority_source is empty/None or results is empty.
    """
    if not (priority_source and results):
        return results
    target = priority_source.strip().lower()
    for r in results:
        if (r.get("source") or "").lower() == target:
            r["quality_score"] = (r.get("quality_score") or 0) + PRIORITY_SOURCE_BOOST
    results.sort(key=lambda r: r.get("quality_score") or 0, reverse=True)
    return results


def clean_bulk_import_line(line: str) -> str:
    """Clean a line from bulk import text

    Removes common prefixes like:
    - Numbers: "1.", "1)", "01."
    - Bullets: "•", "-", "*"
    - Comments: "#"
    - Extra whitespace and tabs
    """
    # Strip whitespace
    line = line.strip()

    # Skip comments
    if line.startswith('#'):
        return ""

    # Remove common list prefixes: "1. ", "1) ", "01. ", etc.
    line = re.sub(r'^\d+[\.\)]\s*', '', line)

    # Remove bullet points at start
    line = re.sub(r'^[•\-\*]\s*', '', line)

    # Remove common music symbols
    line = re.sub(r'[♫♪🎵🎶]', '', line)

    # Normalise multiple spaces/tabs to single space
    line = re.sub(r'\s+', ' ', line)

    return line.strip()


def start_bulk_import_for_tracks(
    tracks: list[tuple[str, str]],
    convert_to_flac: bool,
    watch_playlist_id: Optional[str] = None,
    use_playlists_dir: bool = False,
    watch_artist_id: Optional[str] = None,
    user_id: Optional[str] = None,
    preferred_sources: Optional[str] = None,
    override_dir: Optional[str] = None,
    album_release_mbid: Optional[str] = None,
    album_total_tracks: Optional[int] = None,
    custom_subdir: Optional[str] = None,
    priority_source: Optional[str] = None,
) -> str:
    """Create a bulk import job from a list of (artist, title) tuples.

    priority_source, when set, applies a large quality-score bonus to results
    from that source during selection, so the user's preferred indexer wins
    nearly every close call (see PRIORITY_SOURCE_BOOST).
    """
    import_id = str(uuid.uuid4())[:8]

    # Normalise priority_source: empty string and "any" both mean "no preference".
    _priority = (priority_source or "").strip().lower() or None
    if _priority in ("any", "all", "none"):
        _priority = None

    with db_conn() as conn:
        conn.execute(
            """INSERT INTO bulk_imports
               (id, status, total_tracks, create_playlist, playlist_name, convert_to_flac,
                watch_playlist_id, use_playlists_dir, watch_artist_id, user_id, preferred_sources,
                override_dir, album_release_mbid, album_total_tracks, custom_subdir, priority_source)
               VALUES (?, 'pending', ?, 0, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (import_id, len(tracks), int(convert_to_flac), watch_playlist_id,
             int(use_playlists_dir), watch_artist_id, user_id, preferred_sources or "all",
             override_dir, album_release_mbid, album_total_tracks, custom_subdir or None,
             _priority)
        )

        for line_num, (artist, song) in enumerate(tracks, 1):
            conn.execute(
                "INSERT INTO bulk_import_tracks (import_id, line_num, artist, song, status) VALUES (?, ?, ?, ?, 'pending')",
                (import_id, line_num, artist, song)
            )

        conn.commit()

    spawn_daemon_thread(process_bulk_import_worker, import_id)

    return import_id


def process_bulk_import_worker(import_id: str):
    """Background worker to process bulk import tracks one by one

    Searches all available sources in parallel
    via search_all() and picks the best result by quality score.
    """
    # Load import details
    with db_conn() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute("SELECT * FROM bulk_imports WHERE id = ?", (import_id,))
        import_row = cursor.fetchone()
        if not import_row:
            return

        convert_to_flac = bool(import_row["convert_to_flac"])
        create_playlist = bool(import_row["create_playlist"])
        playlist_name = import_row["playlist_name"]
        watch_playlist_id = import_row["watch_playlist_id"]
        watch_artist_id = import_row["watch_artist_id"]
        use_playlists_dir = bool(import_row["use_playlists_dir"])
        user_id = import_row["user_id"]
        override_dir = import_row["override_dir"]  # absolute path string or None
        custom_subdir = (import_row["custom_subdir"] or "").strip() or None
        album_release_mbid = (import_row["album_release_mbid"] or "").strip() or None
        _preferred_sources_raw = import_row["preferred_sources"] or "all"
        # Parse "youtube,soundcloud" into ["youtube", "soundcloud"], or None for "all"
        preferred_sources_list = (
            None if _preferred_sources_raw == "all"
            else [s.strip() for s in _preferred_sources_raw.split(",") if s.strip()]
        )
        # priority_source is a single source ID that gets a quality_score bonus
        # applied below, so it wins close calls against other sources.
        try:
            _priority_source = import_row["priority_source"]
        except (IndexError, KeyError):
            _priority_source = None  # Pre-migration row, column missing
        priority_source = (_priority_source or "").strip().lower() or None

        # For watched playlist imports, playlist_name is stored as NULL in bulk_imports.
        # Fetch the actual name from watched_playlists so folder routing works correctly.
        if (use_playlists_dir or custom_subdir) and not playlist_name and watch_playlist_id:
            row = conn.execute(
                "SELECT name FROM watched_playlists WHERE id = ?", (watch_playlist_id,)
            ).fetchone()
            if row:
                playlist_name = row["name"]

        conn.execute("UPDATE bulk_imports SET status = 'processing' WHERE id = ?", (import_id,))
        conn.commit()

    base_delay = BULK_IMPORT_SEARCH_DELAY

    try:
        while True:
            # Get next pending track
            with db_conn() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT * FROM bulk_import_tracks WHERE import_id = ? AND status = 'pending' ORDER BY line_num LIMIT 1",
                    (import_id,)
                )
                track = cursor.fetchone()
                # Materialise before releasing connection
                track = dict(track) if track else None

            if not track:
                break

            track_id = track["id"]
            artist = track["artist"]
            song = track["song"]

            with db_conn() as conn:
                conn.execute("UPDATE bulk_import_tracks SET status = 'searching' WHERE id = ?", (track_id,))
                conn.commit()

            # Search preferred (or all) sources in parallel, ranked by quality score
            try:
                search_query = f"{artist} - {song}"
                search_results, _ = search_all(search_query, limit=10, sources=preferred_sources_list, include_soulseek=True)

                # Apply the priority-source boost before logging so the ranked log
                # reflects what the worker will actually pick.
                search_results = apply_priority_source_boost(search_results, priority_source)

                log_ranked_results(f"Bulk import {import_id}", search_query, search_results)

                if not search_results:
                    with db_conn() as conn:
                        conn.execute(
                            "UPDATE bulk_import_tracks SET status = 'failed', error = ? WHERE id = ?",
                            ("No results found", track_id)
                        )
                        conn.execute(
                            "UPDATE bulk_imports SET searched = searched + 1, failed = failed + 1 WHERE id = ?",
                            (import_id,)
                        )
                        conn.commit()
                    time.sleep(base_delay)
                    continue

                # Results are already sorted by quality_score descending.
                best_match = search_results[0]
                if override_dir and artist:
                    # Album mode: be strict on artist to avoid tribute/cover uploads.
                    strict_matches = [
                        c for c in search_results
                        if not _candidate_looks_like_cover(c)
                        and _candidate_channel_matches_expected_artist(c, artist)
                    ]
                    if strict_matches:
                        best_match = strict_matches[0]
                    else:
                        with db_conn() as conn:
                            conn.execute(
                                "UPDATE bulk_import_tracks SET status = 'failed', error = ? WHERE id = ?",
                                ("No strict artist match found", track_id)
                            )
                            conn.execute(
                                "UPDATE bulk_imports SET searched = searched + 1, failed = failed + 1 WHERE id = ?",
                                (import_id,)
                            )
                            conn.commit()
                        print(
                            f"Album import {import_id}: no strict artist match for "
                            f"'{artist} - {song}', skipping track"
                        )
                        time.sleep(base_delay)
                        continue
                elif (watch_playlist_id or watch_artist_id) and artist:
                    # Watched imports: prefer a result that mentions the expected artist.
                    # If nothing matches, fail rather than downloading a random top result
                    # that could be a completely different song.
                    for candidate in search_results:
                        if _candidate_mentions_expected_artist(candidate, artist):
                            best_match = candidate
                            break
                    else:
                        wid = watch_playlist_id or watch_artist_id
                        top_title = search_results[0].get("title", "?")
                        top_channel = search_results[0].get("channel", "?")
                        print(
                            f"Watched import {wid}: no candidate matched "
                            f"expected artist '{artist}' for '{song}', "
                            f"refusing top result '{top_title}' by {top_channel}"
                        )
                        with db_conn() as conn:
                            conn.execute(
                                "UPDATE bulk_import_tracks SET status = 'failed', error = ? WHERE id = ?",
                                (f"No artist match (top result was '{top_title}' by {top_channel})", track_id)
                            )
                            conn.execute(
                                "UPDATE bulk_imports SET searched = searched + 1, failed = failed + 1 WHERE id = ?",
                                (import_id,)
                            )
                            conn.commit()
                        time.sleep(base_delay)
                        continue

                video_id = best_match["video_id"]
                source = best_match.get("source", "youtube")
                source_url = best_match.get("source_url")
                slskd_username = best_match.get("slskd_username")
                slskd_filename = best_match.get("slskd_filename")
                slskd_size = best_match.get("slskd_size") or best_match.get("size")
                if watch_playlist_id or watch_artist_id:
                    wid = watch_playlist_id or watch_artist_id
                    print(
                        f"Watched import {wid}: selected {source} for "
                        f"'{artist} - {song}' ({video_id})"
                    )

                # Create download job and update tracking
                job_id = str(uuid.uuid4())[:8]

                with db_conn() as conn:
                    if source == "soulseek":
                        conn.execute(
                            """INSERT INTO jobs
                               (id, video_id, title, artist, status, download_type, playlist_name, source,
                                slskd_username, slskd_filename, slskd_size, source_url, convert_to_flac, user_id)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                job_id, video_id, song, artist, "queued", "single",
                                import_id if create_playlist else None,
                                source, slskd_username, slskd_filename, slskd_size,
                                source_url, int(convert_to_flac), user_id,
                            )
                        )
                    elif create_playlist:
                        conn.execute(
                            "INSERT INTO jobs (id, video_id, title, artist, status, download_type, playlist_name, source, source_url, convert_to_flac, user_id) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (job_id, video_id, song, artist, "queued", "single", import_id, source, source_url, int(convert_to_flac), user_id)
                        )
                    else:
                        conn.execute(
                            "INSERT INTO jobs (id, video_id, title, artist, status, download_type, source, source_url, convert_to_flac, user_id) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (job_id, video_id, song, artist, "queued", "single", source, source_url, int(convert_to_flac), user_id)
                        )

                    conn.execute(
                        "UPDATE bulk_import_tracks SET status = 'queued', job_id = ?, video_id = ? WHERE id = ?",
                        (job_id, video_id, track_id)
                    )
                    if watch_playlist_id:
                        track_hash = hash_track(artist, song)
                        conn.execute(
                            "UPDATE watched_playlist_tracks SET job_id = ? WHERE playlist_id = ? AND track_hash = ?",
                            (job_id, watch_playlist_id, track_hash)
                        )
                    if watch_artist_id:
                        track_hash = hash_track(artist, song)
                        conn.execute(
                            "UPDATE watched_artist_tracks SET job_id = ? WHERE artist_id = ? AND track_hash = ?",
                            (job_id, watch_artist_id, track_hash)
                        )
                    conn.execute(
                        "UPDATE bulk_imports SET searched = searched + 1, queued = queued + 1 WHERE id = ?",
                        (import_id,)
                    )

                    # Record why the scorer picked this candidate over its rivals.
                    # Done inside the same transaction to avoid "database is locked"
                    # from a second connection fighting for the write lock.
                    def _candidate_summary(r: dict) -> dict:
                        return {
                            "video_id": r.get("video_id", ""),
                            "title": r.get("title", ""),
                            "channel": r.get("channel", ""),
                            "source": r.get("source", "unknown"),
                            "score": r.get("quality_score"),
                            "breakdown": r.get("score_breakdown", []),
                        }

                    import json as _json
                    try:
                        _blob = _json.dumps({
                            "selected": _candidate_summary(best_match),
                            "runners_up": [
                                _candidate_summary(r)
                                for r in search_results[:4]
                                if r.get("video_id") != best_match.get("video_id")
                            ][:3],
                        })
                        conn.execute(
                            "INSERT INTO search_decisions (job_id, query, decision_json) VALUES (?, ?, ?)",
                            (job_id, search_query, _blob),
                        )
                    except Exception as e:
                        print(f"Failed to save search decision: {e}")

                    conn.commit()

                # mp3phoenix is a fast HTTP stream — skip the pool entirely so it
                # doesn't queue behind slow yt-dlp jobs.  Everything else goes through
                # the bounded pool (max 3 concurrent) to avoid hammering YouTube.
                _pname = playlist_name if (use_playlists_dir or custom_subdir) else None
                # Album downloads (override_dir set) bypass dupe checks — you picked the album
                # intentionally, and the track lives in Albums/ not Singles/ anyway.
                _skip_dupes = bool(override_dir)

                # Register the album track lock so retries stay dupe-check-free
                # even if the thread that spawned them lost the skip_dupe_check flag.
                if override_dir:
                    _od = Path(override_dir)
                    _album_name_lock = _od.name or ""
                    _album_artist_lock = _od.parent.name or ""
                    upsert_album_track_lock(
                        album_release_mbid, _album_name_lock, _album_artist_lock, song, job_id
                    )
                if source == "soulseek":
                    _get_download_pool().submit(
                        process_slskd_download,
                        job_id,
                        slskd_username,
                        slskd_filename,
                        artist,
                        song,
                        convert_to_flac,
                        user_id=user_id,
                        override_dir=override_dir,
                        playlist_name=_pname,
                        use_playlists_dir=use_playlists_dir,
                        custom_subdir=custom_subdir,
                        slskd_size=slskd_size,
                    )
                elif source == "mp3phoenix":
                    spawn_daemon_thread(process_download, job_id, video_id, convert_to_flac,
                                        source_url, _pname, use_playlists_dir,
                                        user_id=user_id, override_dir=override_dir,
                                        skip_dupe_check=_skip_dupes, custom_subdir=custom_subdir)
                else:
                    _get_download_pool().submit(process_download, job_id, video_id, convert_to_flac,
                                          source_url, _pname, use_playlists_dir,
                                          user_id=user_id, override_dir=override_dir,
                                          skip_dupe_check=_skip_dupes, custom_subdir=custom_subdir)

            except Exception as e:
                with db_conn() as conn:
                    conn.execute(
                        "UPDATE bulk_import_tracks SET status = 'failed', error = ? WHERE id = ?",
                        (str(e)[:200], track_id)
                    )
                    conn.execute(
                        "UPDATE bulk_imports SET searched = searched + 1, failed = failed + 1 WHERE id = ?",
                        (import_id,)
                    )
                    conn.commit()

            # Standard delay between searches
            time.sleep(base_delay)

        # All tracks processed - mark import as complete
        with db_conn() as conn:
            conn.row_factory = sqlite3.Row
            conn.execute(
                "UPDATE bulk_imports SET status = 'completed', completed_at = CURRENT_TIMESTAMP WHERE id = ?",
                (import_id,)
            )
            conn.commit()

            # Get final counts for notification
            cursor = conn.execute(
                """
                SELECT total_tracks, queued, failed, skipped, create_playlist, playlist_name
                FROM bulk_imports
                WHERE id = ?
                """,
                (import_id,)
            )
            final_row = cursor.fetchone()
            final_queued = final_row["queued"] if final_row else 0
            final_failed = final_row["failed"] if final_row else 0
            final_skipped = final_row["skipped"] if final_row else 0
            final_total = final_row["total_tracks"] if final_row else 0
            # Re-read these flags at completion to avoid a race where API updates
            # create_playlist/playlist_name immediately after worker start.
            if final_row:
                create_playlist = bool(final_row["create_playlist"])
                playlist_name = final_row["playlist_name"]

        # Send notification for bulk import
        bulk_status = "completed_with_errors" if final_failed > 0 else "completed"
        send_notification(
            notification_type="bulk",
            title=playlist_name or f"Bulk import {import_id}",
            status=bulk_status,
            track_count=final_total,
            failed_count=final_failed,
            skipped_count=final_skipped,
            user_id=user_id,
        )

        # Create playlist if requested
        if create_playlist and final_queued > 0:
            spawn_daemon_thread(
                create_bulk_playlist,
                import_id,
                playlist_name or f"Playlist {import_id}",
                final_queued,
                use_playlists_dir,
                user_id,
            )

    except Exception as e:
        with db_conn() as conn:
            conn.execute(
                "UPDATE bulk_imports SET status = 'error', error = ? WHERE id = ?",
                (str(e)[:500], import_id)
            )
            conn.commit()

        # Send notification for bulk import failure
        send_notification(
            notification_type="error",
            title=playlist_name or f"Bulk import {import_id}",
            status="failed",
            error=str(e),
            user_id=user_id,
        )
