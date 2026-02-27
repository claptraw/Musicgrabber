"""
MusicGrabber - YouTube / yt-dlp Operations

Cookie handling, bot backoff, search, and scoring.
"""

import json
import re
import random
import subprocess
import threading
import time

from constants import (
    BOT_BACKOFF_MIN_SECONDS, BOT_BACKOFF_MAX_SECONDS,
    COOKIES_FILE, TIMEOUT_YTDLP_SEARCH,
    YOUTUBE_SEARCH_MULTIPLIER, YOUTUBE_SEARCH_MIN_FETCH,
    YTDLP_PLAYER_CLIENT, MIN_SONG_DURATION_SECS,
)
from settings import get_setting, get_setting_int


# YouTube bot/backoff state
_bot_backoff_until = 0.0
_bot_backoff_lock = threading.Lock()
_cookies_disabled_until = 0.0
_cookies_lock = threading.Lock()


def _has_valid_cookie_entries(cookies_text: str) -> bool:
    """Check for at least one Netscape-format cookie entry (tabs-separated)."""
    for raw_line in cookies_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # Netscape format can prefix HttpOnly entries with "#HttpOnly_"
        if line.startswith("#HttpOnly_"):
            if line.count("\t") >= 6:
                return True
            continue
        # Skip comments
        if line.startswith("#"):
            continue
        if line.count("\t") >= 6:
            return True
    return False


def _cookie_lines_for_domain_check(cookies_text: str) -> list[str]:
    """Return cookie lines (including HttpOnly-prefixed entries) for domain checks."""
    lines = []
    for raw_line in cookies_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#HttpOnly_"):
            lines.append(line)
            continue
        if line.startswith("#"):
            continue
        lines.append(line)
    return lines


# Auth cookies are the ones that actually matter for YouTube login. Session/state
# cookies (ST-*, CONSISTENCY, YSC, etc.) are ephemeral noise and don't gate access.
_AUTH_COOKIE_NAMES = {
    "SID", "HSID", "SSID", "APISID", "SAPISID",
    "__Secure-1PSID", "__Secure-3PSID",
    "__Secure-1PAPISID", "__Secure-3PAPISID",
    "__Secure-1PSIDTS", "__Secure-3PSIDTS",
    "__Secure-1PSIDCC", "__Secure-3PSIDCC",
    "LOGIN_INFO",
}


def get_cookies_expiry(cookies_text: str) -> int | None:
    """Return the soonest expiry Unix timestamp among auth cookies, or None if
    no expiry info is found (session cookies with expiry=0 are ignored)."""
    soonest = None
    for raw_line in cookies_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        # Strip #HttpOnly_ prefix so the tab-split works normally
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        name = parts[5]
        if name not in _AUTH_COOKIE_NAMES:
            continue
        try:
            expiry = int(parts[4])
        except ValueError:
            continue
        if expiry <= 0:
            continue  # Session cookie  -  no fixed expiry
        if soonest is None or expiry < soonest:
            soonest = expiry
    return soonest


def clear_expired_cookies() -> bool:
    """Delete cookies from settings if all auth cookies have expired.
    Returns True if cookies were cleared, False otherwise."""
    from settings import get_setting, set_setting
    cookies_text = get_setting("youtube_cookies", "")
    if not cookies_text.strip():
        return False
    expiry = get_cookies_expiry(cookies_text)
    if expiry is None:
        return False
    if time.time() < expiry:
        return False
    # Every auth cookie has expired  -  bin them
    print("YouTube cookies have expired  -  clearing from settings")
    set_setting("youtube_cookies", "")
    _sync_cookies_file()
    return True


def _sync_cookies_file():
    """Write YouTube cookies from settings to the cookies file on disk.
    Called when settings are saved and at startup."""
    cookies = get_setting("youtube_cookies", "")
    if cookies.strip():
        if not _has_valid_cookie_entries(cookies):
            # Avoid writing invalid cookie data that can break yt-dlp
            if COOKIES_FILE.exists():
                COOKIES_FILE.unlink()
            return
        COOKIES_FILE.parent.mkdir(parents=True, exist_ok=True)
        COOKIES_FILE.write_text(cookies)
    elif COOKIES_FILE.exists():
        COOKIES_FILE.unlink()


