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

