"""
MusicGrabber - Spotify Playlist Fetching

Scrapes Spotify embed pages and uses headless browser for large playlists.
Spotify killed their public API for playlist access, so we scrape the embed
page which has a predictable JSON-in-HTML structure. For playlists with >100
tracks, the embed is truncated and we fall back to Playwright.
"""

import json
import os
import subprocess
import tempfile

from fastapi import HTTPException

from constants import TIMEOUT_SPOTIFY_BROWSER


def fetch_spotify_playlist_via_browser(spotify_id: str, spotify_type: str) -> dict:
    """Fetch playlist/album tracks using a headless browser

    This method works without API credentials by loading the Spotify page
    and scrolling to load all tracks (Spotify lazy-loads them).

    Runs Playwright in a completely separate subprocess to avoid any
    interference from uvicorn's event loop.

    Returns dict with: tracks (list of "Artist - Title"), playlist_name, count
    """
    url = f"https://open.spotify.com/{spotify_type}/{spotify_id}"
    print(f"Fetching Spotify {spotify_type} via headless browser: {url}")

    # Write the script to a temp file to avoid shell escaping issues
    selector = '[data-testid="tracklist-row"]'
    script_content = f"""
import json
import time
from playwright.sync_api import sync_playwright

url = "https://open.spotify.com/{spotify_type}/{spotify_id}"
tracks = []
playlist_name = "Spotify {spotify_type.title()}"
SELECTOR = '{selector}'

try:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={{"width": 1280, "height": 800}}
        )
        page = context.new_page()

        page.goto(url, timeout=60000)
        time.sleep(3)

        # Accept cookie consent if present - this can block page rendering
        cookie_selectors = [
            "button:has-text('Accept cookies')",
            "button:has-text('Accept Cookies')",
            "button:has-text('ACCEPT COOKIES')",
            "[data-testid='cookie-policy-manage-dialog-accept-button']",
            "button.onetrust-close-btn-handler"
        ]
        for selector in cookie_selectors:
            try:
                btn = page.query_selector(selector)
                if btn:
                    print(f"DEBUG: Found cookie button with selector: {{selector}}", file=__import__("sys").stderr)
                    btn.click()
                    time.sleep(2)
                    break
            except Exception as e:
                print(f"DEBUG: Cookie selector {{selector}} failed: {{e}}", file=__import__("sys").stderr)
                pass

        import sys as _sys

        # Wait for track list to load
        page.wait_for_selector(SELECTOR, timeout=30000)

        try:
            # Get playlist name from the page
            title_elem = page.query_selector('[data-testid="playlist-page"] h1')
            if not title_elem:
                title_elem = page.query_selector('[data-testid="entityTitle"] h1')
            if not title_elem:
                title_elem = page.query_selector('h1')
            if title_elem:
                name = title_elem.inner_text().strip()
                if name and name != "Your Library":
                    playlist_name = name
        except Exception:
            pass

        # Spotify uses virtualized scrolling - tracks get unloaded as you scroll
        # We need to extract tracks incrementally while scrolling
        seen_tracks = set()
        stale_count = 0
        last_seen_count = 0

        def extract_visible_tracks():
            extracted = []
            for row in page.query_selector_all(SELECTOR):
                try:
                    text = row.inner_text().strip()
                    parts = text.split(chr(10))
                    parts = [p.strip() for p in parts if p.strip()]

                    # Only extract tracks that have a track number (actual playlist tracks)
                    if not parts or not parts[0].isdigit():
                        continue

                    # Skip the track number
                    parts = parts[1:]

                    # Skip "E" for Explicit marker
                    if parts and parts[0] == "E":
                        parts = parts[1:]

                    if len(parts) >= 2:
                        track_name = parts[0].strip()
                        artist = parts[1].strip()
                        # Handle case where "E" slipped through as artist
                        if artist == "E" and len(parts) >= 3:
                            artist = parts[2].strip()
                        if track_name and artist and artist != "E":
                            track_str = artist + " - " + track_name
                            if track_str not in seen_tracks:
                                seen_tracks.add(track_str)
                                extracted.append(track_str)
                except Exception:
                    continue
            return extracted

        # First extraction before scrolling
        extract_visible_tracks()

        while stale_count < 20:
            # Scroll the last visible track row into view to trigger loading more
            rows = page.query_selector_all(SELECTOR)
            if rows:
                rows[-1].scroll_into_view_if_needed()
            time.sleep(0.3)

            # Extract any new visible tracks
            extract_visible_tracks()

            if len(seen_tracks) == last_seen_count:
                stale_count += 1
            else:
                stale_count = 0
                last_seen_count = len(seen_tracks)
        tracks = list(seen_tracks)

        browser.close()

    print(json.dumps({{"success": True, "tracks": tracks, "playlist_name": playlist_name, "count": len(tracks)}}))

except Exception as e:
    print(json.dumps({{"success": False, "error": str(e)}}))
"""

    # Write script to temp file and execute it
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write(script_content)
        script_path = f.name

    print(f"Running browser script: {script_path}")

    try:
        result = subprocess.run(
            ["python3", script_path],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SPOTIFY_BROWSER
        )
        print(f"Script return code: {result.returncode}")
        print(f"Script stdout: {result.stdout[:500] if result.stdout else 'empty'}")
        print(f"Script stderr: {result.stderr[:500] if result.stderr else 'empty'}")
    finally:
        # Clean up temp file
        try:
            os.unlink(script_path)
        except Exception:
            pass

    if result.returncode != 0:
        error_msg = result.stderr or "Unknown error"
        raise HTTPException(
            status_code=502,
            detail=f"Failed to fetch Spotify {spotify_type} via browser: {error_msg}"
        )

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=502,
            detail=f"Invalid response from browser subprocess: {result.stdout[:200]}"
        )

    if not data.get("success"):
        raise HTTPException(
            status_code=502,
            detail=f"Failed to fetch Spotify {spotify_type} via browser: {data.get('error', 'Unknown error')}"
        )

    tracks = data["tracks"]
    playlist_name = data["playlist_name"]

    print(f"Successfully extracted {len(tracks)} tracks via browser")

    if not tracks:
        raise HTTPException(
            status_code=422,
            detail=f"Could not extract tracks from {spotify_type}. The page structure may have changed."
        )

    return {
        "tracks": tracks,
        "playlist_name": playlist_name,
        "count": len(tracks)
    }
