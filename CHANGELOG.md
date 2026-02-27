# Changelog

## v2.2.3 (unreleased)

### Fixed
- **Watched imports could pick a short wrong-artist clip over the correct full-length track**: Three changes combined to close this gap. First, the duration penalty for sub-30s clips in search scoring was doubled from -40 to -80, and a new -40 tier added for 30-60s clips, so clips can no longer be rescued by official-channel bonuses. Second, AcoustID fingerprinting is now skipped for any downloaded file shorter than 30 seconds; a 21-second clip can technically fingerprint as the correct song, but tagging it with that metadata would be wrong. Third, watched playlist imports now walk the ranked candidate list and prefer the first result whose title or channel actually contains the expected artist, rather than blindly picking the top scorer regardless of artist
- **Watched playlist refresh re-queued tracks that existed in Navidrome but not in MusicGrabber's own folders**: The file-existence check on refresh only looked at MusicGrabber's local library. Tracks that lived elsewhere on the same filesystem (pre-existing library, different path) were not found, so `downloaded_at` was cleared and the track got re-queued on every refresh. The check now also consults Navidrome as a final fallback, accepting absolute paths only, so tracks Navidrome can vouch for with a real path are left alone
- **YouTube titles with leading label/channel prefixes were parsed with the wrong artist**: Uploads tagged like `[UKF release] S.P.Y - Sweet Sound` or `(Monstercat) Razihel - Love U` had the prefix absorbed into the artist name, so `S.P.Y` never matched the expected artist and the track was rejected. `extract_artist_title()` now tries stripping a leading `[...]` or `(...)` block first, and only uses the stripped version when the remainder starts cleanly with a word character. This means `[UKF release] S.P.Y - Sweet Sound` correctly resolves to `S.P.Y / Sweet Sound`, while `[IVY], A Little Sound - Can't Love Me` is left intact (stripping `[IVY]` leaves a comma-prefixed remainder, which fails the guard)
- **Watched playlist M3U silently missing tracks when Navidrome real path mode is off**: The M3U builder was correctly skipping Navidrome's synthetic relative paths (unusable as M3U entries), but doing so in silence, making it look like tracks simply failed to download. The rebuild logic now distinguishes three outcomes: real path found and written, Navidrome sentinel returned (track exists but path is fake), and genuinely unresolved. Synthetic-path drops now emit a clear WARNING log naming every affected track and telling the user exactly what to fix, rather than leaving them staring at a shorter M3U wondering why
- **Playwright browser unavailable when running with PUID/PGID**: The Docker build installs Chromium as root into `/root/.cache/ms-playwright`. When `PUID`/`PGID` is set the app runs as a non-root user whose home resolves differently, so Playwright's binary lookup fails and Spotify browser fallback silently falls back to the 100-track embed limit. Fixed by setting `PLAYWRIGHT_BROWSERS_PATH=/ms-playwright` in the image so the install and runtime always agree on where the browser lives, regardless of which user is driving
- **Preview/sample audio could be saved as a full track**: Some CDNs serve preview segments with a large `start_time` baked into the container (for example 120 seconds into the actual recording). These files sounded fine to `ffprobe` duration checks but played as an ear-splitting sample from the middle of a song. The integrity validator now also checks `start_time`; anything above 1 second is rejected as a likely preview segment, triggering the usual retry and blacklist flow
- **Large Spotify playlists could stop short of full count during browser fallback**: The headless Spotify extractor now reads the playlist's reported `song_count`, performs fast visibility checks for cookie banners (skipping hidden selectors instead of burning 30s click timeouts), tracks rows by numeric playlist index so virtualised scrolling doesn't lose ordering, and uses a time-based progress stall window instead of a tiny fixed stale-loop cap. Browser subprocess timeout is also scaled by expected playlist size (capped) so slower hosts are less likely to be cut off mid-scroll. These limits are now configurable via Settings (or env overrides): `spotify_browser_timeout_seconds` / `SPOTIFY_BROWSER_TIMEOUT_SECONDS` and `spotify_browser_stall_seconds` / `SPOTIFY_BROWSER_STALL_SECONDS`. Validation against `https://open.spotify.com/playlist/5VAiK705RGhocNFGWW6iTT` now returns the full 1360 tracks instead of truncating at 1331

- **Watched playlist M3U entries silently rejected for remix/collaborator formatting differences**: The mismatch check compared Spotify's metadata format (remixer in artist field, remix in title as a dash-suffix like `Track - Remixer Remix`) against YouTube's format (only primary artists tagged, remix in parentheses which get stripped). This caused ~36 of 150 tracks in a real playlist run to be excluded from the M3U as "mismatches" despite being the correct download. The normalisation logic now: strips inline `feat./ft./featuring` clauses from titles (not just bracketed ones), strips trailing `official` and `visualizer` keywords, treats a Spotify-side remix dash-suffix as a prefix match when the got-title is a clean prefix and the extra words end in a remix indicator word, and uses word-set subset comparison for artists (handling order swaps, remixer additions, and separator differences like comma vs `feat.` vs `x` vs `&`)
- **Wrong tracks downloaded from YouTube could pass integrity checks undetected**: A track with a plausible duration but completely wrong content (different artist, wrong version) could sail through the corruption and start-time checks with no complaint. MusicBrainz now also returns the expected track duration (`recording.length`) alongside metadata, and a post-download duration check rejects files that fall outside 10% of the expected length. This catches the Civil War / short-edit scenario without needing a full fingerprint
- **Bootleg edits, karaoke versions, and copyright-butchered uploads could rank above originals**: YouTube search scoring now applies hard penalties severe enough to make these unselectable in practice. Karaoke, nightcore, sped-up, slowed, 8D audio, and bass-boosted versions get -200 (they cannot overcome any combination of official-channel and title-match bonuses). Bootleg, flip, refix, rework, and mashup edits get -100 unless the query itself names them. Copyright-filtered uploads with pitch-shifted or muted audio get -200. Nobody asked for the karaoke version

### Changed
- **Spotify advanced tuning now exposed in UI**: Settings tab now includes `Browser Timeout (seconds)` and `No-progress Stall Window (seconds)` under **Spotify (Advanced)**, with env-variable override support as documented above

---

## v2.2.2 (2026-02-25)

