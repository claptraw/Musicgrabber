"""
Unit tests for MusicBrainz retry / unavailable handling on the Albums tab.
These are pure unit tests; httpx is monkeypatched so no network calls happen.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _import_metadata_or_skip():
    try:
        import metadata
    except ModuleNotFoundError as exc:
        pytest.skip(f"metadata module dependencies unavailable: {exc.name}")
    return metadata


class _FakeResp:
    def __init__(self, status_code, json_body=None):
        self.status_code = status_code
        self._json = json_body or {}

    def json(self):
        return self._json


class _FakeClient:
    """Drop-in replacement for httpx.Client. Each call returns the next item
    from the shared `responses` list -- can be a _FakeResp to return, or an
    Exception to raise. Shared list because the retry helper opens a fresh
    Client per attempt; if each had its own list, retries would never advance."""

    def __init__(self, responses):
        self._responses = responses  # NB: shared reference, not copy

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, params=None, headers=None):
        if not self._responses:
            raise RuntimeError("Test ran out of canned responses")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _patch_httpx(monkeypatch, metadata, responses, sleep_calls=None):
    """Patch metadata.httpx.Client to return our fake. The retry helper opens
    a fresh Client per attempt, so all instances must share one response queue
    or each retry would see the same first response.

    Optionally capture sleep calls so we can assert backoff was applied without
    actually waiting."""
    shared = list(responses)
    monkeypatch.setattr(metadata.httpx, "Client", lambda *a, **kw: _FakeClient(shared))
    # The retry helper does a local `import time as _time`; stub its sleep so
    # tests do not actually wait 1+3 seconds.
    import time
    if sleep_calls is None:
        sleep_calls = []
    monkeypatch.setattr(time, "sleep", lambda s: sleep_calls.append(s))
    return sleep_calls


def test_retry_helper_returns_first_success(monkeypatch):
    metadata = _import_metadata_or_skip()
    sleeps = _patch_httpx(monkeypatch, metadata, [_FakeResp(200, {"artists": []})])

    resp = metadata._mb_get_with_retry(
        "https://example/", params={}, headers={}, timeout=1,
    )
    assert resp.status_code == 200
    assert sleeps == []  # No retries needed, no backoff


def test_retry_helper_retries_on_503_then_succeeds(monkeypatch):
    metadata = _import_metadata_or_skip()
    sleeps = _patch_httpx(monkeypatch, metadata, [
        _FakeResp(503), _FakeResp(200, {"ok": True}),
    ])

    resp = metadata._mb_get_with_retry(
        "https://example/", params={}, headers={}, timeout=1,
    )
    assert resp.status_code == 200
    assert sleeps == [1]  # One backoff between two attempts


def test_retry_helper_retries_on_timeout_then_succeeds(monkeypatch):
    metadata = _import_metadata_or_skip()
    import httpx
    sleeps = _patch_httpx(monkeypatch, metadata, [
        httpx.TimeoutException("slow"),
        _FakeResp(200, {"ok": True}),
    ])

    resp = metadata._mb_get_with_retry(
        "https://example/", params={}, headers={}, timeout=1,
    )
    assert resp.status_code == 200
    assert sleeps == [1]


def test_retry_helper_raises_unavailable_after_persistent_failures(monkeypatch):
    metadata = _import_metadata_or_skip()
    sleeps = _patch_httpx(monkeypatch, metadata, [
        _FakeResp(503), _FakeResp(502), _FakeResp(504),
    ])

    with pytest.raises(metadata.MusicBrainzUnavailable):
        metadata._mb_get_with_retry(
            "https://example/", params={}, headers={}, timeout=1,
        )
    # Two backoffs (after attempts 1 and 2)
    assert sleeps == [1, 3]


def test_retry_helper_does_not_swallow_unexpected_exceptions(monkeypatch):
    metadata = _import_metadata_or_skip()
    _patch_httpx(monkeypatch, metadata, [ValueError("boom")])

    with pytest.raises(ValueError):
        metadata._mb_get_with_retry(
            "https://example/", params={}, headers={}, timeout=1,
        )


def test_search_artist_propagates_unavailable(monkeypatch):
    """The Albums tab endpoint relies on this propagation to send back HTTP 503."""
    metadata = _import_metadata_or_skip()
    _patch_httpx(monkeypatch, metadata, [_FakeResp(503), _FakeResp(503), _FakeResp(503)])

    with pytest.raises(metadata.MusicBrainzUnavailable):
        metadata.search_artist_mbid("Bowie")


def test_search_artist_returns_empty_list_on_4xx(monkeypatch):
    """4xx is a definitive 'no data', not a network problem."""
    metadata = _import_metadata_or_skip()
    _patch_httpx(monkeypatch, metadata, [_FakeResp(400, {})])

    assert metadata.search_artist_mbid("???") == []


def test_fetch_album_tracks_propagates_unavailable(monkeypatch):
    metadata = _import_metadata_or_skip()
    _patch_httpx(monkeypatch, metadata, [_FakeResp(503), _FakeResp(503), _FakeResp(503)])

    with pytest.raises(metadata.MusicBrainzUnavailable):
        metadata.fetch_album_tracks("fake-mbid")


# ---------------------------------------------------------------------------
# Track numbers on singles (GitLab #43)
#
# MusicBrainz returns the matched track under `track` (singular) from the search
# API and `tracks` (plural) from the lookup API. We read the wrong one for
# years, so singles arrived with no track number at all.
# ---------------------------------------------------------------------------


def test_extract_track_position_reads_search_api_singular_key():
    """Search API shape: media[].track[] (singular), number as a string."""
    metadata = _import_metadata_or_skip()
    release = {
        "media": [{
            "position": 2,
            "track": [{"id": "abc", "number": "7", "title": "Praise You"}],
            "track-count": 19,
            "track-offset": 6,
        }]
    }
    assert metadata._extract_track_position(release) == (7, 19)


def test_extract_track_position_reads_lookup_api_plural_key():
    """Lookup API shape: media[].tracks[] (plural), one matched entry."""
    metadata = _import_metadata_or_skip()
    release = {
        "media": [{
            "position": 1,
            "tracks": [{"position": 3, "number": "3", "title": "Rockafeller Skank"}],
            "track-count": 12,
            "track-offset": 2,
        }]
    }
    assert metadata._extract_track_position(release) == (3, 12)


def test_extract_track_position_falls_back_to_offset_for_vinyl():
    """Vinyl prints 'A1' rather than '1'; track-offset keeps us honest."""
    metadata = _import_metadata_or_skip()
    release = {
        "media": [{
            "track": [{"number": "B2", "title": "Side two, second one"}],
            "track-count": 10,
            "track-offset": 6,
        }]
    }
    assert metadata._extract_track_position(release) == (7, 10)


def test_extract_track_position_skips_empty_media():
    """An empty first medium must not stop us finding the populated one."""
    metadata = _import_metadata_or_skip()
    release = {
        "media": [
            {"track": [], "track-count": 0},
            {"track": [{"number": "4"}], "track-count": 11, "track-offset": 3},
        ]
    }
    assert metadata._extract_track_position(release) == (4, 11)


def test_extract_track_position_returns_none_when_hopeless():
    metadata = _import_metadata_or_skip()
    assert metadata._extract_track_position({}) == (None, None)
    assert metadata._extract_track_position({"media": []}) == (None, None)
    assert metadata._extract_track_position(
        {"media": [{"track": [{"number": "A"}], "track-count": 5}]}
    ) == (None, None)


def test_lookup_musicbrainz_tags_track_number_from_search_response(monkeypatch):
    """The real regression: a plain text search must yield a track number."""
    metadata = _import_metadata_or_skip()
    monkeypatch.setattr(metadata, "get_setting_bool", lambda *a, **kw: True)
    monkeypatch.setattr(
        metadata, "_mb_resolve_recording_via_release_group",
        lambda *args, **kwargs: None,
    )
    _patch_httpx(monkeypatch, metadata, [_FakeResp(200, {
        "recordings": [{
            "id": "rec-1",
            "score": 100,
            "title": "Praise You",
            "length": 323000,
            "artist-credit": [{"name": "Fatboy Slim"}],
            "releases": [{
                "id": "rel-1",
                "title": "You've Come a Long Way, Baby",
                "date": "1998-10-19",
                "release-group": {"primary-type": "Album"},
                "media": [{
                    "position": 1,
                    "track": [{"id": "t-1", "number": "6", "title": "Praise You"}],
                    "track-count": 11,
                    "track-offset": 5,
                }],
            }],
        }]
    })])

    result = metadata.lookup_musicbrainz("Fatboy Slim", "Praise You")
    assert result["track_number"] == 6
    assert result["track_total"] == 11


def test_lookup_musicbrainz_by_id_requests_media_inc(monkeypatch):
    """Without inc=media the release has no media block, so no track number."""
    metadata = _import_metadata_or_skip()
    seen = {}

    class _RecordingClient(_FakeClient):
        def get(self, url, params=None, headers=None):
            seen["inc"] = (params or {}).get("inc", "")
            return super().get(url, params=params, headers=headers)

    monkeypatch.setattr(
        metadata.httpx, "Client",
        lambda *a, **kw: _RecordingClient([_FakeResp(200, {
            "length": 323000,
            "releases": [{
                "id": "rel-1",
                "title": "You've Come a Long Way, Baby",
                "date": "1998",
                "release-group": {"primary-type": "Album"},
                "media": [{
                    "tracks": [{"position": 6, "number": "6"}],
                    "track-count": 11,
                    "track-offset": 5,
                }],
            }],
        })]),
    )

    result = metadata._lookup_musicbrainz_by_id("rec-1", expected_artist="Fatboy Slim")
    assert "media" in seen["inc"].split()
    assert result["track_number"] == 6
    assert result["track_total"] == 11


# ---------------------------------------------------------------------------
# MusicBrainz release URLs -> album download fields
# ---------------------------------------------------------------------------


def test_parse_musicbrainz_release_url_accepts_the_real_shapes():
    metadata = _import_metadata_or_skip()
    mbid = "435fe38e-404c-3887-8300-cc94420a121c"
    cases = {
        f"https://musicbrainz.org/release/{mbid}": ("release", mbid),
        f"https://musicbrainz.org/release-group/{mbid}": ("release-group", mbid),
        f"http://musicbrainz.org/release/{mbid}": ("release", mbid),
        f"https://beta.musicbrainz.org/release-group/{mbid}/disc/1": ("release-group", mbid),
        f"https://MUSICBRAINZ.org/RELEASE/{mbid.upper()}": ("release", mbid),
    }
    for url, expected in cases.items():
        assert metadata.parse_musicbrainz_release_url(url) == expected, url


def test_parse_musicbrainz_release_url_rejects_everything_else():
    metadata = _import_metadata_or_skip()
    mbid = "435fe38e-404c-3887-8300-cc94420a121c"
    for url in (
        f"https://musicbrainz.org/artist/{mbid}",
        f"https://musicbrainz.org/recording/{mbid}",
        "https://musicbrainz.org/release/not-a-uuid",
        "https://open.spotify.com/album/4aawyAB9vmqN3uQ7FjRGTy",
        "musicbrainz.org/release/" + mbid,  # No scheme, no deal
        "not a url at all",
        "",
        None,
    ):
        assert metadata.parse_musicbrainz_release_url(url) is None, url


def test_pick_release_from_group_prefers_official_and_earliest():
    metadata = _import_metadata_or_skip()
    chosen = metadata._pick_release_from_group([
        {"id": "bootleg", "status": "Bootleg", "date": "1997-01-01"},
        {"id": "reissue", "status": "Official", "date": "2008-05-05"},
        {"id": "original", "status": "Official", "date": "1998-10-12"},
    ])
    assert chosen["id"] == "original"


def test_pick_release_from_group_does_not_let_vague_dates_win():
    """A bare '1998' string-sorts before '1998-10-12'; it must not win on that."""
    metadata = _import_metadata_or_skip()
    chosen = metadata._pick_release_from_group([
        {"id": "vague", "status": "Official", "date": "1998"},
        {"id": "precise", "status": "Official", "date": "1998-10-12"},
    ])
    assert chosen["id"] == "precise"


def test_pick_release_from_group_handles_missing_dates_and_empties():
    metadata = _import_metadata_or_skip()
    assert metadata._pick_release_from_group([]) is None
    assert metadata._pick_release_from_group([{"no": "id"}]) is None
    chosen = metadata._pick_release_from_group([
        {"id": "dateless", "status": "Official"},
        {"id": "dated", "status": "Official", "date": "2001-01-01"},
    ])
    assert chosen["id"] == "dated"


def test_fetch_release_summary_returns_album_download_fields(monkeypatch):
    metadata = _import_metadata_or_skip()
    monkeypatch.setattr(metadata, "get_setting_bool", lambda *a, **kw: True)
    _patch_httpx(monkeypatch, metadata, [_FakeResp(200, {
        "id": "rel-1",
        "title": "You've Come a Long Way, Baby",
        "date": "1998-10-12",
        "artist-credit": [{"name": "Fatboy Slim"}],
        "media": [{"track-count": 11}],
    })])

    summary = metadata.fetch_release_summary("release", "rel-1")
    assert summary == {
        "artist": "Fatboy Slim",
        "album_title": "You've Come a Long Way, Baby",
        "release_mbid": "rel-1",
        "year": "1998",
        "track_count": 11,
    }


def test_fetch_release_summary_sums_multi_disc_track_counts(monkeypatch):
    metadata = _import_metadata_or_skip()
    monkeypatch.setattr(metadata, "get_setting_bool", lambda *a, **kw: True)
    _patch_httpx(monkeypatch, metadata, [_FakeResp(200, {
        "id": "rel-2",
        "title": "A Double Album",
        "date": "2001",
        "artist-credit": [{"name": "Someone"}],
        "media": [{"track-count": 12}, {"track-count": 9}],
    })])

    assert metadata.fetch_release_summary("release", "rel-2")["track_count"] == 21


def test_fetch_release_summary_resolves_a_release_group(monkeypatch):
    """Group URLs are what MusicBrainz search actually hands you."""
    metadata = _import_metadata_or_skip()
    monkeypatch.setattr(metadata, "get_setting_bool", lambda *a, **kw: True)
    _patch_httpx(monkeypatch, metadata, [
        _FakeResp(200, {  # release-group lookup
            "id": "rg-1",
            "title": "You've Come a Long Way, Baby",
            "releases": [
                {"id": "rel-late", "status": "Official", "date": "2008-01-01"},
                {"id": "rel-first", "status": "Official", "date": "1998-10-12"},
            ],
        }),
        _FakeResp(200, {  # then the chosen release
            "id": "rel-first",
            "title": "You've Come a Long Way, Baby",
            "date": "1998-10-12",
            "artist-credit": [{"name": "Fatboy Slim"}],
            "media": [{"track-count": 11}],
        }),
    ])

    summary = metadata.fetch_release_summary("release-group", "rg-1")
    assert summary["release_mbid"] == "rel-first"
    assert summary["artist"] == "Fatboy Slim"


def test_fetch_release_summary_joins_multiple_credited_artists(monkeypatch):
    metadata = _import_metadata_or_skip()
    monkeypatch.setattr(metadata, "get_setting_bool", lambda *a, **kw: True)
    _patch_httpx(monkeypatch, metadata, [_FakeResp(200, {
        "id": "rel-3",
        "title": "A Collaboration",
        "date": "2015-03-01",
        "artist-credit": [
            {"name": "Underworld", "joinphrase": " & "},
            {"name": "Iggy Pop"},
        ],
        "media": [{"track-count": 8}],
    })])

    assert metadata.fetch_release_summary("release", "rel-3")["artist"] == "Underworld & Iggy Pop"


def test_text_guess_joins_multiple_credited_artists_without_double_space(monkeypatch):
    """Recording credits get the same joinphrase treatment as release credits.

    MusicBrainz puts the separator inside each credit's joinphrase, so joining
    on a space too used to give "Underworld &  Iggy Pop". No release here, so
    the function returns before it would go anywhere near the network.
    """
    metadata = _import_metadata_or_skip()
    recording = {
        "id": "rec-1",
        "title": "Born Slippy",
        "artist-credit": [
            {"name": "Underworld", "joinphrase": " & "},
            {"name": "Iggy Pop"},
        ],
    }

    guess = metadata._build_musicbrainz_guess_for_release(
        recording, None, "Underworld", "Born Slippy", {},
    )
    assert guess["artist"] == "Underworld & Iggy Pop"
    assert guess["album_artist"] == "Underworld & Iggy Pop"


def test_text_guess_joins_release_credits_without_double_space(monkeypatch):
    """Same again for the release's own credit list. The release lookup is
    given a 404 so we stop right after the join we care about."""
    metadata = _import_metadata_or_skip()
    _patch_httpx(monkeypatch, metadata, [_FakeResp(404, {})])
    recording = {"id": "rec-2", "title": "Go", "artist-credit": [{"name": "The Chemical Brothers"}]}
    release = {
        "id": "rel-9",
        "title": "Born in the Echoes",
        "date": "2015-07-17",
        "artist-credit": [
            {"name": "The Chemical Brothers", "joinphrase": " feat. "},
            {"name": "Q-Tip"},
        ],
    }

    guess = metadata._build_musicbrainz_guess_for_release(
        recording, release, "The Chemical Brothers", "Go", {},
    )
    assert guess["album_artist"] == "The Chemical Brothers feat. Q-Tip"
    assert guess["album"] == "Born in the Echoes"
    assert guess["year"] == "2015"


def test_fetch_release_summary_returns_none_for_unknown_release(monkeypatch):
    metadata = _import_metadata_or_skip()
    monkeypatch.setattr(metadata, "get_setting_bool", lambda *a, **kw: True)
    _patch_httpx(monkeypatch, metadata, [_FakeResp(404, {})])
    assert metadata.fetch_release_summary("release", "nope") is None


def test_fetch_release_summary_returns_none_without_artist_or_title(monkeypatch):
    metadata = _import_metadata_or_skip()
    monkeypatch.setattr(metadata, "get_setting_bool", lambda *a, **kw: True)
    _patch_httpx(monkeypatch, metadata, [_FakeResp(200, {"id": "rel-4", "title": ""})])
    assert metadata.fetch_release_summary("release", "rel-4") is None


def test_fetch_release_summary_respects_the_musicbrainz_kill_switch(monkeypatch):
    metadata = _import_metadata_or_skip()
    monkeypatch.setattr(metadata, "get_setting_bool", lambda *a, **kw: False)
    assert metadata.fetch_release_summary("release", "rel-1") is None


def test_fetch_release_summary_propagates_unavailable(monkeypatch):
    metadata = _import_metadata_or_skip()
    monkeypatch.setattr(metadata, "get_setting_bool", lambda *a, **kw: True)
    _patch_httpx(monkeypatch, metadata, [_FakeResp(503), _FakeResp(503), _FakeResp(503)])
    with pytest.raises(metadata.MusicBrainzUnavailable):
        metadata.fetch_release_summary("release", "rel-1")
