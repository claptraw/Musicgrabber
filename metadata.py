"""
MusicGrabber - Metadata Enrichment

AcoustID fingerprinting, MusicBrainz lookups, LRClib lyrics, and audio file tagging.
"""

import json
import base64
import re
import subprocess
from pathlib import Path
from typing import Optional

import httpx
from mutagen.flac import FLAC

from constants import (
    VERSION, TIMEOUT_HTTP_REQUEST, TIMEOUT_FPCALC,
    ACOUSTID_MIN_SCORE, MIN_SONG_DURATION_SECS,
    MB_ARTIST_SEARCH_LIMIT, TIMEOUT_MUSICBRAINZ_ARTIST,
)
from settings import get_setting, get_setting_bool
from utils import set_file_permissions


class MusicBrainzUnavailable(Exception):
    """Raised when MusicBrainz is unreachable after retries (timeout, connect
    error, or persistent 5xx/429). Distinct from "MB returned a valid empty
    result" so the API layer can show a sensible 'try again' message instead
    of pretending the artist or album simply doesn't exist."""


def _mb_get_with_retry(url: str, *, params: dict, headers: dict, timeout: float, attempts: int = 3) -> httpx.Response:
    """GET against MusicBrainz with retry on timeouts, connection errors and
    transient HTTP statuses (429/5xx). Returns the final httpx.Response on
    success, or raises MusicBrainzUnavailable if every attempt fails.

    Backoff is 1s, then 3s -- gentle enough that we do not hammer MB's
    one-request-per-second rate limit on the way back up.
    """
    import time as _time

    retriable_statuses = {429, 500, 502, 503, 504}
    last_error: Optional[str] = None

    for attempt in range(1, attempts + 1):
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.get(url, params=params, headers=headers)
            if resp.status_code in retriable_statuses:
                last_error = f"HTTP {resp.status_code}"
            else:
                return resp
        except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        except Exception:
            # Anything weirder, let the caller decide -- do not swallow.
            raise

        if attempt < attempts:
            _time.sleep(1 if attempt == 1 else 3)

    raise MusicBrainzUnavailable(
        f"MusicBrainz unreachable after {attempts} attempts ({last_error or 'unknown error'})"
    )


def lookup_musicbrainz(artist: str, title: str) -> Optional[dict]:
    """Look up track metadata from MusicBrainz"""
    if not get_setting_bool("enable_musicbrainz", True):
        return None

    try:
        headers = {"User-Agent": f"MusicGrabber/{VERSION} (https://gitlab.com/g33kphr33k/musicgrabber)"}

        search_url = "https://musicbrainz.org/ws/2/recording/"
        params = {
            "query": f'artist:"{artist}" AND recording:"{title}"',
            "fmt": "json",
            "limit": 1,
            "inc": "releases release-groups artist-credits",
        }

        with httpx.Client(timeout=TIMEOUT_HTTP_REQUEST) as client:
            response = client.get(search_url, params=params, headers=headers)

        if response.status_code != 200:
            return None

        data = response.json()

        if not data.get("recordings"):
            return None

        recording = data["recordings"][0]

        # MusicBrainz scores text matches 0-100. Below 85 is too shaky to trust  -
        # at that point we'd be replacing decent source metadata with a guess.
        mb_score = int(recording.get("score", 0))
        if mb_score < 85:
            print(f"MusicBrainz text search score too low ({mb_score}) for {artist} - {title}, skipping")
            return None

        # Extract metadata
        metadata = {
            "title": recording.get("title"),
            "artist": recording["artist-credit"][0]["name"] if recording.get("artist-credit") else None,
            "metadata_source": "musicbrainz_text",
        }

        # length is in milliseconds; convert to seconds for the duration check
        length_ms = recording.get("length")
        if length_ms:
            metadata["expected_duration_secs"] = length_ms / 1000.0

        # Get release information for album, date, and track position.
        # Score releases to avoid landing on 'Promo Only Radio Vol. 47' type junk.
        if recording.get("releases"):
            def _release_score_text(rel: dict) -> int:
                rg = rel.get("release-group") or {}
                rg_for_score = dict(rg)
                if not rg_for_score.get("artist-credit"):
                    rg_for_score["artist-credit"] = rel.get("artist-credit") or []
                # Stash release date so the scorer can prefer earlier pressings
                if rel.get("date") and not rg_for_score.get("first-release-date"):
                    rg_for_score["_date"] = rel["date"]
                return _score_release_group(rg_for_score, artist)

            release = max(recording["releases"], key=_release_score_text)
            metadata["release_mbid"] = release.get("id")
            metadata["album"] = release.get("title")
            metadata["date"] = release.get("date")

            # Extract year from date
            if metadata.get("date"):
                year_match = re.match(r'(\d{4})', metadata["date"])
                if year_match:
                    metadata["year"] = year_match.group(1)

            # Track position within the release  -  inc=releases includes media/tracks
            for medium in release.get("media", []):
                for track in medium.get("tracks", []):
                    metadata["track_number"] = track.get("number")
                    metadata["track_total"] = medium.get("track-count")
                    break
                else:
                    continue
                break

        return metadata

    except Exception:
        # If MusicBrainz lookup fails, just continue without it
        return None