### Fixed
- **Settings buttons layout**: The "Save Settings", "Buy me a coffee", and "Release Notes" buttons refused to sit in a tidy equal-width row because an invisible save-result div was lurking inside the flex container as a fourth child, silently stealing space. Moved it out. Buttons are now equal size and properly left, centred, and right aligned as the coding Gods intended
- **Navidrome dupe check writes broken paths into M3U playlists**: Navidrome's Subsonic API returns a synthetic relative path by default (`Artist/Album/01-Track.mp3`), not the real filesystem path. MusicGrabber was faithfully writing this nonsense straight into playlist files. The duplicate check now auto-detects real vs synthetic paths: if Navidrome returns an absolute path (starting with `/`), it is used for M3U entries as intended; if synthetic, the track is still flagged as a duplicate to avoid re-downloading, but no path is written. "Test Connection" in Settings now checks whether real paths are enabled and automatically flips the setting for all MusicGrabber players via Navidrome's native API, so for most users, hitting Test Connection once is all it takes. For a permanent fix across new players, add `ND_SUBSONIC_DEFAULTREPORTREALPATH=true` to your Navidrome docker-compose environment
- **Tracks already in Navidrome's library missing from rebuilt M3U**: When rebuilding a watched playlist M3U, tracks that existed in Navidrome but not in MusicGrabber's own download folders were being dropped. Navidrome's absolute path is valid on Navidrome's filesystem, but `.exists()` returns false from inside MusicGrabber's container. Now trusts absolute paths from Navidrome directly (the M3U consumer shares Navidrome's filesystem view), while still filtering out the synthetic sentinel paths used when real path mode is off
- **Stats tab crash when Playlists folder is disabled**: `/api/stats` scanned Singles and Playlists paths and assumed both were always real paths. If `playlists_subdir` was unset/empty (the default), `get_playlists_dir()` returned `None` and stats crashed with `'NoneType' object has no attribute 'exists'`. The scan now skips disabled (`None`) directories
- **Corrupted downloads were treated as successful**: Downloaded audio is now validated with `ffprobe` before we call it done. Files with no readable audio stream or zero duration are rejected, because a zero-second banger is still not a banger
- **Single-track retry flow stopped at first bad file**: Single downloads now do one integrity re-download, then blacklist the failed candidate and try a fresh alternate search result before giving up
- **Playlist and Soulseek downloads could keep bad files**: Playlist tracks now re-download once on integrity failure, then fail cleanly and blacklist the bad candidate. Soulseek now validates both the raw transfer and the final converted file
- **Soulseek scoring favoured file format over relevance**: Soulseek search results now combine `score_search_result` relevance with quality bonuses, plus slot and speed tweaks, so exact matches are less likely to lose to random FLAC noise
- **Watched playlist could mark wrong tracks as downloaded**: A watched track linked by `job_id` could be marked downloaded even when the resolved job metadata was a different song (`Linkin Park - Numb` ended up as `Pink Floyd - Comfortably Numb`, which is certainly one way to ruin playlist trust). Watched download marking now validates expected vs actual artist/title before setting `downloaded_at`; mismatches are flagged as `completed_with_errors` instead
- **Deleted files were still treated as downloaded on refresh**: If tracks were deleted from disk manually, watched playlist refresh trusted stale `downloaded_at` and skipped re-queueing. Refresh now verifies local file existence for downloaded rows and automatically flips missing files back to pending so they are re-imported
- **Playlist-routed tracks could be moved into Singles by artist normalisation**: MusicBrainz artist normalisation could relocate files from `Playlists/<Name>/` into `Singles/<Artist>/`, which broke playlist locality and made M3U entries look haunted. Playlist-owned files now stay in the playlist folder even when artist metadata is normalised
- **Navidrome duplicate sentinel paths could block playlist downloads**: In some cases a synthetic Navidrome path was treated as a valid duplicate for playlist routing, so tracks were marked completed without a usable playlist path. Playlist routing now ignores unusable sentinel matches and proceeds with a real download when needed
- **Service bind address and port were hardcoded**: Startup was pinned to `0.0.0.0:8080`, which made IPv6-only or custom bind setups awkward. Added `LISTEN_ADDR` and `LISTEN_PORT` env vars to entrypoint and local `__main__` startup path, defaulting to `0.0.0.0:8080`
- **Watched-playlist failures were too vague to debug**: Generic `Failed to get video info` errors now include provider-specific context (YouTube vs SoundCloud) and a short reason (403 block, age-gated, extraction failure, timeout, etc.) without dumping raw yt-dlp internals. Monochrome all-tier-403 fallback now runs a cookie-aware YouTube search with one explicit cookieless retry and clearer failure reasons, so fallback behaviour is more consistent under stale-cookie gremlins
- **Queue badge could still show `MO` after a successful YouTube fallback**: When Monochrome all-tier-403 fallback picked a YouTube result, the job source field was not updated, so the queue card looked like it stayed on Monochrome. Fallback handoff now updates `source`, `video_id`, and `source_url` to the selected YouTube candidate before download continues, so the badge and metadata match reality
- **Monochrome fallback ladder skipped available quality tiers**: Monochrome downloads now try `HI_RES_LOSSLESS -> LOSSLESS -> HIGH -> LOW` in order before triggering YouTube fallback, instead of bailing after fewer tiers
- **Monochrome manifest format changed for some tracks**: Some `/track` responses now return DASH MPD XML (`application/dash+xml`) instead of the older JSON `urls` manifest. Downloads now support both formats, so tracks like `Tate McRae - greedy` continue to download correctly
- **Promo suffixes could trigger false watched-track mismatches**: Titles with session/branding tails like `| A COLORS SHOW` now normalise to the base song title for duplicate and watched-match checks, so existing files are recognised without spurious mismatch warnings

---

## v2.2.1 (2026-02-24)

### Fixed
- **"Already exists" shows track number instead of artist name**: Navidrome returns paths like `Artist/Album/01-01 - Title.flac`. Showing just the filename gave useless output like `Already exists: 01-01 - Song Title.flac`. Queue messages now show `Artist/filename.flac` so it's immediately clear whose track it is
- **Monochrome downloads wrong artist when track not on Tidal**: When searching for "Venjent - Who Are Ya", Monochrome would return "Wolf Parade - Who Are Ya" and the +100 lossless quality bonus meant it ranked above every correct YouTube result. Monochrome results now receive a -150 penalty when the result artist clearly doesn't match the query artist, which is more than enough to sink a wrong lossless match below a correct YouTube result

---

## v2.2.0 (2026-02-24)

### Added
- **Playlist routing on search results**: A "Add to playlist..." selector appears below the search bar. Pick any watched playlist or existing `.m3u` file from your Playlists directory and downloads will land there instead of Singles. Watched playlists carry a small warning that they'll be overwritten on next sync (because they will)
- **Missing tracks: Retry and Search**: Each track in the "Missing" panel now has two buttons. "Retry" kicks off an automatic search-and-download back into the watched playlist, same as the original import but forced. "Search" pre-fills the search bar with the track name, switches to the Results tab, and pre-selects the playlist in the selector so whatever you pick routes back correctly
- **Apprise notifications**: One URL to cover Gotify, ntfy, Discord, Pushover, Slack, and about 50 other services. Set an Apprise URL in Settings and you're done. Test button included so you can confirm it's working before relying on it
- **Navidrome pre-download duplicate check**: Before downloading, MusicGrabber queries Navidrome's Subsonic API to see if the track already exists there. If found, the existing file path is used directly, handy for routing into a playlist without re-downloading. Enabled by default when Navidrome is configured; can be turned off with `NAVIDROME_DUPE_CHECK=false`
- **Watched playlist full track list**: Each watched playlist card now has a "Tracks" button that expands a full panel showing every track and its status: Downloaded (green), Failed (red), Queued/Downloading (grey), or Removed upstream (mirror mode). Quicker than squinting at counts
- **Replace bad downloads**: Downloaded tracks in the track list panel have a "Replace" button. Clicking it marks the track as missing, updates the M3U, and pre-fills the search bar so whatever you pick next routes straight back into the playlist. If the file lives in the playlist folder it's deleted; if it was borrowed from your library (e.g. an existing Singles track pulled in by the dupe check) the file is left alone and only removed from the playlist. For when Monochrome decided a piano cover was close enough
- **Monochrome 403 automatic YouTube fallback**: When Monochrome returns 403 on all available quality tiers (LOSSLESS and HIGH), MusicGrabber now automatically searches YouTube and downloads the best matching result rather than failing outright. The job continues under the same ID so queue tracking, notifications, and playlist routing all work as normal
- **ListenBrainz "Created for You" playlist watching**: Enter your ListenBrainz username (or profile URL) in the Watched Playlists tab and MusicGrabber will import each of your "Created for You" playlists (Weekly Jams, Exploration Playlist, etc.) as its own watched playlist. Playlists default to mirror mode and weekly refresh to match ListenBrainz's regeneration schedule. No API key or account required, just your username
- **Release notes modal**: Shows once on first load after an update, summarising what changed and what the app is for. Also accessible any time via the "Release Notes" button in Settings

