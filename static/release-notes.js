// =============================================================================
// Release Notes
// =============================================================================
// One entry per released version. Add new versions at the top.
// Shown once on first load after an update, and accessible via the
// "Release Notes" button in Settings. Keep it human-readable, not a
// changelog dump.

const RELEASE_NOTES = {
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