def _build_musicbrainz_guess_for_release(
    recording: dict,
    release: dict | None,
    artist: str,
    title: str,
    headers: dict,
) -> dict:
    credited_artist = " ".join(
        (ac.get("name") or ac.get("artist", {}).get("name", "")) + (ac.get("joinphrase") or "")
        for ac in (recording.get("artist-credit") or [])
        if isinstance(ac, dict)
    ).strip() or artist

    metadata = {
        "artist": credited_artist,
        "title": recording.get("title") or title,
        "album": "",
        "album_artist": credited_artist,
        "year": "",
        "track_number": None,
        "track_total": None,
        "release_mbid": None,
        "recording_mbid": recording.get("id"),
        "metadata_source": "musicbrainz_text",
    }

    if not release:
        return metadata

    release_id = release.get("id")
    metadata["release_mbid"] = release_id
    metadata["album"] = release.get("title") or ""
    release_artist = " ".join(
        (ac.get("name") or ac.get("artist", {}).get("name", "")) + (ac.get("joinphrase") or "")
        for ac in (release.get("artist-credit") or [])
        if isinstance(ac, dict)
    ).strip()
    if release_artist:
        metadata["album_artist"] = release_artist

    release_date = release.get("date") or ""
    year_match = re.match(r"(\d{4})", release_date)
    if year_match:
        metadata["year"] = year_match.group(1)

    if not release_id:
        return metadata

    with httpx.Client(timeout=TIMEOUT_MUSICBRAINZ_ARTIST) as client:
        release_resp = client.get(
            f"https://musicbrainz.org/ws/2/release/{release_id}",
            params={"inc": "recordings artists", "fmt": "json"},
            headers=headers,
        )
    if release_resp.status_code != 200:
        return metadata

    release_data = release_resp.json()
    release_artist_credit = release_data.get("artist-credit") or []
    release_artist_name = " ".join(
        (ac.get("name") or ac.get("artist", {}).get("name", "")) + (ac.get("joinphrase") or "")
        for ac in release_artist_credit
        if isinstance(ac, dict)
    ).strip()
    if release_artist_name:
        metadata["album_artist"] = release_artist_name
    if not metadata["album"]:
        metadata["album"] = release_data.get("title") or ""
    if not metadata["year"]:
        release_year_match = re.match(r"(\d{4})", release_data.get("date") or "")
        if release_year_match:
            metadata["year"] = release_year_match.group(1)

    recording_id = recording.get("id")
    fallback_track = None
    for medium in release_data.get("media") or []:
        track_total = medium.get("track-count")
        for track in medium.get("tracks") or []:
            track_recording = track.get("recording") or {}
            track_title = track_recording.get("title") or track.get("title") or ""
            matches_recording = recording_id and track_recording.get("id") == recording_id
            matches_title = (
                not fallback_track
                and track_title
                and track_title.strip().lower() == (metadata["title"] or "").strip().lower()
            )
            if matches_recording or matches_title:
                fallback_track = {
                    "track_number": track.get("position") or track.get("number"),
                    "track_total": track_total,
                }
                if matches_recording:
                    break
        if fallback_track and fallback_track.get("track_number"):
            break

    if fallback_track:
        try:
            metadata["track_number"] = int(fallback_track.get("track_number")) if fallback_track.get("track_number") else None
        except (TypeError, ValueError):
            metadata["track_number"] = None
        try:
            metadata["track_total"] = int(fallback_track.get("track_total")) if fallback_track.get("track_total") else None
        except (TypeError, ValueError):
            metadata["track_total"] = None

    return metadata


def guess_musicbrainz_tag_candidates(artist: str, title: str) -> list[dict]:
    """Return ordered MusicBrainz tag candidates for a track."""
    if not get_setting_bool("enable_musicbrainz", True):
        return []

    artist = (artist or "").strip()
    title = (title or "").strip()
    if not artist or not title:
        return []

    try:
        headers = {"User-Agent": f"MusicGrabber/{VERSION} (https://gitlab.com/g33kphr33k/musicgrabber)"}
        params = {
            "query": f'artist:"{artist}" AND recording:"{title}"',
            "fmt": "json",
            "limit": 5,
            "inc": "releases release-groups artist-credits",
        }

        with httpx.Client(timeout=TIMEOUT_HTTP_REQUEST) as client:
            response = client.get("https://musicbrainz.org/ws/2/recording/", params=params, headers=headers)
        if response.status_code != 200:
            return []

        recordings = response.json().get("recordings") or []
        if not recordings:
            return []

        def _recording_score(rec: dict) -> tuple[int, int]:
            raw_score = int(rec.get("score", 0))
            release_bonus = 1 if rec.get("releases") else 0
            return (raw_score, release_bonus)

        def _release_score_text(rel: dict) -> int:
            rg = rel.get("release-group") or {}
            rg_for_score = dict(rg)
            if not rg_for_score.get("artist-credit"):
                rg_for_score["artist-credit"] = rel.get("artist-credit") or []
            if rel.get("date") and not rg_for_score.get("first-release-date"):
                rg_for_score["_date"] = rel["date"]
            return _score_release_group(rg_for_score, artist)

        candidates = []
        seen_release_ids = set()
        seen_album_keys = set()
        sorted_recordings = sorted(recordings, key=_recording_score, reverse=True)
        for recording in sorted_recordings:
            mb_score = int(recording.get("score", 0))
            if mb_score < 85:
                continue
            releases = sorted(recording.get("releases") or [], key=_release_score_text, reverse=True)
            if not releases:
                candidates.append(_build_musicbrainz_guess_for_release(recording, None, artist, title, headers))
                continue
            for release in releases:
                release_id = release.get("id")
                album_key = ((release.get("title") or "").strip().lower(), (release.get("date") or "")[:4])
                if release_id and release_id in seen_release_ids:
                    continue
                if album_key in seen_album_keys:
                    continue
                if release_id:
                    seen_release_ids.add(release_id)
                seen_album_keys.add(album_key)
                candidates.append(_build_musicbrainz_guess_for_release(recording, release, artist, title, headers))

        return candidates
    except Exception as e:
        print(f"MusicBrainz tag guess failed for '{artist} - {title}': {e}")
        return []


def guess_musicbrainz_tags(artist: str, title: str, offset: int = 0) -> Optional[dict]:
    """Return one MusicBrainz tag guess for a track, by ordered candidate index."""
    candidates = guess_musicbrainz_tag_candidates(artist, title)
    if not candidates:
        return None
    if offset < 0 or offset >= len(candidates):
        return None
    guess = dict(candidates[offset])
    guess["candidate_index"] = offset
    guess["candidate_count"] = len(candidates)
    return guess


