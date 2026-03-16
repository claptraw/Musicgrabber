// =============================================================================
// Release Notes
// =============================================================================
// One entry per released version. Add new versions at the top.
// Shown once on first load after an update, and accessible via the
// "Release Notes" button in Settings. Keep it human-readable, not a
// changelog dump.

const RELEASE_NOTES = {
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
