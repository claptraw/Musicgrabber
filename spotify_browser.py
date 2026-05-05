"""
MusicGrabber - Spotify Headless Browser Script

Standalone script executed as a subprocess by spotify.py.
Receives spotify_type and spotify_id via environment variables.
Outputs JSON to stdout: {success, tracks, playlist_name, count} or {success, error}.
"""

import json
import os
import re
import sys
import time

from playwright.sync_api import sync_playwright

spotify_type = os.environ["SPOTIFY_TYPE"]
spotify_id = os.environ["SPOTIFY_ID"]
url = f"https://open.spotify.com/{spotify_type}/{spotify_id}"
SELECTOR = '[data-testid="tracklist-row"]'
is_liked_songs = os.environ.get("SPOTIFY_IS_LIKED_SONGS", "") == "1"

tracks = []
playlist_name = f"Spotify {spotify_type.title()}"
stall_timeout_seconds = 30
stall_raw = (os.environ.get("SPOTIFY_BROWSER_STALL_SECONDS") or "").strip()
if stall_raw.isdigit():
    stall_timeout_seconds = max(5, min(300, int(stall_raw)))

sp_dc = (os.environ.get("SPOTIFY_SP_DC") or "").strip() or None

try:
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
        )
        # Stop Spotify (and other sites) detecting Playwright via navigator.webdriver
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        if sp_dc:
            context.add_cookies([{
                "name": "sp_dc",
                "value": sp_dc,
                "domain": ".spotify.com",
                "path": "/",
                "httpOnly": True,
                "secure": True,
            }])

        page = context.new_page()
        page.goto(url, timeout=60000)

        # If Spotify redirects to the login page the cookie has expired
        if "accounts.spotify.com" in page.url or "/login" in page.url:
            print(json.dumps({"success": False, "error": "spotify_cookies_expired"}))
            sys.exit(0)
        time.sleep(3)

        expected_total = None
        expected_total_env = (os.environ.get("SPOTIFY_EXPECTED_TOTAL") or "").strip()
        if expected_total_env.isdigit():
            expected_total = int(expected_total_env)
        try:
            if expected_total is None:
                total_meta = page.locator('meta[name="music:song_count"]').first
                total_raw = total_meta.get_attribute("content")
                if total_raw and total_raw.isdigit():
                    expected_total = int(total_raw)
        except Exception:
            pass

        if expected_total is None:
            try:
                og_desc = page.locator('meta[property="og:description"]').first.get_attribute("content") or ""
                m = re.search(r"(\d[\d,]*)\s+items?", og_desc, re.IGNORECASE)
                if m:
                    expected_total = int(m.group(1).replace(",", ""))
            except Exception:
                expected_total = None

        # Accept cookie consent if present — this can block page rendering
        cookie_selectors = [
            "button:has-text('Accept cookies')",
            "button:has-text('Accept Cookies')",
            "button:has-text('ACCEPT COOKIES')",
            "[data-testid='cookie-policy-manage-dialog-accept-button']",
            "button.onetrust-close-btn-handler"
        ]
        for sel in cookie_selectors:
            try:
                btn = page.locator(sel).first
                if btn.count() == 0:
                    continue
                if not btn.is_visible(timeout=750):
                    continue
                print(f"DEBUG: Found cookie button with selector: {sel}", file=sys.stderr)
                btn.click(timeout=2000, force=True)
                time.sleep(0.5)
                break
            except Exception as e:
                print(f"DEBUG: Cookie selector {sel} failed: {e}", file=sys.stderr)

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

        # Liked songs page uses the same row structure as a regular playlist.
        if is_liked_songs:
            playlist_name = "Liked Songs"

        # Spotify uses virtualised scrolling — tracks get unloaded as you scroll.
        # Extract tracks incrementally while scrolling.
        seen_tracks_by_index = {}
        last_seen_count = 0
        last_progress_at = time.monotonic()

        def extract_visible_tracks():
            for row in page.query_selector_all(SELECTOR):
                try:
                    text = row.inner_text().strip()
                    parts = text.split(chr(10))
                    parts = [pt.strip() for pt in parts if pt.strip()]

                    # Only extract tracks that start with a numeric row index.
                    if not parts:
                        continue
                    track_index_raw = re.sub(r"[^\d]", "", parts[0])
                    if not track_index_raw:
                        continue
                    track_index = int(track_index_raw)

                    # Skip the track number
                    parts = parts[1:]

                    # Skip "E" for Explicit marker
                    if parts and parts[0] == "E":
                        parts = parts[1:]

                    if len(parts) >= 2:
                        track_name = parts[0].strip()
                        artist = parts[1].strip()
                        if artist == "E" and len(parts) >= 3:
                            artist = parts[2].strip()
                        if not track_name or artist == "E":
                            continue
                        # Music video rows show "Music Video" as the artist.
                        # The real artist follows a bullet (•) separator in the row parts,
                        # or may be extractable from the title field ("Artist - Title" pattern).
                        if artist.lower() == "music video":
                            # Scan parts for the bullet separator; artist is the element after it.
                            bullet_idx = next((i for i, p in enumerate(parts) if p == "\u2022"), None)
                            if bullet_idx is not None and bullet_idx + 1 < len(parts):
                                real_artist = parts[bullet_idx + 1].strip()
                                if real_artist and real_artist.lower() != "music video":
                                    seen_tracks_by_index[track_index] = f"{real_artist} - {track_name}"
                                    continue
                            # Fallback: try splitting title on a dash
                            dash = re.search(r"\s+[-\u2013\u2014]\s+", track_name)
                            if dash:
                                salvaged_artist = track_name[:dash.start()].strip()
                                salvaged_title = track_name[dash.end():].strip()
                                if salvaged_artist and salvaged_title:
                                    seen_tracks_by_index[track_index] = f"{salvaged_artist} - {salvaged_title}"
                            continue
                        seen_tracks_by_index[track_index] = f"{artist} - {track_name}"
                except Exception:
                    continue

        # First extraction before scrolling
        extract_visible_tracks()

        while True:
            if expected_total and len(seen_tracks_by_index) >= expected_total:
                break
            if time.monotonic() - last_progress_at > stall_timeout_seconds:
                break

            rows = page.query_selector_all(SELECTOR)
            if rows:
                rows[-1].scroll_into_view_if_needed()
            else:
                page.mouse.wheel(0, 2000)
            time.sleep(0.3)

            extract_visible_tracks()

            current_seen = len(seen_tracks_by_index)
            if current_seen > last_seen_count:
                last_seen_count = current_seen
                last_progress_at = time.monotonic()

        tracks = [seen_tracks_by_index[idx] for idx in sorted(seen_tracks_by_index)]
        if expected_total and len(tracks) < expected_total:
            print(
                f"DEBUG: Track extraction incomplete ({len(tracks)}/{expected_total})",
                file=sys.stderr,
            )
        browser.close()

    print(json.dumps({"success": True, "tracks": tracks, "playlist_name": playlist_name, "count": len(tracks)}))

except Exception as e:
    print(json.dumps({"success": False, "error": str(e)}))