def _ytdlp_base_args():
    """Return common yt-dlp arguments (cookies, optional player-client override).
    These should be prepended after 'yt-dlp' in every command."""
    args = []
    if _cookies_allowed() and COOKIES_FILE.exists() and COOKIES_FILE.stat().st_size > 0:
        args.extend(["--cookies", str(COOKIES_FILE)])
    if YTDLP_PLAYER_CLIENT:
        args.extend(["--extractor-args", f"youtube:player_client={YTDLP_PLAYER_CLIENT}"])
    return args


def _is_ytdlp_403(stderr: str) -> bool:
    """Check if yt-dlp stderr indicates a YouTube 403/bot-block error."""
    lower = stderr.lower()
    return "403" in lower or "forbidden" in lower or "sign in to confirm" in lower


def _strip_cookies_args(cmd: list[str]) -> list[str]:
    """Return a command list with any --cookies args removed."""
    cleaned = []
    skip_next = False
    for arg in cmd:
        if skip_next:
            skip_next = False
            continue
        if arg == "--cookies":
            skip_next = True
            continue
        cleaned.append(arg)
    return cleaned


def _should_retry_without_cookies(stderr: str) -> bool:
    """Decide if a download failure likely stems from cookie/auth issues."""
    lower = stderr.lower()
    return (
        _is_ytdlp_403(stderr)
        or "downloaded file is empty" in lower
        or "http error 403" in lower
        # Cookies from premium/broken sessions can cause YouTube to return a
        # different format manifest where bestaudio/best finds nothing.
        # Retry without cookies  -  the cookieless manifest is usually saner.
        or "requested format is not available" in lower
    )


def _get_bot_backoff_window() -> tuple[float, float]:
    """Return (min,max) seconds for bot backoff, enforcing sane bounds."""
    min_seconds = float(get_setting_int("youtube_bot_backoff_min", BOT_BACKOFF_MIN_SECONDS))
    max_seconds = float(get_setting_int("youtube_bot_backoff_max", BOT_BACKOFF_MAX_SECONDS))
    if min_seconds < 0:
        min_seconds = 0.0
    if max_seconds < 0:
        max_seconds = 0.0
    if max_seconds < min_seconds:
        print(
            f"youtube_bot_backoff_min ({min_seconds}) exceeded max ({max_seconds}); "
            "swapping to enforce sane bounds."
        )
        min_seconds, max_seconds = max_seconds, min_seconds
    return min_seconds, max_seconds


def _note_bot_block() -> None:
    """Record bot-block and extend the global backoff window."""
    now = time.time()
    min_seconds, max_seconds = _get_bot_backoff_window()
    sleep_for = random.uniform(min_seconds, max_seconds) if max_seconds > 0 else 0
    with _bot_backoff_lock:
        global _bot_backoff_until
        _bot_backoff_until = max(_bot_backoff_until, now + sleep_for)


def _sleep_if_botted() -> None:
    """Sleep if a recent bot-block was detected to reduce request pressure."""
    with _bot_backoff_lock:
        wait_for = _bot_backoff_until - time.time()
    if wait_for > 0:
        time.sleep(wait_for)


def _cookies_allowed() -> bool:
    with _cookies_lock:
        return time.time() >= _cookies_disabled_until


def _note_cookie_failure(cooldown_seconds: int = 7200) -> None:
    """Disable cookie usage for a cooldown window after likely cookie-related failures."""
    with _cookies_lock:
        global _cookies_disabled_until
        _cookies_disabled_until = max(_cookies_disabled_until, time.time() + cooldown_seconds)