def _run_fpcalc(file_path: Path) -> Optional[tuple[int, str]]:
    """Run fpcalc on an audio file and return (duration, fingerprint).

    Returns None if fpcalc isn't installed, the file is unreadable,
    or the audio is too short to fingerprint (happens with previews
    and other sad little clips).
    """
    try:
        result = subprocess.run(
            ["fpcalc", "-json", str(file_path)],
            capture_output=True, text=True,
            timeout=TIMEOUT_FPCALC
        )
        if result.returncode != 0:
            return None

        data = json.loads(result.stdout)
        duration = int(data.get("duration", 0))
        fingerprint = data.get("fingerprint", "")

        if not fingerprint or duration < 1:
            return None

        return duration, fingerprint

    except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception):
        return None


def _score_recording(recording: dict, expected_artist: str, expected_title: str) -> int:
    """Score how well an AcoustID recording matches what we think we downloaded.

    AcoustID returns a pile of recordings for a fingerprint  -  covers, remasters,
    compilations, and occasionally Kylie Minogue. This picks the one that
    actually matches what we asked for.
    """
    score = 0
    artist_names = [a.get("name", "").lower() for a in recording.get("artists", [])]
    rec_title = (recording.get("title") or "").lower()
    exp_artist = expected_artist.lower()
    exp_title = expected_title.lower()

    # Artist match is the strongest signal
    if any(exp_artist in name or name in exp_artist for name in artist_names):
        score += 10

    # Title match  -  bonus for exact match, smaller bonus for substring
    if exp_title == rec_title:
        score += 8
    elif exp_title in rec_title or rec_title in exp_title:
        score += 5

    # Penalise covers, remixes, and karaoke  -  we want the real deal
    if "cover" in rec_title or "karaoke" in rec_title or "tribute" in rec_title:
        score -= 8

    # Penalise remastered/live/session versions  -  prefer the original
    if "remaster" in rec_title or "live" in rec_title or "session" in rec_title:
        score -= 2

    # Slight bonus for having release groups (means it's well-catalogued)
    if recording.get("releasegroups"):
        score += 1

    return score


