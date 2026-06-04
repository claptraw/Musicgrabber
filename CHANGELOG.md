# Changelog

## v2.9.1 (2026-06-04)

### Added
- **New search source: FreeMp3Cloud**: a sixth web source (g2.freemp3cloud.com, backed by the meln.top CDN) joining the MP3 fallback ranks. It is quality-aware: results the site tags "HQ" are genuine 320 kbps and get scored at the same tier as MP3Phoenix, while the un-tagged 128 kbps ones are demoted so they only ever surface when nothing better exists. Toggle it in Settings under Search Sources (on by default), hover-preview works, and it slots into the upgrade scanner's tiering too. As with the other lossy web sources, lossless still always wins.
- **Track Upgrades, Phase 1: the library scan (groundwork for Lidarr-style "hold out for a better copy")**: downloads now get stamped with `SOURCE` and `SOURCE_QUALITY` tags (FLAC, MP3, M4A/MP4, Ogg/Opus), and a new opt-in background scan walks the files MusicGrabber downloaded (Singles and playlist tracks, never your Albums) to flag any sitting below the quality you already download at. The scan is cheap, network-free, and only looks; it never searches, downloads, or touches a file. Off by default; admin/standard only, peons never see it. New `enable_track_upgrades` toggle and `upgrade_scan_interval_hours` in Settings. Design lives in `docs/upgrades-design.md`.
- **Track Upgrades, Phase 2a: the "Watched Upgrades" page (search + preview)**: a new section under the Watched tab, beneath Watched Artists. It lists the below-target files 10 to a page and, as you land on them, quietly searches each one (one at a time, a second apart, so we don't hammer sources) for a better copy, showing the proposed source, quality, and match confidence. Verified sources (Monochrome lossless) say so; slskd results are honestly badged "needs download to confirm". Hover a proposal to preview it before committing. Dismiss anything you're happy with as-is (and it comes back if you later change the file). Results are cached for a few hours so revisits are instant.
- **Track Upgrades, Phase 2b: the actual swap**: each proposal now has an Upgrade button (plus an Upgrade All). It downloads the proposed copy to a staging area, ffprobes it, and only swaps it in if it passes every gate: genuinely better quality than what's on disk, duration within tolerance, a high title/artist match, and an acoustic-fingerprint (fpcalc) check that it's the same recording. Pass, and the old file is moved to a quarantine folder (manual purge, never auto-deleted), the new one takes its place, tags and any playlist M3U entries are fixed up, and Navidrome is poked to rescan. Fail any check and the download is binned and your original is left exactly as it was. Albums are still never touched.
- **Force upgrade**: if a proposal is rejected by the same-recording checks (say you'd rather have the studio cut than your longer "HD" YouTube rip), the rejected row offers a "Force anyway" button. It skips the identity gates but still downloads a valid file and still moves the old one to quarantine, so a forced choice is always reversible.
- **Scan now sees through transcoded FLACs**: a lossy track converted to FLAC is a FLAC container but lossy audio. The scan now reads the honest origin from the SOURCE_QUALITY tag and tiers by that, so e.g. a YouTube-sourced "FLAC" correctly shows as upgradeable to real lossless instead of hiding as already-lossless.

### Fixed
- **A flaky YouTube thumbnail no longer sinks the whole download**: YouTube's thumbnail CDN occasionally fails to serve the cover art, and yt-dlp treated that as fatal, binning a track whose audio had already downloaded perfectly (and was already tagged). The download path now recognises a thumbnail-only post-process failure and salvages the audio via the existing recovery route, so you get your track (just without embedded cover art on those rare occasions) instead of a failed job. Genuine audio failures still fail, as they should.
- **Spotify playlist fetches no longer fall over on a transient Spotify hiccup**: Spotify's embed edge throws the occasional 502/503/504 gateway timeout (especially under load), and a single one of those used to fail the whole playlist fetch with a hard 502. The embed fetch now retries a few times with a short backoff before giving up, so a momentary blip self-heals instead of taking the playlist down with it. A genuine, persistent outage still fails loudly after the retries, as it should. Tuneable via `SPOTIFY_EMBED_MAX_ATTEMPTS` / `SPOTIFY_EMBED_RETRY_BACKOFF`.

## v2.9.0 (2026-05-31)

### Added
- **Sources that are down now get parked automatically instead of filling search with dead results**: MusicGrabber checks source health at startup and refreshes stale checks during multi-source searches. If a source fails its check, its results are hidden for a configurable cooldown and the search response tells the UI which source was skipped. Monochrome is checked against the fragile Qobuz download leg, not just the search API, so lossless results stop appearing when they cannot actually stream. Settings now include a health-check toggle plus interval/cooldown controls.
- **Cross-source fallback when a whole source is offline, now a proper toggle**: when every Monochrome proxy is down (502s, "no route to host", the usual Qobuz-proxy circus), the job used to either fail outright or, worse, burn its retry budget picking three more Monochrome results from the same dead infrastructure. Two fixes. First, the alternate-candidate search now excludes the dead source entirely, so it jumps straight to YouTube, SoundCloud, or Soulseek instead of headbutting the same wall. Second, there's a new "Fall back across sources" setting (default on) in Settings under Search Sources, so anyone who would rather a job fail loudly than quietly land a lower-quality copy can switch it off. Controlled by `SOURCE_OFFLINE_FALLBACK`.
- **Direct qbdlx fallback for Monochrome/Qobuz**: when every public Qobuz proxy is down, Monochrome can now sign the official Qobuz API directly using the shared qbdlx free-account token pool (`https://qbdlx.launchpd.cloud/`). This bypasses the proxy middleman and still returns real Qobuz FLACs. The free shared tokens top out at 16-bit/44.1kHz lossless rather than hi-res, but a real FLAC beats a failed download. The fallback is on by default and can be disabled with `QBDLX_FALLBACK_ENABLED=false` or the new Monochrome fallback setting.
- **Static assets now cache-bust on content, not just version**: the UI tagged `app.js`/`index.html` with `?v=<version>`, so any change made inside a `(DEV)` cycle (where the version stays put) could be masked by a browser happily serving the cached old file. The cache-bust token now folds in the newest static-file timestamp, so edits show up after a rebuild without a hard refresh.
- **Vanished watched playlists now get auto-paused with a note, instead of failing forever in silence**: when an upstream playlist is deleted (or yanked private), the refresh just kept throwing a "not found" every cycle and nobody was any the wiser. Now a 404 counts as a strike, and after three consecutive strikes (configurable via `WATCHED_GONE_STRIKES_BEFORE_PAUSE`) the playlist is paused, not deleted, with a plain-English note explaining it looks deleted or made private upstream (or the platform login token expired). We wait for several strikes precisely because a private playlist with an expired token can also 404, and we would rather not pause a perfectly good playlist over one bad afternoon. The note shows on the card, a notification fires, and hitting Resume wipes the strike count for a clean retry. A successful refresh also clears everything, so the odd transient blip self-heals. DB migration v8 adds the tracking columns.

- **Watched artists for prolific acts (Radiohead, etc.) now page their tracks instead of dumping all of them**: the tracks view is paginated (50 at a time with Prev/Next), so an artist with a few hundred singles no longer renders one enormous wall of rows. The endpoint takes `limit`/`offset` (and `limit=0` still hands back the lot for anyone who wants it).

### Changed
- **Adding (or refreshing) a watched artist no longer freezes the whole app while it seeds**: seeding a prolific artist's back-catalogue from MusicBrainz means hundreds of duplicate-on-disk checks, and the refresh used to do them all inside one long-held write transaction. On a big or slow library that pinned SQLite's single writer lock for minutes, so other requests, even logging in, would block until they hit `database is locked`. The refresh now does its slow scanning lock-free and flushes its writes in small cycles, so the lock is taken in millisecond bursts. On top of that, the first seed (and manual refreshes) now run in the background and the request returns immediately; the card shows live progress via the existing refresh stages, exactly as it already does for scheduled refreshes. No more two-minute hung "Add artist" request, no more app-wide stall.
- **Monochrome no longer gives up on the proxies after one half-hearted attempt**: the Qobuz proxies are gloriously flaky (502 one second, 200 the next), but the downloader only swept the proxy list once before declaring the source dead. It now sweeps all proxies, has a brief lie down, and sweeps again up to five times (configurable via `MONOCHROME_PROXY_RETRY_ROUNDS` / `MONOCHROME_PROXY_RETRY_WAIT`) before handing off to the fallback machinery. Crucially, it only retries when the failures were transport-level (proxy unreachable); a track that genuinely isn't on Qobuz still fails fast instead of making you wait fifteen seconds for bad news.

### Fixed
- **Monochrome hover previews now work when qbdlx is carrying the source**: source health could correctly mark Monochrome as up because qbdlx could stream, but the preview endpoint still only tried the broken Qobuz proxy path. Hover/mobile preview now falls through to qbdlx too, trying Qobuz format 7 first and then format 6, matching the health probe.
- **Queue loads no longer walk the whole library on every refresh**: `/api/jobs` was checking completed jobs against the music folder while rendering the queue. On large or slow mounts that made the Queue tab look like it had failed to load. The queue API now stays cheap and leaves broad missing-file reconciliation to the background monitor.
- **Queue polling no longer hammers SQLite session writes**: every authenticated API request touched `sessions.last_seen`, so several open tabs polling the queue could turn read-only refreshes into constant SQLite writes and intermittently trip `database is locked`. Session touches are now throttled to once per minute per token.
- **A dead or mistyped YouTube playlist URL now returns a sensible "not found" instead of a scary Bad Gateway**: fetching a deleted, private, or malformed playlist used to come back as a 502, implying our server had fallen over. yt-dlp actually tells us it is a client-side problem (the playlist genuinely is not there), so we now surface a 404 with a plain-English reason and reserve 502 for real upstream failures.

## v2.8.18 (2026-05-28)

### Fixed
- **Watched artists re-downloading pre-from_date tracks on every refresh**: tracks released before a watched artist's `from_date` were seeded into `watched_artist_tracks` with no `downloaded_at` and no `job_id`. Every subsequent refresh then mistook them for failed downloads and re-queued them, sailing straight past the `from_date` guard. The retry path now checks the stored `release_date` before queueing, exactly like the new-track path does, so old back-catalogue tracks stay where they belong: in the past.
- **Monochrome missing from Stats tab Sources breakdown**: `data.sources.monochrome` was never read; the bar and legend only knew about YouTube, MP3Phoenix, SoundCloud, zvu4no, and Soulseek. Monochrome now appears in both (teal, matching its badge colour), and is only shown when count > 0.
- **Monochrome silently disabled on fresh installs**: the `default_enabled` flag in the source registry and the fallback in `monochrome_enabled()` both said `False`, so a brand-new install with no DB row for `source_monochrome_enabled` would have Monochrome turned off despite the settings schema defaulting it to `True`. Fixed by making both fall back to `True`, which is what the schema has always said.
- **Monochrome downloads failing on all installs**: `qdl-api.monochrome.tf` expired its Qobuz credentials (returns 400/401 for every track). The Qobuz proxy URL is now a comma-separated fallback list, matching how the hifi-api URLs work. Two working community proxies (`qobuz.kennyy.com.br`, `mono.scavengerfurs.net`) are prepended to the default list so downloads succeed when the primary is down. DB migration v7 rebuilds existing installs' proxy list. Proxies are also health-checked: HTTP 4xx/5xx marks a proxy as deprioritised for 30 minutes, a background thread probes all proxies once per hour and re-promotes ones that recover, and connection failures (transient network) don't blacklist.

## v2.8.17 (2026-05-26)

### Fixed
- **Monochrome hifi-api default endpoints updated**: we now mirror the same live-instance list that Monochrome.tf itself checks. All of the qqdl.site cluster and both apex instances (`api.monochrome.tf`, `eu-central.monochrome.tf`) were returning 503 or 502 as of 26 May 2026. The new primary is `us-west.monochrome.tf` (proper HTTPS, no redirect) with `monochrome-api.samidy.com` as backup. DB migration v6 sweeps stale URLs out of existing installs.

## v2.8.16 (2026-05-25)

### Added
- **Ko-fi support button in the release-notes pop-up**: the "What's New" modal now has a centered "Donate a coffee" button in the same green primary-button style as the rest of the app, linking to `https://ko-fi.com/geekphreek`.
- **Monochrome playlist imports**: Bulk Import and Watched Playlists now accept public Monochrome playlist URLs such as `https://monochrome.tf/playlist/...`. These use the hifi-api playlist endpoint, so they benefit from the same endpoint fallback list as Monochrome search.

### Fixed
- **Bulk Import preferred-source dropdown no longer waits for the Watched tab**: the source list was only fetched through the Watched Playlists rendering path, so the Bulk Import "Preferred source" dropdown could sit on "No preference" until you visited Watched or otherwise caused source chips to render. Sources are now loaded during app startup and again on Bulk tab activation, so the dropdown is ready where it is used.
- **M3U names now survive empty-after-sanitising playlist titles**: playlist/M3U names already stripped path separators like `/`, but a name made entirely of illegal filename characters could collapse to an empty stem and create `.m3u` or an empty playlist folder. Playlist names now use a shared non-empty sanitizer. Imported playlist URLs are kept as a fallback label, so a hostile or silly upstream title falls back to the playlist ID instead of an empty filename.
- **Bulk Import preferred source now behaves like a source choice for that import**: choosing Monochrome in the Bulk Import dropdown now stores `preferred_sources=monochrome` on the import instead of only giving Monochrome a score boost. That means the importer will not silently fall back to YouTube or MP3Phoenix when the user has explicitly picked Monochrome for a run.
- **Monochrome bulk-import searches now survive Spotify punctuation and hifi-api 503s**: Spotify playlist imports produce search strings like `Artist1, Artist2 - Track Name`, and the Monochrome hifi-api search endpoint is pickier about commas and dash separators than the other providers. With only Monochrome enabled, those punctuation-heavy queries could come back as "No results found" even though the track was on Qobuz and the same search worked after manually stripping punctuation. Monochrome now searches both the exact query and a punctuation-softened variant (`Artist1 Artist2 Track Name`), dedupes the Tidal results, and then runs the normal scorer. If the exact punctuation-heavy query throws a 503, the cleaned variant is still tried instead of abandoning the source. Manual search behaviour stays intact, and other providers keep their existing query path.
- **Default Monochrome hifi-api endpoint moved again, with fallback this time**: `https://api.monochrome.tf` is now returning a Render "Service Suspended" page (`x-render-routing: suspend-by-user`), so fresh installs and existing installs still on the old default are moved to a comma-separated endpoint list headed by `https://monochrome-api.samidy.com`, the working hifi-api endpoint currently referenced by the Monochrome frontend. MusicGrabber now treats `MONOCHROME_HIFI_API_URL` / the Settings field as a comma-or-newline separated list, tries candidates in order, and caches the first endpoint that answers for the rest of the process. Custom self-hosted hifi-api URLs are left as a single-candidate list unless you explicitly add more.

## v2.8.15 (2026-05-24)

### Changed
- **Full CSS overhaul, because the app had grown three different button styles and a Google Fonts dependency**: the stylesheet was 4,495 lines of one-bespoke-component-at-a-time, with ~15 distinct button classes, ~91 hardcoded hex colours outside the variable system, and Outfit/JetBrains Mono loaded from Google's CDN (blocking first paint and leaking a referrer). All gone. Elms Sans and SUSE Mono are now self-hosted as woff2 in `static/fonts/` (no network call, works offline). A proper design-token layer landed in `:root`: `--font-sans`, `--font-mono`, a 4px-based `--space-*` scale, `--radius-*`, and a single `accent-color` rule that turns every native checkbox, radio, and progress bar from browser-blue to the app's accent green. Form controls now inherit font and colour globally, killing the need for the 56 redundant `font-family: inherit` declarations sprinkled throughout the file. A shared `.btn` system with `.btn-primary` / `.btn-ghost` / `.btn-danger` variants and `.btn-sm` / `.btn-lg` / `.btn-block` size modifiers replaced the menagerie: 30+ bespoke buttons across Search, Bulk Import, Watched, Queue, Albums, Settings, auth screens, modals, the report dialog, the tag editor, and the floating Save bar now all use the same primitive. A shared `.input-action` wrapper unified the "input + button" pattern so Search, Watched, Bulk Import, ListenBrainz, and Watched Artists all use identical inside-button search-pill layouts at the same size, instead of the previous three different patterns. Visual hierarchy is now consistent: primary actions are filled green, secondary actions are outlined, destructive actions go red on hover, warning actions stay amber, and the compact cards inside Watched playlists keep their dense sizing via tokenised spacing. CSS dropped from 4,495 lines to 4,287 even after adding the new primitives, which is the universe's way of saying you can have nice things.

### Added
- **Retries and a Retry button for MusicBrainz lookups on the Albums tab**: artist search, album list, and tracklist fetches now retry up to three times on timeout/connection error/HTTP 429/5xx with a gentle 1s then 3s backoff (so we do not insult MusicBrainz's one-request-per-second rate limit on the way back up). When all retries fail, the endpoints return a proper 503 with a "MusicBrainz unreachable" message instead of pretending the artist/album does not exist, and the Albums tab now renders an actual Retry button next to the error so you can have another go without typing the artist name in again. Partial album lists from prolific artists are kept rather than thrown away if MusicBrainz dies mid-pagination, on the basis that some Bowie is better than no Bowie.
- **Preferred source for Bulk Import and Watched Playlists**: requested by Max S, who wanted Soulseek to be the primary indexer with YouTube as fallback, in the spirit of Sonarr's indexer priorities. There is now a "Preferred source" dropdown next to "Create M3U playlist" on the Bulk Import tab, and another next to the per-playlist source chips on each Watched Playlist card. Picking a source applies a heavy quality-score boost so it wins almost every close call against other indexers, but the strict-artist guardrails still get to veto a clearly-wrong match (so Soulseek does not get to ship "Despacito (live tribute karaoke)" just because it is preferred). Leaving the dropdown on "No preference" keeps the existing quality-score behaviour. The setting persists on watched playlists, so future refreshes honour it without re-selecting.

### Fixed
- **"Add to playlist" actually adds to the playlist now**: two adjacent paper-cuts were teaming up to swallow tracks. First, when retrying a missing track from the watched-playlist modal, the post-download M3U rebuild looked up the playlist via `bulk_imports`, which the missing-track retry path never populates. Result: `downloaded_at` got stamped (so the track vanished from the missing list), but the actual `.m3u` file on disk was never touched. The lookup now joins through `watched_playlist_tracks` directly, which covers both the refresh path and the retry path. Second, when using "Add to playlist" from the Results tab without a Playlists folder configured, the M3U append silently no-op'd, so the audio landed in Singles and no playlist file appeared anywhere. It now falls back to writing the `.m3u` at the Singles root, mirroring how bulk-import playlist M3Us behave. Reported by Aryan Ovalekar.

## v2.8.14 (2026-05-24)

### Fixed
- **Monochrome hifi-api swap, because of course it happened again**: the default hifi-api endpoint was pinned to `eu-central.monochrome.tf`, which is a single Render node, not a CDN. The owner has suspended that node, so every Tidal metadata lookup now comes back as a Cloudflare 503 ("Service Suspended"), which in turn breaks Monochrome search, Monochrome preview, and Tidal playlist fetch. The default has moved to the apex `https://api.monochrome.tf`, which sits behind Cloudflare and routes to whichever node is actually alive. A v4 database migration retires the dead node URL for anyone whose stored value still points at eu-central (custom self-hosted URLs are left alone). The httpx calls also now follow redirects, because the apex helpfully adds a trailing slash via 307.

## v2.8.13 (2026-05-22)

### Fixed
- **Beatport playlist folder names**: the Top 100 and genre chart pages were being named after Beatport's SEO tagline ("Beatport Top 100 Songs & DJ Tracks Music Downloads & Streaming") rather than anything useful. The folder name is now derived from the URL: `/top-100` gives "Beatport Top 100", `/genre/techno/6/top-100` gives "Techno Top 100", and so on. Named charts still use the og:title, which is the actual chart name.
- **Monochrome (Qobuz) proxy swap**: `qobuz.kennyy.com.br` went dark (502s on ISRC lookup, 401s on the download leg from upstream), which killed every Monochrome preview and download. The Monochrome frontend has switched to `qdl-api.monochrome.tf`, which speaks the exact same API and serves the same Akamai-hosted FLAC CDN. The default has moved with it, a v3 database migration retires the dead URL on existing installs (only when the stored value is still the kennyy host, so self-hosted overrides survive), and the URL remains user-editable in Settings for when this happens again. Because of course it will.
- **Bulk import playlist URL field now accepts Beatport and Tidal links**: the Watched Playlists field happily took them, but the same paste box on the Bulk Import tab was running a frontend allow-list that hadn't been updated since 2.8.11, so Beatport and Tidal URLs got bounced with "Unsupported URL" before the backend ever saw them. The backend supported them all along.

## v2.8.12 (2026-05-19)

### Added
- **Beatport playlists**: Beatport Top 100, genre charts, and editorial charts can now be watched or imported as playlists. Paste a URL like `https://www.beatport.com/top-100` or `https://www.beatport.com/genre/techno/6/top-100` into the Watched Playlists box and MusicGrabber will pull the track list straight from the page's server-rendered JSON. Shiny vinyl icon included.

### Fixed
- **Tidal playlist URLs without `/browse/`**: direct Tidal share links (`tidal.com/playlist/UUID`) were rejected as unrecognised. The regex now accepts both the `/browse/playlist/` and bare `/playlist/` forms.
- **Tidal playlist fetch actually works now**: replaced the dead Monochrome-proxy approach with a direct scrape of Tidal's embed player (`embed.tidal.com/playlists/UUID`), which server-renders the full track list as web components. Regex does the rest. Also now has its own `tidal.py` module.

## v2.8.11 (2026-05-17)

### Fixed
- **Monochrome is enabled out of the gate again**: the runtime default had drifted back to off, which meant fresh installs only searched YouTube, SoundCloud, MP3Phoenix, and zvu4no until the admin found the Search Sources toggle. The default is back to on, and the README now documents Monochrome/Qobuz as part of the standard source set with `SOURCE_MONOCHROME_ENABLED=false` available for anyone who wants to disable it.
- **Monochrome URL settings now survive a version-skip upgrade**: users who had Monochrome before it was removed (v2.6.6) and then jumped straight to v2.8.x found the Monochrome and Qobuz proxy URL fields blank in the database, because an empty string stored from the old install beat the new schema defaults. A DB migration (version 2) now seeds both URLs with the correct defaults for anyone whose DB has them missing or empty, while leaving intentional custom values alone.

## v2.8.10 (2026-05-16)

### Fixed
- **Monochrome top result now reliably the canonical studio album track**: the old scoring handed a +210 bonus to anything tagged HI_RES_LOSSLESS, so piano-cover albums and movie soundtracks could outrank the legitimate studio master purely on quality tier. The Tidal items also carry two excellent signals MusicGrabber was ignoring: a `version` field that explicitly names live/remix/demo/karaoke/muppet takes, and a `popularity` score that tracks how canonical a master is. Scoring now: trims the HI_RES bonus to a 15-point edge over LOSSLESS; applies version penalties for live, demo, karaoke, instrumental, remix, and tribute annotations (a bare "Remastered 2011" tag is kept neutral, because Tidal almost never carries the un-remastered original); applies album penalties for soundtrack, live, compilation, and karaoke albums; and adds a small popularity tiebreaker (up to +10). Each penalty has a query-aware waiver, so asking for "spawn soundtrack" or "live at wembley" or "karaoke" still surfaces the version you actually wanted. Searches for Come As You Are, Bohemian Rhapsody, Smells Like Teen Spirit, Thriller, and Wonderwall all top with the studio album; "crystal method - trip like i do" hits the Vegas master while "spawn soundtrack trip like i do" hits the Filter collab.

## v2.8.9 (2026-05-16)

### Fixed
- **Album-routed filenames now use album artist, not every credited track artist**: when MusicBrainz returned a track artist credit like `Luis Fonsi, Daddy Yankee, Justin Bieber`, album-routed files could inherit that whole artist string in the filename/folder even though the album artist was just `Luis Fonsi`. That confused Lidarr-style layouts expecting `Album Artist - Album - Track` naming. Album routing now prefers `album_artist` for folders and flat filename prefixes, while keeping the full credited artist list in the audio metadata where featured artists belong.

## v2.8.8 (2026-05-11)

### Added
- **End-to-end QA test (`tests/test_qa_e2e.py`)**: one slow test that drives the full acquisition flow: single download, small album, Spotify playlist import (3 tracks), watched playlist add, then a manual scheduled-refresh on that same watched playlist. It runs the lot once with Navidrome and Lidarr dupe checks switched off (so every track actually has to land), then re-runs the same actions with both back on to prove the library dupe check skips the second pass. The refresh step is the one that catches the Navidrome-sentinel regression specifically: it asserts the refresher queues zero downloads when every track is already present, which is exactly what the original Bug B fix has to guarantee. Cleans up jobs, files, and the watched playlist on the way out. Skips politely when Navidrome isn't configured or MusicBrainz can't find the test album. Lives behind the `slow` marker, so `tests/run_tests.sh --slow` opts in. Verified end to end in 3m26s against the test VM.
- **`dupe_skipped` count on `/api/bulk-import/{id}/status`**: counts jobs that completed via the "Already exists" short-circuit, separately from genuine fresh downloads. Until now there was no API-visible way to tell whether the library dupe check actually engaged, which made any test of it weak by construction. The QA test now asserts on this field directly.

### Fixed
- **Second Spotify fallback was still gated behind `sp_dc`**: the v2.8.8 fix that dropped the cookie requirement on the 95+ track browser fallback missed its sibling code path in `watched_playlists._fetch_spotify_playlist`. When the embed parser returned zero tracks (small public playlists where Spotify is no longer rendering the tracklist server-side), the code only tried the headless browser if `sp_dc` was set, so users without Spotify cookies hit a hard 422 even though the browser path could read the page just fine. Gate removed; the original "page structure may have changed" message is preserved as the final fallback so the diagnostics still make sense.
- **Watched playlists were rejecting every add with a 500 on older databases**: the v1 multi-user migration recreated the `watched_playlists` table from a hard-coded schema that quietly forgot the `custom_subdir` column, so on any DB that came through that migration the `INSERT` blew up with `table watched_playlists has no column named custom_subdir`. The migration now keeps the column on the way through, and a defensive `ALTER TABLE` runs after the migration so any database that already lost the column gets it back on next start.
- **Watched playlists were re-downloading every track on re-add**: `_has_local_track_file` was using `.is_absolute()` to filter Navidrome's dupe-check result, which threw away the sentinel return that means "exists but Navidrome real-path mode is off". The refresher then treated every track as missing and queued the lot again. Sentinel is now honoured the way it was always supposed to be: any non-None return from `check_navidrome_duplicate` blocks the re-download.
- **SoundCloud info-lookup errors were uniformly opaque**: yt-dlp could fail for half a dozen different reasons and every one of them rendered as "SoundCloud info lookup failed: provider rejected the request", because the catch-all branch in the error summariser was eating actual errors. New patterns for 404/410/geo/DRM/private-track land, the first real `ERROR:` line from yt-dlp is surfaced as a fallback, and the raw stderr now lands in the container log so we can diagnose without dragging the reporter through three rounds of "what does the log say".
- **Watched playlist add now shows the real server error**, not a cryptic "Unexpected token 'I' is not valid JSON" tantrum. The frontend was confidently `await response.json()`-ing a plain-text 500 body; it now checks `Content-Type` first and surfaces the actual status and message so you have a fighting chance of knowing what broke.
- **Spotify private and embed-blocked playlists now fall back to the headless browser** for both watched playlists and bulk imports. Previously the embed scraper would catch a 401/403 (or get back zero tracks) and immediately surrender, even though the Playwright path with your `sp_dc` cookie could have walked right in. The browser was already used for 95+ track playlists; it now also covers the cases where the embed never gets a foot in the door.
- **Bulk Import button now tells you when the headless browser is doing the work**. For large or private Spotify playlists the server can take 30 to 90 seconds to scroll the page; the button used to sit there saying "Fetching..." like a loading bar with no power. It now updates to "Spinning up browser..." and then "Scrolling playlist..." so you know it's earning its keep, not hung.
- **Large public Spotify playlists no longer cap at 100 tracks without cookies**. The 95+ track browser fallback was gated behind `sp_dc`, on the (outdated) theory that the headless browser couldn't load Spotify pages without a session. It can. The gate is gone, so a 200-track public playlist now actually returns 200 tracks even when you haven't pasted any Spotify cookies into Settings.

## v2.8.7 (2026-05-10)

### Added
- **Apple Music private playlist support**: add your `Music-User-Token` in Settings under Apple Music to import playlists from your personal library (`music.apple.com/library/...`). The web bearer token is still fetched automatically from Apple's public JS bundle; only the user token needs to be provided.
- **New song matching engine for Soulseek (used by bulk imports and watched playlists)**: Soulseek results are now scored by a path-aware confidence engine instead of being shoved through the YouTube-shaped scorer like cattle. Each path segment in the slskd filename (`Artist/Album/Track.flac`) is matched independently for artist, album, and title; SequenceMatcher does the fuzzy work so `Beyoncé` finds `Beyonce`, `Pig & Dan` finds `Pig&Dan`, and `Kraftwerk` survives a typo as `Kraftwork`. Word-boundary matching means `Muse` no longer wins inside a folder called `Museum Of Sound`. Results below the new `SLSKD_MATCH_CONFIDENCE_FLOOR` (default 0.55, env-tunable) are dropped rather than shipped as the least-bad option, and `Various Artists` / `VA` / `Unknown` folders are rejected outright. Heavy-handed inspiration from the open-source SoulSync project, adapted for our slskd specifics. This affects both bulk imports and watched playlist downloads, since both share the same search pipeline.
- **YouTube, MP3Phoenix, and Monochrome song matching now use the same confidence engine**: the YouTube scorer's old token-overlap title/artist block has been retired in favour of a unified SequenceMatcher-based confidence calculation. `Beyoncé` finds `Beyonce` (instead of `Beyonce` taking a `-12 artist_mismatch` for not having an accent); `Don't Stop` finds `Dont Stop` via the core-title fast path (instead of getting tripped up by the apostrophe); `Stay (Remix)` no longer silently merges with `Stay` because parens-stripping discarded the version word before the comparison. The classic YouTube channel suffixes `- Topic` and `VEVO` are stripped before artist matching so `BritneySpearsVEVO` reads as `Britney Spears`. The regex stack for live/cover/karaoke/copyright-dodge/official/VEVO/MusicBrainz-duration is preserved untouched, since those are real YouTube-specific signals worth keeping. MP3Phoenix and Monochrome ride the same scorer so they benefit automatically.
- **Version-aware similarity**: a search for `Stay` will no longer be politely satisfied with `Stay (Live in Tokyo)` or `Stay (R3hab Remix)`; remix/live/acoustic suffixes drop the score below threshold so the original mix wins. Remasters get a light penalty only, since they're the same recording wearing a slightly nicer coat.
- **Core-title fast path**: punctuation differences are no longer load-bearing. `Don't Stop` and `Dont Stop` short-circuit to high confidence as long as the artist segment also lines up, so apostrophes and stray hyphens stop sabotaging perfectly good matches.
- **Unit test suite for the matching engine**: 48 unit tests covering normalisation, version detection, junk-folder rejection, path-segment matching, CJK preservation, streaming-source confidence, and the title hard-gate. Runs as part of the fast suite.

### Fixed
- **Bulk Import refused private Apple Music library URLs**: the bulk import URL validator required a country code (`/us/`, `/gb/`, etc.) and rejected `music.apple.com/library/playlist/...` URLs outright, even though watched playlists happily accepted them. Validator now matches either the country-code or library form, consistent with the rest of the app.
- **Wrong-title results from the same artist were ranking too high**: searching `Machine Head - silver` was pulling up `ØUTSIDER`, `Circle The Drain`, and other Machine Head tracks above the right `Silver` matches because the artist match was overpowering the title penalty, especially for Monochrome HI_RES candidates whose +120 quality bonus could outweigh a soft title mismatch. Two fixes: a title hard-gate that scales confidence down proportionally when title similarity is below 0.4 (so a strong artist match cannot prop up an unrelated song), and a no-shared-token guard in the fuzzy similarity check that stops `silver` and `utsider` scoring 0.6 just because they happen to share s/i/e/r. Typos like `Kraftwerk` vs `Kraftwork` are unaffected because they sit above the typo floor (0.85 char ratio).
- **Right-title slskd matches were losing cross-source rankings**: the new slskd scorer's confidence-times-100 base was undershooting the YouTube/Monochrome 100-220 typical range, so a perfect FLAC 24-bit/192kHz match could sit below a Monochrome lossless candidate purely because the relevance scale was too small. Bumped the slskd confidence scale to ×200 so a perfect match earns 200 of relevance before quality, source-trust, and hi-res bonuses stack on top.
- **Wrong-title results from the same artist STILL ranked too high (round 2)**: the `match_mismatch` penalty at -90 was nowhere near enough to dominate Monochrome's +210 HI_RES quality bonus, so wrong songs by the right artist (`ØUTSIDER`, `Game Over`, `UNHALLØWED`) kept climbing the rankings on hi-res alone. Penalty bumps for poor and mismatched candidates have been cranked up: `match_poor` to -280, `match_mismatch` to -400, with intermediate tiers tightened. A wrong-song match cannot beat a right-song match no matter how lossless the wrong song is.
- **Metal track names with Ø, Æ, Þ, and friends were getting butchered by normalisation**: `ØUTSIDER` was collapsing to `UTSIDER` and `Mötley Crüe` to `mtley cre` because NFKD doesn't decompose those characters (the diacritic is part of the glyph, not a combining mark). Added an explicit Latin-extended mapping so `Ø/Æ/Œ/Þ/Ð/ß/Ł` fold to ASCII the way users actually expect.

## v2.8.6 (2026-05-09)

### Fixed
- **Filename inside the album subfolder still included the artist prefix**: even when routing correctly moved the file to `Rock/Creedence Clearwater Revival/Pendulum/`, the filename was `Creedence Clearwater Revival - Have You Ever Seen The Rain.flac` instead of `Have You Ever Seen The Rain.flac`. It now matches the singles convention: artist is in the folder, not repeated in the name. Track-number filenames (`5 - Title.flac`) also no longer include the redundant artist prefix once inside an artist subfolder.

## v2.8.5 (2026-05-08)

### Fixed
- **Playlist album routing missed Soulseek and MP3Phoenix sources**: the v2.8.4 fix for `Playlists/Name/Artist/Album/` routing only wired up the YouTube download path. Soulseek and MP3Phoenix both had the old `not playlists_dir` guard that skipped the routing call entirely, so tracks from those sources still landed flat. All three sources now route consistently.

## v2.8.4 (2026-05-06)

### Fixed
- **Album routing now applies inside playlist folders**: when "Auto-route to album folder" was on, tracks downloaded as part of a playlist stubbornly landed flat in `Playlists/Name/Artist - Title.flac`, ignoring the setting entirely. They now land in `Playlists/Name/Artist/Album/Title.flac`, consistent with how singles behave. Track numbers in filenames also work correctly here; the artist prefix is dropped from the filename since the folder structure already provides the context.
- **Spotify playlist parsing was broken**: Spotify quietly added `"contentRatings":{"labels":[]}` to each track object in their embed page. The non-greedy regex we were using to extract the `trackList` array stopped dead at the first `]` followed by `}`, so every fetch came back with exactly one track. The fix was to stop wrestling with regex and parse the `__NEXT_DATA__` JSON blob that Spotify already embeds for their own Next.js frontend, which gives us a clean, properly structured track list regardless of what nested arrays Spotify add in future. Old regex kept as a fallback.
- **Large Spotify playlists silently truncated with a confusing error**: playlists over 100 tracks would attempt the headless browser fallback, but Playwright was being identified as a bot and served a login wall rather than track listings. This produced a 30-second stall followed by a misleading "page structure may have changed" error. Two fixes: without a Spotify cookie the headless browser attempt is now skipped immediately (no point waiting) and a clear warning is shown instead ("add your Spotify cookies in Settings"); with a cookie it still works as before. The warning now surfaces in the GUI on both the Fetch Playlist and the Add Watched Playlist paths.
- **Playwright bot detection on Spotify**: added `--disable-blink-features=AutomationControlled` and masked `navigator.webdriver`, which is the main fingerprint Spotify use to distinguish headless browsers from real ones. Large playlists now fetch fully without needing a cookie at all.

## v2.8.3 (2026-05-04)

### Added
- **ALAC quality picker, MP3-style**: the Audio Format selector now grows an ALAC quality row when ALAC is the chosen format. "Lossless" gives you proper Apple Lossless inside an .m4a, exactly as before. The new 320k / 256k / 192k / 128k options quietly switch the encoder to AAC inside the same .m4a wrapper, for anyone who wants iPod-friendly files but can live with lossy audio. ALAC purists, look away. Yes, AAC-in-m4a is technically not ALAC; the UI is honest about it.

### Fixed
- **MP3 and Opus bitrate pickers never actually saved**: choosing "MP3 320k" in Settings appeared to take, but the PUT to `/api/settings` was silently discarding `mp3_bitrate` and `opus_bitrate` because neither field had ever been added to the Pydantic `SettingsUpdate` model. Same Pydantic-shrug-and-drop pattern that bit Monochrome in v2.8.1. The DB therefore kept the default of `v2` forever, so every "320 kbps" download came out at the libmp3lame V2 default of around 192 kbps. Fields added; settings now persist.
- **Even when MP3/Opus CBR did get through, yt-dlp dropped the bitrate**: the CBR path was passing `--audio-quality 320K` to yt-dlp, whose audio postprocessor runs the value through `float_or_none()`. The trailing "K" makes that return None, so ffmpeg quietly fell back to its libmp3lame default. VBR (`v0`/`v2`) escaped because those pass a bare digit. Stripped the "k" before handing the value off, so 320k now actually means 320k. Belt-and-braces with the Pydantic fix above.



## v2.8.2 (2026-05-04)

### Added
- **Singles-only mode**: a new Settings toggle that hides the Albums tab for everyone. Aimed at admins running MusicGrabber as a singles feeder for Navidrome who would rather their family did not poke at the album browser. Per-track auto-album routing still works if you want it, this just removes the dedicated Albums workflow from the UI.
- **New "Peon" user role**: the most stripped-down account level yet. Peons get Search, Bulk Import, Queue, Albums and Watched, and that's it. Settings and Stats tabs vanish entirely, and they inherit the admin's Navidrome, Soulseek, conversion format and other global settings whether they like it or not. The header Convert toggle and the watched-playlist Convert toggle are hidden too; the server force-overrides any incoming `convert_to_flac` flag for peons onto the global default, so a peon waving dev tools around still cannot change formats. Settings writes are blocked at the API level on top of all that, in case a curious peon tries to poke around with curl. The Clear Queue button is hidden for peons as well, since they have no business nuking shared queue state.

### Changed
- **Tidied admin-only Settings**: global, system-level rows in the Settings tab (skip duplicates, MusicBrainz/lyrics, search sources, audio format, AcoustID key, minimum bitrate, Monochrome URLs, the new singles-only toggle) are now hidden from non-admin users. Per-user bits — Navidrome/Jellyfin/Lidarr creds, Spotify cookies, notifications, your own subfolders, change password — are still right where you left them. Backend writes were already admin-gated; this just stops the cosmetic clutter for standard accounts.
- **Clear Queue is now role-aware**: standard users hitting Clear Queue only wipe their own completed/failed/stale jobs from the database, leaving everyone else's queue untouched. Admins still get the full nuke as before, which is handy when the shared queue has gone feral. Peons do not get the button at all, and the API rejects them outright for good measure.

### Fixed
- **Album downloads were silently failing every track**: the `album_track_locks` table was missing the unique index on `(release_mbid, track_title)` that the upsert query relies on, so every queued album track tripped over `ON CONFLICT clause does not match any PRIMARY KEY or UNIQUE constraint` before the download even reached the worker pool. Tracks ended up stuck on `failed` while the queue UI showed them as `queued`, which was a quietly maddening combination. Index added; existing installs pick it up automatically on next start.
- **Adding the second user left the user list looking unchanged**: going from one account to two flips MusicGrabber from open single-user mode into session-required mode. The current admin had no session, so the immediate refresh request was 401'd and silently ignored, leaving the newly-created user invisible until a manual reload and login. The create response now signals this transition so the UI clears state and bounces to login, same as it already did for the very first account.
- **Monochrome results without an ISRC are now hidden**: Tidal occasionally returns items with no ISRC, which means Qobuz cannot resolve them and we cannot preview or download them. Showing these as available was a polite lie, so the search now filters them out at source.
- **Monochrome metadata is no longer guesswork**: Tidal hands us the ISRC at search time, but the tagger was happily ignoring that and letting AcoustID's fingerprint roulette pick a remaster, a karaoke version, or whatever it fancied. The download flow now asks MusicBrainz for the exact ISRC first, and only falls back to fingerprint/text guessing if MB has never heard of the recording. Tags should now match the track you actually asked for, complete with the right album and year, instead of the 2011 Deluxe Anniversary Compilation Edition reissue.
- **Monochrome dead-ends now fall through instead of failing the job**: when the Qobuz proxy had nothing for a given ISRC at the requested quality (looking at you, Happy Baby Lullaby Collection), the job just gave up. Now Monochrome steps down through HI_RES_LOSSLESS, LOSSLESS and HIGH before declaring defeat, and if Qobuz genuinely has nothing at any tier, the alternate-source machinery picks up and tries YouTube/Soulseek/etc. like it does for other failure modes.
- **Watched playlist downloads could still land in Singles**: playlist/custom folder routing was not consistently winning over Singles auto-album routing. The yt-dlp path mostly had the right guard, but Soulseek downloads still chose the normal Singles layout first, and direct sources could be moved out of the playlist folder afterward by auto-album routing. Soulseek now resolves custom and Playlists destinations before falling back to Singles, and Soulseek/direct-source auto-album routing is skipped when the track is playlist-routed.
- **Initial watched playlist imports dropped custom destinations**: newly added watched playlists stored `custom_subdir` on the playlist row, but the immediately spawned bulk import did not receive it, so those first queued tracks could ignore the custom destination. Initial imports now pass the custom subfolder through, and bulk imports treat custom playlist destinations as playlist-routed even when the standard Playlists toggle is off.


## v2.8.1 (2026-05-01)

### Added
- **You can now add numbers to your tracks**: A feature that people kept asking for. You can now insert the track number at the beginning of the filename with a Settings toggle.

### Fixed
- **Monochrome toggle would not stick**: the new Monochrome source toggle and its hifi-api / Qobuz proxy URL fields were quietly being thrown overboard by the settings API because the Pydantic model never got the memo about v2.8.0. Added the fields properly, so the switch now stays where you put it.

## v2.8.0 (2026-05-01)

### Added
- **Monochrome source is back**: Monochrome.tf returned from the dead with Qobuz under the hood. MusicGrabber now searches the Tidal catalogue via hifi-api for metadata and ISRC, then pulls a direct FLAC from the Qobuz CDN. No DASH segments, no 30-second previews, no nonsense — just proper lossless audio. Hi-res (24-bit/192 kHz) and standard FLAC supported. Enable via Search Sources toggle in Settings.
- **Hover preview for Monochrome**: the CDN URL is resolved server-side and streamed to the browser, so you can audition a track before downloading it.
- **Tidal playlist support restored**: `tidal.com/browse/playlist/UUID` URLs now work in the watched playlist importer.
- **Monochrome settings section**: configurable hifi-api URL and Qobuz proxy URL for anyone running self-hosted instances.

### Changed

## v2.7.1 (2026-04-29)

### Added
- **Soulseek for watched playlists**: watched playlist source chips now include Soulseek when the global Soulseek toggle is on, and watched playlist imports can queue real slskd download jobs with the required username, filename, and size fields.
- **Soulseek ranking boost**: Soulseek results now get a source-trust bonus, with extra lift for lossless and 24-bit files, and can contribute up to six candidates in merged searches. Properly shared FLACs should now beat lossy web sources when the title/artist match is solid.

### Changed
- **Local Docker Soulseek downloads mount**: the local `docker-compose.yml` now mounts `/mnt/music/downloads` as `/downloads` and sets `SLSKD_DOWNLOADS_PATH=/downloads`, so local testing uses the same shared NAS landing folder as slskd.

### Fixed
- **ListenBrainz Weekly Exploration stayed on the old week**: ListenBrainz can keep the previous weekly playlist visible after publishing the new one. MusicGrabber was re-resolving correctly, then exact-matching the stale old title and choosing it again. Stale weekly playlists now pick the newest matching playlist family, so `Weekly Exploration` advances to the current generated week.
- **slskd incomplete-path race**: slskd can report a local path under `/downloads/incomplete/...` and then move the file to the completed downloads folder before MusicGrabber copies it. MusicGrabber now checks the completed equivalent path before failing, so downloads do not die because the file finished too quickly.
- **NAS rename failures after Soulseek downloads**: some SMB/NAS mounts can return `Errno 5` while MusicGrabber renames a fully downloaded Soulseek file from the source filename to the cleaned library filename. Completed-file moves now fall back to copy-and-verify before failing the job, so files do not get stranded under the uploader's track-numbered name.

## v2.7.0 (2026-04-28)

### Added
- **Soulseek/slskd downloads are now properly wired up**: MusicGrabber now preserves the slskd file size from search results and sends it back when queueing downloads, which stops slskd rejecting MusicGrabber-created transfers as expected-size-zero nonsense. Old Soulseek jobs without a stored size do a fresh search before retrying.
- **Soulseek source toggle**: Soulseek is now disabled by default and has an explicit Search Sources toggle in Settings. Configuring slskd URL, username, and password no longer enables Soulseek by itself. Use the UI toggle or `SOURCE_SOULSEEK_ENABLED=true`.
- **slskd downloads path warning**: the Settings tab now calls out that credentials are enough for search, but downloads need slskd's completed-downloads directory mounted into MusicGrabber. The slskd connection test also checks that the configured downloads path is visible.

### Fixed
- **MusicGrabber could not find completed slskd downloads**: slskd can store completed files under album/source folders instead of the original Soulseek user path. MusicGrabber now searches the configured downloads root recursively after the stricter lookup fails, so completed downloads can be imported, tagged, and moved into the library.
- **Numeric slskd transfer states**: newer slskd responses can expose transfer states numerically. MusicGrabber now normalises those states before deciding whether a download is queued, in progress, succeeded, or failed.

## v2.6.6 (2026-04-26)

### Removed
- **Monochrome/Tidal source**: Monochrome shut down on 25 April 2026. Pour one out, it was genuinely the best source we had, giving us real lossless FLAC from the Tidal CDN. All associated code has been removed, including the API client, mirror pool, DASH manifest fetching, preview proxy, Tidal playlist import, and the cover art fallback that hit the Tidal image CDN. YouTube, SoundCloud, MP3Phoenix, and Soulseek remain.

### Added
- **zvu4no search source**: `zvu4no.org` is now wired in as a first-class source with `ZV` result badges, per-source settings, watched-playlist source chips, and `SOURCE_ZVU4NO_ENABLED` env override support. It scrapes the server-rendered `/tracks/<query>` pages, scores results through the existing matcher, and contributes up to four candidates to merged "All" searches. Direct `data.zvu4no.org` MP3 URLs mean hover/mobile preview works without yt-dlp, and downloads go through the same integrity, metadata, lyrics, duplicate, fallback, and format-conversion flow as MP3Phoenix. `TIMEOUT_ZVU4NO_DOWNLOAD` controls the stream timeout.

### Fixed
- **ListenBrainz "Created for You" playlists duplicating each week**: re-adding a ListenBrainz username after LB had rotated to a new week created fresh watched playlist rows for every playlist instead of recognising the ones already being watched. The dedup check only compared URLs, and LB issues a brand-new UUID each week, so the old URL never matched. It now falls back to a name-prefix + username check ("Weekly Exploration for karl%"), which catches the rotation and skips the duplicate.
- **Retry/alternate paths losing custom subfolder routing**: when a download retried due to an integrity failure, duration mismatch, or source alternation, the `custom_subdir` playlist folder was silently dropped, so retried tracks landed in the wrong place. The three retry branches in `process_download` now carry it through. The "Retry missing track" and "Queue candidate" endpoints for watched playlists also now read and forward `custom_subdir` from the playlist row.


## v2.6.5 (2026-04-19)

### Fixed
- **Monochrome instances serving 30-second preview clips**: some instances in the mirror pool have degraded Tidal subscriptions and hand out `trackPresentation: PREVIEW` manifests instead of full tracks. MusicGrabber now checks this field on every manifest response and skips any instance that returns a preview, trying the next healthy instance. If no instance can provide a full track, it falls back to YouTube as usual, rather than quietly saving a 30-second clip to your library.

## v2.6.4 (2026-04-17)

### Changed
- **Monochrome mirror pool**: search, track info, Tidal playlist import, and playback manifest fetches now use a rotating pool of currently healthy Monochrome-compatible instances instead of relying on a single API host. `MONOCHROME_API_URLS` can override the full pool, while `MONOCHROME_MANIFEST_URLS` can override just playback manifests
- **Monochrome DASH retry path**: when a Monochrome manifest is fetched successfully but the signed DASH URL fails in ffmpeg with a transient CDN-style error, MusicGrabber now retries briefly, then fetches a fresh manifest from another instance before falling back to other sources. Corrupt/truncated Monochrome downloads also retry with a different manifest instance

## v2.6.3 (2026-04-15)

### Fixed
- **Retried watched-playlist jobs landing in Singles instead of the playlist folder**: when a job belonging to a watched playlist was retried via the queue Retry button, Force Accept, or accepting a mismatch, the playlist routing context (`playlist_name`, `use_playlists_dir`) was never restored. The job row doesn't store these, so the retry went to Singles, the next refresh found the file there and stopped trying to re-download, but the M3U never included it because it was looking in the wrong folder. The retry path now looks up the playlist context via `watched_playlist_tracks` (for manually-queued candidates) or `bulk_import_tracks` (for normal refresh jobs) and passes it through correctly

## v2.6.2 (2026-04-12)

### Fixed
- **Album downloads could wrongly fail retried tracks as duplicates**: when an album track failed and was retried, the transient `skip_dupe_check` flag was lost, so the retry could find the artist/title in your Singles folder and bail out instead of downloading the album version. Album track state is now persisted in a new `album_track_locks` table: the lock is created when the track is queued and cleared only when the file lands on disk. Any retry, regardless of how it was triggered, consults the lock and skips dupe checking for the duration

## v2.6.1 (2026-04-07)

### Added
- **Configurable MP3 and Opus quality**: the Settings tab now shows a quality sub-row when MP3 or Opus is selected. MP3 can be set to LAME VBR V2 (~192k, default), V0 (~245k), or fixed CBR 320k/256k/192k/128k. Opus can be set to 320k (default), 256k, 192k, 128k, or 96k. Both settings respect `MP3_BITRATE` and `OPUS_BITRATE` env vars, which lock the UI field in the usual way. Default behaviour is unchanged, so nobody gets a surprise downgrade

### Fixed
- **Apple Music playlists truncated at ~300 tracks**: the old HTML scraping path only saw the tracks Apple server-renders into the initial page, so large playlists were silently chopped off. MusicGrabber now loads the public Apple Music page, extracts the current web bundle URL, pulls the web MusicKit bearer token from that bundle, and paginates through Apple's `amp-api` track endpoint directly. Falls back to the server-rendered HTML scrape if the API path fails, so short public playlists still work instead of erroring out.

## v2.6.0 (2026-04-07)

### Added
- **Dedicated track tag editor modal**: queue items no longer open an inline tag form inside the constantly-refreshing queue card. `Edit Tags` now opens a proper modal with artist, title, album, album artist, year, and track number fields, plus a filename preview and reset/save actions. This keeps focus stable while the queue continues updating underneath
- **MusicBrainz-assisted tag guessing**: the tag editor now has a `Guess from MusicBrainz` button that can fill in album, album artist, year, and track numbering for completed downloads before saving
- **`Guess Again` candidate cycling**: repeated MusicBrainz guesses now walk through the next available candidate release instead of returning the same first match every time. Useful when MusicBrainz has multiple plausible releases and the first one is close-but-wrong

### Changed
- **All-source search now uses per-source merge caps**: merged searches no longer give every source the same tiny contribution limit. Monochrome/Tidal can now contribute up to 10 scored candidates into an `All` search, YouTube up to 6, and the noisier sources stay tighter. This gives lossless results more room to compete without letting every source flood the merged ranking

### Fixed
- **Queue refresh could kick you out of tag editing**: the original inline queue editor was being destroyed on each poll/render cycle, which closed the form and stole focus mid-edit. Moving editing into a detached modal fixes that properly instead of playing DOM whack-a-mole
- **MusicBrainz guesses could prefer promo/sampler releases over the real album**: release scoring now penalises titles like `Extracts from ...`, `sampler`, `advance`, and similar promo-style junk, so tracks are less likely to land on teaser releases with bogus track counts instead of the proper album
- **Monochrome scoring wrongly penalised correct VIP/remix results**: when a Spotify track has a dash-separated variant in the title (e.g. "Paro House - Luciid VIP"), the Tidal result with the same content in parentheses ("Paro House (Luciid VIP)") was getting a -110 variant penalty, leaving a completely different wrong track to win. The penalty is now skipped when the bracketed content is already present in the query
- **Multi-artist watched imports refused perfectly good Monochrome results**: for tracks credited to multiple artists ("OGUZ, Nyctonian"), the artist check was normalising the whole comma-separated string and requiring it verbatim in the result. Tidal/Monochrome typically credits only the primary artist, so it never matched. The check now splits on commas and passes if any individual artist is mentioned
- **AcoustID could overwrite correct metadata with a low-confidence cover version**: a recording with only a title match (score 9/18) was enough to override the downloaded file's artist field. "Killing in the Name" would match any cover version, including an obscure German band, which then caused the mismatch check to trash the file. The minimum acceptable AcoustID match score is now 10, requiring at least an artist match to accept a metadata override
- **Label/distributor channel names leaking into the artist field**: channels named "Premiere Eczko" or "Monstercat Silk" were being used verbatim as the artist when no "Artist - Title" pattern could be found in the video title. Common distributor prefixes are now stripped from channel names before using them as the artist fallback
- **Database lock crashes during large playlist imports**: the DB connection pool was exhausted under heavy concurrent load (bulk import worker + multiple download threads), causing new connections to pile up and fight over SQLite's single write lock. The pool now blocks briefly on checkout instead of spawning unbounded connections, and the pool size is bumped from 5 to 8
- **Watched playlist mismatch checker rejected valid VIP/variant tracks**: `Paro House - Luciid VIP` (Spotify dash-form) vs `Paro House (Luciid VIP)` (Tidal bracket-form) failed the title match because `vip` was not a recognised remix suffix word, and single-word variant labels like `TechnoBack` had no path to acceptance at all. `vip` is now a recognised suffix, and any single-word trailing variant is accepted as a bracket equivalent
- **Watched playlist mismatch checker rejected tracks with "Original Mix" suffix**: Spotify often appends `- Original Mix` to track titles; Tidal omits it. The normaliser now strips `original mix`, `extended mix`, `club mix`, and `vip mix` dash-suffixes before comparing
- **Monochrome duration mismatch now falls back to another source**: when Tidal serves a shorter radio edit or alternate version that fails the MusicBrainz duration check, the download was just marked failed and trashed. It now triggers the same cross-source fallback as a geo-restricted track, so YouTube or mp3phoenix get a chance to find the right version

## v2.5.6 (2026-04-05)

### Added
- **SoundCloud playlists**: paste a SoundCloud sets URL (`soundcloud.com/username/sets/playlist-name`) or likes page (`soundcloud.com/username/likes`) into the watched playlists form and it works like any other platform. Fetches via yt-dlp, same as YouTube playlists. Full append/mirror sync, M3U generation, and per-playlist source selection all included, because it seemed rude to add a platform and leave bits of it out. Importing a SoundCloud playlist automatically pre-selects SoundCloud as the download source, since the tracks are already right there and there is no good reason to go rummaging through Monochrome instead

## v2.5.5 (2026-04-03)

### Fixed
- **Duplicate detection missed auto-routed album folders**: when a single had already been auto-filed into `Singles/Artist/Album/` or `Albums/Artist/Album/`, a later duplicate check only looked in the flat artist folders and failed to see it. Re-downloads could then slip through and create a second copy. Duplicate scanning now checks one level deeper in those artist folders so auto-routed tracks are found properly
- **Soulseek "Add to playlist" didn’t actually finish the playlist job**: Soulseek downloads were not receiving `playlist_name` or `use_playlists_dir` from the API layer, so the duplicate-skip path could not mark the track as "added to playlist" and successful downloads never appended to the physical `.m3u`. Soulseek now follows the same playlist-aware flow as the other sources
- **Per-user playlist M3Us ignored custom playlist folders**: `_append_to_physical_m3u()` looked up the playlists directory without `user_id`, so user-specific `playlists_subdir` settings were invisible and M3U updates could land in the wrong place. All call sites now pass `user_id`, and the helper resolves the correct per-user playlists directory consistently across sources
- **Bulk-created playlist M3Us could still use the global folder in multi-user setups**: the bulk playlist builder still resolved `playlists_subdir`, `singles` paths, and duplicate checks without `user_id`, so rebuilt/import-generated `.m3u` files could be written against the wrong user's library even after the per-track append path was fixed. Bulk playlist generation now stays in the owning user's directory scope all the way through

## v2.5.4 (2026-03-30)

### Fixed
- **Monochrome downloads broke after the playback endpoint changed**: search and info still worked, but the legacy `/track/` API path now returned `403 {"detail":"Upstream API error"}` for many tracks. MusicGrabber now uses Monochrome's newer `/trackManifests/` playback endpoint with instance rotation and signed DASH manifests, matching how the website itself fetches streams. Regional instance hosts are tried automatically, so a rate-limited instance no longer takes the whole source down
- **Monochrome hover preview broke after the same playback change**: the new Monochrome API returns signed DASH MPD manifests, which browsers cannot play directly in a plain `<audio>` element. Preview now runs through a short server-side ffmpeg transcode to an MP3 preview stream, so hover-to-play works again
- **Watched playlist source chips forgetting their state**: the per-playlist source toggles (YT, PX, SC, MO) were saved to the database correctly, but on first page load the UI forgot which ones you'd turned off and showed everything as enabled. The cached source list wasn't ready when the chips first rendered, so the re-population step read an empty DOM and cheerfully defaulted to "all on". Chips now carry their saved preference as a data attribute, so the re-render uses the real value instead of optimistically guessing
- **Watched playlists occasionally downloading the wrong song**: when no search result mentioned the expected artist, the watched playlist importer shrugged and grabbed the top-scoring result anyway, which could be a completely different track. About 2% of a large playlist could end up as strangers. Now fails the track with a descriptive error (including what it nearly downloaded) so you can retry from the Missing panel with a manual search, rather than discovering months later that your chill playlist has a death metal interloper
- **Monochrome/Tidal CDN 403 errors killing downloads instantly**: once a valid Monochrome manifest is fetched, the downstream Tidal CDN can still throw the occasional 403/429 (rate limit, geo hiccup, or expired edge token). The DASH download path now retries with backoff before giving up instead of dying on the first wobble

## v2.5.3 (2026-03-28)

### Added
- **Trash bin**: deleting a file now moves it to a trash folder under `/data/` instead of permanently nuking it. Restore files from the bin to skip re-downloading, or empty it to reclaim space. The Queue tab gets a new Trash Bin section at the bottom (hidden when empty) with per-file Restore and Delete buttons, plus an "Empty Trash" button for the brave. Directory structure is preserved in the bin, so restores land exactly where the file came from. Files that fail mismatch or duration checks also land in the trash now, so you can listen and decide before they vanish
- **Play button on queue cards**: completed downloads now have a play button in their expanded details so you can preview what actually downloaded without hunting for the file. Toggle on/off, uses the same audio player as search previews
- **Play button on trash items**: listen to trashed files before deciding whether to restore or permanently delete them. Particularly handy for mismatch rejects where the track might actually be fine

### Fixed
- **"Database is locked" during bulk imports**: the new search decision recording was opening a second DB connection inside the bulk import loop, fighting the existing connection for the write lock. Moved the insert into the same transaction so everything commits together without contention
- **Trash bin inaccessible on mergerfs/FUSE music volumes**: the trash directory was created inside the music volume, which on FUSE-based setups (mergerfs being the main culprit) returned EINVAL on directory listing despite correct permissions. Trash now lives under `/data/` alongside the database, well away from temperamental mounted filesystems
- **ListenBrainz "Created for You" playlists stuck on old UUID after rotation**: playlists added before the auto-re-resolution feature was introduced had no username stored, so the rotation logic was silently skipped and the playlist errored on every refresh. The refresh now self-heals by extracting the username from the playlist name and persisting it, then immediately re-resolves to the current week's playlist

## v2.5.2 (2026-03-24)

### Added
- **Download progress stages**: the queue now shows what each download is actually doing instead of just "downloading" until it finishes. Cycles through stages like "Fetching info", "Downloading audio", "Looking up metadata", "Tagging file", "Fetching lyrics", etc. Updates every 3 seconds via the existing poll, so you can see at a glance whether a track is stuck on MusicBrainz or cheerfully converting to FLAC
- **Concurrent downloads setting**: configurable in Settings (1-10, default 3) or via `MAX_CONCURRENT_DOWNLOADS` env var. Controls how many bulk import / watched playlist downloads run simultaneously. Previously hardcoded to 3. Careful on bot-rejection though!
- **"Why this result?" scoring rationale**: failed and problematic queue items now show a "Why this result?" link that explains exactly why the scorer picked one candidate over its rivals. Shows the winner's score breakdown in plain English (e.g. "Official channel +40", "Live/session penalty -180") plus the top 3 runners-up for comparison. Includes a raw scoring expander for the technically curious and a clipboard copy button for easy sharing. Only recorded for automated searches (bulk imports, watched playlists, watched artists), not manual picks

### Fixed
- **Track numbers now tagged on singles**: single track downloads were leaving the TRACKNUMBER tag empty, which made Beets and other library managers grumpy during lookups. MusicBrainz already returned track position data, we just weren't using it. Now resolved with a priority chain: explicit album context wins, then existing file tags (Tidal/Monochrome FLACs arrive with track info baked in), then MusicBrainz lookup as a fallback. Existing tags are never overwritten by a MusicBrainz guess
- Creating the first user account now forces a browser refresh and clears local storage, so the login page appears immediately instead of leaving you staring at a locked UI
- User management buttons (Remove, Force reset) and album artist Select button now actually work; `jsStr()` was producing double-quoted strings inside double-quoted HTML attributes, which the browser decoded into a syntax error and silently broke all three buttons
- **Cross-playlist duplicates no longer trigger false mismatches** (GitLab #38): when a track appeared in two watched playlists, the second playlist's download would find the file via duplicate check (correct) but then run the mismatch comparison against the original download's metadata (incorrect), failing because Spotify and Monochrome disagree on punctuation. Duplicate-skip paths now bypass the mismatch check entirely, since the file was already verified when it was first downloaded
- **Watched playlists were still getting a bit too excited about live versions**: obvious live uploads were already penalised, but not hard enough, and the regex was still letting a lot of stagey branding stroll past in sunglasses. The shared scorer now hits live/session-style results much harder unless the query explicitly asks for one, catching things like `Tiny Desk`, `KEXP`, `Mahogany`, `COLORS`, `Radio 1`, and `From The Basement` across YouTube, Monochrome, MP3Phoenix, SoundCloud, and Soulseek. Watched-track matching also stops pretending `live` is harmless title fluff, so a concert version no longer gets waved through as the plain studio track

## v2.5.1 (2026-03-22)

### Added
- **Force download for mismatched watched tracks**: the mismatch log in the Stats tab now has a "Force Download" button on each row. If YouTube's idea of an artist name doesn't quite match Spotify's but you know it's the right track, hit the button and it'll re-queue the download with the name check disabled. The mismatch record is cleared once accepted, so your log stays tidy. The same button also appears on the queue card itself when a mismatch error is shown, so you don't have to go hunting through the Stats tab.

### Improved
- **Static asset cache-busting**: CSS and JS files now include the version number in their URL (`style.css?v=2.5.1`), so browsers automatically fetch fresh files after an update. No more Ctrl+Shift+R to see new features.

### Fixed
- **Opus files not converted to MP3 (or other target format)**: when yt-dlp's built-in format conversion failed mid-stream (ffmpeg post-processor error), the recovery path would salvage the raw Opus file and call it done, ignoring the user's chosen audio format entirely. A new post-download format enforcement step now catches any file that survived in the wrong container and converts it properly. If ffmpeg still refuses, the original file is kept rather than losing the download.
- **MP3Phoenix ignoring audio format setting**: MP3Phoenix downloads always converted to FLAC regardless of what you'd set in Settings. If your format was MP3, it would pointlessly transcode MP3 to FLAC (lossy-to-lossless, worst of both worlds). Now respects the `audio_format` setting like every other source.
- **Force Download failing for old mismatches**: if the original download job had been cleaned up (via Stats reset or cleanup), the Force Download button would fail with "Original job not found". Now creates a fresh job from the mismatch record's expected artist/title, re-links the watched playlist track, and queues the download as if nothing happened.
- **Windows setup script failing on some networks**: the `curl` command downloading Docker Desktop could fail with a certificate revocation check error (`0x80092012`) on Windows 10 machines behind corporate proxies or restrictive networks. Added `--ssl-no-revoke` to skip the CRL check.


## v2.5.0 (2026-03-21)

### Added
- **Spotify Liked Songs support**: paste `https://open.spotify.com/collection/tracks` into the playlist import or watched playlists field and it just works, provided you've set your `sp_dc` cookie in Settings. Works for one-off imports and watched playlists alike. If your cookies expire while a liked songs playlist is being watched, you'll get a notification and a clear error on the card telling you to update them.
- **Spotify cookie expiry notifications**: when a watched Spotify playlist (liked songs, private playlists, anything needing `sp_dc`) fails due to expired cookies, a notification fires via your configured channels (Telegram, email, Apprise, etc.) so you don't have to discover it manually.
- **Proper album cover art for all downloads**: previously, only album-mode downloads got real album artwork from Cover Art Archive. Singles, playlist tracks, Soulseek, and MP3Phoenix downloads were stuck with whatever YouTube video thumbnail yt-dlp could scrounge up (or nothing at all). Now every download runs through a four-source fallback chain: Cover Art Archive (MusicBrainz release), Tidal CDN (Monochrome tracks), iTunes Search API, and Deezer API. No API keys needed. If all four come up empty, the yt-dlp thumbnail is preserved as a last resort rather than leaving the file naked.
- **Search-to-album shortcut**: when you search for "Artist - Title", MusicGrabber now looks up the artist's discography on MusicBrainz in parallel (no extra wait) and shows an "Artist and Album" chip in the Related Searches box if it finds a matching album. Click it and you're taken straight to the Albums tab with the tracklist loaded, ready to download. Monochrome/Tidal results also get a clickable album name in the result metadata line for the same one-click album browsing.
- **Configurable download/conversion timeouts**: the yt-dlp download timeout (default 5 min), ffmpeg conversion timeout (default 2 min), and MP3Phoenix download timeout (default 2 min) are now overridable via `TIMEOUT_YTDLP_DOWNLOAD`, `TIMEOUT_FFMPEG_CONVERT`, and `TIMEOUT_MP3PHOENIX_DOWNLOAD` env vars. If long DJ mixes or symphonies were producing broken files, bump these up in your docker-compose.

- **Paginated download queue**: the queue now fetches the last 250 jobs but displays them 10 at a time with prev/next page controls, so it doesn't turn into an endless scroll of regret. The "Downloadable to Device" list is also paginated to 15 per page.

### Improved
- **Music video artist extraction**: the Playwright browser scraper now properly handles Spotify music video rows by scanning for the bullet (•) separator to find the real artist, instead of relying on fixed array positions that broke when explicit "E" markers shifted things around. Fixes tracks silently dropped from import when all your liked songs happen to be music videos.
- **Monochrome cover art survives format conversion**: Tidal cover art was being embedded directly into the FLAC via mutagen before any format conversion, but when converting to MP3/Opus, the re-tagging step had no art bytes to work with. Cover art now flows through the same `album_art_bytes` path as every other source, so it persists through transcoding.

### Fixed
- **Artist search case sensitivity**: searching for "SiR" in the Albums or Watched tabs now correctly floats an exact case match to the top of results, rather than treating it identically to "Sir". Exact case wins, case-insensitive match is second, MusicBrainz relevance score breaks remaining ties.
- **Invisible toast blocking settings bar buttons**: the toast notification (the little green "Settings saved" popup) was sliding back down to `bottom: 0` when hidden, sitting invisibly on top of the Save Settings, Ko-fi, and Release Notes buttons and swallowing clicks like a polite ghost. Added `pointer-events: none` when hidden so it stops being a nuisance.
- **File permissions not applied to artist/album directories**: `set_file_permissions` was only fixing the files themselves, leaving parent directories (artist folders, album folders) at the container's default umask (typically `0o755`). On NAS/SMB shares this meant the files inside were fine but the folder they sat in wasn't. The function now walks up and fixes every directory between the file and the music root, and directories always get `0o777` because you need execute bits to actually enter a folder.
- **Session/download-token expiry could overshoot by up to a day**: `create_session()` and `create_download_token()` were storing expiry timestamps in Python's ISO format (`2026-03-20T01:00:00+00:00`) but comparing against SQLite's `datetime('now')` format (`2026-03-20 01:00:00`). The `T` won the text comparison on expiry day, so tokens stayed valid longer than intended. Now stores timestamps in SQLite's own format so expiry checks are actually chronological.
- **Non-admin users could set `music_dir` to any path on the server**: in multi-user mode, `music_dir` was in the user-writable settings list, meaning a regular user could redirect their downloads (and file deletions) anywhere the container could write. Removed from user-scoped settings; only admins can change it now.
- **SQLite foreign key cascades were declared but never enforced**: `PRAGMA foreign_keys` was never enabled, so `ON DELETE CASCADE` constraints on sessions, download tokens, user settings, and watched playlist/artist tracks were decorative. Orphan rows could accumulate silently. Now enabled on every connection.
- **Integration test endpoints (Navidrome, Jellyfin, Lidarr) accepted arbitrary URLs from non-admin users**: in multi-user mode, any authenticated user could use the test buttons to make the server send HTTP requests to caller-supplied URLs, effectively an SSRF probe. Non-admin users can still test their saved settings, but body-supplied URLs are now ignored unless you're an admin.

## v2.4.6 (2026-03-18)

### Added
- **Configurable downloaded file permissions**: new setting (admin-only) lets you choose between `666` (the default, rw for everyone) and `777` (rwx for everyone, for NAS/share setups where root-owned files refuse to behave). Validated at both the Pydantic model and route level so no funny business gets through. Also overridable via `FILE_PERMISSIONS` env var for the docker-compose crowd.
- **Sub-hourly watched playlist and artist intervals**: refresh intervals now go down to every 30 minutes (also hourly, 6h, 12h), not just daily/weekly/monthly. The backend already supported arbitrary values; the UI just hadn't exposed them. Bot-ban risk is on you if you go nuts with it.
- **Failed-job tracks resolved by disk check on next refresh**: if a watched playlist track has a failed job but the file actually landed on disk via another route (manual download, different playlist sync, etc.), the next refresh now spots it, stamps `downloaded_at`, and removes it from Missing. Previously it would sit in Missing forever despite the file being right there.

### Fixed
- **Skip duplicates toggle not saving**: `skip_dupes` (and `navidrome_dupe_check`) were missing from the `SettingsUpdate` Pydantic model, so every save silently discarded them. The toggle looked like it worked, then cheerfully forgot everything the moment you refreshed.
- **Lidarr config not surviving restarts**: same root cause as above — `lidarr_url` and `lidarr_api_key` were also missing from `SettingsUpdate`, so Lidarr credentials were quietly dropped on every save and lost on restart.
- **"Will be overwritten" warning shown for Append playlists**: the playlist routing selector showed a sync-overwrite warning for every watched playlist, including ones on Append mode that don't get cleared on sync. The warning now only appears for Mirror playlists, where it actually applies. Text updated to reflect what Mirror mode actually does.
- **Remaining watched card buttons using inline `onclick`**: Refresh, Missing, and Pause/Resume on both playlist and artist cards were still using inline handlers, which break on certain proxy setups and are generally fragile. All six migrated to the delegated `data-action` pattern used by the other buttons.


## v2.4.5 (2026-03-17)

### Added

### Fixed
- **Non-Docker installs: `/music` fibs**: if your real music folder was somewhere else, Settings kept pretending everything lived under `/music`. It now tells the truth.
- **Reverse-proxy subpaths**: MusicGrabber can now behave itself under a prefix like `/musicgrabber` instead of hardcoding `/static` and `/api` off the domain root. Set `ROOT_PATH=/musicgrabber` and have your proxy strip that prefix before forwarding.
- **Watched tab buttons had a little lie down**: `Search`, `Retry`, `Tracks`, `Copy URL`, `Delete`, and friends could go dead thanks to brittle inline button handlers, especially when a track name arrived with apostrophes. They now use safer click handling, and manual `Search` jumps back to Results with the right playlist already selected.
- **Duplicate tracks can stop sulking in the wrong playlist**: if a song already exists in one playlist folder, duplicate checking now spots it for the next playlist too, reuses the on-disk path, and stops calling the second playlist entry a failure.

### Changed
- **Near-match scoring got a bit less dense**: collaborator order and `feat.` / `with` wording now get more benefit of the doubt, so close matches are less likely to be ignored just because the artist names lined up differently.

## v2.4.4 (2026-03-16)

### Fixed
- **Non-admin users blocked from testing their own connections**: the Navidrome, Jellyfin, Lidarr, Apprise, YouTube cookies, and Spotify cookies test endpoints all incorrectly required admin privileges. Non-admin users could save their settings fine, but every "Test" button returned 403. The endpoints now work for all users, reading from the requesting user's own settings rather than the global store.

## v2.4.3 (2026-03-16)

### Added
- **Route album-routed singles to the Albums folder**: a second opt-in setting "Route to Albums folder" (requires auto-album routing to be enabled) sends MusicBrainz-matched singles to `Albums/Artist/Album/Track.ext` instead of `Singles/Artist/Album/Track.ext`. For anyone who wants that classic `Artist/Album/Track` layout without touching the Singles subfolder path. Environment variable: `AUTO_ALBUM_SINGLES_USE_ALBUMS_DIR`.
- **Auto-album routing for singles**: opt-in setting (off by default) that moves a successfully downloaded single into `Singles/Artist/Album/Track.ext` when MusicBrainz returns an album match. Track number and total are tagged automatically. Falls back silently to the normal `Singles/Artist/` layout when MB has no match (new releases, obscure tracks, etc.). Wired into YouTube, Soulseek, MP3Phoenix, and now Monochrome (using Tidal's own album metadata).

### Fixed
- **Album routing ignored for Monochrome downloads**: Monochrome has always had Tidal's album title available, but the auto-album routing call was simply never wired into the Monochrome path. Fixed.
- **MusicBrainz picking radio compilations as the canonical album**: all three MB lookup paths (`_extract_recording_metadata`, `_lookup_musicbrainz_by_id`, `lookup_musicbrainz`) were taking the first release returned without any quality filtering. This caused tracks to be tagged with albums like "Promo Only Modern Rock Radio, December 2001" or "Various Artists: Now That's What I Call Music". The release picker now scores releases, strongly preferring studio albums by the actual artist and penalising compilations, Various Artists credits, and anything with "Promo Only", "Greatest Hits", "Best Of" etc. in the title.
- **Re-download always fetched the same bad result**: hitting Re-download on a failed job (e.g. duration mismatch, wrong track) would re-attempt the exact same video ID and fail again. The original ID is now passed as already-attempted, so the download path searches for an alternate candidate instead.
- **MusicBrainz album lookup skipped when year already known**: the MB-by-ID follow-up call (which fetches album name and track position) was gated on year being absent from the AcoustID result. Since most tracks have a year, album data was almost never retrieved. Now always called when a recording ID is available.
- **MP3Phoenix artist stored as Unknown**: the search result artist was being sent as `channel` but the download payload only checked `artist`, so jobs were stored with a blank artist. Both fields are now checked, and MB artist/title are applied post-download as they are for other sources.
- **Auto-album routing setting not persisting**: `auto_album_singles` was missing from the `SettingsUpdate` Pydantic model, so Pydantic silently dropped it from every PUT request and the toggle never saved.

## v2.4.2 (2026-03-13)

### Added
- **Mid-track silence detection**: catches Content ID fraud uploads where someone pads the middle of a track with silence to avoid fingerprinting while hitting the right total duration. ffmpeg scans the first 60% of the track (leaving hidden/secret album-closer tracks alone) and rejects anything with more than 8 consecutive seconds of silence after the 15s mark. Feeds the normal retry/blacklist path.

### Fixed
- **Fresh install schema incomplete**: `watched_playlists` and `bulk_imports` base `CREATE TABLE` statements were missing columns added since v2.3.x (`preferred_sources`, `lb_username`, `make_m3u`, `use_playlists_dir`, `sync_mode`, `user_id`, and others). Fresh installs would immediately hit `OperationalError: table has no column` errors. Upgraders were unaffected as `ALTER TABLE` migrations ran correctly.
- **WebM remux atomicity**: album-routed WebM files are now verified with ffprobe before the original is unlinked — a corrupt remux no longer silently destroys the source file
- **MBID validation**: invalid UUID strings passed as MusicBrainz IDs now return a 422 immediately rather than silently failing downstream
- **MP3Phoenix truncated downloads**: file size is checked against `Content-Length` after download; empty or truncated files are deleted and the job fails cleanly rather than leaving a stub on disk

## v2.4.1 (2026-03-12)

### Fixed
- **ListenBrainz weekly playlist URL rotation**: "Created for You" playlists (Weekly Exploration, Weekly Jams, etc.) are regenerated every Monday with a new UUID. The watched entry stored the old URL and would 404 forever. MusicGrabber now stores the ListenBrainz username alongside the playlist entry; on a 404 during refresh it re-queries the `createdfor` API, matches by playlist name, updates the stored URL automatically, and continues as normal. **Existing users should delete their ListenBrainz watched playlist cards and re-add them via their username** — cards added before this update don't have the username stored and won't self-heal.

## v2.4.0 (2026-03-12)

### Added
- **Album Download tab**: Dedicated tab for intentional album downloads. Search for an artist via MusicBrainz, pick an album, preview the tracklist, and download the lot. Files land at `Albums/Artist/Album/Track.flac` rather than rattling around in Singles. Optional M3U generation included.
- **Configurable Albums folder**: New `Albums folder` setting in Settings (alongside Singles and Playlists). Defaults to `Albums` under your music directory. Leave it blank to dump everything in the same place as Singles.
- **Album existing-track precheck + partial queueing**: After selecting an album, MusicGrabber now checks the destination album folder and shows how many tracks already exist. Downloading queues only missing tracks and skips the rest. If all tracks already exist, nothing is queued and the UI clearly says so.
- **Albums completion reset action**: A `Search Another Album` button now fades in after album completion (or already-complete detection). It resets the album flow and scrolls back to the top of the Albums tab.
- **Albums expectation note**: Added an advisory note under the artist search input explaining Album mode is best-effort track sourcing, not exact release mirroring, and recommending Lidarr/Soulseek for strict album acquisition workflows.
- **Album art embedding for album mode**: Album downloads now use the selected MusicBrainz release MBID to fetch front cover art from Cover Art Archive and embed it into each downloaded track's metadata (FLAC, MP3, M4A/MP4, and OGG/Opus where supported).
- **Unified "Add to..." destination picker**: The separate "Add to playlist" and "Add to album" chips in search results are merged into a single "Add to..." button. Choosing Album browses the Albums directory on disk by artist then album folder, reads the `.albuminfo` sidecar to get the MusicBrainz release context, and auto-matches the track. Manual track override is available if auto-match can't place it.
- **`.albuminfo` sidecar file**: Albums created via the Albums tab now write a hidden `.albuminfo` JSON file into the album directory (artist, album title, MusicBrainz release MBID). Survives DB wipes and is used by the new picker to restore full album context without guessing from a folder name.

### Fixed
- **`/api/playlists` 500 error**: Broken or stale mounts under the Playlists directory (e.g. a dead NFS share returning `OSError: [Errno 22] Invalid argument`) no longer crash the endpoint. Returns an empty list instead of exploding.
- **Albums folder custom path not persisting**: Typed custom Albums paths now save correctly and survive refresh/restart instead of snapping back to `Albums`.
- **Albums artist chips spacing/layout**: Artist result buttons in Albums were still cramped because JS forced `display:block` inline, overriding layout CSS. The inline override was removed so flex wrapping/gaps apply consistently.
- **Album metadata missing track numbers**: Album downloads now embed per-track `track_number` and album `track_total` tags when writing metadata.
- **Album mode duplicate handling during source fallback**: Album downloads now preserve duplicate-check bypass behavior throughout fallback/retry paths, preventing partial album runs from being blocked by single-track duplicate guards.
- **Monochrome all-tier-403 fallback too narrow**: Monochrome 403 fallback no longer jumps straight to YouTube-only. It now searches for alternate candidates across sources and retries with the next best untried match first.
- **Album-mode false positives (covers/tributes) in candidate selection**: Album matching now applies stricter artist/channel relevance checks to reduce bad picks like piano/tribute substitutions when looking for original album tracks.
- **Album M3U creation race + location**: In some album runs, `Generate M3U playlist` was enabled but no playlist file was produced because the worker read stale flags at startup. Completion now re-reads the final `create_playlist` state, and album M3Us are rebuilt directly in the selected album folder (`Albums/Artist/Album/`) every time.
- **Album mode ignored convert toggle**: The Albums tab download request was hardcoded with `convert_to_flac: true`, so tracks converted even when conversion was turned off. Album downloads now follow the global convert toggle correctly.
- **Scheduler double-start race**: Both the watched playlist and watched artist schedulers could be started twice in quick succession (e.g. app startup + first API call) because the `_scheduler_running` flag was checked without a lock. Added `threading.Lock()` around the start guard in both schedulers.
- **Album remux: empty output file not caught**: If ffmpeg produced a zero-byte output file while still returning exit code 0, the original WebM would be deleted and nothing useful kept. The remux check now also verifies non-zero output size before unlinking the source.
- **`_update_job()` unvalidated column names**: Job update calls used string concatenation for the `SET` clause with no column whitelist. Column names are now validated against a known-good frozenset before the query is built.
- **Album `track_total` tag wrong on partial downloads**: If only some tracks were missing (e.g. 3 of 12), those tracks got tagged `3/3` instead of `3/12` because the count came from the bulk import batch size rather than the full album. A new `album_total_tracks` column on `bulk_imports` stores the real count and it's now used for all TRACKTOTAL tags.
- **Album double-queue on impatient re-submit**: Clicking Download Album twice in quick succession (before any files had arrived) would queue the same tracks twice and both downloads would race each other to the same files. The endpoint now checks for an in-flight import against the same album directory and returns the existing import ID instead of starting a new one.
- **Album M3U missing track info**: Album playlist files were bare filename lists with no duration or display title. They now include proper `#EXTINF` entries (duration + title tag) so players show correct metadata immediately without waiting for a library scan.
- **Album track fuzzy match too loose**: The fallback matcher for mapping a search result title to an album tracklist could fire on a single shared token (e.g. "My" from "My Love"). Threshold tightened to require ≥80% of the shorter token set to overlap, floored at 2 tokens.
- **Download Album button stays active-looking when disabled**: Added a proper `:disabled` CSS rule to `.bulk-import-btn` so the button visibly greys out when an album is queuing, downloading, or complete.
- **Album folder routing rejected for no-sidecar folders**: When adding a track to an album folder that had no `.albuminfo` (no MBID), the backend validation incorrectly required `album_release_mbid` to be present. Folder-only routing now accepts `album_name` + `album_artist` without an MBID.
- **Cover art missing when adding a single track to an album folder**: Tracks routed into an album folder via "Add to..." had no bulk import row for the DB to look up the MusicBrainz MBID from, so cover art was never fetched or embedded. The download path now falls back to reading the `.albuminfo` sidecar directly for the MBID.
- **Watch normaliser: Scandinavian and non-decomposable characters**: Characters like `Ø`, `ø`, `Ł`, `ł`, `æ`, `Æ`, `ß`, etc. are distinct letters that NFKD decomposition leaves untouched, so `BYØRN` was failing to match `BYORN`. An explicit mapping now converts these to their nearest ASCII equivalents before normalisation.
- **Watch normaliser: mixtape/album title prefixes**: Some streaming services store track titles with a project prefix in ALL CAPS, e.g. `STONEHENGE - GEEKED UP`. The normaliser now strips a leading all-caps prefix (3+ chars, no lowercase, followed by ` - `) before comparison.
- **Monochrome duplicate results for same track**: Search results could show two Monochrome entries for the same recording (one `HI_RES_LOSSLESS`, one `LOSSLESS`) as separate cards. Results are now deduplicated by ISRC, keeping the highest-quality entry and awarding it a +20 score bonus. The download path already tries HI_RES first regardless of which card you click, so the duplicates were just noise.

### Changed
- **Album download endpoint behavior**: `/api/albums/download` now returns richer status for existing-vs-missing tracks (`existing_count`, `missing_count`, `queued_count`, warning text), enabling better UI messaging and no-op handling when an album is already complete.
- **`/api/albums/download` now typed**: The endpoint uses a proper `AlbumDownloadRequest` Pydantic model instead of an unvalidated raw dict.


## v2.3.5 (2026-03-10)

### Added

### Fixed
- **Manual downloads bypass MusicBrainz duration check**: If you picked a specific track from the search results yourself, MusicGrabber now trusts your choice instead of rejecting it because MusicBrainz disagrees about the duration. Automated downloads (watched playlists, bulk import) still reject mismatches so junk doesn't sneak in unattended.
- **Playlist selector: refresh on open**: The "Add to playlist" dropdown now reloads the playlist list each time it's opened, rather than only at page load. Fixes cases where the initial load failed silently (e.g. a brief auth hiccup on page init). Also logs the failure reason to the browser console now, which should help debug the remaining mystery of why some users see an empty list.

## v2.3.4 (2026-03-09)

### Added
- **Apple Music playlist import**: Public Apple Music playlists and albums can now be imported and watched. No browser required — Apple server-renders the full track list into the page, so it's a plain HTTP fetch. Supports all regional storefronts. Private playlists and personal libraries (anything requiring sign-in) are not supported.
- **ALAC output format**: ALAC is now a selectable audio format alongside FLAC, Opus, and MP3. Files are saved as .m4a (Apple Lossless Audio Codec). Good for modded iPods and Apple devices that want lossless without FLAC support.

### Fixed
- **Watched track falsely shown as missing**: Tracks already in the library via Monochrome (or any album-structured folder outside Singles) were being re-queued on every playlist refresh. The file check now consults the stored resolved path first, so if MusicGrabber recorded exactly where it landed, that's the ground truth, no more fruitless rummaging through the Singles folder for something filed under an album path.
- **Queue: "Already exists" shows friendlier label**: The download queue previously showed the full file path as an error for duplicate-skipped tracks. It now displays "Already in library" inline, with the actual path moved into the expanded details row.
- **ListenBrainz Alpha badge**: Removed the Alpha badge from ListenBrainz watched playlist cards.
- **Watched track mismatch: colon subtitles**: Spotify stores track subtitles as `Title (Subtitle)` while YouTube/releases use `Title: Subtitle`. The normaliser now strips colon-introduced subtitles before comparison, so tracks like `This Land: Theme from Borderlands 4` correctly match `This Land (Theme from Borderlands 4)`.
- **Watch mismatch log**: Mismatches are now stored persistently in the database and shown in a new panel at the bottom of the Stats tab. Each entry shows expected vs actual artist/title, the normalised forms that were compared, and which playlist triggered it. Survives container restarts. Includes a Clear Log button for when you've investigated and moved on.
- **Watch normaliser improvements** (from mismatch log analysis): three additional gap fixes: (1) accented/decorated characters (e.g. `JAŸ-Z`) now decompose to ASCII equivalents via NFKD before comparison; (2) artist names that normalise entirely to single characters (e.g. `B.o.B` → `b o b`) no longer produce an empty word-set that fails the subset check; (3) YouTube translation suffixes (`× TRADUÇÃO`, `x Translation`, etc.) are stripped from titles before matching.

---

## v2.3.3 (2026-03-07)

### Added
- **Per-playlist source selection**: Each watched playlist now has a Sources setting, toggle which search sources (YouTube, SoundCloud, MP3Phoenix, Monochrome) are used when downloading new tracks. All sources remain active by default. Chips appear on the playlist card and in the Watch form. Selecting a source that's globally disabled falls back to all enabled sources rather than finding nothing.

### Changed
- **Live recording scoring**: Unambiguous live tags — `(Live)`, `[Live]`, `- Live`, `Live at ...`, `Live from ...`, `Live Version` — now score -80 instead of the previous flat -50 for any mention of "live". A bare occurrence of the word without a clear qualifier drops to -30, reducing false positives for artists actually named "Live" or titles that contain the word incidentally.

### Fixed
- **Multi-user scoping**: `update_watched_playlist` was fetching the updated record without the user scope, meaning an admin update could theoretically return another user's row. `delete_job_file` was using the global playlists directory instead of the requesting user's.
- **Playlist selector missing watched playlists for upgraded installs**: `GET /api/playlists` used a hand-rolled scope query that didn't include legacy `user_id IS NULL` rows for admin users. Long-term users who upgraded from single-user mode couldn't see their watched playlists in the "Add to playlist" selector. Fixed to use the same `_user_scope()` helper as every other endpoint.


## v2.3.2 (2026-03-07)

### Added
- **Skip duplicates toggle**: New toggle in Settings → General. When disabled, MusicGrabber still queries your local library, Navidrome, and Lidarr for path resolution (so watched playlist M3Us stay accurate), but tracks are downloaded regardless of whether they already exist. Default on.
- **Lidarr duplicate check**: Configure a Lidarr URL and API key in Settings and MusicGrabber will query your Lidarr library before downloading. Tracks already in Lidarr with a file are skipped. Real file paths are resolved via the trackfile API, so watched playlist M3U entries work correctly for Lidarr-managed tracks too. Runs as a final fallback after the local filesystem and Navidrome checks.

### Fixed
- **Bulk import broken**: `process_bulk_import_worker` was being called with a `user_id` keyword argument it doesn't accept, crashing the background thread immediately. The worker reads `user_id` from the DB row itself, so the extra argument was redundant.
- **Watched playlist M3U track order**: M3Us were ordered by `first_seen` timestamp, which meant the initial bulk sync produced arbitrary ordering and subsequent refreshes didn't reflect moves within the source playlist. Track positions are now stored on every refresh and M3Us are built in upstream order. Existing installs get correct ordering automatically on the next playlist refresh. Old rows without a position sort after newly-positioned ones, so nothing gets scrambled on upgrade.

### Changed

---

## v2.3.1 (2026-03-06)

### Added
- **Create playlist from search page**: The playlist selector on the search results page now includes a "+ New playlist..." option. Type a name, press Enter (or click the tick), and the new playlist is available immediately without leaving the page.
- **Preview fade-in**: Audio fades in over 5 seconds rather than starting at full volume. No more surprise heart attacks.
- **Floating Save Settings bar**: The Save Settings button, Ko-fi link, and Release Notes button now sit in a fixed bar at the bottom of the viewport on the Settings tab, visible no matter how far down you've scrolled.
- **Unraid Community Apps template**: Added an Unraid XML config example to the README for easy one-click install via Community Apps.

### Fixed
- **Security: several admin-only endpoints were accessible to regular users**: Stats, job cleanup, check-all (watched playlists and artists), and all blacklist endpoints were missing admin checks. All now return 403 for non-admin users.
- **Security: search token validation was not scoped to the requesting user**: A search token issued to one user could be consumed by another. Tokens are now validated against the requesting user's own records.
- **Security: search logs were missing user attribution**: `_log_search()` was writing rows without a `user_id`, which broke per-user search history and made token scoping impossible. User ID is now threaded through from the route handler.
- **ListenBrainz "Created for You" playlists badge**: Removed the "alpha" label - it's been working reliably and doesn't need the warning anymore.
- **Watched track mismatch on multiplication sign in titles**: `×` (U+00D7 MULTIPLICATION SIGN) was not being mapped to ASCII `x` during normalisation, causing tracks like "4x4xU" to fail matching against Spotify's "4×4×U". Added to the fullwidth/lookalike character map alongside the existing fullwidth pipe and dash fixes.
- **Spotify playlist import broken by music videos**: When a Spotify playlist contains a music video entry, Spotify returns `"Music Video"` as the artist name rather than the actual artist. Both the embed scraper and headless browser scraper now detect this and attempt to salvage the artist by parsing the track title (which often contains "Artist - Title"). If a clean artist/title split can be found the track is imported correctly; if not, the entry is skipped rather than polluting the list with garbage.

### Changed

---

## v2.3.0 - g33kphr33k's Birthday Edition (2026-03-05)

### Added
- **Multi-user support**: MusicGrabber can now run with proper user accounts. No login is required until you actually create a user, so existing single-user installs continue to work exactly as before with zero configuration changes. Once you create your first account in Settings → Users, a login screen appears and all data is scoped per-user. Admin users manage accounts and global settings; regular users get their own download queue, watched playlists/artists, notifications, music directory, and Navidrome/Jellyfin credentials. The global API key still works as an admin-equivalent bearer token for scripts and external integrations
- **Session-based authentication**: Username and password login with 30-day session tokens. Tokens are sent as `Authorization: Bearer` headers by the frontend. Sessions are cleaned up automatically by the stale job monitor
- **Per-user settings**: Music directory, singles/playlists subdirectory structure, organise-by-artist toggle, Navidrome credentials, Jellyfin credentials, and all notification settings (Telegram, email/SMTP, Apprise, generic webhook) are now stored per-user. Global settings (audio format, MusicBrainz, lyrics, slskd, Spotify, AcoustID, API key) remain admin-only. All existing settings are preserved and continue to work as global defaults for single-user installs
- **Per-user YouTube cookies**: Cookie files are now stored per-user at `/data/cookies-{user_id}.txt` alongside the global `/data/cookies.txt`, so each user can authenticate YouTube independently
- **User management UI**: Admin-only section in the Settings tab for creating, listing, and deleting users, resetting passwords, and flagging accounts to force a password change on next login. Regular users get a "Change my password" form. First login with a freshly-created account, or after an admin forces a reset, triggers a forced password-change screen before reaching the app
- **Per-user data isolation**: Download jobs, bulk imports, watched playlists, watched artists, and blacklist entries are all scoped by user. Each user sees only their own data; admins see their own data too (not other users' queues)
- **Spotify cookie authentication**: Paste your Netscape-format `cookies.txt` from `open.spotify.com` into Settings to unlock private playlists, saved albums, and personal library playlists — anything that requires a login. The `sp_dc` session cookie is extracted and injected into both the embed scraper and the Playwright browser path. Works per-user, same pattern as YouTube cookies. If cookies expire mid-use, the flag is set automatically, an amber warning banner appears in Settings, and a clear message is shown at the fetch site. Cookie validity is tested by fetching the Spotify embed page for a known public playlist — the same path the scraper uses for real fetches, so if the CDN hates your server IP, you'd know
- **Admin password reset button**: Admins can now flag any user's account to force a password change on next login, directly from the user list in Settings. Their active sessions are terminated immediately, so the change can't be deferred
- **Per-source enable/disable**: Each search source (YouTube, MP3Phoenix, SoundCloud, Monochrome) can now be independently toggled in Settings → General. Disabled sources are skipped in search results, watched playlist matching, and bulk imports. Overridable per-source via env vars (`SOURCE_YOUTUBE_ENABLED`, `SOURCE_MP3PHOENIX_ENABLED`, `SOURCE_SOUNDCLOUD_ENABLED`, `SOURCE_MONOCHROME_ENABLED`)

### Changed
- **DB migration to version 1**: `watched_playlists` and `watched_artists` unique constraints relaxed from `UNIQUE(url)` / `UNIQUE(mbid)` to `UNIQUE(user_id, url)` / `UNIQUE(user_id, mbid)`, allowing multiple users to watch the same playlist or artist independently. `search_logs` unique index similarly scoped to `(user_id, search_token)`. Migration runs once on startup and preserves all existing data
- **slskd token cache now keyed by instance**: Previously a single global cached token; now a dict keyed by `(slskd_url, slskd_user)` so users with different slskd instances get independent token caches

### Security
- **Login brute-force protection**: Per-username attempt tracking with lockout after configurable failed attempts (`LOGIN_MAX_ATTEMPTS`, `LOGIN_LOCKOUT_SECONDS`, `LOGIN_ATTEMPT_WINDOW`). Generic 401 response regardless of whether the username exists, and timing-safe bcrypt checks to resist username enumeration
- **Single-use download tokens**: File download links now use short-lived (60s) single-use tokens scoped to a specific job, issued via `POST /api/auth/download-token`. The session token no longer appears in any URL
- **Minimum password length**: All three password-setting endpoints reject passwords shorter than 8 characters. Previously you could set a one-character password and the backend would happily hash it, which is fine for art installations but not much else
- **XSS audit complete**: Full pass over every `innerHTML` assignment in the frontend. Three remaining unescaped API-data sinks closed: `r.thumbnail` injected raw into `src=""` (a crafted URL could execute JS via `onerror`), `r.video_id` in `data-video-id=""` attributes, and `r.source` in CSS class names. All three now go through `escapeHtml()`. No further unescaped paths found
- **HTTPS-only mode and security headers**: Optional `HTTPS_ONLY=true` env var rejects plain HTTP requests. All responses now include `X-Content-Type-Options`, `X-Frame-Options`, and `Referrer-Policy` headers. `Strict-Transport-Security` added when HTTPS is detected
- **API key query-param disabled by default**: `?api_key=` in URLs is now opt-in via `ALLOW_API_KEY_QUERY_PARAM=true`. Prevents credentials leaking into reverse-proxy access logs
- **Settings test endpoints restricted to admins**: Connection test endpoints (`/api/settings/test/*`) now return 403 for non-admin users, closing an SSRF primitive against internal services
- **Password changes revoke old sessions**: Changing your own password invalidates all other active sessions for that account. Admin password resets invalidate all sessions for the target user

### Fixed
- **Single source flooding "All" search results**: Each source was contributing up to `limit` results to the merged pool, so MP3Phoenix could fill all 10 slots with near-identical tracks and push a superior Monochrome lossless result off the page entirely. Each source is now capped at 4 results in the merged pool; scoring determines the final order
- **Disabled sources still appearing in search results**: The per-source enable/disable setting was being read with `get_setting()`, which returns a string. Checking `bool("false")` is `True`, so toggling a source off in Settings had no effect. Both the `search_all` and `search_source` paths now use `get_setting_bool()`
- **MusicBrainz duration scoring bands tightened**: The tolerance windows were calibrated for longer tracks but most songs are ~3 minutes, where 25% is 45 seconds of slop. Bands are now ≤2% (+40), ≤5% (+20), ≤10% (neutral), ≤25% (-30), >25% (-60)
- **Monochrome quality bonuses and MP3Phoenix bonus rebalanced**: The gap between lossless (Monochrome) and 320 kbps lossy (MP3Phoenix) was only 20 points after the previous round of tuning, narrow enough for a duration scoring nudge to flip the result. MP3Phoenix bonus reduced from +50 to +30; LOSSLESS raised from +70 to +80; HI_RES_LOSSLESS from +90 to +100. Clean 50-point gap: lossless always wins on equal relevance
- **Monochrome variant tracks unfairly promoted by quality bonus**: Tidal's catalogue occasionally surfaces alternate-mix variants with opaque parenthetical suffixes (e.g. "Hey Man Nice Shot (½ oz)") that score the same as the clean studio title after normalisation strips the brackets. Combined with the LOSSLESS quality bonus, these variants were ranking above the plain track from every other source. Two fixes: title variants with unrecognised parenthetical content now take a -110 penalty in the Monochrome scorer (enough to neutralise even HI_RES_LOSSLESS), and the LOSSLESS/HI_RES_LOSSLESS bonuses have been trimmed from 100/120 to 80/100 so that a stack of official signals on a YouTube result can still win. Known-benign suffixes (Remastered, Deluxe Edition, feat., etc.) are unaffected
- **Monochrome tracks ignored the audio format setting**: The Monochrome download path always wrote `.flac` and never consulted `audio_format`. If you'd set MP3 or Opus as your preferred format, YouTube downloads would convert correctly and Monochrome downloads would quietly stay FLAC. The lossless file is now transcoded to MP3 (VBR ~192k) or Opus (320k) after integrity checks and metadata embedding, same as every other source
- **Docker healthcheck ignored `LISTEN_PORT`**: The `HEALTHCHECK` instruction was hardcoded to `localhost:8080`, so any container using a non-default `LISTEN_PORT` would show as unhealthy. The healthcheck now uses `${LISTEN_PORT:-8080}`, falling back to 8080 when the variable isn't set

## v2.2.7 (2026-03-02)

### Fixed
- **ListenBrainz "Created for You" playlists added empty**: The `/playlists/createdfor` listing endpoint always returns `"track": []` for each entry, just an index, not a full data dump. Tracks only exist on the per-playlist JSPF endpoint. The code was parsing tracks from the listing response and getting nothing. Each playlist is now fetched individually during the fan-out; any that fail (e.g. a rotated UUID) are skipped with a log line rather than aborting the whole import
- **Watched track mismatch on visually identical titles**: YouTube channel owners frequently use fullwidth Unicode punctuation, `｜` (U+FF5C) instead of `|`, `－` (U+FF0D) instead of `-`, in video titles. The normalisation function's pipe-strip regex only matched the ASCII form, so titles that looked identical in the log were producing different normalised strings and triggering spurious mismatch deletes. Both characters are now mapped to their ASCII equivalents early in normalisation, before any regex runs

## v2.2.6 (2026-03-01)

### Fixed
- **YouTube downloads fail with "Requested format is not available" when cookies are set**: When a logged-in YouTube account's cookies are active, Google serves a format manifest that includes Premium-only audio streams. `bestaudio/best` greedily selects these because they score highest, then they 403 on actual download because the session isn't Premium (or has gone slightly stale). The format selector is now `bestaudio[ext=webm]/bestaudio[ext=m4a]/bestaudio/best`, which sticks to standard Opus/AAC streams and sidesteps the Premium bait entirely. The existing cookieless retry fallback remains as a backstop
- **Format errors incorrectly triggered the bot-block backoff sleep**: "Requested format is not available" was being treated the same as a 403 bot block, causing an unnecessary multi-second sleep before the cookieless retry. Format errors are a manifest/cookie mismatch, not rate-limiting. Backoff sleep is now only triggered by genuine 403 responses
- **Cookie test always reported "real-world auth untested"**: The test was probing age-restricted YouTube videos, which turned out to be geo-blocked in many regions (including the UK, apparently). The test now hits `youtube.com/feed/library` instead  -  an account page that requires login, isn't geo-blocked because it's your own data, and fails with a clear auth error when cookies are expired

### Added
- **Watched Artists**: New section below Watched Playlists on the Watched tab. Search for an artist by name, pick the right MusicBrainz match from up to five candidates, set a "download from" date (defaults to today, so your entire back-catalogue stays exactly where it is), and MusicGrabber will check MusicBrainz for new singles on the same schedule as your playlists. New singles are queued automatically through the normal bulk import pipeline. Back-catalogue is seeded as already-known at add time, and any tracks already on disk are recognised immediately on the first refresh rather than re-queued. Singles-only: remixes, live cuts, compilations, soundtracks and DJ mixes are filtered out via MusicBrainz release group secondary types. Per-artist settings: check interval, convert-to-FLAC toggle, pause/resume, and the usual missing/tracks panels with download-to-device buttons. Artist refreshes use the same atomic-lock pattern as playlists, so manual and scheduled runs can't trip over each other
- **Downloadable to Device**: New section at the bottom of the Queue tab (jump link in the header). Lists every completed download newest-first, 50 per page, each with a Save button that serves the file directly to your browser. Handy for browsing and pulling the full library to a phone or laptop without digging through the queue history
- **Save to device**: Completed downloads in the Queue tab now have a "Save to device" button that serves the audio file directly to your browser. Handy for pulling tracks from the server to a phone or laptop without needing separate library access. Watched playlist downloaded tracks also get a download icon in the track list. Works via a new `GET /api/jobs/{id}/download` endpoint; if an API key is set, it's appended as a query param so browser-native downloads aren't blocked by missing headers
- **Playlist add progress feedback**: Adding a watched playlist no longer sits silently with a greyed-out button. A spinner with elapsed time now appears in the add form as soon as you click Watch, using the same stage display as the refresh indicator on playlist cards. Spotify large-playlist hint kicks in after 90 seconds, same as on refresh

### Fixed
- **Watched track mismatch on Spotify dash-suffix variants and pipe-separated session tags**: Spotify encodes version qualifiers as title suffixes with a dash separator (`Better Now - Acoustic`, `Forever Young - From NBC's Parenthood`); YouTube puts the same qualifier in brackets or drops it entirely. YouTube also appends session/channel names with a pipe (`Tugboats | OurVinyl Sessions`). The normaliser now strips known dash-suffixes from the expected title and pipe-separated suffixes from both sides before comparison. Correctly-downloaded tracks were being deleted and re-queued because the titles looked different on paper but were the same song
- **File reconciler and queue endpoint falsely marked playlist-folder tracks as deleted**: `check_duplicate()` only searches the Singles directory tree. Tracks downloaded into a Playlists subfolder (via watched playlist or playlist routing) were not found there, so the reconciler set `file_deleted=1` and cleared `resolved_path`, and the queue endpoint showed "File Deleted" for perfectly healthy files. Both now prefer the stored `resolved_path` from `watched_playlist_tracks` and only fall back to `check_duplicate` when no resolved path exists
- **Save to device returned 404 for tracks in playlist folders**: The `GET /api/jobs/{id}/download` endpoint used `check_duplicate()` to locate files, which also only searches Singles. It now checks `watched_playlist_tracks.resolved_path` first, so playlist-folder tracks are served correctly

## v2.2.5 (2026-02-28)

### Added
- **MP3Phoenix as a new search and download source**: Russian MP3 portal backed by the VK audio CDN. Returns ~320 kbps MP3s, no auth required, and works with a plain HTTP GET so no yt-dlp or Playwright needed. Appears in search results with a `PX` badge between YouTube and SoundCloud, runs in parallel with all other sources, and supports hover preview playback. The VK download tokens were empirically confirmed to survive at least 20 minutes, so there is no meaningful race between search and download. Downloads are converted to FLAC if the setting is enabled, then go through the usual integrity check, MusicBrainz metadata enrichment, and lyrics fetch. Cover art is not embedded (mp3phoenix does not provide it), so MusicBrainz fills in what it can
- **MusicBrainz expected duration used as a search-time scoring signal**: Search results for `Artist - Title` queries are now cross-referenced against the canonical track duration from MusicBrainz, and scored accordingly. Results within 5% of the expected duration get a +40 bonus; within 12% get +20; within 25% are neutral; 25-50% off get -30; more than 50% off get -60. This stops a 1:41 DJ medley from outranking a 3:31 studio track when you searched for the studio track. The MB lookup runs in a background thread alongside the source searches so it adds no latency. Skipped automatically when the query contains a variation keyword (remix, live, extended, cover, etc.), since you presumably want that version
- **Watched playlist card now shows live refresh state and stage**: Refresh is no longer "toast-only". Each card now shows an in-card running badge with stage text (`Fetching playlist`, `Comparing tracks`, `Queueing downloads`, `Rebuilding M3U`, etc.), disables the Refresh button while active, and preserves state when switching tabs because status is persisted in `watched_playlists`

### Changed
- **Compilation albums penalised in search scoring**: Results tagged to an anthology, greatest hits collection, or similarly-named compilation album now receive a -25 score penalty. The right song but from a dodgy "Gold Series" comp shouldn't beat the original album release, and now it won't
- **Watched refresh state is now persisted and self-healing**: Added refresh tracking fields to `watched_playlists` (`refresh_state`, `refresh_stage`, `refresh_started_at`, `refresh_completed_at`, `refresh_error`, `refresh_import_id`) and a stale refresh cleanup path. Stuck `running` states are auto-marked failed after `WATCHED_REFRESH_STALE_SECONDS` (default 1800s / 30 minutes), so interrupted runs don't leave cards spinning forever

### Fixed
- **MP3Phoenix downloads stalled behind slow yt-dlp jobs in bulk imports**: Bulk import uses a bounded thread pool (3 workers) to throttle concurrent YouTube downloads. MP3Phoenix jobs were submitted to the same pool and could queue indefinitely behind a backlog of yt-dlp/Monochrome futures. MP3Phoenix downloads are a plain HTTP stream that completes in seconds, so they now bypass the pool entirely and run on a daemon thread, same as single downloads from the UI
- **MP3Phoenix jobs could stay queued forever without starting**: The MP3Phoenix download path fetched `(title, artist)` from SQLite and then read the result as a dict row (`row["artist"]` / `row["title"]`). In pooled DB connections, `row_factory` may be reset to default tuple mode, causing a `TypeError` before the job is marked `downloading` or `failed`. The MP3Phoenix branch now explicitly sets `conn.row_factory = sqlite3.Row` before reading the job row, so queued PX jobs correctly enter download processing
- **MP3Phoenix duration-mismatch failures could keep retrying the same bad candidate**: The dedicated MP3Phoenix path did not use the alternate-candidate fallback logic used by the main yt-dlp path. On repeated retries, the same PX result could be selected again and fail with the same MusicBrainz duration mismatch. MP3Phoenix failures from integrity/duration checks now blacklist the bad candidate and immediately pivot to the next untried search result/source for the same job
- **Manual file deletions/renames could leave stale completed jobs and watched-track state**: Missing-file reconciliation previously depended on loading the queue endpoint. A new background reconcile pass now runs at startup and on a schedule (`LIBRARY_RECONCILE_INTERVAL`, default 30m), marks missing completed jobs as `file_deleted=1`, and clears linked `watched_playlist_tracks.downloaded_at`/`resolved_path` so watched refresh can re-queue tracks cleanly
- **Watched playlist refresh timer could show an ever-growing elapsed time on click**: While a local refresh request was pending, the UI reused the playlist's persisted `refresh_started_at` value from previous runs. The watched card now uses a fresh local `startedAt` timestamp for local pending state, and only uses persisted `refresh_started_at` when the backend explicitly reports `refresh_state='running'`
- **ARM64 Docker builds broken after host reboot**: `binfmt_misc` QEMU handlers are volatile and disappear on reboot, so the `multiarch` buildx builder silently dropped back to `linux/amd64`-only. Fixed by registering a `binfmt-qemu` systemd service that re-runs `tonistiigi/binfmt --install all` at boot, then restarting the builder to pick up the new registrations. The `publish-multiarch.sh` guard from v2.2.4 will catch any future lapses before a bad push gets out
- **Watched playlist M3U silently drops tracks with non-ASCII artist names**: Spotify playlists with Japanese (or other non-ASCII) artist names like `山下達郎` were not matching the romanised filenames (`Tatsuro Yamashita - JODY.flac`) that actually land on disk, so those tracks were silently absent from M3U rebuilds. The download path now stores the actual resolved file path in the database at completion time. M3U rebuild uses the stored path directly when available, bypassing the artist/title lookup entirely. Existing entries with no stored path fall back to the current lookup, so nothing regresses for pre-existing downloads
- **Watched playlist mismatch leaves wrong file on disk**: When AcoustID/MusicBrainz identified a downloaded track as something other than what was expected, the mismatch was logged and the job marked `completed_with_errors`, but the wrongly-named file was left sitting in the library. The mismatch path now deletes the audio file and any accompanying `.lrc` lyrics file, then flips the job to `failed` so the watched playlist retry mechanism can have another crack at it
- **Watched playlist refreshes could overlap and race each other**: Manual refresh, scheduler refresh, and "Check All Now" could all hit the same playlist close together, causing duplicate work and confusing status feedback. Refresh now takes an atomic per-playlist lock in the database; if a run is already active, additional refresh requests return `already_running` instead of starting a second run

## v2.2.4 (2026-02-27)

### Fixed
- **ARM64 image publish guard (prevent amd64-only releases)**: Added `scripts/publish-multiarch.sh`, which hard-fails before publish unless the selected buildx builder reports both `linux/amd64` and `linux/arm64`. This prevents accidental "multi-arch" pushes when `qemu/binfmt` arm64 support has dropped from the host. The script always publishes with explicit `--platform linux/amd64,linux/arm64`
- **Watched playlist cards silently show fewer M3U tracks than expected when Navidrome has stale entries**: When Navidrome's database still contains records for files that have since been deleted (visible under Navidrome > Settings > Missing Files), MusicGrabber trusts those paths and writes them into the M3U, leaving dead entries that don't play. The playlist card now counts how many such stale paths were detected during the last M3U rebuild and shows a warning banner with instructions to clean up Navidrome's missing file records

---

## v2.2.3 (2026-02-27)

### Fixed
- **Watched imports could pick a short wrong-artist clip over the correct full-length track**: Three changes combined to close this gap. First, the duration penalty for sub-30s clips in search scoring was doubled from -40 to -80, and a new -40 tier added for 30-60s clips, so clips can no longer be rescued by official-channel bonuses. Second, AcoustID fingerprinting is now skipped for any downloaded file shorter than 30 seconds; a 21-second clip can technically fingerprint as the correct song, but tagging it with that metadata would be wrong. Third, watched playlist imports now walk the ranked candidate list and prefer the first result whose title or channel actually contains the expected artist, rather than blindly picking the top scorer regardless of artist
- **Watched playlist refresh re-queued tracks that existed in Navidrome but not in MusicGrabber's own folders**: The file-existence check on refresh only looked at MusicGrabber's local library. Tracks that lived elsewhere on the same filesystem (pre-existing library, different path) were not found, so `downloaded_at` was cleared and the track got re-queued on every refresh. The check now also consults Navidrome as a final fallback, accepting absolute paths only, so tracks Navidrome can vouch for with a real path are left alone
- **YouTube titles with leading label/channel prefixes were parsed with the wrong artist**: Uploads tagged like `[UKF release] S.P.Y - Sweet Sound` or `(Monstercat) Razihel - Love U` had the prefix absorbed into the artist name, so `S.P.Y` never matched the expected artist and the track was rejected. `extract_artist_title()` now tries stripping a leading `[...]` or `(...)` block first, and only uses the stripped version when the remainder starts cleanly with a word character. This means `[UKF release] S.P.Y - Sweet Sound` correctly resolves to `S.P.Y / Sweet Sound`, while `[IVY], A Little Sound - Can't Love Me` is left intact (stripping `[IVY]` leaves a comma-prefixed remainder, which fails the guard)
- **Watched playlist M3U silently missing tracks when Navidrome real path mode is off**: The M3U builder was correctly skipping Navidrome's synthetic relative paths (unusable as M3U entries), but doing so in silence, making it look like tracks simply failed to download. The rebuild logic now distinguishes three outcomes: real path found and written, Navidrome sentinel returned (track exists but path is fake), and genuinely unresolved. Synthetic-path drops now emit a clear WARNING log naming every affected track and telling the user exactly what to fix, rather than leaving them staring at a shorter M3U wondering why
- **Playwright browser unavailable when running with PUID/PGID**: The Docker build installs Chromium as root into `/root/.cache/ms-playwright`. When `PUID`/`PGID` is set the app runs as a non-root user whose home resolves differently, so Playwright's binary lookup fails and Spotify browser fallback silently falls back to the 100-track embed limit. Fixed by setting `PLAYWRIGHT_BROWSERS_PATH=/ms-playwright` in the image so the install and runtime always agree on where the browser lives, regardless of which user is driving
- **Preview/sample audio could be saved as a full track**: Some CDNs serve preview segments with a large `start_time` baked into the container (for example 120 seconds into the actual recording). These files sounded fine to `ffprobe` duration checks but played as an ear-splitting sample from the middle of a song. The integrity validator now also checks `start_time`; anything above 1 second is rejected as a likely preview segment, triggering the usual retry and blacklist flow
- **Large Spotify playlists could stop short of full count during browser fallback**: The headless Spotify extractor now reads the playlist's reported `song_count`, performs fast visibility checks for cookie banners (skipping hidden selectors instead of burning 30s click timeouts), tracks rows by numeric playlist index so virtualised scrolling doesn't lose ordering, and uses a time-based progress stall window instead of a tiny fixed stale-loop cap. Browser subprocess timeout is also scaled by expected playlist size (capped) so slower hosts are less likely to be cut off mid-scroll. These limits are now configurable via Settings (or env overrides): `spotify_browser_timeout_seconds` / `SPOTIFY_BROWSER_TIMEOUT_SECONDS` and `spotify_browser_stall_seconds` / `SPOTIFY_BROWSER_STALL_SECONDS`. Validation against `https://open.spotify.com/playlist/5VAiK705RGhocNFGWW6iTT` now returns the full 1360 tracks instead of truncating at 1331
- **Watched playlist M3U entries silently rejected for remix/collaborator formatting differences**: The mismatch check compared Spotify's metadata format (remixer in artist field, remix in title as a dash-suffix like `Track - Remixer Remix`) against YouTube's format (only primary artists tagged, remix in parentheses which get stripped). This caused ~36 of 150 tracks in a real playlist run to be excluded from the M3U as "mismatches" despite being the correct download. The normalisation logic now: strips inline `feat./ft./featuring` clauses from titles (not just bracketed ones), strips trailing `official` and `visualizer` keywords, treats a Spotify-side remix dash-suffix as a prefix match when the got-title is a clean prefix and the extra words end in a remix indicator word, and uses word-set subset comparison for artists (handling order swaps, remixer additions, and separator differences like comma vs `feat.` vs `x` vs `&`)
- **Wrong tracks downloaded from YouTube could pass integrity checks undetected**: A track with a plausible duration but completely wrong content (different artist, wrong version) could sail through the corruption and start-time checks with no complaint. MusicBrainz now also returns the expected track duration (`recording.length`) alongside metadata, and a post-download duration check rejects files that fall outside 10% of the expected length. This catches the Civil War 8min / short-edit scenario without needing a full fingerprint
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