def parse_duration(seconds: float) -> str:
    """Convert seconds to MM:SS or HH:MM:SS format"""
    seconds = int(seconds)
    if seconds < 3600:
        return f"{seconds // 60}:{seconds % 60:02d}"
    return f"{seconds // 3600}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"


def _normalise_search_text(text: str) -> str:
    """Normalise text for loose search matching."""
    text = text.lower()
    text = re.sub(r'[\(\[][^\)\]]*[\)\]]', '', text)
    text = re.sub(r'[^a-z0-9\s]', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def _parse_query_artist_title(query: str) -> tuple[str | None, str | None]:
    if not query:
        return None, None
    for sep in (" - ", " – ", "  -  ", " | "):
        if sep in query:
            artist, title = query.split(sep, 1)
            return artist.strip(), title.strip()
    return None, None


def score_search_result(
    title: str,
    channel: str,
    query: str | None = None,
    duration_seconds: float | None = None,
    view_count: int | None = None,
    album: str | None = None,
) -> int:
    """Score a search result to prioritise official content over live versions

    Higher score = better match
    Lower score = worse match (live, cover, remix, etc.)
    """
    title_lower = title.lower()
    channel_lower = channel.lower()
    album_lower = (album or "").lower()
    score = 100  # Start with base score

    # Penalties for live performances
    if re.search(r'\b(live|concert|tour|performance|unplugged)\b', title_lower):
        score -= 50

    # Absolute disqualifiers: results containing these words are never what anyone wants,
    # regardless of query or context. Scored so low they cannot win even against silence.
    query_lower = (query or "").lower()
    _never_re = r'\b(karaoke|nightcore|sped[- ]up|slowed|8d audio|bass boosted)\b'
    if re.search(_never_re, title_lower) or re.search(_never_re, album_lower):
        score -= 200

    # Bootlegs, flips, and refixes are unofficial fan edits  -  never what we want unless
    # the query explicitly names them (which would be unusual but technically possible).
    _bootleg_re = r'\b(bootleg|flip|refix|rework|mashup)\b'
    if re.search(_bootleg_re, title_lower):
        if not re.search(_bootleg_re, query_lower):
            score -= 100

    # Penalties for covers, remixes, instrumentals  -  check title AND album name.
    # Piano cover albums tag the track artist as the original artist, so the
    # album is often the only place the word "cover" appears.
    # Remix/edit penalties are waived when the query itself requests that version.
    _cover_re = r'\b(cover|remix|instrumental|acoustic version|live session|piano version|tribute)\b'
    if re.search(_cover_re, title_lower) or re.search(_cover_re, album_lower):
        # Don't penalise a remix result when we're explicitly searching for a remix
        if not re.search(r'\b(remix|edit|mix)\b', query_lower):
            score -= 40

    # Penalties for lyric videos (usually lower quality)
    if re.search(r'\b(lyric|lyrics)\b', title_lower):
        score -= 20

    # Penalties for fan uploads or unofficial - no cell phone video, thanks
    if re.search(r'\b(fan|unofficial|tribute)\b', title_lower):
        score -= 30
    if re.search(r'\b(fan|fanpage|tribute|cover)\b', channel_lower):
        score -= 25

    # Copyright-filtered uploads: audio muted, pitch-shifted, or otherwise butchered
    # to dodge Content ID. The file is useless. Nuke it from orbit.
    if re.search(r'filter(?:ed)?\s*(?:for\s*)?copyright|copyright\s*filter|pitch\s*shift|freq\s*shift', title_lower):
        score -= 200

    # Bonuses for official content
    if re.search(r'\b(official|vevo)\b', title_lower):
        score += 30

    if re.search(r'\b(official|vevo)\b', channel_lower):
        score += 40

    # Bonus for "Topic" channels (often official audio)
    if channel_lower.endswith(" - topic"):
        score += 35

    # Bonus for "official music video" or "official video"
    if re.search(r'official\s*(music)?\s*video', title_lower):
        score += 25

    # Bonus for official audio (best signal for a music grabber)
    if re.search(r'official\s*audio', title_lower):
        score += 35

    # Bonus when channel name appears in title (often "Artist - Title")
    if channel_lower and channel_lower in title_lower:
        score += 10

    # Query-aware matching (helps prefer exact artist/title matches)
    if query:
        query_norm = _normalise_search_text(query)
        title_norm = _normalise_search_text(title)
        channel_norm = _normalise_search_text(channel)
        combined_norm = f"{title_norm} {channel_norm}".strip()

        stopwords = {
            "official", "music", "video", "lyrics", "lyric", "audio",
            "hd", "hq", "remaster", "remastered", "live", "full", "album",
        }
        query_tokens = [t for t in query_norm.split() if t not in stopwords]
        if query_tokens:
            matches = sum(1 for t in query_tokens if t in combined_norm)
            coverage = matches / len(query_tokens)
            if coverage == 1:
                score += 20
            elif coverage >= 0.7:
                score += 10
            elif coverage < 0.4:
                score -= 15

        expected_artist, expected_title = _parse_query_artist_title(query)
        expected_artist_norm = _normalise_search_text(expected_artist or "")
        expected_title_norm = _normalise_search_text(expected_title or "")
        if expected_title_norm:
            if expected_title_norm in title_norm:
                score += 25
            else:
                score -= 25
        if expected_artist_norm:
            if expected_artist_norm in title_norm:
                score += 15
            elif expected_artist_norm in channel_norm:
                score += 10
            else:
                score -= 10
        if expected_artist_norm and expected_title_norm:
            if f"{expected_artist_norm} {expected_title_norm}" in title_norm:
                score += 20

    # Penalty for reaction videos, compilations
    if re.search(r'\b(reaction|react|compilation|vs)\b', title_lower):
        score -= 60

    # Penalty for extended versions (often DJ mixes)
    if re.search(r'\b(extended|extended mix|extended version)\b', title_lower):
        score -= 15

    # Penalties for non-song results or modified audio
    if re.search(r'\b(full album|album|mix|playlist|soundtrack)\b', title_lower):
        score -= 40
    if re.search(r'\b(reverb)\b', title_lower):
        score -= 45

    # Duration scoring  -  typical songs are 2-6 minutes
    if duration_seconds is not None and duration_seconds > 0:
        if duration_seconds < MIN_SONG_DURATION_SECS:
            score -= 80   # Previews, intros, clips  -  essentially disqualified
        elif duration_seconds < 60:
            score -= 40   # Very short  -  probably not the full track
        elif duration_seconds < 90:
            score -= 15   # Short but could be a genuine interlude
        elif duration_seconds <= 420:
            score += 10   # Sweet spot (1:30 – 7:00)
        elif duration_seconds <= 720:
            pass          # 7-12 min  -  could be legit long track
        elif duration_seconds <= 1200:
            score -= 20   # 12-20 min  -  likely extended mix
        else:
            score -= 40   # 20+ min  -  album, mix, or compilation

    # View count  -  modest tiebreaker, log-scale to avoid domination
    if view_count is not None and view_count >= 0:
        if view_count < 1_000:
            score -= 10   # Suspiciously low
        elif view_count >= 100_000:
            score += 5    # Decent signal of legitimacy
            if view_count >= 10_000_000:
                score += 5  # Very likely official (+10 total)

    return score


def parse_youtube_search_results(stdout: str, query: str | None = None) -> list[dict]:
    results = []
    for line in stdout.strip().split('\n'):
        if not line:
            continue
        try:
            data = json.loads(line)
            is_playlist = data.get("_type") == "playlist" or "playlist" in data.get("ie_key", "").lower()
            video_count = data.get("playlist_count") or data.get("n_entries")

            title = data.get("title", "Unknown")
            channel = data.get("channel", data.get("uploader", "Unknown"))
            duration_secs = data.get("duration") or 0
            views = data.get("view_count")
            quality_score = score_search_result(
                title, channel, query,
                duration_seconds=duration_secs or None,
                view_count=views,
            )

            results.append({
                "video_id": data.get("id", ""),
                "title": title,
                "channel": channel,
                "duration": parse_duration(data.get("duration", 0) or 0) if not is_playlist else "",
                "thumbnail": data.get("thumbnail", f"https://i.ytimg.com/vi/{data.get('id')}/mqdefault.jpg"),
                "is_playlist": is_playlist,
                "video_count": video_count,
                "source": "youtube",
                "source_url": data.get("webpage_url") or (
                    f"https://www.youtube.com/watch?v={data.get('id')}" if data.get("id") else ""
                ),
                "quality": None,
                "quality_score": quality_score,
                "slskd_username": None,
                "slskd_filename": None,
            })
        except json.JSONDecodeError:
            continue
    return results


def search_youtube(query: str, limit: int) -> list[dict]:
    """Search YouTube and return normalized results"""
    try:
        fetch_limit = max(limit * YOUTUBE_SEARCH_MULTIPLIER, YOUTUBE_SEARCH_MIN_FETCH)

        cmd = [
            "yt-dlp",
            *_ytdlp_base_args(),
            "--dump-json",
            "--flat-playlist",
            "--no-warnings",
            f"ytsearch{fetch_limit}:{query}",
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_YTDLP_SEARCH)

        if result.returncode != 0:
            stderr = result.stderr or ""
            lower = stderr.lower()

            def _reason(err_lower: str) -> str:
                if not err_lower.strip():
                    return "unknown provider error"
                if "sign in to confirm your age" in err_lower or "age-restricted" in err_lower:
                    return "age-restricted content requires valid cookies"
                if "private video" in err_lower:
                    return "private video"
                if "video unavailable" in err_lower:
                    return "video unavailable or region-restricted"
                if "http error 429" in err_lower or "too many requests" in err_lower:
                    return "rate-limited by YouTube"
                if _is_ytdlp_403(err_lower):
                    return "request blocked (403)"
                if "unable to extract" in err_lower or "failed to extract" in err_lower:
                    return "metadata extraction failed"
                if "unable to download webpage" in err_lower or "timed out" in err_lower:
                    return "provider/network timeout"
                if "requested format is not available" in err_lower:
                    return "format manifest unavailable"
                return "provider rejected the request"

            reason = _reason(lower)
            if _should_retry_without_cookies(stderr):
                _note_bot_block()

            used_cookies = "--cookies" in cmd
            if used_cookies and _should_retry_without_cookies(stderr):
                cmd_no_cookies = _strip_cookies_args(cmd)
                try:
                    result_no_cookies = subprocess.run(
                        cmd_no_cookies,
                        capture_output=True,
                        text=True,
                        timeout=TIMEOUT_YTDLP_SEARCH,
                    )
                except Exception as e:
                    print(f"YouTube search failed for '{query}': {reason}. Cookieless retry error: {e}")
                    return []

                if result_no_cookies.returncode == 0:
                    results = parse_youtube_search_results(result_no_cookies.stdout, query=query)
                    results.sort(key=lambda x: x["quality_score"], reverse=True)
                    if results:
                        print(f"YouTube search cookieless retry succeeded for '{query}', cookies look stale")
                        _note_cookie_failure()
                        return results[:limit]
                    print(f"YouTube search failed for '{query}': {reason}. Cookieless retry returned no parseable results")
                    return []

                reason2 = _reason((result_no_cookies.stderr or "").lower())
                if _should_retry_without_cookies(result_no_cookies.stderr):
                    _note_bot_block()
                print(f"YouTube search failed for '{query}': {reason}. Cookieless retry failed: {reason2}")
                return []

            print(f"YouTube search failed for '{query}': {reason}")
            return []

        results = parse_youtube_search_results(result.stdout, query=query)
        results.sort(key=lambda x: x["quality_score"], reverse=True)
        return results[:limit]

    except Exception as e:
        print(f"YouTube search error: {e}")
        return []
