// =============================================================================
// Search progress quips
// =============================================================================
// Cheeky-but-honest status lines for the live search progress panel.
// One global object, mirroring how release-notes.js is loaded before app.js.
//
// Structure:
//   SEARCH_QUIPS.searching[<source>]  -> shown while a source is still being searched
//   SEARCH_QUIPS.searching._default   -> fallback for any unknown source
//   SEARCH_QUIPS.empty                -> source answered but found nothing
//   SEARCH_QUIPS.timeout              -> source took too long and got left behind
//   SEARCH_QUIPS.offline              -> source is parked/offline and was skipped
//   SEARCH_QUIPS.error                -> source threw a wobbly
//
// House style: British, light, factual-ish, and absolutely no emdashes.
// Add as many as you like; one is picked at random.

const SEARCH_QUIPS = {

    searching: {

        youtube: [
            "Poking YouTube with a stick",
            "Sifting the YouTube haystack",
            "Wading through the reaction videos",
            "Skipping past the ten-hour loops",
            "Ignoring 4,000 lyric videos",
            "Asking YouTube to be serious for one second",
            "Scrolling past the sped-up nightcore",
            "Filtering out the bedroom covers",
            "Dodging the 'official' bootlegs",
            "Squinting at the thumbnails",
            "Politely declining the algorithm's suggestions",
            "Hunting the actual song, not the meme",
            "Wrestling the recommendation engine",
            "Looking past the 'FULL ALBUM (no ads)' uploads",
        ],

        monochrome: [
            "Rummaging through Qobuz's bins",
            "Leaning on the lossless lot",
            "Asking Deezer ever so nicely",
            "Checking the hi-res shelf",
            "Cross-examining the ISRCs",
            "Negotiating with the proxies",
            "Demanding nothing less than FLAC",
            "Holding out for the studio master",
            "Reading the fine print on the bitrate",
            "Verifying it actually exists before bragging",
            "Tutting at anything under 16-bit",
            "Politely refusing the live version",
            "Triangulating Tidal and Qobuz",
            "Insisting on the proper master",
        ],

        soundcloud: [
            "Leafing through SoundCloud",
            "Nudging the SoundClouders",
            "Wading past the DJ mixes",
            "Skipping the 47-minute mega-sets",
            "Ignoring the 'prod. by' tags",
            "Sidestepping the remixes of remixes",
            "Looking past the unmastered demos",
            "Checking who actually uploaded this",
            "Filtering out the podcast episodes",
            "Hunting the real upload, not the rip",
        ],

        zvu4no: [
            "Knocking on zvu4no's door",
            "Whispering to zvu4no",
            "Pronouncing zvu4no correctly (attempt 3)",
            "Slipping zvu4no a note",
            "Catching zvu4no on a good day",
            "Decoding what zvu4no even means",
            "Politely badgering zvu4no",
            "Waiting for zvu4no to wake up",
        ],

        freemp3cloud: [
            "Coaxing FreeMp3Cloud",
            "Wringing out the cloud",
            "Catching the drips from the cloud",
            "Waiting for the cloud to clear",
            "Asking the cloud to hurry along",
            "Squeezing an MP3 from the sky",
            "Checking the forecast (cloudy, obviously)",
            "Reminding the cloud it's free",
        ],

        soulseek: [
            "Seeking souls on Soulseek",
            "Bribing the Soulseek peers",
            "Queuing politely on Soulseek",
            "Waiting for a free upload slot",
            "Befriending a stranger with good taste",
            "Joining the queue behind 38 people",
            "Hoping someone's still got their PC on",
            "Negotiating peer-to-peer diplomacy",
            "Trusting a stranger's folder names",
            "Checking who's actually sharing today",
            "Tipping the uploader (in good vibes)",
            "Communing with the Soulseek hive",
            "Dowsing for souls",
        ],

        _default: [
            "Having a rummage",
            "Asking nicely",
            "Turning over the cushions",
            "Checking down the back of the sofa",
            "Making some calls",
            "Following a hunch",
            "Casting the net",
            "Giving it a good shake",
        ],
    },

    empty: [
        "nothing here, sorry",
        "came up empty",
        "not a sausage",
        "drew a blank",
        "nada, zip, zilch",
        "nope, nothing",
        "all quiet on this one",
        "the cupboard's bare",
        "not a dickie bird",
        "computer says no",
    ],

    // Shown when a source is still going long after the others finished. Per
    // source, with a fallback. Soulseek gets soul-themed lines for its retry.
    slow: {
        soulseek: [
            "Soulseek's taking its time seeking souls, bear with it",
            "Still seeking souls, they're a shy bunch",
            "The souls are playing hard to get, having another go",
            "Deep in the Soulseek catacombs, hang on",
            "Giving the souls a second chance to answer",
            "Souls are slow to surface, one more knock",
        ],
        _default: [
            "This one's having a proper think",
            "Still rummaging, won't be long",
            "Taking the scenic route",
            "Hang on, nearly there",
            "Just coaxing the last drop out",
        ],
    },

    // Soulseek-specific empty result, because "soulless" was right there
    soulless: [
        "came back soulless",
        "not a soul in sight",
        "no souls found, sorry",
        "the souls have left the building",
        "soulless, nothing doing",
        "couldn't find a single soul",
        "souls all out, try again later",
    ],

    timeout: [
        "took too long, moved on",
        "still faffing, left it behind",
        "off having a cup of tea",
        "ran out of patience",
        "gave up waiting",
        "stuck in traffic, carried on without it",
        "ghosted us, frankly",
        "left it to think about what it's done",
        "dragging its heels, so we left",
        "no reply, moving swiftly along",
        "asleep at the wheel",
        "took the scenic route, missed the deadline",
    ],

    offline: [
        "offline, skipped",
        "out to lunch",
        "closed for business",
        "not picking up",
        "gone dark, skipping",
        "having a duvet day",
        "lights off, nobody home",
        "on holiday, apparently",
        "down for the count",
        "skipped, it's having a moment",
        "currently unreachable",
        "taking a well-earned nap",
    ],

    error: [
        "had a wobble",
        "threw a strop",
        "tripped over its own feet",
        "spat the dummy",
        "fell over, dusting itself off",
        "had a little meltdown",
        "coughed up an error",
        "did not enjoy that question",
        "went a funny colour",
        "lost the plot momentarily",
        "blue-screened in spirit",
        "had a senior moment",
    ],
};