### Changed
- **Frontend split into HTML + JS**: `index.html` has been split into `index.html` (pure HTML structure) and `app.js` (all JavaScript). Same behaviour, much less of a beast to navigate
- **Favicon used as logo**: The header logo is now the actual favicon image rather than a green gradient square with text in it. Consistent branding, zero extra assets
- **Font Awesome icons throughout**: Replaced every emoji in the UI (empty states, platform badges, theme toggle, Ko-fi button, Soulseek placeholder) with Font Awesome 6 icons. Looks intentional now rather than a Discord server from 2019
- **Emoji purge**: Removed all emoji from code, comments, and user-facing strings. Unicode characters are used where a glyph is needed (FA icons via webfont); nothing is left to the mercy of the OS emoji renderer
- **AcoustID API key now configurable**: The AcoustID key used for audio fingerprinting can now be set in Settings > General or via the `ACOUSTID_API_KEY` env var. A shared built-in key is included so fingerprinting works out of the box, but it may hit rate limits if enough people use it. Register a free personal key at [acoustid.org](https://acoustid.org/login) if it stops working
- **Organise by Artist setting now shows both path formats**: The hint text under the toggle now shows exactly what the file path looks like in both modes, so it's obvious which way round it is

### Fixed
- **Queue tab stops refreshing**: The queue used a `setTimeout` chain that would die silently on any network error, leaving the tab frozen until you switched away and back. Replaced with a proper `setInterval` managed by tab focus, starts when you open the tab and stops when you leave, ticking regardless of errors or whether there are active jobs
- **Monochrome YouTube fallback always fails**: The fallback called `search_all(query, limit=5)` which merges all sources by quality score. Monochrome's lossless score bonuses (+100/+120) meant the top 5 results were always Monochrome tracks, leaving zero YouTube results to fall back to. Now calls `search_youtube()` directly, bypassing the multi-source scoring entirely
- **Monochrome downloads crash on playlist routing**: `_append_to_physical_m3u` was called with `audio_file` (undefined in the Monochrome path) instead of `output_path`. Any Monochrome download with a playlist selected would fail with `NameError: name 'audio_file' is not defined`
- **"Add to playlist" shown before search**: The playlist selector row was visible on page load even before any search had been performed. It now stays hidden until a search returns results
- **Cover versions beating originals in search results**: Album title is now passed to the search scorer. Albums with "Piano Covers", "Tribute", "Karaoke", or similar in the name now correctly receive the cover penalty, preventing a piano cover album from Monochrome from ranking above the original recording
- **Navidrome dupe false positives on cover albums**: The Navidrome duplicate check now inspects the `albumArtist` field. If the album artist doesn't match the expected artist, the match is rejected. Catches covers albums where the track `artist` tag is the original artist but `albumArtist` gives the game away
- **Settings links now match app accent colour**: Inline help links in Settings no longer render browser-default blue. Anchor styling is now unified to the app's green accent for consistent theming
- **Settings helper text readability on mobile**: Small helper copy now scales up on phones with improved line-height, making guidance text legible without zooming
- **Mobile Settings button/input overlap**: Clear/Show buttons next to fields in Settings could overflow and overlap card boundaries on narrow screens. Input action rows now stack vertically on mobile and keep all controls within the card width
- **Cookie test leaks raw yt-dlp stderr**: The final fallthrough case of the cookie test endpoint returned raw subprocess error output to the client, contradicting a comment in the same function. Now returns a generic message; detail stays server-side in logs
- **Navidrome-matched tracks not added to playlist**: When a duplicate was found via Navidrome rather than the local filesystem, the M3U append was skipped because the path wasn't accessible from the MusicGrabber container. The path string is all that's needed to write an M3U entry, it doesn't need to be locally mounted. Both the per-download append and the full M3U rebuild now use the Navidrome path regardless of local accessibility
- **Watched playlist M3U missing pre-existing library tracks**: On first watch, tracks already in your library were found by the duplicate check and skipped from downloading, but `rebuild_watched_playlist_m3u` only looked in the playlist folder. Tracks borrowed from Singles (or found only in Navidrome) were silently dropped from the M3U. The rebuild now falls back through local duplicate check then Navidrome for any track not found in the playlist folder
- **Inline onclick handlers used HTML-escape in JS string context**: `escapeHtml()` was used to interpolate playlist names, URLs, artist and title into `onclick="..."` attributes. HTML-escaping is not sufficient for single-quoted JS string literals. All inline handler interpolations now use `escapeAttr()`, which was already defined in the codebase but never called
- **Navidrome duplicate check missing tracks with curly apostrophes**: Artist names containing apostrophes (Guns N' Roses, Livin' On A Prayer, etc.) would fail to match because Spotify sends straight ASCII apostrophes and Navidrome stores the curly Unicode variant. All apostrophe variants are now normalised before comparison, so the match goes through regardless of which flavour either side chose on that particular Tuesday
- **Navidrome duplicate check too strict on artist field**: Artist and album artist were both required to match the search artist. Tracks on compilations have `albumArtist = Various Artists`, so they would never match even when the track artist was correct. Either field matching is now sufficient
- **YouTube cookies causing "Requested format is not available"**: Cookies from premium or broken sessions can cause YouTube to return a different format manifest where `bestaudio/best` finds nothing. The error was not recognised as a cookie-related failure, so the cookieless retry never fired and the download just died. Now triggers the same cookieless retry path as 403 errors

---

## v2.1.2 (2026-02-23)

### Added
- **Watched playlist sync mode**: Each watched playlist now has a Sync setting — Append (default, existing behaviour: M3U grows as new tracks arrive) or Mirror (M3U stays in sync with the upstream playlist; tracks removed from the source drop out of the M3U on next refresh). Audio files are never deleted either way — only the M3U changes
- **Missing tracks view**: Each watched playlist card now has a "Missing" button that shows tracks which failed to download (never got a `downloaded_at`, job failed or was never started). Click again to dismiss
- **M3U updated per-track**: Watched playlist M3U files now update immediately each time a track finishes downloading, rather than waiting for the next full refresh cycle. It grows as downloads complete
- **MP3 output format**: Settings now offers FLAC | Opus | MP3 as the audio format picker. MP3 uses LAME VBR ~192 kbps (`-q:a 2`) — roughly 4-5 MB per track, noticeably smaller than FLAC/Opus at equivalent duration. A warning note appears in the UI when MP3 is selected, because nobody should be surprised by lossy-to-lossy re-encoding

### Fixed
- **YouTube Music playlist URLs rejected**: `music.youtube.com/playlist?list=...` URLs were blocked by the frontend validator despite the backend supporting them just fine. Now accepted alongside regular `youtube.com` playlist URLs
- **YouTube Mix/Radio playlists rejected**: Watch-page URLs with a `list=RD...` parameter (Mixes, Radio, auto-generated playlists) were rejected by both the frontend validator and the backend. Both now accept any YouTube URL containing a `list=` parameter. Mix playlists are also passed to yt-dlp as-is rather than being reconstructed as a bare `/playlist?list=RD...` URL, which YouTube refuses

---

## v2.1.1 (2026-02-22)

### Fixed
- **Watched playlist "Playlists folder" toggle ignored**: Enabling the Playlists folder toggle on an existing watched playlist had no effect -- new tracks still landed in Singles. The bulk import worker was reading the playlist name as NULL (watched playlists store their name separately, not in the bulk_imports row) so folder routing silently fell through. Worker now fetches the playlist name from `watched_playlists` when needed
- **Mobile: Preview and Similar buttons overlapping**: On touch devices both buttons were rendering on top of each other. Buttons now live in a dedicated row below the card content, side by side, each taking equal width. On desktop the row appears on hover with only the Similar button (hover-to-preview handles the rest); on mobile both are always visible
- **Similar artists: flaky first-load error**: The ListenBrainz-powered similar artists service would occasionally fail on the first request, immediately showing an error. Now retries up to 3 times with a short pause between attempts before giving up

---

## v2.1.0 (2026-02-22)

### Added
- **ARM64 Docker image**: Multi-arch build now published to Docker Hub. ARM64 users (Raspberry Pi 4/5, Apple Silicon VMs, Hetzner ARM) get a native image automatically -- no more running x86 under emulation
- **Similar artist exploration**: Hover any result card and click `~ Similar` to discover artists similar to whoever you just searched. Powered by MusicBrainz + ListenBrainz Labs -- no account required, fully public APIs. Results load progressively with the same source/quality badges as normal search
- **Download All from explore**: The explore panel has a "Download All" button (disabled until results finish loading) with an optional "Save as playlist" checkbox. Downloads feed straight into the bulk importer, auto-named "Similar to [Artist]". Supports M3U generation and all the usual duplicate detection
- **Tidal public playlist import**: Paste a `tidal.com/playlist/...` URL into the playlist importer or watched playlists. Track list is fetched via the Monochrome API -- no browser, no scraping, no auth required. Works for any playlist marked public on Tidal

### Changed
- **Rate limit raised from 60 to 200 req/min**: Was too tight for legitimate burst usage (explore fires up to 25 searches in parallel). Single-user self-hosted tool; no reason to be stingy

### Fixed
- **Scheduler started at import time**: `start_scheduler()` was called at module scope, meaning any process that imported `app` would spawn a watched-playlist scheduler thread. Now correctly starts alongside the other background monitors at app boot
- **Stale cleanup could delete active jobs**: `DELETE /api/jobs/cleanup?status=stale` was deleting all queued/downloading jobs regardless of age -- including ones that were 5 seconds old and actively downloading. Now only deletes jobs older than the stale timeout (15 minutes), matching the stale job monitor's own logic
- **Internal errors leaked to API clients**: Test endpoints for slskd, Navidrome, Jellyfin, and YouTube cookies were returning raw `str(e)` and stderr fragments to the client, potentially exposing internal hostnames, file paths, and command output. Now logs full detail server-side and returns a safe generic message
- **X-Forwarded-For trusted unconditionally**: Rate limiting could be bypassed by any client spoofing the `X-Forwarded-For` header. Now only trusted when the direct connection is from a localhost/loopback address (i.e. a genuine reverse proxy)
- **Storage stats ignored playlist directory**: Dashboard file count and storage usage only scanned `Singles/`. Users with a separate Playlists folder configured would see underreported stats. Now scans both, deduplicated by inode to avoid double-counting
- **Bulk import completion count underreported**: Progress counter only counted `completed` jobs, missing `completed_with_errors`. Both now count as done
- **Playlists folder toggle defaulted to off in key flows**: Even with `playlists_subdir` configured, new bulk imports with "Create M3U playlist" and newly added watched playlists could still save into `Singles/` unless the per-action toggle was manually enabled. UI now defaults those `use_playlists_dir` toggles to on when a Playlists folder is configured, while still allowing explicit opt-out
- **Explore "Download All" saved to Singles instead of Playlists**: When "Save as playlist" was ticked, tracks were still landing in `Singles/` due to two bugs: `use_playlists_dir` was not passed in the request, and Monochrome downloads bypassed playlist routing entirely (always writing to `Singles/Artist/`). Both fixed
- **Duplicate tracks excluded from explore playlist M3U**: Tracks that already existed in the library were skipped by the duplicate check before reaching the playlist folder, so they never appeared in the generated M3U. The M3U builder now falls back to `check_duplicate()` for any track not found in the playlist folder, so pre-existing tracks are still included in the playlist file from wherever they live

---

## v2.0.5 (2026-02-21)

### Added
- **Flat mode filename prefix**: In flat directory mode (no artist subfolders), files are now saved as `Artist - Title.flac` instead of bare `Title.flac`. Avoids a directory full of mystery files
- **Watched playlist M3U generation**: Watched playlists now have a "Generate M3U" toggle. When enabled, a `.m3u` file is created and updated on every refresh cycle as new tracks are downloaded -- the playlist file grows with the library rather than being a one-shot snapshot
- **Mobile track preview**: Search results on touch devices now show a `Preview ▶` button. Tapping it previews the track without triggering a download; tapping again stops playback. Desktop hover-to-preview behaviour is unchanged
- **YouTube Music playlist support**: `music.youtube.com/playlist` URLs are now accepted wherever YouTube playlist URLs are -- watched playlists, bulk import, direct download
- **Opus output format option**: Settings tab now has a FLAC|Opus format picker, independent of the conversion toggle. The header toggle remains on/off (convert vs keep original); the picker controls which format to convert to. Opus targets 320k VBR. Monochrome (Tidal) always downloads genuine lossless FLAC regardless of this setting
- **Playlists folder organisation**: New `playlists_subdir` setting (Settings tab, under Singles Subfolder). When set, playlist downloads go to a dedicated folder (e.g. `Playlists/`) instead of landing in Singles. Tracks inside playlist folders are always named `Artist - Title.ext` regardless of the organise-by-artist setting. M3U files sit one level above the named subfolder (`Playlists/PlaylistName.m3u`) with relative paths. Three contexts: direct YouTube playlist downloads always use the Playlists folder when configured; bulk import adds a "Save files to Playlists folder" checkbox (shown when "Create M3U playlist" is ticked and the setting is configured); watched playlists gain a per-playlist "Playlists folder" toggle. Leave `playlists_subdir` empty (default) to keep the previous Singles-only behaviour

### Fixed
- **Playlist M3U includes duplicates**: Tracks that were skipped as duplicates are now still included in the generated M3U playlist file, rather than silently missing from it
- **Spotify >100 track error messaging**: When the headless browser fallback fails (e.g. insufficient shared memory on ARM/constrained hosts), the error now explains what went wrong and points to the `shm_size: '2gb'` fix in docker-compose.yml. Previously it silently returned a truncated list with a vague warning
- **Strip YouTube source branding from tags**: The COMMENT tag block yt-dlp writes ("Provided to YouTube by...", "Auto-generated by YouTube", "℗ year Label", "Released on:...") is now cleared when applying metadata. User-written comments are left untouched
- **Metadata accuracy improvements**: MusicBrainz text search results are now rejected below a confidence score of 85 (was: any result accepted). AcoustID recording matches now require at least one positive signal (artist or title match) before overwriting tags. Monochrome (Tidal) downloads no longer have their artist/title/album overwritten by MusicBrainz -- only the year is filled in, since Tidal's metadata is authoritative. Album tag no longer defaults to `"Singles"` (an internal directory name) when no real album is available -- the tag is left empty instead
- **Watched playlist crash on null channel**: Adding a watched playlist would crash with a `TypeError` if a YouTube video had no channel/uploader in its yt-dlp metadata (`NoneType` passed to `re.sub`). `extract_artist_title()` now guards against null inputs
- **Download path clarity**: Settings tab now shows a live path preview beneath the Singles and Playlists subfolder pickers (e.g. `Files saved to: /music/Singles/Artist Name/Track Title.flac`), updating as the dropdown and organise-by-artist toggle change. Flat mode correctly shows `Artist Name - Track Title.flac`
- **YouTube cookie handling**: Cookie test now uses an age-restricted video as the test target (a public video is completely useless as a cookie test -- it passes with or without them). Cookie failure cooldown is now only applied when a cookieless retry actually succeeds, confirming the cookies were at fault. Previously, any single 403 would disable cookies globally for two hours even if the failure was unrelated (geo-block, ContentID, etc.)
- **Cookie test video unavailability**: If the age-restricted test video isn't accessible in the user's region, the cookie test no longer dumps raw yt-dlp error output at them. It now detects that both cookie and cookieless attempts failed for the same reason and returns a sensible "cookies loaded, test inconclusive" message instead

---

## v2.0.4 (2026-02-19)

### Fixed
- **Monochrome quality fallback**: Some tracks return 403 at the LOSSLESS tier (Tidal restricts certain catalogue items). Now falls back to HIGH quality automatically rather than failing the download outright

## v2.0.3 (2026-02-19)

### Changed
- **Multi-source bulk import and watched playlists**: Bulk import and watched playlist auto-downloads now search all sources (YouTube, SoundCloud, Monochrome) in parallel instead of YouTube only. The highest-scoring result wins -- so if a track is available lossless on Monochrome, that's what gets downloaded

## v2.0.2 (2026-02-16)

### Changed
- **Simplified directory picker**: Replaced the browsable tree (with breadcrumb navigation and "Browse" button) with a single flat dropdown. Lists all existing subdirectories up to 2 levels deep, plus `/music (root)` for flat downloads and a `Custom path...` option for freeform input. Fewer clicks, less faff
- **Recursive directory listing**: `/api/music-dirs` now supports `recursive=true` and `max_depth` parameters, so the dropdown fetches the full folder tree in one request instead of level-by-level AJAX calls
- **Case-insensitive directory sorting**: Folder lists are now sorted case-insensitively so `Albums` and `albums` sit together

### Added
- **Server-side `singles_subdir` validation**: The settings API now validates the subfolder path on save -- rejects traversal attempts (`..`), normalises slashes, and confirms the resolved path stays within `MUSIC_DIR`
- **Music root as download target**: Setting the subfolder to `.` (via the `/music (root)` dropdown option) saves files directly into the music directory with no subfolder

### Fixed
- **Custom path env-lock inheritance**: The custom path text input now correctly inherits the disabled state when the setting is locked via environment variable

## v2.0.1 (2026-02-15)

### Changed
- **Singles subfolder is now a dropdown**: Replaced the free-text input with a directory picker that lists existing subdirectories from the music library. Prevents typos, trailing spaces, and other user-input gremlins. Includes a "New folder..." option for creating new directories with validated input
- **Filtered system directories**: The directory picker hides dotfiles and `@`-prefixed system folders (Synology `@Recycle`, `@Recently-Snapshot`, etc.)

### Added
- **`GET /api/music-dirs` endpoint**: Lists subdirectories of `MUSIC_DIR` for the subfolder picker

## v2.0.0 (2026-02-15)

### Added
- **Full Monochrome/Tidal search**: Free-text search via the Monochrome API returns lossless FLAC results with proper artist, album, cover art, and quality metadata. Monochrome results appear alongside YouTube and SoundCloud when searching, ranked higher thanks to genuine lossless quality
- **Direct FLAC downloads from Monochrome**: Downloads bypass yt-dlp entirely -- FLAC files stream directly from the Tidal CDN. Faster, simpler, no bot detection headaches. Cover art is embedded automatically from Tidal's image CDN
- **Hi-Res and Lossless quality badges**: Search results show "Lossless" or "Hi-Res" badges with colour-coded styling. Monochrome results display album name alongside artist
- **Monochrome API preview**: Preview playback uses AAC streams from the API (browser-native, no yt-dlp subprocess)
- **Configurable API instance**: `MONOCHROME_API_URL` env var lets you point at community mirror instances
- **Default search source changed to "All"**: Searches all sources in parallel by default so Monochrome lossless results compete with YouTube/SoundCloud on quality score

### Changed
- **Monochrome metadata source label**: Downloads from Monochrome now report metadata source as "Monochrome/Tidal API" rather than "guessed" -- because Tidal actually knows what it's serving
- **Version bump to 2.0.0**: Major feature release -- Monochrome integration turns MusicGrabber from a YouTube downloader into a proper multi-source music acquisition tool


## v1.9.2 (2026-02-13)

### Added
- **MusicBrainz artist normalisation**: When MusicBrainz returns a canonical artist name, it's now used everywhere — file tags, directory name, and the jobs database. Files are automatically relocated to the correct artist folder if the name differs from the original source. Prevents duplicate artist folders from inconsistent casing or spelling across YouTube/SoundCloud uploaders

### Fixed
- **Top Artists case grouping**: Stats queries now group artists case-insensitively, displaying the most popular casing variant and summing counts across all variants. "BAD BUNNY" (2) and "Bad Bunny" (1) now merge into a single "BAD BUNNY" (3) entry
- **SoundCloud preview playback**: SoundCloud migrated some tracks to a new CDN with different format IDs (`http_mp3_standard` instead of `http_mp3_1_0`). The old format selector would fall through to HLS (`application/vnd.apple.mpegurl`) which browsers can't play. Preview now tries both format IDs before falling back


## v1.9.1 (2026-02-13)

### Added
- **AcoustID fingerprint metadata lookup**: New metadata pipeline fingerprints downloaded audio with `fpcalc`/Chromaprint, looks up AcoustID matches, and enriches year/album via MusicBrainz recording ID. Falls back to text-based MusicBrainz lookup when fingerprinting is unavailable or low-confidence
- **Flat directory mode**: New `organise_by_artist` setting and UI toggle ("Organise by Artist"). When disabled, tracks are saved directly under `Singles` with no artist subfolders
- **Stats reset action**: New `DELETE /api/stats` endpoint and "Reset Stats" button in the Stats tab to explicitly clear historical stats data
- **Metadata provenance tracking**: Jobs now store a `metadata_source` value so queue details can show where final tags came from (`AcoustID fingerprint`, `MusicBrainz text match`, or source guessed metadata for YouTube/SoundCloud/Soulseek)

### Changed
- **Queue clear semantics**: "Clear Queue" remains queue/job cleanup only; stats/history reset is now a separate explicit action
- **Duplicate/path handling across layouts**: Duplicate detection and bulk playlist file resolution now work across both folder layouts (artist subfolders and flat)
- **AcoustID configuration**: `ACOUSTID_API_KEY` now supports environment override via `ACOUSTID_API_KEY`

### Fixed
- **Settings API model mismatch**: `organise_by_artist` is now included in `SettingsUpdate`, so the Settings toggle persists correctly via `PUT /api/settings`
- **Stats reset safety**: `DELETE /api/stats` now requires explicit confirmation (`?confirm=true`) to prevent accidental history wipes
- **YouTube title edge case -> hidden output file**: Titles with trailing separators/suffixes (e.g. patterns like `Artist -- Title - Official Video`) could be cleaned to an empty title, causing yt-dlp to output hidden files like `.webm.flac` and fail with "audio file not found". Parsing now rejects empty cleaned titles, falls back safely, and download naming enforces a non-empty basename

## v1.9.0 (2026-02-10)

### Added
- **SoundCloud search**: Search SoundCloud via yt-dlp `scsearch` — returns results with correct artist (from `uploader` field), duration, thumbnails, and quality scoring. No auth required
- **Source selector**: Segmented button group (YouTube / SoundCloud / All) on the search bar. Selection persisted to localStorage. "All" searches both sources in parallel and merges results by quality score
- **Extensible source architecture**: New `search.py` module with `SOURCE_REGISTRY` dict — adding a new source is one search function and one registry entry. Includes `search_source()`, `search_all()`, and `get_available_sources()` API
- **`GET /api/sources` endpoint**: Returns available search sources with labels, badges, and colours for the frontend
- **SoundCloud downloads**: Full download pipeline support — SoundCloud URLs route through yt-dlp without YouTube-specific cookie/backoff logic
- **SoundCloud preview**: Hover-to-preview works for SoundCloud tracks (passes source URL to the preview endpoint)
- **Source badges**: Search results and queue items show coloured source badges (YT red, SC orange, SLK teal) with consistent `getSourceBadge()` / `getSourceLabel()` helpers
- **Donation link**: Added a subtle Ko-fi "Buy me a coffee" link with coffee icon in Settings (`https://ko-fi.com/geekphreek`)
- **Amazon Music playlist import**: Paste a public Amazon Music playlist URL and import the tracks into bulk import. Uses headless Playwright to scrape Amazon's JS-rendered pages, handling cookie consent banners and virtualised scrolling. Extracted 132 unique tracks from a 139-track playlist in testing (7 were duplicates). Supports user playlists, curated playlists, and all regional Amazon domains
- **Generalised playlist endpoint**: New `/api/fetch-playlist` endpoint routes to Spotify or Amazon scraper based on URL. Old `/api/spotify-playlist` path kept as backwards-compat alias
- **Custom singles subfolder**: New `singles_subdir` setting in Settings > General lets you change the download subfolder name (default: `Singles`). Overridable via `SINGLES_SUBDIR` env var. Changes take effect immediately without restart
- **Source badges on queue items**: Queue entries now show a coloured source badge (YT/SC/SLK) in the bottom-right corner of each card

- **Report / Blacklist system**: Flag bad tracks (wrong track, poor quality, slowed/pitched, ContentID dodge) directly from the queue with a Report button. Blacklisted videos are hidden from search results and bulk imports; blocked uploaders get a heavy score penalty so they sink to the bottom. Manage all entries in Settings > Blacklist with one-click removal
- **Blacklist API**: New `POST /api/blacklist`, `GET /api/blacklist`, `DELETE /api/blacklist/{id}` endpoints for reporting and managing blacklisted tracks and uploaders
- **Uploader tracking**: Jobs now store the raw uploader/channel name (separate from the cleaned artist name) for accurate blacklist matching

### Changed
- **Honest audio quality reporting**: FLAC files converted from lossy sources now show their true origin (e.g. "FLAC (from MP3 128kbps)" instead of "FLAC 44.1kHz 24bit"). The min-bitrate quality gate also uses the source bitrate, so a 64kbps Opus wrapped in FLAC won't sneak past
- **Search routing**: `/api/search` now dispatches via `search.py` based on `source` param instead of calling `search_youtube()` directly
- **Download routing**: `process_download()` accepts optional `source_url` param; SoundCloud downloads skip YouTube ID validation, cookie handling, and 403 retry logic
- **Preview routing**: `/api/preview/{video_id}` accepts `source` and `url` query params for non-YouTube sources
- **Retry routing**: `/api/jobs/{job_id}/retry` passes stored `source_url` for SoundCloud re-downloads
- **Stats source breakdown**: Now shows YouTube, SoundCloud, and Soulseek counts with correct colours
- **Playlist input UX**: Bulk import playlist URL field now uses generic wording and includes a supported-services hint/tooltip driven from a centralised service list
- **Watched playlists**: URL input and description updated to mention Amazon Music alongside Spotify and YouTube

### Fixed
- **SoundCloud preview**: SoundCloud returns HLS `.m3u8` playlist URLs for `bestaudio` which browsers can't play natively in `<audio>`. Preview now requests the direct HTTP MP3 stream (`http_mp3_1_0`) instead
- **SoundCloud queue false-fail**: Fixed a thread spawn bug where SoundCloud downloads were inserted as `queued` but the API returned an error (`Failed to queue`) because keyword args (`source_url`) were not forwarded to the background thread helper
- **Queue delete button state**: "Delete File" now persists per job after successful deletion (`file_deleted=1`), renders as disabled/greyed "File Deleted", and is reset when "Re-download" is clicked
- **Delete button for missing files**: If a file was deleted externally, the delete button now greys out automatically instead of throwing an error. The jobs list checks file existence on load and updates the flag in the database
- **Empty singles subfolder fallback**: Clearing the singles subfolder setting no longer dumps files into the music root -- it falls back to "Singles"
- **Queue action buttons with special characters**: "Delete File" and "Report" now work reliably for tracks with apostrophes/quotes in artist or title. Switched from fragile inline argument interpolation to data-attribute event binding
- **Queue card expansion state**: Expanded queue items now stay expanded across refreshes after actions like delete/report/reload, instead of collapsing unexpectedly

## v1.8.5 (2026-02-08)

### Added
- **Dark/light theme toggle**: Moon/sun button in the header switches between dark and light themes. Preference saved to localStorage
- **Webhook notifications**: New generic webhook URL setting — sends a JSON POST on download completion/failure with event type, title, artist, status, source, and track counts. Configure via Settings > Notifications or the `WEBHOOK_URL` env var
- **Statistics dashboard**: New "Stats" tab with download overview — completed/failed counts, success rate, library storage usage, daily download chart (last 14 days), source breakdown (YouTube vs Soulseek), top 10 artists, and recent downloads
- **Search analytics in Stats**: Search queries are now logged and shown in the Stats tab with total searches, successful search rate, search-to-download conversion, and most searched artists
- **Delete from library**: Completed jobs in the queue now have a "Delete File" button that removes the audio file and lyrics from disk, plus cleans up empty artist directories
- **Re-download**: Completed and failed jobs now have a "Re-download" button in the queue details to re-queue the download (overwrites existing file)
- **Audio quality display**: Completed downloads now show the audio quality (e.g. "FLAC 44.1kHz 16bit", "OPUS 160kbps") in the queue job details
- **Minimum bitrate setting**: New "Minimum Audio Bitrate" setting in Settings > General. Downloads below this bitrate are automatically rejected with a clear error message. Set to 0 (default) to disable. Lossless formats (FLAC) always pass

### Changed
- **Tab bar**: Now horizontally scrolls on narrow screens to accommodate the sixth tab without wrapping
- **Date display format**: UI dates now render consistently as `YYYY-MM-DD` instead of locale-specific formats

### Fixed
- **Audio quality: 64kbps downloads**: Removed the forced Android YouTube player client (`player_client=android`) which was causing yt-dlp to pull very low bitrate audio (64kbps). Downloads now use YouTube's default web client which serves full-quality audio (~160kbps Opus). An env-var escape hatch (`YTDLP_PLAYER_CLIENT`) is available if needed
- **Search conversion overcounting**: Search-to-download conversion now uses a per-search server token instead of matching raw query text, preventing repeated identical searches from inflating conversion rate
- **Search attribution trust boundary**: Download attribution now validates server-issued search tokens and ignores invalid/untrusted values
- **Search analytics retention**: Added automatic pruning of old `search_logs` rows (90-day retention) to keep stats queries fast and DB growth bounded

## v1.8.4 (2026-02-06)

### Fixed
- **Playlist download permissions**: Audio files downloaded as part of a playlist now get `set_file_permissions` applied, matching single track and Soulseek downloads. Previously, playlist tracks had different permissions on NAS/SMB shares
- **Silent download success with no file**: `process_download` now raises an error if no audio file is found after yt-dlp completes, instead of silently marking the job as completed with no file on disk
- **Stale job timestamp mismatch**: Stale job cleanup now uses SQLite's native `datetime()` functions instead of Python `isoformat()`, fixing a string comparison mismatch between `T` and space separators
- **Scheduler crash on bad playlist URL**: `fetch_playlist_tracks` now guards the `list=` regex match, preventing an `AttributeError` crash if a stored YouTube URL has no `list=` parameter

### Changed
- **Search scoring: duration awareness**: Results are now scored by duration — typical song length (1:30–7:00) gets a bonus, while clips (<30s), snippets (<90s), extended mixes (12–20min), and full albums (20min+) are penalised
- **Search scoring: view count tiebreaker**: View count is now a modest scoring signal — suspiciously low views (<1K) get a small penalty, high views (100K+) get a small bonus. Deliberately conservative to avoid penalising niche artists
- **Search scoring: Official Audio boost**: "Official Audio" bonus increased from +20 to +35, matching the Topic channel bonus — both signal official studio audio, which is the ideal source for a music grabber
- **Title cleaning: trailing suffixes**: `clean_title()` now strips unbracketed trailing suffixes like "- Official Audio", "- Official Music Video", and "- Official Lyric Video", plus any dangling separators left after cleanup
- **Audio extensions centralised**: The repeated `['.flac', '.opus', '.m4a', '.webm', '.mp3', '.ogg']` list (5 occurrences) is now a single `AUDIO_EXTENSIONS` constant in `constants.py`
- **Navidrome auth deduplicated**: Subsonic API auth logic (salt, MD5 token, params) extracted to `subsonic_auth_params()` in `utils.py`, fixing inconsistent API versions and client names between test and scan endpoints
- **Bulk import thread pool**: Downloads spawned by bulk imports now use a `ThreadPoolExecutor(max_workers=3)` instead of unbounded daemon threads, preventing hundreds of concurrent yt-dlp subprocesses on large imports
- **Bulk import DB connection**: The bulk import worker now acquires and releases DB connections per query instead of holding one for its entire lifetime (which could be hours)
- **Spotify browser script extracted**: The 130-line Playwright f-string with double-brace escaping is now a standalone `spotify_browser.py` script that receives parameters via environment variables — proper syntax highlighting, linting, and no escaping bugs
- **Dockerfile version pins**: Python packages now pinned with compatible release specifiers (`~=`) for reproducible builds
- **Entrypoint banner**: Replaced hardcoded `http://localhost:38274` (Docker host port) with a message showing the actual container port (8080)

### Removed
- **beautifulsoup4**: Removed unused dependency from Dockerfile (~500KB saved)
- **Dead section header**: Removed empty "Legacy Bulk Import (synchronous)" comment block from `app.py`
- **Unused enumerate**: Removed discarded index variable in playlist download loop

## v1.8.3 (2026-02-04)

### Added
- **PUID/PGID support**: Run the container as a specific user/group for correct file ownership (like *arr stack). Set `PUID=1000` and `PGID=1000` in your environment to match your host user
- **Preview button visibility**: The play/preview button on search results is now always visible (dimmed) and highlights on hover, making the feature more discoverable
- **Volume mount warning**: Shows a dismissible warning banner if the music directory doesn't appear to be mounted as a volume (helps catch misconfigured setups where downloads would be lost on container restart)
- **Custom tooltips**: Search results now show "Hover to preview, click to download" tooltip after 0.25s (faster and more reliable than native browser tooltips)

### Fixed
- **Queue timestamps ignore timezone**: Timestamps in the queue now correctly respect the user's timezone. SQLite stores times in UTC, and the frontend now properly interprets them as UTC before converting to local time

## v1.8.2 (2026-02-03)

(Skipped - changes merged into 1.8.3)

## v1.8.1 (2026-01-31)

### Added
- **Queue timestamps ignore timezone**: Timestamps in the queue now correctly respect the user's timezone. SQLite stores times in UTC, and the frontend now properly interprets them as UTC before converting to local time

## v1.8.1 (2026-01-31)

### Added
- **Settings clear buttons**: All text and password settings now have an inline "Clear" button that clears the field and saves in a single click

### Fixed
- **Download permission errors**: When yt-dlp fails with a permission denied error on temp file rename (e.g. `Brunette.temp.flac` → `Brunette.flac`), leftover `.temp.*` files are now cleaned up and the download is retried automatically. Applies to both single track and playlist downloads

## v1.8.0 (2026-01-31)

### Changed
- **Codebase split**: Monolithic `app.py` (~4778 lines) split into 15 focused modules — `app.py` is now a thin route layer, with main logic in `constants.py`, `models.py`, `db.py`, `settings.py`, `utils.py`, `middleware.py`, `youtube.py`, `slskd.py`, `spotify.py`, `metadata.py`, `notifications.py`, `downloads.py`, `bulk_import.py`, and `watched_playlists.py`
- **Notification function renamed**: `send_telegram_notification` → `send_notification`
- **Dockerfile**: Now copies all Python modules (`COPY *.py`) instead of just `app.py`
- **YouTube backoff settings**: Warns when min/max are misconfigured and swapped
- **Title cleaning**: Consolidated title cleanup regexes into a single pass
- **Search ranking**: YouTube scoring now uses query-aware token matching and stricter artist/title alignment
- **DB connections**: Switched call sites to a context-managed connection helper to ensure closes on error
- **YouTube cookies**: Added a Settings upload button and automatic cooldown when cookies appear stale
- **Background work**: Standardized background downloads/retries to use daemon threads
- **Background threads**: Centralized the daemon thread helper in `utils` for shared use
- **Bulk import search**: Reused shared YouTube search parsing/scoring logic to avoid drift
- **SQLite pooling**: Added a small connection pool for reuse
- **SQLite pooling fix**: Enabled cross-thread connections for pooled reuse in FastAPI
- **File permissions**: Audio files now get `0o666` instead of `0o777` (no execute bit)
- **Bulk import progress**: Progress display now tracks downloads through to completion instead of showing "Complete" while tracks are still downloading
- **YouTube Topic channels**: Artist names from YouTube auto-generated "- Topic" channels are now cleaned up properly
- **Rate limiting**: Added periodic cleanup to prevent long-lived IP entries from accumulating
- **Scheduler jitter**: Watched playlist checks add a small random offset to avoid synchronized polling

### Removed
- **Sync bulk import endpoint**: Removed `/api/bulk-import` (the sync, event-loop-blocking version). Use `/api/bulk-import-async` instead
- **Legacy bulk import model**: Removed unused `BulkImportRequest`
- **Notification alias**: Removed unused `send_telegram_notification` alias

### Fixed
- **Search scoring**: Removed duplicate cover/remix penalty in YouTube scoring
- **YouTube ID validation**: Added basic ID validation before building yt-dlp URLs
- **Title splitting**: Hyphens in compound words (e.g. "T-4") no longer incorrectly split artist from title
- **Variable safety**: `process_download` no longer uses fragile `dir()` checks for variable existence
- **Playlist track failures**: Fixed `NameError` (`processed_tracks` -> `completed_tracks`) that caused a single track failure to kill the entire playlist job
- **Download success path**: Fixed indentation bug where successful first-attempt downloads skipped metadata, library scans, and job completion
- **Connection pool safety**: `row_factory` is now reset when connections are returned to the pool, preventing leaked state between callers
- **DB rollback semantics**: Only roll back open transactions on `db_conn()` exit
- **YouTube cookie test cleanup**: Temp cookie files are now cleaned up on all failure paths
- **yt-dlp retry logic**: Consolidated cookie/backoff retry logic to avoid drift across download paths
- **API key compare**: Constant-time comparison for API keys
- **Search input validation**: Added max length constraints to search queries
- **MusicBrainz UA**: Standardized the User-Agent URL used for MusicBrainz lookups

## v1.7.1 (2026-01-30)

### Added
- **Watched playlist FLAC controls**: Per-playlist FLAC toggle plus a "Convert to FLAC" option when adding a watched playlist
- **Queue job details**: Click completed/failed items in the queue to expand and see source URL, queued/completed timestamps, and download duration
- **Source URL tracking**: Jobs now store the YouTube URL or Soulseek path they were downloaded from
- **Stale job detection**: Background monitor marks stuck downloading/queued jobs as failed after 15 minutes of no progress. Also runs at startup to catch jobs orphaned by container restarts
- **YouTube cookie support**: Paste browser cookies in Settings to authenticate yt-dlp requests and avoid YouTube 403 bot-detection blocks. Includes a "Test Cookies" button that validates against YouTube before saving
- **YouTube 403 auto-retry**: Downloads that hit a 403/Forbidden error automatically retry up to 2 times with increasing backoff. Failed jobs show a clear hint about cookies in the queue error message

### Changed
- **Watched playlist creation**: Now honours the FLAC setting selected at creation time
- **Settings env lock badge**: Replaced "ENV" with a clearer "CONFIG LOCKED" pill
- **Clear Queue**: Now also cleans up stale/stuck downloads, not just completed and failed jobs
- **YouTube download client**: Default yt-dlp player client set to Android to reduce bot blocks (reverted in v1.8.5 — caused 64kbps audio)
- **Bot backoff**: Queue now applies a randomized delay after bot/403 signals to ease rate limits

### Fixed
- **Env-locked settings**: Greyed out locked fields and added hover hint explaining they are set via docker-compose.yml
- **Stuck downloads**: Jobs that were permanently stuck in "downloading" status (e.g. from crashed background tasks or container restarts) are now automatically timed out and can be cleared
- **Queue errors**: Completed jobs now clear stale error messages


## v1.7.0 (2026-01-25)

### Added
- **Settings tab**: New UI tab for configuring all integrations without editing docker-compose.yml
  - Configure slskd, Navidrome, Jellyfin connections
  - Set up notification channels (Telegram, SMTP)
  - Toggle MusicBrainz metadata and lyrics fetching
  - Test connection buttons for slskd, Navidrome, Jellyfin
  - Password fields with show/hide toggle
  - Environment variables override database values (shown as locked in UI)
- **API authentication**: Optional API key protection for all endpoints
  - Set API key in Settings or via `API_KEY` environment variable
  - Frontend prompts for key and stores in browser localStorage
  - Clear/change stored key via Settings UI
- **Rate limiting**: 60 requests per minute per IP address
  - Proper 429 responses with `Retry-After` header
  - `X-RateLimit-Limit`, `X-RateLimit-Remaining` headers on all API responses
  - Respects `X-Forwarded-For` for reverse proxy setups

### Changed
- **Configuration approach**: Settings can now be managed via UI instead of environment variables
- **Security section in README**: Updated with API key authentication details

### Fixed
- **Watched playlist scheduler**: Now checks for due playlists immediately on startup instead of waiting for the first interval to elapse
- **Test connection buttons**: Now use current form values instead of requiring save first
- **Test connection result display**: Results now properly appear after testing
- **Settings save**: Only saves fields that have actually changed (prevents saving placeholder text)
- **FLAC toggle sync**: Header FLAC toggle and Settings FLAC checkbox now stay in sync

### Technical Details
- Settings stored in SQLite `settings` table
- `AuthMiddleware` handles API key validation and rate limiting
- `/api/config` endpoint now returns `auth_required` flag
- All fetch calls wrapped in `apiFetch()` for automatic auth header injection

## v1.6.1 (2026-01-22)

### Added
- **Copy playlist URL**: Watched playlists now include a "Copy URL" button in the UI
- **Watched playlist bulk import**: Newly watched playlists now queue downloads via bulk import
- **Notifications**: Get notified when downloads complete or fail
  - Telegram support via webhook URL (`TELEGRAM_WEBHOOK_URL`)
  - Email support via SMTP (`SMTP_HOST`, `SMTP_USER`, etc.)
  - Shared triggers for all channels (`NOTIFY_ON`): singles, playlists, bulk, errors

### Changed
- **Watched playlist refresh**: Refresh now requeues missing tracks and only pulls what is not yet downloaded

### Fixed
- **Watched playlist download tracking**: Completed jobs now update watched track download status
- **Favicon not showing in browser**: Added mount in FastAPI

## v1.6.0 (2026-01-20)

### Added
- **Centralised configuration constants**: All timeout values and magic numbers now defined at top of `app.py` for easy tuning
- **Dynamic version display**: Frontend now fetches version from `/api/config` endpoint instead of hardcoding

### Changed
- **Version management**: Single `VERSION` constant used throughout backend (FastAPI app, User-Agent strings, API responses)
- **Consistent User-Agent**: All HTTP clients now use `MusicGrabber/{VERSION}` format (fixed outdated `1.1.0` in lyrics fetcher)

### Technical Details
New constants section at top of `app.py`:
- Timeout values: `TIMEOUT_YTDLP_*`, `TIMEOUT_SLSKD_*`, `TIMEOUT_HTTP_*`, `TIMEOUT_FFMPEG_CONVERT`, `TIMEOUT_SPOTIFY_BROWSER`
- Bulk import: `BULK_IMPORT_SEARCH_DELAY`, `BULK_IMPORT_BACKOFF_DELAYS`, `BULK_IMPORT_BACKOFF_RESET_AFTER`
- Playlist: `PLAYLIST_WAIT_MAX`, `PLAYLIST_WAIT_INTERVAL`
- Search: `YOUTUBE_SEARCH_MULTIPLIER`, `YOUTUBE_SEARCH_MIN_FETCH`, `SLSKD_MAX_RESULTS`, `SLSKD_MIN_QUALITY_SCORE`
- Files: `MAX_FILENAME_LENGTH`

### Removed
- Dead code block in Spotify browser scraper (unreachable `for row in []` loop)

## v1.5.2 (2026-01-19)

### Added
- **Full Spotify playlist support**: Large playlists (100+ tracks) now fully supported via headless browser scraping
- **Playwright integration**: Added Chromium-based browser automation for Spotify pages that exceed embed limits
- **Virtualized scroll handling**: Extracts tracks incrementally while scrolling to handle Spotify's lazy-loading
- **Cookie consent automation**: Automatically dismisses Spotify's cookie banner during scraping

### Technical Details
- Spotify's embed endpoint only returns ~100 tracks maximum
- For larger playlists, MusicGrabber launches a headless Chromium browser via Playwright
- The browser loads the full Spotify page, accepts cookies, then scrolls through the tracklist
- Tracks are extracted incrementally during scrolling (Spotify uses virtualized lists that unload off-screen items)
- Only numbered tracks are extracted, filtering out "Recommended" suggestions at the bottom
- Docker image now includes Playwright and Chromium (~400MB additional)
- Added `shm_size: 2gb` to docker-compose for Chromium's shared memory requirements

### Changed
- Dockerfile now installs Playwright and Chromium browser
- Updated README with detailed Spotify integration documentation

### Removed
- Spotify API authentication code (Spotify has disabled new app creation, so this was unusable)

### Fixed
- **Soulseek retry bug**: Failed Soulseek downloads can now be retried correctly (metadata is persisted in jobs table)
- **Playlist status reporting**: Playlist jobs now track individual track failures and report partial success
- **Path traversal protection**: slskd download paths are now validated to prevent copying files from outside allowed directories

### Security
- Added security documentation to README about lack of built-in authentication
- Recommend reverse proxy with auth for external access

## v1.5.1 (2026-01-18)

### Added
- **Async bulk import**: Large playlist imports (1000+ tracks) now process asynchronously with real-time progress tracking
- **Parallel search and download**: Downloads start immediately as tracks are found, rather than waiting for all searches to complete
- **Rate limiting protection**: Automatic exponential backoff (30s, 60s, 120s, 300s) when YouTube returns 429 errors
- **Spotify album support**: Can now import from Spotify album URLs in addition to playlists
- **Jellyfin integration**: Added support for Jellyfin library refresh after downloads (configure via `JELLYFIN_URL` and `JELLYFIN_API_KEY`)
- **Progress UI**: New 5-column progress display showing Searched, Queued, Done, Failed, and Total counts

### Fixed
- Unicode escape errors when parsing Spotify track names with special characters
- Recent Activity now shows recently processed tracks correctly during bulk imports

### Changed
- Removed 70-track limit on bulk imports
- Bulk import state persisted to database for resilience across restarts

## v1.5.0

### Added
- Jellyfin integration for automatic library refresh

## v1.4.1

### Fixed
- Improved slskd download handling for transient failures

## v1.4.0

### Added
- Soulseek/slskd integration for higher quality sources (requires VPN port forwarding)

## v1.3.0

### Added
- Spotify public playlist import
- Various database async fixes

## Earlier versions

- YouTube search and download via yt-dlp
- MusicBrainz metadata lookups
- LRClib lyrics fetching
- Navidrome library refresh trigger
- Duplicate detection
- M3U playlist generation
- FLAC conversion
