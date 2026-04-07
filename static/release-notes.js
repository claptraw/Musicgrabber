// =============================================================================
// Release Notes
// =============================================================================
// One entry per released version. Add new versions at the top.
// Shown once on first load after an update, and accessible via the
// "Release Notes" button in Settings. Keep it human-readable, not a
// changelog dump.

const RELEASE_NOTES = {
    "2.6.0": {
        title: "What's New in v2.6.0",
        sections: [
            {
                heading: "Tag Editor Modal",
                items: [
                    "Queue items no longer open an inline tag form inside the constantly-refreshing queue card. 'Edit Tags' now opens a proper modal with artist, title, album, album artist, year, and track number fields, a filename preview, and reset/save actions. Your edits survive the queue refreshing underneath.",
                    "The tag editor also has a 'Guess from MusicBrainz' button that fills in album, album artist, year, and track numbering for completed downloads. If the first guess is close-but-wrong, 'Guess Again' walks through the next available candidate instead of giving you the same answer twice.",
                ]
            },
            {
                heading: "Search: Better Lossless Results",
                items: [
                    "All-source searches now give Monochrome/Tidal up to 10 scored candidates in the merged ranking (was a small fixed cap shared with all sources). Lossless results have more room to compete without every source flooding the list.",
                ]
            },
            {
                heading: "Fixed: Watched Playlist Mismatch Checker Too Strict",
                items: [
                    "Several patterns that should match were being incorrectly rejected. 'Paro House - Luciid VIP' (Spotify dash-form) now matches 'Paro House (Luciid VIP)' (Tidal bracket-form). 'NO DRAMA - Original Mix' matches 'NO DRAMA'. Single-word variant labels like 'TechnoBack' are now accepted as bracket equivalents.",
                    "The artist check for multi-artist credits (e.g. 'OGUZ, Nyctonian') now passes if any individual artist is mentioned in the result, rather than requiring the whole comma-joined string verbatim. Tidal typically credits only the primary artist, so the old check was silently failing good matches.",
                ]
            },
            {
                heading: "Fixed: Monochrome Duration Mismatch Now Falls Back",
                items: [
                    "When Tidal serves a shorter radio edit or different version that fails the MusicBrainz duration check, MusicGrabber now falls back to YouTube or mp3phoenix to find the right version, rather than just failing the download outright.",
                ]
            },
            {
                heading: "Fixed: Various Trash Bin Causes",
                items: [
                    "Monochrome scoring was penalising correct VIP/remix results when the variant was written with dashes in the query but brackets in the Tidal title. The -110 penalty is now skipped when the bracketed content is already present in the search query.",
                    "AcoustID now requires an artist match to accept a metadata override. Previously, a title-only match (score 9) was enough, which let cover versions overwrite the correct artist field and trigger a mismatch failure.",
                    "Channel names with distributor prefixes like 'Premiere Eczko' are now stripped to just 'Eczko' when used as the artist fallback.",
                ]
            },
            {
                heading: "Fixed: Large Playlist Import Crashes",
                items: [
                    "The database connection pool was exhausted under heavy concurrent load, causing 'database is locked' crashes mid-import. The pool now blocks briefly on checkout instead of spawning unlimited competing connections. Pool size also bumped from 5 to 8.",
                ]
            },
        ]
    },
    "2.5.6": {
        title: "What's New in v2.5.6",
        sections: [
            {
                heading: "SoundCloud Playlists",
                items: [
                    "You can now paste a SoundCloud sets URL or likes page into the watched playlists form and it works like any other platform. Full append/mirror sync, M3U generation, and per-playlist source selection all included.",
                    "Importing a SoundCloud playlist automatically pre-selects SoundCloud as the download source, since the tracks are already right there.",
                ]
            },
        ]
    },
    "2.5.5": {
        title: "What's New in v2.5.5",
        sections: [
            {
                heading: "Fixed: Duplicate Detection Missing Auto-Routed Album Folders",
                items: [
                    "If a single had already been auto-filed into an album subfolder, duplicate detection could still miss it because it only checked the flat artist folders. That meant a second download could sneak through and create another copy in the wrong place.",
                    "MusicGrabber now scans one level deeper inside artist folders when checking for duplicates, so tracks already routed into `Artist/Album/` are found properly before anything gets downloaded again.",
                ]
            },
            {
                heading: "Fixed: Soulseek 'Add to Playlist' Was Incomplete",
                items: [
                    "Soulseek downloads were not receiving the playlist parameters from the API layer, so 'Add to playlist' did not behave like the other sources. Duplicate-skip jobs could not finish as 'added to playlist', and successful Soulseek downloads were not appended to the physical `.m3u` file.",
                    "That path now receives the same playlist context as YouTube, Monochrome, and the rest, so Soulseek tracks can be added to playlist folders and M3Us correctly.",
                ]
            },
            {
                heading: "Fixed: Per-User Playlist M3Us Using the Wrong Folder",
                items: [
                    "Physical playlist updates were resolving the playlists directory without `user_id`, which meant user-specific playlist subfolder settings could be ignored in multi-user setups.",
                    "All playlist M3U writes now resolve the correct per-user playlists directory consistently across every source.",
                ]
            },
        ]
    },
    "2.5.4": {
        title: "What's New in v2.5.4",
        sections: [
            {
                heading: "Fixed: Monochrome Playback Endpoint Change",
                items: [
                    "Monochrome changed how its website fetches playable tracks. Search and info still worked, but the old `/track/` endpoint MusicGrabber used for downloads started returning `403 Upstream API error` for lots of perfectly normal tracks.",
                    "MusicGrabber now uses Monochrome's newer `/trackManifests/` playback endpoint with the same signed DASH manifest flow the website uses. It also rotates across multiple Monochrome instance hosts automatically, so one rate-limited instance no longer sinks the whole source.",
                ]
            },
            {
                heading: "Fixed: Monochrome Hover Preview",
                items: [
                    "The new Monochrome playback API returns a DASH manifest, which browsers will not play directly in a normal audio element. Hover preview now runs through a tiny server-side ffmpeg transcode to a short MP3 preview stream, so Monochrome previews work again.",
                ]
            },
            {
                heading: "Fixed: Watched Playlist Source Toggles Forgetting Their State",
                items: [
                    "The per-playlist source toggles (YT, PX, SC, MO) were saving correctly to the database, but the UI forgot your choices on every page load and cheerfully turned everything back on. Turns out the source list wasn't cached yet when the chips first rendered, so the re-population step saw an empty page and assumed you wanted all of them. Your preferences now survive page loads as intended.",
                ]
            },
            {
                heading: "Fixed: Watched Playlists Occasionally Grabbing the Wrong Song",
                items: [
                    "When searching for a watched playlist track, if no result mentioned the expected artist at all, the importer used to shrug and download the highest-scoring result anyway. This meant roughly 2% of a large playlist could end up as completely wrong tracks, which is a fun surprise if you like musical roulette, less so if you don't.",
                    "Now fails the track with a clear error explaining what it nearly downloaded, so you can retry it manually from the Missing panel instead of discovering the interloper six months later.",
                ]
            },
            {
                heading: "Fixed: Monochrome/Tidal CDN Wobbles",
                items: [
                    "Even after Monochrome hands back a valid playback manifest, the downstream Tidal CDN can still wobble with the occasional 403 or 429. The download path now retries with backoff before giving up, which makes transient edge failures much less fatal.",
                ]
            },
        ]
    },
    "2.5.3": {
        title: "What's New in v2.5.3",
        sections: [
            {
                heading: "New: Trash Bin",
                items: [
                    "Deleting a track now moves it to a trash folder instead of wiping it forever. Think of it as a recycling bin for your ears.",
                    "Changed your mind? Hit Restore in the Trash Bin to put it back exactly where it came from, no re-download needed.",
                    "The Queue tab now has a Trash Bin section at the bottom (only appears when there's something in it) with Restore and permanent Delete buttons per file, plus an 'Empty Trash' button for the brave.",
                    "Files that fail mismatch or duration checks now land in the trash too, so you can listen before the evidence disappears.",
                ]
            },
            {
                heading: "New: Play Button on Queue and Trash",
                items: [
                    "Completed downloads now have a play button in their expanded details. Quick way to check what actually got downloaded without leaving the tab.",
                    "Trashed files get a play button too, so you can listen before deciding whether to restore or permanently bin them. Especially useful for mismatch rejects that might be perfectly fine.",
                ]
            },
            {
                heading: "Fixed: Database Locking During Bulk Imports",
                items: [
                    "The new score rationale feature was accidentally opening a second database connection mid-transaction, which could cause 'database is locked' errors during bulk imports and watched playlist refreshes. Now shares the existing connection properly.",
                ]
            },
            {
                heading: "Fixed: ListenBrainz Weekly Playlists Stuck After Rotation",
                items: [
                    "If your ListenBrainz 'Created for You' playlists were added before the auto-rotation feature existed, they had no username stored and would get stuck showing 'playlist not found' every week after rotation. The refresh now self-heals by pulling the username out of the playlist name and updating the record, so it silently picks up the new week's playlist as intended.",
                ]
            },
        ]
    },
    "2.5.2": {
        title: "What's New in v2.5.2",
        sections: [
            {
                heading: "New: Download Progress Stages",
                items: [
                    "The queue now shows what each download is doing in real time instead of just sitting on 'downloading' until it finishes. You'll see stages like 'Fetching info', 'Downloading audio', 'Looking up metadata', 'Tagging file', 'Fetching lyrics', and more. Updates every 3 seconds.",
                    "Works across all sources: YouTube, SoundCloud, Monochrome/Tidal, MP3Phoenix, and Soulseek.",
                ]
            },
            {
                heading: "New: Concurrent Downloads Setting",
                items: [
                    "You can now control how many bulk import and watched playlist downloads run at the same time. Find it in Settings under General (admin-only), or set the MAX_CONCURRENT_DOWNLOADS env var. Range is 1-10, default 3. Higher is faster but increases the risk of bot detection.",
                ]
            },
            {
                heading: "New: \"Why This Result?\" Scoring Rationale",
                items: [
                    "When a bulk import or watched playlist download goes wrong, you can now find out exactly why the scorer picked that particular result. Failed and problematic queue items get a 'Why this result?' link in their expanded details.",
                    "Shows the winning candidate's score with human-readable reasons (e.g. 'Official channel +40', 'Live/session penalty -180'), plus the top 3 runners-up so you can see what it passed over and why.",
                    "There's a 'Raw scoring' expander for the technically curious, and a clipboard copy button if you want to share the evidence of the scorer's questionable life choices.",
                ]
            },
            {
                heading: "Fixed: Cross-Playlist Duplicate Mismatches",
                items: [
                    "If the same track appeared in two different watched playlists, the second playlist would mark it 'completed with errors' because Spotify and Monochrome/YouTube disagreed on punctuation (brackets vs hyphens, subtitles, etc.). The file was already on disk and perfectly fine, it was just the name check being overzealous. Duplicate-skip paths now bypass the mismatch comparison entirely.",
                ]
            },
            {
                heading: "Fixed: Watched Playlists Getting Too Into Live Versions",
                items: [
                    "Watched playlists are now much less likely to wander off with a live/session recording when what you actually wanted was the normal studio track. Performance-style results get hit with much heavier score penalties unless the query explicitly asks for one.",
                    "The live detector also learned some new vocabulary. It's no longer just looking for the word 'live' — it now catches the usual suspects like Tiny Desk, KEXP, Mahogany, COLORS, Radio 1, From The Basement, sessions, and other \"this definitely happened in front of people\" uploads across YouTube, Monochrome, MP3Phoenix, SoundCloud, and Soulseek.",
                    "On top of that, the watched-track matcher stops shrugging and treating 'live' as harmless title fluff, so a concert version no longer gets waved through as if it were the plain studio release.",
                ]
            },
            {
                heading: "Fixed: Track Numbers on Singles",
                items: [
                    "Single track downloads were leaving the TRACKNUMBER tag empty, which upset Beets and other library managers during lookups. MusicBrainz already had the data, we just weren't writing it to the file. Now uses a priority chain: album context first, then existing file tags (Tidal FLACs already have this baked in), then MusicBrainz as a fallback. Existing tags are never overwritten.",
                ]
            },
            {
                heading: "Fixed: First User Login & Button Breakage",
                items: [
                    "Creating the first user account now automatically refreshes the browser and shows the login page. Previously you'd be left on an unresponsive UI until you manually hit F5.",
                    "User management buttons (Remove, Force reset) and album artist Select button were silently broken by a quoting bug. They work now.",
                ]
            },
        ]
    },
    "2.5.1": {
        title: "What's New in v2.5.1",
        sections: [
            {
                heading: "Force Download for Mismatched Tracks",
                items: [
                    "Watched playlist tracks that fail the name-matching check now have a \"Force Download\" button in the Stats tab mismatch log and on the queue card itself. If you can see the track is correct but the names just don't line up (YouTube vs Spotify naming quirks, non-English characters, etc.), hit the button and it'll re-download without the comparison. The mismatch record is cleaned up automatically.",
                    "Works even if the original job has been cleaned up (e.g. via Stats reset). A fresh job is created from the expected artist/title and queued with the name check skipped.",
                    "Static assets (CSS, JS) are now cache-busted with the version number, so you'll always see new features immediately after an update without needing to hard-refresh.",
                ]
            },
            {
                heading: "Bug Fixes",
                items: [
                    "Opus files are now properly converted to your chosen audio format (MP3, FLAC, etc.) even when yt-dlp's built-in converter fails mid-stream. Previously, a failed conversion would leave the raw Opus file in your library.",
                    "MP3Phoenix downloads now respect your audio format setting instead of always converting to FLAC.",
                ]
            },
        ]
    },
    "2.5.0": {
        title: "What's New in v2.5.0",
        sections: [
            {
                heading: "New: Spotify Liked Songs",
                items: [
                    "Paste your Liked Songs URL (open.spotify.com/collection/tracks) into the playlist import or watched playlists field and it works like any other playlist. Requires the sp_dc cookie to be set in Settings, since Spotify considers your likes a private affair.",
                    "Works as a watched playlist too, so new likes get picked up automatically on the next refresh. If your cookies expire, you'll get a notification and a clear error on the playlist card rather than silent failures.",
                ]
            },
            {
                heading: "New: Proper Album Cover Art",
                items: [
                    "Every download now tries really hard to find proper album artwork instead of relying on YouTube video thumbnails. The fallback chain: Cover Art Archive (MusicBrainz), Tidal CDN (Monochrome tracks), iTunes Search API, then Deezer API. No API keys needed, no configuration required.",
                    "Soulseek and MP3Phoenix downloads, which previously had zero cover art, now get artwork from the same fallback chain. If all sources come up empty, the yt-dlp thumbnail is kept as a last resort.",
                    "Monochrome/Tidal cover art now survives format conversion to MP3/Opus. Previously it was embedded into the FLAC but lost when converting to other formats.",
                ]
            },
            {
                heading: "New: Search to Album Shortcut",
                items: [
                    "Search for 'Artist - Title' and MusicGrabber will look up the artist's discography on MusicBrainz in the background. If the track belongs to a known album, an 'Artist and Album' chip appears in the Related Searches box. One click takes you straight to the Albums tab with the full tracklist loaded, ready for download.",
                    "Monochrome/Tidal results also get a clickable album name in the result line. Spot a track from a good album? Click the album name and you're there.",
                ]
            },
            {
                heading: "New: Configurable Download Timeouts",
                items: [
                    "If you've ever had a long track (DJ mixes, live recordings, symphonies) come out broken or truncated, the download or conversion was probably hitting a hard time limit. Those limits are now configurable via environment variables in your docker-compose: TIMEOUT_YTDLP_DOWNLOAD, TIMEOUT_FFMPEG_CONVERT, and TIMEOUT_MP3PHOENIX_DOWNLOAD. Defaults are unchanged, so nothing breaks if you don't touch them.",
                ]
            },
            {
                heading: "New: Paginated Queue",
                items: [
                    "The download queue now shows the last 250 jobs, paginated 10 at a time with prev/next controls. No more scrolling through your entire download history to find that one track that failed. The 'Downloadable to Device' list is similarly paginated at 15 per page.",
                ]
            },
            {
                heading: "Improvements",
                items: [
                    "Spotify music video rows are now parsed correctly. Previously, tracks marked as music videos could be silently dropped if the artist couldn't be extracted from the title. The scraper now finds the artist via Spotify's bullet separator, which handles explicit markers and other row variations without falling over.",
                    "Artist search in Albums and Watched tabs now respects case sensitivity. Searching for 'SiR' no longer returns every artist named 'Sir'. Exact case match floats to the top.",
                    "File permissions now apply to artist and album directories too, not just the files inside them. If your NAS could see the files but the folder itself had the wrong permissions, that's sorted.",
                ]
            },
            {
                heading: "Security Fixes",
                items: [
                    "Session tokens and download tokens now expire when they're supposed to. A timestamp format mismatch meant tokens could stay valid a bit longer than intended on their expiry day. Sorted.",
                    "In multi-user mode, regular users could previously change their music directory to any path on the server. That's now admin-only, as it should be.",
                    "The 'Test Connection' buttons for Navidrome, Jellyfin, and Lidarr could be used by non-admin users to poke arbitrary URLs from the server. Non-admins can still test their own saved settings, but can no longer supply custom URLs.",
                    "SQLite foreign key cascades (cleanup of sessions, tokens, and settings when a user is deleted) were declared in the database schema but never actually switched on. They work now, so deleting a user properly cleans up after itself.",
                ]
            }
        ]
    },
    "2.4.6": {
        title: "What's New in v2.4.6",
        sections: [
            {
                heading: "New: configurable file permissions",
                items: [
                    "Downloaded files have always been set to 666 (rw for everyone) for NAS and SMB compatibility. If your media server or NAS refuses to write to files owned by root, you can now switch to 777 in Settings (admin only). Two options, no free-text field — we're not animals.",
                ]
            },
            {
                heading: "New: sub-hourly watched playlist intervals",
                items: [
                    "Refresh intervals now go down to every 30 minutes, with hourly, 6h, and 12h options added alongside the existing daily/weekly/monthly. The backend already supported it; the UI just hadn't bothered to expose it. Bot-ban consequences are your own.",
                ]
            },
            {
                heading: "Bug fixes",
                items: [
                    "The 'Skip duplicates' toggle was gaslighting you — it looked saved, then quietly forgot every time you refreshed. The setting is now actually written to the database like it promised.",
                    "The playlist routing selector was showing a 'will be overwritten on sync' warning for Append playlists, which don't get overwritten on sync at all. The warning now only appears for Mirror playlists, where it actually means something.",
                    "Tracks stuck in Missing because their download job failed, despite the file already being on disk from another route (manual download, different playlist), will now be spotted and cleared on the next refresh.",
                ]
            }
        ]
    },
    "2.4.5": {
        title: "What's New in v2.4.5",
        sections: [
            {
                heading: "Bug fixes",
                items: [
                    "If your music folder wasn't actually /music, Settings was telling porkies. It now shows the real path.",
                    "MusicGrabber can now live under a reverse-proxy subpath like /musicgrabber without losing all its CSS, JS, and API calls in the woods. Set ROOT_PATH and let your proxy strip it.",
                    "A bunch of Watched tab buttons had stage fright and stopped responding. Search, Retry, Tracks, Copy URL, Delete, and friends are awake again, and manual Search now jumps back to Results with the right playlist already picked.",
                    "Duplicate tracks that already exist in one playlist folder are now spotted for the next playlist too. No more marking a perfectly good track as failed just because it already lives somewhere on disk.",
                ]
            }
        ]
    },
    "2.4.4": {
        title: "What's New in v2.4.4",
        sections: [
            {
                heading: "Bug fixes",
                items: [
                    "Non-admin users were getting a 403 when hitting any 'Test connection' button in Settings, for Navidrome, Jellyfin, Lidarr, Apprise, and both cookie fields. Settings saved fine, but the test buttons were gated admin-only by mistake. Fixed.",
                ]
            }
        ]
    },
    "2.4.3": {
        title: "What's New in v2.4.3",
        sections: [
            {
                heading: "Auto-album routing for singles",
                items: [
                    "New opt-in setting: when enabled, singles with a MusicBrainz album match are automatically moved into Artist/Album/ after download, complete with track number tags. Falls back silently to Singles/Artist/ for new or unrecognised tracks — no errors, no fuss.",
                    "A second toggle, 'Route to Albums folder', sends matched tracks to Albums/Artist/Album/ instead of Singles/Artist/Album/. Handy if you want a clean Artist/Album/Track layout without touching your Singles folder.",
                    "Find both settings in Settings under Library. Both are off by default.",
                ]
            },
            {
                heading: "Bug fixes",
                items: [
                    "Monochrome downloads weren't triggering album routing at all. Tidal gives us the album title directly, so the routing now uses that instead of waiting for MusicBrainz. Fixed.",
                    "MusicBrainz was routinely picking radio compilations and promo discs as the canonical album — 'Promo Only Modern Rock Radio, December 2001' instead of the actual studio album. The release picker now scores options and strongly prefers studio albums by the actual artist, penalising Various Artists credits, compilations, and anything with 'Promo Only', 'Greatest Hits', or 'Best Of' in the title.",
                    "Re-download was picking the same bad result every time. It now excludes the failed video ID and searches for a fresh candidate.",
                    "MusicBrainz album and track number data was being skipped for most tracks — the lookup was only triggered when a year was missing, which is almost never. Fixed.",
                    "MP3Phoenix downloads were storing the artist as Unknown in the queue. The artist from the search result is now passed through correctly.",
                    "The auto-album routing toggle wasn't saving due to a missing field in the settings model. Fixed.",
                ]
            }
        ]
    },
    "2.4.2": {
        title: "What's New in v2.4.2",
        sections: [
            {
                heading: "Bug fixes",
                items: [
                    "Fresh installs were broken — the database schema was missing columns added in recent releases, causing immediate errors on first run. Upgraders were fine as their databases were patched automatically, but anyone starting fresh from v2.4.0 or v2.4.1 would hit a crash. Fixed.",
                    "Mid-track silence detection: ffmpeg now scans the first 60% of every downloaded track for suspicious gaps of 8+ seconds. This catches Content ID fraud uploads where someone pads a track with silence in the middle to avoid fingerprinting while still matching the expected duration. The first 15 seconds and the final 40% are ignored, so legitimate long intros and hidden tracks on album closers are left alone.",
                    "WebM remux safety: album-routed files that need remuxing are now verified before the original is deleted. A corrupt output no longer silently destroys the source.",
                    "MBID validation: invalid MusicBrainz IDs now fail fast with a clear error rather than quietly failing deep in a lookup.",
                    "MP3Phoenix downloads: size is checked after download — truncated files are deleted immediately rather than left as stubs.",
                ]
            }
        ]
    },
    "2.4.1": {
        title: "What's New in v2.4.1",
        sections: [
            {
                heading: "Bug fix",
                items: [
                    "ListenBrainz 'Created for You' playlists (Weekly Exploration, Weekly Jams, etc.) rotate to a new URL every Monday. MusicGrabber was storing the old UUID and refreshing a dead playlist forever. It now re-resolves the current week's URL automatically going forward.",
                    "Action required for existing users: delete your ListenBrainz watched playlist cards and re-add them using your ListenBrainz username. This lets MusicGrabber store the information it needs to auto-update the URL each week. Cards added before this update won't self-heal.",
                ]
            }
        ]
    },
    "2.4.0": {
        title: "What's New in v2.4.0",
        sections: [
            {
                heading: "Album Download tab",
                items: [
                    "There's now a dedicated Albums tab for downloading full albums intentionally. Search for an artist, pick an album, preview the tracklist, and download the lot in one go.",
                    "Files are saved as Albums/Artist/Album/Track rather than getting mixed in with Singles. No more manually sorting things after the fact.",
                    "Optionally generate an M3U playlist alongside the download.",
                    "Artist and album data comes from MusicBrainz, so you get proper metadata rather than YouTube's creative guesswork.",
                    "The Albums folder path is configurable in Settings, right next to the Singles and Playlists folders.",
                    "Only missing tracks are queued — if half the album is already there, only the gaps are downloaded. The precheck tells you upfront how many tracks exist and how many will be fetched.",
                ]
            },
            {
                heading: "Unified \"Add to...\" destination picker",
                items: [
                    "The separate \"Add to playlist\" and \"Add to album\" chips in search results have been replaced by a single \"Add to...\" button.",
                    "Choosing Album opens a two-level browser: pick an artist folder, then an album folder. MusicGrabber reads the .albuminfo sidecar written at download time to get the MusicBrainz context automatically.",
                    "If auto-matching can't place the track, a manual track picker appears so you can select the right slot yourself.",
                    "Folders without an .albuminfo sidecar still work — the track lands in the right folder, just without MusicBrainz metadata enrichment.",
                ]
            },
            {
                heading: "Album quality-of-life fixes",
                items: [
                    "TRACKTOTAL tags are now correct when only some tracks were missing. Previously a partial download (e.g. 3 of 12 tracks) would tag those files 3/3 instead of 3/12.",
                    "Album M3U files now include proper duration and title entries so players show correct metadata immediately, without waiting for a library scan.",
                    "Clicking Download Album twice in quick succession no longer queues everything twice and races to download the same files. The second click is a no-op if an import is already running for that album.",
                ]
            },
            {
                heading: "Watched Artists",
                items: [
                    "Follow an artist by MusicBrainz ID and new singles are downloaded automatically as they appear. Same controls as watched playlists: check interval, convert-to-FLAC, missing panel, track list.",
                    "Singles only — remixes, live versions, soundtracks, DJ mixes, and compilations are filtered out at the MusicBrainz level.",
                    "Tracks already on disk are recognised on first refresh, so you won't re-download things you already have.",
                ]
            },
            {
                heading: "Watched playlist matching improvements",
                items: [
                    "Scandinavian and other non-decomposable characters (Ø, ø, Ł, æ, ß, etc.) now normalise correctly. BYØRN was failing to match BYORN because NFKD can't decompose those letters.",
                    "Tracks namespaced under a mixtape or project in ALL CAPS (e.g. STONEHENGE - GEEKED UP) now strip the leading prefix before matching, so they resolve to the correct track.",
                ]
            },
            {
                heading: "Bug fixes",
                items: [
                    "Monochrome search results no longer show duplicate cards for the same recording at different quality tiers. The highest quality entry is kept and given a scoring boost; the duplicates are dropped.",
                    "Monochrome downloads that fail on all quality tiers now search across all enabled sources for the best alternative, rather than defaulting straight to YouTube.",
                    "Fixed a crash on /api/playlists when the Playlists directory was on a broken or stale mount. Returns an empty list now instead of a 500 error.",
                ]
            }
        ]
    },
    "2.3.5": {
        title: "What's New in v2.3.5",
        sections: [
            {
                heading: "Bug fixes",
                items: [
                    "Manual downloads no longer get rejected by the MusicBrainz duration check. If you picked a specific track from the search results, MusicGrabber now trusts your judgement. Automated downloads (watched playlists, bulk import) still reject duration mismatches.",
                    "The \"Add to playlist\" dropdown now refreshes every time you open it, rather than only on page load. Fixes cases where the initial load failed silently and left the list empty.",
                ]
            }
        ]
    },
    "2.3.4": {
        title: "What's New in v2.3.4",
        sections: [
            {
                heading: "Apple Music import",
                items: [
                    "Public Apple Music playlists and albums can now be imported and watched. Paste the URL in the Watch or bulk import box.",
                    "No browser or API key needed — Apple server-renders the full track list, so a plain HTTP fetch is all it takes.",
                    "Supports all regional storefronts. Private playlists and personal libraries aren't accessible (Apple won't let us in without a sign-in).",
                ]
            },
            {
                heading: "ALAC output format",
                items: [
                    "ALAC (Apple Lossless) is now a selectable audio format in Settings, alongside FLAC, Opus, and MP3.",
                    "Files are saved as .m4a. Lossless quality, great for modded iPods and Apple devices that don't speak FLAC.",
                ]
            },
            {
                heading: "Bug fixes",
                items: [
                    "Watched tracks in album-structured folders (e.g. from Monochrome) were being re-queued as missing on every refresh. MusicGrabber now checks the exact path it recorded at download time, so tracks outside the Singles folder are recognised correctly.",
                    "The download queue now shows \"Already in library\" instead of a raw file path for duplicate-skipped tracks. The full path is still there if you expand the job.",
                ]
            }
        ]
    },
    "2.3.3": {
        title: "What's New in v2.3.3",
        sections: [
            {
                heading: "Per-playlist source selection",
                items: [
                    "Each watched playlist now has a Sources row with toggleable chips — one per search source (YouTube, SoundCloud, MP3Phoenix, Monochrome).",
                    "By default all sources are active. Deselect any you don't want used for that playlist.",
                    "The Watch form also has the selector so you can set preferences on the way in.",
                    "If you pick a source that's globally disabled in Settings, MusicGrabber quietly falls back to all enabled sources instead of finding nothing.",
                    "Bug fix on playlist selector fixed.",
                ]
            }
        ]
    },
    "2.3.2": {
        title: "What's New in v2.3.2",
        sections: [
            {
                heading: "Lidarr duplicate check",
                items: [
                    "If you run Lidarr alongside MusicGrabber, you can now point MusicGrabber at your Lidarr instance (Settings → Lidarr, URL and API key). Before downloading anything, MusicGrabber will check whether the track already exists in your Lidarr library. If it does, the download is skipped",
                    "Real file paths are resolved via Lidarr's trackfile API, so watched playlist M3U entries include the correct path to the Lidarr-managed file - useful if you use Plex, Plexamp, or any other player that reads M3Us from a shared music directory",
                    "Runs as a final fallback after the local filesystem and Navidrome checks, so it doesn't slow things down when the track is already in your MusicGrabber library"
                ]
            },
            {
                heading: "Bulk import crash fix",
                items: [
                    "Bulk import was broken - pasting tracks into the box would appear to start but immediately fail silently. The background worker was being called with a user_id argument it doesn't accept; it reads that from the database itself. One redundant argument removed, bulk import works again"
                ]
            },
            {
                heading: "Watched playlist track order",
                items: [
                    "M3U files generated from watched playlists now follow the same track order as the source playlist. Previously they were ordered by when MusicGrabber first saw each track, which was effectively random for the initial sync",
                    "Positions are updated on every refresh, so if someone reorders the source playlist MusicGrabber will reflect that on the next check",
                    "Existing playlists get correct ordering automatically after their next refresh - no manual intervention needed"
                ]
            }
        ]
    },
    "2.3.1": {
        title: "What's New in v2.3.1",
        sections: [
            {
                heading: "Spotify music video fix",
                items: [
                    "Playlists containing music video entries no longer break the import. Spotify labels these with 'Music Video' as the artist, so both the embed scraper and the headless browser now detect this and attempt to salvage the real artist and title from the track name. If it can't be parsed cleanly, the entry is skipped rather than imported as garbage",
                    "Tested against a 2000+ track playlist with a mix of music videos and regular tracks - all came through correctly"
                ]
            },
            {
                heading: "Single-user mode restored after deleting accounts",
                items: [
                    "If you deleted all guest accounts and only your own admin account remained, MusicGrabber would incorrectly keep showing the login screen. Multi-user mode now only activates when there are two or more accounts - one account is treated the same as none"
                ]
            },
            {
                heading: "UI polish",
                items: [
                    "Preview audio fades in over 5 seconds rather than jumping to full volume",
                    "The Save Settings button, Ko-fi link, and Release Notes button now live in a fixed bar at the bottom of the settings page, always within reach no matter how far you've scrolled",
                    "Create a new playlist on the fly from the search results page - no need to go to the Watched tab first"
                ]
            },
            {
                heading: "Security",
                items: [
                    "Several admin-only endpoints (stats, job cleanup, check-all, blacklist) were missing access checks and were reachable by regular users. Fixed",
                    "Search token validation is now scoped to the user who issued the token"
                ]
            }
        ]
    },
    "2.3.0": {
        title: "What's New in v2.3.0 - g33kphr33k's Birthday Edition",
        sections: [
            {
                heading: "Important - database migration",
                warning: "This update modifies the database schema. MusicGrabber will run the migration automatically on first start and it is safe to run on an existing install - no data is lost. That said, take a backup of your /data/music_grabber.db before upgrading, just in case. Downgrading to v2.2.x after running the migration is not supported."
            },
            {
                heading: "Multi-user support",
                items: [
                    "Multiple people can now share a MusicGrabber instance without stepping on each other. Each user has their own download history, watched playlists, watched artists, and bulk imports",
                    "Per-user settings: each user configures their own music directory, Navidrome and Jellyfin credentials, and notification endpoints independently",
                    "Out of the box, everything works exactly as before - no login required until you create an account. Multi-user mode kicks in the moment you create your first user in Settings",
                    "Login is username and password, sessions last 30 days, and the old API key still works for scripts and integrations",
                    "Admin role manages global settings (audio format, slskd, YouTube cookies, Spotify settings) and can create, remove, and reset user accounts",
                    "YouTube cookies are now per-user, so one user's authenticated downloads don't collide with another's"
                ]
            },
            {
                heading: "Spotify private playlists",
                items: [
                    "Paste your Netscape-format cookies.txt from open.spotify.com into Settings to unlock private playlists, saved albums, and personal library playlists - anything that requires a Spotify login",
                    "Works per-user, same pattern as YouTube cookies. If the cookies expire mid-use, an amber banner appears in Settings and a clear message is shown when you try to fetch a playlist",
                    "See the README for step-by-step instructions on exporting cookies from your browser"
                ]
            },
            {
                heading: "Search source toggles",
                items: [
                    "Each search source (YouTube, MP3Phoenix, SoundCloud, Monochrome) can now be individually enabled or disabled in Settings",
                    "Applies everywhere: search results, watched playlist matching, and bulk imports all skip disabled sources",
                    "Useful if a source is slow, unreliable, or just not relevant to what you're grabbing",
                    "Bug fix included: the toggle was silently broken on first release - disabled sources were still appearing in results. Now fixed"
                ]
            },
            {
                heading: "Search quality improvements",
                items: [
                    "Monochrome lossless results now reliably beat 320 kbps MP3 results on equal relevance. The scoring gap between lossless and lossy was too narrow and could be flipped by a small duration scoring nudge",
                    "A single source can no longer flood the results. Previously, MP3Phoenix could contribute ten near-identical tracks and push a Monochrome lossless result off the page entirely. Each source is now capped at four results in the merged pool before quality scoring decides the final order",
                    "Tidal variant tracks (e.g. 'Hey Man Nice Shot (½ oz)') no longer float to the top just because they're lossless. Unknown parenthetical suffixes are now treated as a variant signal and penalised accordingly. Standard suffixes like Remastered and Deluxe Edition are unaffected",
                    "MusicBrainz duration matching is tighter. The old tolerances were calibrated for long tracks; most songs are around 3 minutes, where the previous 25% band was 45 seconds of slop. Bands are now much stricter"
                ]
            },
            {
                heading: "Security improvements",
                items: [
                    "Login brute-force protection: accounts lock after repeated failed attempts",
                    "File download links are now short-lived single-use tokens - the session token no longer appears in any URL",
                    "HTTPS-only mode available via HTTPS_ONLY=true environment variable",
                    "API key in query params is now opt-in (ALLOW_API_KEY_QUERY_PARAM=true) to prevent credentials leaking into proxy logs",
                    "XSS audit complete - all API data rendered into the page now goes through escapeHtml()"
                ]
            },
            {
                heading: "Bug fixes",
                items: [
                    "Monochrome tracks now respect the audio format setting - if you asked for MP3 or Opus, you'll actually get it instead of quietly keeping FLAC",
                    "Docker healthcheck now respects the LISTEN_PORT environment variable instead of always probing port 8080"
                ]
            }
        ]
    },
    "2.2.7": {
        title: "What's New in v2.2.7",
        sections: [
            {
                heading: "What is Music Grabber?",
                body: "Music Grabber is a self-hosted tool for grabbing individual tracks. You heard something on the radio, in a film, at a party - you want that song. It searches YouTube, SoundCloud, and Monochrome (Tidal lossless) in parallel, downloads the best quality version, and drops it neatly into your library. It is not Lidarr. It does not manage your collection. It just gets the track. This is a personal pet project, actively developed but rough around the edges. If something breaks, please check the issue tracker before raising a duplicate."
            },
            {
                heading: "Bug fixes in this version",
                items: [
                    "Watched track matching no longer falls over when YouTube uses fullwidth Unicode punctuation (｜ instead of |, － instead of -) in video titles. Titles that looked identical in the logs but weren't are now correctly matched",
                    "ListenBrainz 'Created for You' playlists now actually populate with tracks when added as a watched playlist. The listing API returns empty track arrays - each playlist has to be fetched individually to get its contents, which we weren't doing"
                ]
            }
        ]
    },
    "2.2.6": {
        title: "What's New in v2.2.6",
        sections: [
            {
                heading: "What is Music Grabber?",
                body: "Music Grabber is a self-hosted tool for grabbing individual tracks. You heard something on the radio, in a film, at a party - you want that song. It searches YouTube, SoundCloud, and Monochrome (Tidal lossless) in parallel, downloads the best quality version, and drops it neatly into your library. It is not Lidarr. It does not manage your collection. It just gets the track. This is a personal pet project, actively developed but rough around the edges. If something breaks, please check the issue tracker before raising a duplicate."
            },
            {
                heading: "Watched Artists - new feature",
                items: [
                    "Follow an artist on MusicBrainz and new singles are downloaded automatically as they appear. Search by name, pick from up to five candidates, set a from-date (defaults to today so your back-catalogue stays put), and MusicGrabber does the rest",
                    "Singles only - remixes, live versions, soundtracks, DJ mixes, and compilations are filtered out at the MusicBrainz level so you don't get flooded with every variant ever released",
                    "Tracks already on disk are recognised immediately on first refresh - no duplicate downloads",
                    "Same controls as watched playlists: check interval, convert-to-FLAC, pause/resume, missing panel, track list with download buttons"
                ]
            },
            {
                heading: "Other new bits",
                items: [
                    "Downloadable to Device: new section at the bottom of the Queue tab lists every completed download newest-first with a Save button to pull it straight to your browser - handy for grabbing tracks to a phone or laptop",
                    "Save to device buttons on watched playlist and artist track lists",
                    "Adding a watched playlist now shows a spinner and elapsed timer instead of sitting silently with a greyed-out button",
                    "YouTube cookies no longer cause 'Requested format is not available' - the format selector now skips Premium-only streams that your account can't actually download"
                ]
            },
            {
                heading: "Bug fixes",
                items: [
                    "Watched track matching is smarter: Spotify dash-suffixes, bracketed equivalents, and pipe-separated session tags (Tugboats | OurVinyl Sessions) all resolve to the same track instead of triggering a mismatch delete and re-download",
                    "Tracks in playlist folders are no longer falsely marked as deleted by the file reconciler",
                    "Format errors no longer trigger the bot-block backoff sleep - that sleep is for genuine 403s, not manifest mismatches"
                ]
            }
        ]
    },
    "2.2.5": {
        title: "What's New in v2.2.5",
        sections: [
            {
                heading: "What is Music Grabber?",
                body: "Music Grabber is a self-hosted tool for grabbing individual tracks. You heard something on the radio, in a film, at a party - you want that song. It searches YouTube, SoundCloud, and Monochrome (Tidal lossless) in parallel, downloads the best quality version, and drops it neatly into your library. It is not Lidarr. It does not manage your collection. It just gets the track. This is a personal pet project, actively developed but rough around the edges. If something breaks, please check the issue tracker before raising a duplicate."
            },
            {
                heading: "Short and sweet",
                items: [
                    "MP3Phoenix is now fully wired in, with much more reliable queueing and fallback behavior",
                    "Watched playlist refresh status is clearer and the timer now starts fresh when you click Refresh",
                    "Manual file deletes/renames now reconcile automatically in the background so queue and watched states stay in sync"
                ]
            }
        ]
    },
    "2.2.4": {
        title: "What's New in v2.2.4",
        sections: [
            {
                heading: "What is Music Grabber?",
                body: "Music Grabber is a self-hosted tool for grabbing individual tracks. You heard something on the radio, in a film, at a party - you want that song. It searches YouTube, SoundCloud, and Monochrome (Tidal lossless) in parallel, downloads the best quality version, and drops it neatly into your library. It is not Lidarr. It does not manage your collection. It just gets the track. This is a personal pet project, actively developed but rough around the edges. If something breaks, please check the issue tracker before raising a duplicate."
            },
            {
                heading: "Bug fixes in this version",
                items: [
                    "Watched playlist cards now warn you when Navidrome has stale entries pointing to files that no longer exist on disk. If the M3U looks shorter than expected, an amber warning banner on the card will tell you exactly how many dead entries were found and what to do about it (Navidrome > Settings > Missing Files > Remove from Database)"
                ]
            }
        ]
    },
    "2.2.3": {
        title: "What's New in v2.2.3",
        sections: [
            {
                heading: "What is Music Grabber?",
                body: "Music Grabber is a self-hosted tool for grabbing individual tracks. You heard something on the radio, in a film, at a party - you want that song. It searches YouTube, SoundCloud, and Monochrome (Tidal lossless) in parallel, downloads the best quality version, and drops it neatly into your library. It is not Lidarr. It does not manage your collection. It just gets the track. This is a personal pet project, actively developed but rough around the edges. If something breaks, please check the issue tracker before raising a duplicate."
            },
            {
                heading: "Bug fixes in this version",
                items: [
                    "Watched playlist imports no longer get confused by remix and collaborator formatting differences between Spotify and YouTube. Tracks like 'Only Human - MPH Remix' on Spotify now correctly match 'Only Human (MPH Remix)' from YouTube, and artist comparisons handle order swaps, remixer additions, and separator differences (comma vs feat. vs x vs &)",
                    "MusicBrainz now checks the expected track duration after download. If the file is more than 10% shorter or longer than MusicBrainz expects, it's rejected as the wrong version. Catches wrong edits that pass all other integrity checks",
                    "Karaoke, nightcore, sped-up, slowed, 8D audio, and bass-boosted versions are now scored so low they cannot win a search result regardless of other factors. Nobody asked for the karaoke version",
                    "Bootleg edits, flips, refixes, reworks, and mashups are penalised heavily unless the search query specifically names them",
                    "Copyright-filtered uploads (pitch-shifted or muted to dodge Content ID) are disqualified. The audio is useless anyway",
                    "YouTube titles with leading label prefixes like '[UKF release] S.P.Y - Sweet Sound' now correctly parse to S.P.Y as the artist, instead of absorbing the prefix into the artist name",
                    "Watched playlist refresh no longer re-queues tracks that already exist in Navidrome but not in MusicGrabber's own folders",
                    "Audio integrity check now also catches preview segments baked with a large start_time offset - these sounded fine in ffprobe but played from the middle of the song"
                ]
            }
        ]
    },
    "2.2.2": {
        title: "What's New in v2.2.2",
        sections: [
            {
                heading: "What is Music Grabber?",
                body: "Music Grabber is a self-hosted tool for grabbing individual tracks. You heard something on the radio, in a film, at a party - you want that song. It searches YouTube, SoundCloud, and Monochrome (Tidal lossless) in parallel, downloads the best quality version, and drops it neatly into your library. It is not Lidarr. It does not manage your collection. It just gets the track. This is a personal pet project, actively developed but rough around the edges. If something breaks, please check the issue tracker before raising a duplicate."
            },
            {
                heading: "Bug fixes in this version",
                items: [
                    "Monochrome downloads now support the new DASH MPD manifest format, in addition to the old JSON format. HI_RES_LOSSLESS was silently failing for many tracks because the response format changed; it now works again",
                    "Monochrome quality ladder now tries HI_RES_LOSSLESS, then LOSSLESS, then HIGH, then LOW before falling back to YouTube, so you reliably get the best available tier",
                    "Downloads are now validated with ffprobe after completing. Truncated or corrupt files are retried, and if they keep failing the candidate is blacklisted so you don't get the same bad file twice",
                    "Soulseek results now factor in actual search relevance, not just format quality. A FLAC of the wrong song no longer beats an MP3 of the right one",
                    "Watched playlist refresh now checks whether local files actually exist before trusting the database. Manually deleted tracks are automatically re-queued on the next refresh",
                    "Watched playlists no longer mark the wrong track as downloaded. If the downloaded metadata doesn't match what was expected, the job is flagged as completed with errors instead of silently logged as done",
                    "Navidrome Test Connection now auto-enables real file paths for MusicGrabber players. Without this, M3U playlist entries could contain synthetic Navidrome paths instead of real ones",
                    "Stats tab no longer crashes when the Playlists folder is disabled",
                    "Service bind address and port are now configurable via LISTEN_ADDR and LISTEN_PORT environment variables, for IPv6 or non-standard setups"
                ]
            }
        ]
    },
    "2.2.1": {
        title: "What's New in v2.2.1",
        sections: [
            {
                heading: "What is Music Grabber?",
                body: "Music Grabber is a self-hosted tool for grabbing individual tracks. You heard something on the radio, in a film, at a party - you want that song. It searches YouTube, SoundCloud, and Monochrome (Tidal lossless) in parallel, downloads the best quality version, and drops it neatly into your library. It is not Lidarr. It does not manage your collection. It just gets the track. This is a personal pet project, actively developed but rough around the edges. If something breaks, please check the issue tracker before raising a duplicate."
            },
            {
                heading: "Bug fixes in this version",
                items: [
                    "Monochrome no longer downloads the wrong artist when a track isn't on Tidal - a lossless score bonus was overriding artist matching, so 'Venjent - Who Are Ya' could come back as 'Wolf Parade - Who Are Ya'. Fixed",
                    "Queue 'Already exists' messages now show Artist/filename instead of a bare track number like 01-01 - Title.flac",
                    "YouTube cookies causing 'Requested format is not available' now trigger the cookieless retry, same as 403 errors",
                    "Navidrome duplicate check now matches artists with curly apostrophes (Guns N\u2019 Roses vs Guns N\u0027 Roses) and handles compilation tracks where albumArtist is Various Artists"
                ]
            }
        ]
    },
    "2.2.0": {
        title: "What's New in v2.2.0",
        sections: [
            {
                heading: "What is Music Grabber?",
                body: "Music Grabber is a self-hosted tool for grabbing individual tracks. You heard something on the radio, in a film, at a party - you want that song. It searches YouTube, SoundCloud, and Monochrome (Tidal lossless) in parallel, downloads the best quality version, and drops it neatly into your library. It is not Lidarr. It does not manage your collection. It just gets the track. This is a personal pet project, actively developed but rough around the edges. If something breaks, please check the issue tracker before raising a duplicate."
            },
            {
                heading: "New in this version",
                items: [
                    "Watched playlists now show a full track list with per-track status and a Replace button for bad downloads",
                    "Playlist routing - route any download directly into a watched playlist or .m3u file from the search results",
                    "Monochrome 403 fallback now actually works (was broken - YouTube results were being crowded out by Monochrome's own score bonuses)",
                    "Missing track Retry and Search buttons on watched playlist cards",
                    "Apprise notification support - one URL covers Gotify, ntfy, Discord, Pushover, Slack, and about 50 others",
                    "Navidrome pre-download duplicate check - skips re-downloading tracks already in your library",
                    "ListenBrainz 'Created for You' playlist watching - Weekly Jams, Exploration Playlist, etc.",
                    "Frontend split into index.html + app.js - same behaviour, easier to navigate"
                ]
            }
        ]
    }
};