def _score_release_group(rg: dict, expected_artist: str) -> int:
    """Score a MusicBrainz release group for use as the canonical album.

    Higher = better. Prefers studio albums by the actual artist; penalises
    compilations, promos, radio edits, deluxe/remaster editions, and Various
    Artists releases so we don't end up tagging everything as 'Promo Only
    Modern Rock Radio, Vol 47' or 'Astroworld X (Expanded Anniversary Remix)'.
    """
    score = 0
    primary_type  = (rg.get("type") or rg.get("primary-type") or "").lower()
    secondary_types = [t.lower() for t in (rg.get("secondary-types") or rg.get("secondarytypes") or [])]
    title = (rg.get("title") or "").lower()

    # Strongly prefer studio albums
    if primary_type == "album":
        score += 10
    elif primary_type == "single":
        score += 4
    elif primary_type == "ep":
        score += 3

    # Penalise compilations, soundtracks, remixes, live albums, promos
    _bad_secondary = {"compilation", "live", "remix", "soundtrack", "dj-mix", "mixtape/street", "demo"}
    if any(t in _bad_secondary for t in secondary_types):
        score -= 8

    # Penalise "Promo Only", "Radio", "Now That's What I Call Music", etc.
    _bad_title_fragments = ["promo only", "promo-only", "various artist", "radio edit",
                            "now that's what i call", "now that's what", "hits ", "greatest hits",
                            "best of", "collection", "the very best", "extracts from",
                            "extracts", "sampler", "advance", "promo sampler", "album sampler"]
    if any(frag in title for frag in _bad_title_fragments):
        score -= 10

    # Penalise reissues, deluxe editions, anniversary pressings, etc.
    # These are almost never the canonical release the user actually wants.
    _edition_fragments = ["deluxe", "remaster", "anniversary", "expanded",
                          "bonus track", "special edition", "collector",
                          "complete edition", "super deluxe"]
    if any(frag in title for frag in _edition_fragments):
        score -= 4

    # Slight preference for shorter titles; the original album is usually
    # "Astroworld" not "Astroworld X (Expanded Anniversary Edition)".
    # Cap the penalty so absurdly long names don't dominate the score.
    title_len = len(rg.get("title") or "")
    if title_len > 30:
        score -= min((title_len - 30) // 10, 3)  # -1 per 10 chars over 30, max -3

    # Prefer earlier releases; the original pressing is more likely canonical.
    # Works with both release-group `first-release-date` and individual
    # release `date` (callers may stash it under `_date` before scoring).
    date_str = rg.get("first-release-date") or rg.get("_date") or ""
    year_match = re.match(r'(\d{4})', date_str)
    if year_match:
        year = int(year_match.group(1))
        # Small bonus scaled so earlier years win ties but don't override
        # type-based scoring. 2000 -> +2, 2010 -> +1, 2020+ -> 0
        score += max(0, (2025 - year) // 10)

    # Check release group artist credits
    rg_artist_credit = rg.get("artist-credit") or []
    for ac in rg_artist_credit:
        if isinstance(ac, dict):
            artist_name = (ac.get("name") or ac.get("artist", {}).get("name") or "").lower()
            if "various" in artist_name:
                score -= 12
            elif expected_artist and expected_artist.lower() in artist_name:
                score += 6  # Artist's own release
            elif expected_artist and artist_name in expected_artist.lower():
                score += 4  # Close enough

    return score


def _extract_recording_metadata(recording: dict, expected_artist: str = "") -> dict:
    """Pull artist, title, album, and recording_id from an AcoustID recording."""
    metadata = {
        "title": recording.get("title"),
        "artist": None,
        "album": None,
        "year": None,
        "recording_id": recording.get("id"),
    }

    artists = recording.get("artists", [])
    if artists:
        metadata["artist"] = " & ".join(
            a.get("name", "") for a in artists if a.get("name")
        )

    artist_for_scoring = metadata["artist"] or expected_artist

    # Extract album from release groups  -  prefer studio albums by the actual artist
    releasegroups = recording.get("releasegroups", [])
    if releasegroups:
        album_rg = max(releasegroups, key=lambda rg: _score_release_group(rg, artist_for_scoring))
        metadata["album"] = album_rg.get("title")

    return metadata


def _lookup_acoustid(duration: int, fingerprint: str,
                     expected_artist: str = "", expected_title: str = "") -> Optional[dict]:
    """Ask AcoustID what this audio actually is.

    Returns a dict with title, artist, album, and recording_id
    if we get a confident match, or None if AcoustID shrugs.
    Uses the expected artist/title to pick the best recording from
    the (often chaotic) list AcoustID returns.
    """
    try:
        headers = {"User-Agent": f"MusicGrabber/{VERSION} (https://gitlab.com/g33kphr33k/musicgrabber)"}

        params = {
            "client": get_setting("acoustid_api_key", "0NILMQojj4"),
            "duration": duration,
            "fingerprint": fingerprint,
            "meta": "recordings releasegroups",
        }

        with httpx.Client(timeout=TIMEOUT_HTTP_REQUEST) as client:
            response = client.get(
                "https://api.acoustid.org/v2/lookup",
                params=params, headers=headers
            )

        if response.status_code != 200:
            return None

        data = response.json()
        results = data.get("results", [])
        if not results:
            return None

        # Collect all recordings from results with a good fingerprint score
        all_recordings = []
        for result in results:
            fp_score = result.get("score", 0)
            if fp_score < ACOUSTID_MIN_SCORE:
                continue
            for rec in result.get("recordings", []):
                if rec.get("title"):
                    all_recordings.append((fp_score, rec))

        if not all_recordings:
            best_score = results[0].get("score", 0) if results else 0
            print(f"AcoustID: no usable recordings (best fingerprint score {best_score:.2f})")
            return None

        # Pick the recording that best matches what we think we downloaded
        best_rec = max(
            all_recordings,
            key=lambda x: _score_recording(x[1], expected_artist, expected_title)
        )
        fp_score, recording = best_rec
        match_score = _score_recording(recording, expected_artist, expected_title)

        # Require a meaningful positive signal. Score breakdown: artist match=+10,
        # exact title=+8, partial title=+5, release groups=+1. A score of 0-9 means
        # the title matched but the artist didn't — that's not enough to trust, since
        # "Killing in the Name" will match any cover version. Require at least artist
        # OR (title + release group), i.e. a minimum of 10 to accept.
        if match_score < 10:
            print(f"AcoustID: best recording match score {match_score} is too low, skipping")
            return None

        metadata = _extract_recording_metadata(recording, expected_artist=expected_artist)

        print(f"AcoustID match (fp {fp_score:.2f}, match {match_score}): {metadata['artist']} - {metadata['title']}")
        return metadata

    except Exception as e:
        print(f"AcoustID lookup failed: {e}")
        return None


def _lookup_musicbrainz_by_id(recording_id: str, expected_artist: str = "") -> Optional[dict]:
    """Fetch release date from MusicBrainz using a recording MBID.

    AcoustID gives us the recording ID but not the release date,
    so we pop over to MusicBrainz to fill in that gap.
    """
    try:
        headers = {"User-Agent": f"MusicGrabber/{VERSION} (https://gitlab.com/g33kphr33k/musicgrabber)"}

        url = f"https://musicbrainz.org/ws/2/recording/{recording_id}"
        params = {"inc": "releases release-groups artist-credits", "fmt": "json"}

        with httpx.Client(timeout=TIMEOUT_HTTP_REQUEST) as client:
            response = client.get(url, params=params, headers=headers)

        if response.status_code != 200:
            return None

        data = response.json()
        releases = data.get("releases", [])
        if not releases:
            return None

        # Pick the best release rather than blindly taking the first.
        # Wraps the release in a fake release-group dict so _score_release_group can do its job.
        def _release_score(rel: dict) -> int:
            rg = rel.get("release-group") or {}
            # Fold release-level artist credit into the rg dict for scoring
            rg_for_score = dict(rg)
            if not rg_for_score.get("artist-credit"):
                rg_for_score["artist-credit"] = rel.get("artist-credit") or []
            # Stash release date so the scorer can prefer earlier pressings
            if rel.get("date") and not rg_for_score.get("first-release-date"):
                rg_for_score["_date"] = rel["date"]
            return _score_release_group(rg_for_score, expected_artist)

        release = max(releases, key=_release_score)
        result = {}
        result["release_mbid"] = release.get("id")

        date_str = release.get("date", "")
        if date_str:
            year_match = re.match(r'(\d{4})', date_str)
            if year_match:
                result["year"] = year_match.group(1)

        if release.get("title"):
            result["album"] = release["title"]

        # Track position within the release
        for medium in release.get("media", []):
            for track in medium.get("tracks", []):
                result["track_number"] = track.get("number")
                result["track_total"] = medium.get("track-count")
                break
            else:
                continue
            break

        # Recording-level length (ms) is on the top-level recording object
        length_ms = data.get("length")
        if length_ms:
            result["expected_duration_secs"] = length_ms / 1000.0

        return result if result else None

    except Exception:
        return None


def lookup_musicbrainz_by_isrc(isrc: str, expected_artist: str = "") -> Optional[dict]:
    """Look up a recording by ISRC.

    Tidal hands us a real ISRC at search time, so we can ask MusicBrainz the
    exact question instead of guessing by title and crossing our fingers.
    The ISRC endpoint returns the recording; we then reuse the by-ID release
    scoring to land on a sensible album/year/track number.
    """
    if not get_setting_bool("enable_musicbrainz", True):
        return None
    if not isrc:
        return None
    try:
        headers = {"User-Agent": f"MusicGrabber/{VERSION} (https://gitlab.com/g33kphr33k/musicgrabber)"}
        url = f"https://musicbrainz.org/ws/2/isrc/{isrc}"
        params = {"inc": "artist-credits", "fmt": "json"}
        with httpx.Client(timeout=TIMEOUT_HTTP_REQUEST) as client:
            response = client.get(url, params=params, headers=headers)
        if response.status_code != 200:
            return None
        data = response.json()
        recordings = data.get("recordings") or []
        if not recordings:
            return None

        recording = recordings[0]
        recording_id = recording.get("id")
        artist_credit = recording.get("artist-credit") or []
        artist_name = " ".join(
            (ac.get("name") or ac.get("artist", {}).get("name", "")) + (ac.get("joinphrase") or "")
            for ac in artist_credit
            if isinstance(ac, dict)
        ).strip() or expected_artist or None

        metadata = {
            "title": recording.get("title"),
            "artist": artist_name,
            "recording_id": recording_id,
            "metadata_source": "musicbrainz_isrc",
        }
        length_ms = recording.get("length")
        if length_ms:
            metadata["expected_duration_secs"] = length_ms / 1000.0

        # Re-use the by-ID lookup so we get the same release-scoring as everyone else
        if recording_id:
            extra = _lookup_musicbrainz_by_id(recording_id, expected_artist=artist_name or expected_artist)
            if extra:
                for k, v in extra.items():
                    if v and not metadata.get(k):
                        metadata[k] = v
        return metadata

    except Exception as e:
        print(f"MusicBrainz ISRC lookup failed for {isrc}: {e}")
        return None


def lookup_metadata(artist: str, title: str, file_path: Path = None) -> Optional[dict]:
    """Look up track metadata, trying audio fingerprinting first.

    The hierarchy of increasingly desperate measures:
    1. Fingerprint the file with fpcalc -> query AcoustID
    2. If AcoustID matches, fetch the release date from MusicBrainz by recording ID
    3. If fingerprinting fails or scores too low, fall back to text-based MusicBrainz search

    Returns a dict with 'title', 'artist', 'album', 'year' or None.
    """
    if not get_setting_bool("enable_musicbrainz", True):
        return None

    # Step 1: Try AcoustID fingerprinting (if we have a file to work with)
    if file_path and file_path.exists():
        fp_result = _run_fpcalc(file_path)
        if fp_result:
            duration, fingerprint = fp_result
            if duration < MIN_SONG_DURATION_SECS:
                # Clips this short fingerprint unreliably  -  AcoustID might return
                # a confident match for the correct song, but we'd be tagging the wrong
                # (too short) file with metadata that doesn't describe it. Skip it.
                print(
                    f"AcoustID skipped: file is only {duration}s "
                    f"(< {MIN_SONG_DURATION_SECS}s), too short to fingerprint reliably"
                )
                fp_result = None
        if fp_result:
            duration, fingerprint = fp_result
            acoustid_meta = _lookup_acoustid(duration, fingerprint, artist, title)

            if acoustid_meta:
                acoustid_meta["metadata_source"] = "acoustid_fingerprint"
                # Step 2: Fill in release info (album, year, duration) from MusicBrainz
                recording_id = acoustid_meta.get("recording_id")
                if recording_id:
                    mb_extra = _lookup_musicbrainz_by_id(recording_id, expected_artist=artist)
                    if mb_extra:
                        if mb_extra.get("year") and not acoustid_meta.get("year"):
                            acoustid_meta["year"] = mb_extra["year"]
                        if mb_extra.get("album") and not acoustid_meta.get("album"):
                            acoustid_meta["album"] = mb_extra["album"]
                        if mb_extra.get("track_number") and not acoustid_meta.get("track_number"):
                            acoustid_meta["track_number"] = mb_extra["track_number"]
                        if mb_extra.get("track_total") and not acoustid_meta.get("track_total"):
                            acoustid_meta["track_total"] = mb_extra["track_total"]
                        if mb_extra.get("expected_duration_secs") and not acoustid_meta.get("expected_duration_secs"):
                            acoustid_meta["expected_duration_secs"] = mb_extra["expected_duration_secs"]
                        if mb_extra.get("release_mbid") and not acoustid_meta.get("release_mbid"):
                            acoustid_meta["release_mbid"] = mb_extra["release_mbid"]

                return acoustid_meta

    # Step 3: Fall back to text-based MusicBrainz search
    return lookup_musicbrainz(artist, title)


def fetch_mb_expected_duration(artist: str, title: str) -> Optional[float]:
    """Quick MusicBrainz lookup to get the canonical duration for a track.

    Used at search time to score results by how close their duration is to
    what MusicBrainz considers the real thing. Returns seconds as a float,
    or None if MB is disabled, the track is unknown, or the lookup fails.
    No file required  -  text search only.
    """
    result = lookup_musicbrainz(artist, title)
    return result.get("expected_duration_secs") if result else None


def fetch_lyrics(artist: str, title: str) -> Optional[str]:
    """Fetch synced lyrics from LRClib API"""
    if not get_setting_bool("enable_lyrics", True):
        return None

    try:
        headers = {"User-Agent": f"MusicGrabber/{VERSION} (https://gitlab.com/g33kphr33k/musicgrabber)"}

        with httpx.Client(timeout=TIMEOUT_HTTP_REQUEST) as client:
            # Try the get endpoint first (exact match)
            params = {
                "artist_name": artist,
                "track_name": title
            }

            response = client.get(
                "https://lrclib.net/api/get",
                params=params,
                headers=headers
            )

            if response.status_code == 200:
                data = response.json()
                # Prefer synced lyrics, fall back to plain
                if data.get("syncedLyrics"):
                    return data["syncedLyrics"]
                elif data.get("plainLyrics"):
                    return data["plainLyrics"]

            # If exact match fails, try search
            search_params = {"q": f"{artist} {title}"}
            search_response = client.get(
                "https://lrclib.net/api/search",
                params=search_params,
                headers=headers
            )

            if search_response.status_code == 200:
                results = search_response.json()
                if results:
                    # Return first match with synced lyrics, or first with plain
                    for result in results:
                        if result.get("syncedLyrics"):
                            return result["syncedLyrics"]
                    for result in results:
                        if result.get("plainLyrics"):
                            return result["plainLyrics"]

        return None

    except Exception as e:
        # If lyrics lookup fails, log and continue without
        print(f"Lyrics lookup failed for {artist} - {title}: {e}")
        return None


def save_lyrics_file(flac_path: Path, lyrics: str):
    """Save lyrics as .lrc file alongside the audio file"""
    lrc_path = flac_path.with_suffix(".lrc")
    lrc_path.write_text(lyrics, encoding="utf-8")
    set_file_permissions(lrc_path)


def _is_source_branding(text: str) -> bool:
    """Return True if the string looks like YouTube/distributor auto-generated boilerplate.

    Matches the block that yt-dlp stuffs into COMMENT tags, e.g.:
      "Provided to YouTube by DistroKid\\n\\nTrack Name · Artist\\n\\n℗ 2024 Label\\n\\n..."
    Also catches the shorter auto-generated variant and standalone rights lines.
    """
    if not text:
        return False
    t = text.strip()
    return bool(
        re.match(r'Provided to YouTube by ', t)
        or re.match(r'Auto-generated by YouTube', t, re.IGNORECASE)
        or re.match(r'℗\s*\d{4}', t)
        or re.match(r'Released on:\s', t)
    )


def read_existing_track_number(file_path: Path) -> tuple[int | None, int | None]:
    """Read existing track number and total from an audio file's tags.

    Returns (track_number, track_total), either or both may be None.
    Useful for checking whether a source already baked in track info before we
    overwrite it with MusicBrainz guesses.
    """
    try:
        suffix = file_path.suffix.lower()
        if suffix == ".flac":
            audio = FLAC(str(file_path))
            tn = audio.get("TRACKNUMBER", [None])[0]
            tt = audio.get("TRACKTOTAL", audio.get("TOTALTRACKS", [None]))[0]
        elif suffix == ".mp3":
            from mutagen.easyid3 import EasyID3
            audio = EasyID3(str(file_path))
            raw = (audio.get("tracknumber", [None]) or [None])[0]
            if raw and "/" in str(raw):
                parts = str(raw).split("/", 1)
                tn, tt = parts[0], parts[1]
            else:
                tn, tt = raw, None
        elif suffix in (".m4a", ".mp4"):
            from mutagen.mp4 import MP4
            audio = MP4(str(file_path))
            trkn = audio.get("trkn", [(None, None)])[0]
            tn, tt = (trkn[0], trkn[1]) if trkn else (None, None)
            if tt == 0:
                tt = None
        elif suffix in (".ogg", ".opus"):
            from mutagen.oggopus import OggOpus
            from mutagen.oggvorbis import OggVorbis
            audio = OggOpus(str(file_path)) if suffix == ".opus" else OggVorbis(str(file_path))
            tn = audio.get("TRACKNUMBER", [None])[0]
            tt = audio.get("TRACKTOTAL", audio.get("TOTALTRACKS", [None]))[0]
        else:
            return None, None

        tn_int = int(tn) if tn else None
        tt_int = int(tt) if tt else None
        return (tn_int if tn_int and tn_int > 0 else None,
                tt_int if tt_int and tt_int > 0 else None)
    except Exception:
        return None, None


def apply_metadata_to_file(
    file_path: Path,
    artist: str,
    title: str,
    album: str = "",
    year: str = None,
    track_number: int | None = None,
    track_total: int | None = None,
    album_art_bytes: bytes | None = None,
    album_art_mime: str | None = None,
    album_artist: str | None = None,
    source: str | None = None,
    source_quality: str | None = None,
):
    """Apply metadata to audio file using mutagen (supports multiple formats).

    source / source_quality stamp where MusicGrabber fetched the audio and at
    what quality. The SOURCE tag doubles as the "this file is ours" eligibility
    marker for the track-upgrades feature; without it a file is invisible to
    upgrades. Only written when provided, so existing tags are never clobbered.
    """
    try:
        suffix = file_path.suffix.lower()
        track_number = int(track_number) if track_number else None
        track_total = int(track_total) if track_total else None
        art_mime = (album_art_mime or "image/jpeg").lower()
        has_art = bool(album_art_bytes)

        if suffix == '.flac':
            from mutagen.flac import Picture
            audio = FLAC(str(file_path))
            audio["ARTIST"] = artist
            audio["TITLE"] = title
            if album:
                audio["ALBUM"] = album
            if album_artist:
                audio["ALBUMARTIST"] = album_artist
                audio["ALBUM ARTIST"] = album_artist
            if year:
                audio["DATE"] = year
            if track_number:
                audio["TRACKNUMBER"] = str(track_number)
                if track_total:
                    audio["TRACKTOTAL"] = str(track_total)
                    audio["TOTALTRACKS"] = str(track_total)
            # Wipe yt-dlp source branding from COMMENT tag
            if any(_is_source_branding(c) for c in audio.get("COMMENT", [])):
                audio["COMMENT"] = []
            if source:
                audio["SOURCE"] = source
            if source_quality:
                audio["SOURCE_QUALITY"] = source_quality
            if has_art:
                pic = Picture()
                pic.type = 3  # front cover
                pic.mime = art_mime
                pic.data = album_art_bytes
                audio.clear_pictures()
                audio.add_picture(pic)
            audio.save()

        elif suffix == '.mp3':
            from mutagen.easyid3 import EasyID3
            from mutagen.mp3 import MP3
            from mutagen.id3 import ID3, APIC
            try:
                audio = EasyID3(str(file_path))
            except Exception:
                # If no ID3 tag exists, create one
                mp3 = MP3(str(file_path))
                mp3.add_tags()
                mp3.save()
                audio = EasyID3(str(file_path))
            audio["artist"] = artist
            audio["title"] = title
            if album:
                audio["album"] = album
            if album_artist:
                audio["albumartist"] = [album_artist]
            if year:
                audio["date"] = year
            if track_number:
                tn = f"{track_number}/{track_total}" if track_total else str(track_number)
                audio["tracknumber"] = [tn]
            if any(_is_source_branding(c) for c in audio.get("comment", [])):
                audio["comment"] = []
            if source or source_quality:
                # EasyID3 won't take arbitrary keys; register them as TXXX frames.
                EasyID3.RegisterTXXXKey("source", "SOURCE")
                EasyID3.RegisterTXXXKey("source_quality", "SOURCE_QUALITY")
                if source:
                    audio["source"] = source
                if source_quality:
                    audio["source_quality"] = source_quality
            audio.save()
            if has_art:
                mp3 = MP3(str(file_path), ID3=ID3)
                if mp3.tags is None:
                    mp3.add_tags()
                mp3.tags.delall("APIC")
                mp3.tags.add(APIC(encoding=3, mime=art_mime, type=3, desc="Cover", data=album_art_bytes))
                mp3.save(v2_version=3)

        elif suffix in ['.m4a', '.mp4']:
            from mutagen.mp4 import MP4, MP4Cover
            audio = MP4(str(file_path))
            audio["\xa9ART"] = [artist]
            audio["\xa9nam"] = [title]
            if album:
                audio["\xa9alb"] = [album]
            if album_artist:
                audio["aART"] = [album_artist]
            if year:
                audio["\xa9day"] = [year]
            if track_number:
                audio["trkn"] = [(track_number, track_total or 0)]
            # \xa9cmt is the comment atom
            if any(_is_source_branding(c) for c in audio.get("\xa9cmt", [])):
                audio["\xa9cmt"] = []
            # Freeform atoms for our source/quality markers (values are bytes)
            if source:
                audio["----:com.musicgrabber:SOURCE"] = [source.encode("utf-8")]
            if source_quality:
                audio["----:com.musicgrabber:SOURCE_QUALITY"] = [source_quality.encode("utf-8")]
            if has_art:
                fmt = MP4Cover.FORMAT_PNG if art_mime == "image/png" else MP4Cover.FORMAT_JPEG
                audio["covr"] = [MP4Cover(album_art_bytes, imageformat=fmt)]
            audio.save()

        elif suffix in ['.ogg', '.opus']:
            from mutagen.oggopus import OggOpus
            from mutagen.oggvorbis import OggVorbis
            from mutagen.flac import Picture
            try:
                if suffix == '.opus':
                    audio = OggOpus(str(file_path))
                else:
                    audio = OggVorbis(str(file_path))
                audio["ARTIST"] = artist
                audio["TITLE"] = title
                if album:
                    audio["ALBUM"] = album
                if album_artist:
                    audio["ALBUMARTIST"] = album_artist
                    audio["ALBUM ARTIST"] = album_artist
                if year:
                    audio["DATE"] = year
                if track_number:
                    audio["TRACKNUMBER"] = str(track_number)
                    if track_total:
                        audio["TRACKTOTAL"] = str(track_total)
                        audio["TOTALTRACKS"] = str(track_total)
                if any(_is_source_branding(c) for c in audio.get("COMMENT", [])):
                    audio["COMMENT"] = []
                if source:
                    audio["SOURCE"] = source
                if source_quality:
                    audio["SOURCE_QUALITY"] = source_quality
                if has_art:
                    pic = Picture()
                    pic.type = 3  # front cover
                    pic.mime = art_mime
                    pic.data = album_art_bytes
                    audio["METADATA_BLOCK_PICTURE"] = [base64.b64encode(pic.write()).decode("ascii")]
                audio.save()
            except Exception:
                pass  # Some ogg variants may not be supported

        # For .webm and other unsupported formats, skip metadata (yt-dlp handles it)

    except Exception:
        # If metadata application fails, continue anyway
        pass


def search_artist_mbid(name: str) -> list[dict]:
    """Search MusicBrainz for an artist by name.

    Returns up to MB_ARTIST_SEARCH_LIMIT candidates ordered by match score,
    each as {mbid, name, disambiguation, score}. Empty list when MB returned
    a valid empty result; raises MusicBrainzUnavailable when MB is unreachable
    so the API layer can tell the user to retry instead of pretending the
    artist does not exist.
    """
    headers = {"User-Agent": f"MusicGrabber/{VERSION} (https://gitlab.com/g33kphr33k/musicgrabber)"}
    params = {
        "query": name,
        "limit": MB_ARTIST_SEARCH_LIMIT,
        "fmt": "json",
    }
    response = _mb_get_with_retry(
        "https://musicbrainz.org/ws/2/artist",
        params=params, headers=headers, timeout=TIMEOUT_MUSICBRAINZ_ARTIST,
    )
    if response.status_code != 200:
        # 4xx (bad query etc) -- definitive, not a network problem.
        return []
    artists = response.json().get("artists", [])
    results = []
    for a in artists:
        results.append({
            "mbid": a.get("id", ""),
            "name": a.get("name", ""),
            "disambiguation": a.get("disambiguation", ""),
            "score": int(a.get("score", 0)),
        })
    # Exact case match first, then case-insensitive, then MB relevance score.
    # Matters for artists like "SiR" where lowercasing loses the distinction.
    name_lower = name.lower()
    results.sort(key=lambda r: (
        0 if r["name"] == name else
        1 if r["name"].lower() == name_lower else
        2,
        -r["score"]
    ))
    return results


def fetch_artist_singles(mbid: str) -> list[dict]:
    """Fetch all singles for an artist from MusicBrainz.

    Returns a flat list of track dicts: {title, artist, release_date, release_mbid}.
    Singles with multiple tracks (A-side + B-side) are each returned as separate rows.
    Release date may be an empty string if MusicBrainz doesn't know it yet.
    Paginates automatically; sleeps 1 second between pages to respect rate limits.
    """
    import time as _time
    headers = {"User-Agent": f"MusicGrabber/{VERSION} (https://gitlab.com/g33kphr33k/musicgrabber)"}
    tracks: list[dict] = []
    offset = 0
    limit = 100
    total = None

    # Secondary types that disqualify a release from being a plain single.
    # MusicBrainz uses these on the release-group to tag remixes, live cuts,
    # compilations, soundtracks and the like.
    _EXCLUDED_SECONDARY_TYPES = {
        "Remix", "Live", "Compilation", "Soundtrack", "Interview",
        "Spokenword", "Audiobook", "Audio drama", "DJ-mix", "Mixtape/Street",
    }

    try:
        while True:
            params = {
                "artist": mbid,
                "type": "single",
                "limit": limit,
                "offset": offset,
                "inc": "recordings artist-credits release-groups",
                "fmt": "json",
            }
            with httpx.Client(timeout=TIMEOUT_MUSICBRAINZ_ARTIST) as client:
                response = client.get("https://musicbrainz.org/ws/2/release", params=params, headers=headers)
            if response.status_code != 200:
                print(f"MusicBrainz singles fetch failed for {mbid}: HTTP {response.status_code}")
                break
            data = response.json()
            if total is None:
                total = data.get("release-count", 0)
            releases = data.get("releases", [])
            if not releases:
                break

            for release in releases:
                # Skip anything with a disqualifying secondary type
                rg = release.get("release-group") or {}
                secondary_types = rg.get("secondary-types") or []
                if any(t in _EXCLUDED_SECONDARY_TYPES for t in secondary_types):
                    continue

                release_mbid = release.get("id", "")
                release_date = release.get("date") or release.get("first-release-date") or ""
                # Flatten all recordings on the release to individual track rows
                for medium in release.get("media", []):
                    for track in medium.get("tracks", []):
                        recording = track.get("recording", {})
                        rec_title = recording.get("title") or track.get("title") or release.get("title", "")
                        # Prefer the credited artist on the recording; fall back to release artist
                        artist_credits = (
                            recording.get("artist-credit")
                            or release.get("artist-credit")
                            or []
                        )
                        artist_name = " ".join(
                            (ac.get("name") or ac.get("artist", {}).get("name", ""))
                            + (ac.get("joinphrase") or "")
                            for ac in artist_credits
                            if isinstance(ac, dict)
                        ).strip() or ""
                        if rec_title:
                            tracks.append({
                                "title": rec_title,
                                "artist": artist_name,
                                "release_date": release_date,
                                "release_mbid": release_mbid,
                            })
            offset += len(releases)
            if offset >= total:
                break
            _time.sleep(1)  # MusicBrainz rate limit: 1 req/sec
    except Exception as e:
        print(f"MusicBrainz singles fetch error for {mbid}: {e}")

    return tracks


def fetch_artist_albums(mbid: str) -> list[dict]:
    """Fetch studio albums for an artist from MusicBrainz.

    Returns [{title, year, release_mbid}, ...] sorted by year ascending.
    Filters out compilations, live albums, soundtracks and other non-studio releases.
    Paginates automatically; sleeps 1 second between pages to respect rate limits.
    Raises MusicBrainzUnavailable when MB is unreachable on the very first page
    (so the UI can show a retry prompt). If MB dies partway through pagination
    we keep whatever we already collected -- a partial list beats nothing.
    """
    import time as _time
    headers = {"User-Agent": f"MusicGrabber/{VERSION} (https://gitlab.com/g33kphr33k/musicgrabber)"}
    albums: list[dict] = []
    offset = 0
    limit = 100
    total = None
    seen_release_groups: set[str] = set()

    _EXCLUDED_SECONDARY_TYPES = {
        "Compilation", "Live", "Remix", "Soundtrack", "Interview",
        "Spokenword", "Audiobook", "Audio drama", "DJ-mix", "Mixtape/Street",
        "Demo",
    }

    while True:
        params = {
            "artist": mbid,
            "type": "album",
            "limit": limit,
            "offset": offset,
            "inc": "release-groups",
            "fmt": "json",
        }
        try:
            response = _mb_get_with_retry(
                "https://musicbrainz.org/ws/2/release",
                params=params, headers=headers, timeout=TIMEOUT_MUSICBRAINZ_ARTIST,
            )
        except MusicBrainzUnavailable:
            if offset == 0:
                # Nothing collected yet -- bubble up so the UI prompts a retry.
                raise
            # Partial data is better than none; stop here and return what we have.
            print(f"MusicBrainz albums fetch for {mbid} stopped after partial pagination (offset={offset})")
            break
        if response.status_code != 200:
            print(f"MusicBrainz albums fetch failed for {mbid}: HTTP {response.status_code}")
            break
        try:
            data = response.json()
        except Exception as e:
            print(f"MusicBrainz albums fetch parse error for {mbid}: {e}")
            break
        if total is None:
            total = data.get("release-count", 0)
        releases = data.get("releases", [])
        if not releases:
            break

        for release in releases:
            rg = release.get("release-group") or {}
            rg_id = rg.get("id", "")
            secondary_types = rg.get("secondary-types") or []
            if any(t in _EXCLUDED_SECONDARY_TYPES for t in secondary_types):
                continue
            # One entry per release group — earliest release wins
            if rg_id and rg_id in seen_release_groups:
                continue
            if rg_id:
                seen_release_groups.add(rg_id)

            title = release.get("title", "")
            date = release.get("date") or release.get("first-release-date") or ""
            year = date[:4] if date else ""
            release_mbid = release.get("id", "")
            if title:
                albums.append({
                    "title": title,
                    "year": year,
                    "release_mbid": release_mbid,
                })

        offset += len(releases)
        if offset >= total:
            break
        _time.sleep(1)  # MusicBrainz rate limit: 1 req/sec

    albums.sort(key=lambda a: a["year"] or "9999")
    return albums


def fetch_album_tracks(release_mbid: str) -> list[dict]:
    """Fetch the tracklist for a specific release from MusicBrainz.

    Returns [{position, title}, ...] in track order.
    Position is a string (e.g. "1", "A1") as MusicBrainz provides it.
    Raises MusicBrainzUnavailable when MB is unreachable after retries.
    """
    headers = {"User-Agent": f"MusicGrabber/{VERSION} (https://gitlab.com/g33kphr33k/musicgrabber)"}
    params = {"inc": "recordings", "fmt": "json"}
    response = _mb_get_with_retry(
        f"https://musicbrainz.org/ws/2/release/{release_mbid}",
        params=params, headers=headers, timeout=TIMEOUT_MUSICBRAINZ_ARTIST,
    )
    if response.status_code != 200:
        print(f"MusicBrainz tracklist fetch failed for {release_mbid}: HTTP {response.status_code}")
        return []
    try:
        data = response.json()
    except Exception as e:
        print(f"MusicBrainz tracklist parse error for {release_mbid}: {e}")
        return []
    tracks: list[dict] = []
    for medium in data.get("media", []):
        for track in medium.get("tracks", []):
            recording = track.get("recording") or {}
            title = recording.get("title") or track.get("title", "")
            position = str(track.get("position") or track.get("number") or "")
            if title:
                tracks.append({"position": position, "title": title})
    return tracks
