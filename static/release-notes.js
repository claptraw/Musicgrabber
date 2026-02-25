// =============================================================================
// Release Notes
// =============================================================================
// One entry per released version. Add new versions at the top.
// Shown once on first load after an update, and accessible via the
// "Release Notes" button in Settings. Keep it human-readable, not a
// changelog dump.

const RELEASE_NOTES = {
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
