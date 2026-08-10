"""
Unit tests for metadata.fetch_artist_albums, the call behind the Albums tab's
"Albums by X" list and behind watched-artist album following.

The regression that prompted these: clicking "Haunted House" on a Knife Party
search result reported that MusicBrainz had never heard of it. MusicBrainz had
heard of it perfectly well; we were asking for `type=album` and Haunted House is
typed EP, as is nearly everything Knife Party ever released. The whole
discography came back as one record, Abandon Ship, and the album browser
politely told the user to pick something manually from a list of one.

Payloads below are shaped like the real thing. httpx is monkeypatched, so
nothing here touches the network.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

KNIFE_PARTY_MBID = "82a8aee6-0772-4399-9238-b5903eab413f"


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


class _RecordingClient:
    """httpx.Client stand-in that hands back canned pages and keeps a copy of
    every query it was asked to make, so a test can check what we actually
    requested rather than only what we did with the answer."""

    def __init__(self, responses, calls):
        self._responses = responses
        self._calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, params=None, headers=None):
        self._calls.append({"url": url, "params": dict(params or {})})
        if not self._responses:
            raise RuntimeError("Test ran out of canned responses")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _patch_httpx(monkeypatch, metadata, responses):
    """Returns the list of calls made, filled in as the code under test runs."""
    shared = list(responses)
    calls: list[dict] = []
    monkeypatch.setattr(metadata.httpx, "Client", lambda *a, **kw: _RecordingClient(shared, calls))
    import time
    monkeypatch.setattr(time, "sleep", lambda s: None)
    return calls


def _release(
    title, date, rel_id, rg_id, primary_type="Album", secondary=None,
    *, status=None, media=None, group_title=None,
):
    """One release as the MusicBrainz browse endpoint returns it."""
    return {
        "id": rel_id,
        "title": title,
        "date": date,
        "status": status,
        "media": media or [],
        "release-group": {
            "id": rg_id,
            "title": group_title or title,
            "primary-type": primary_type,
            "secondary-types": secondary or [],
        },
    }


# The shape of a real browse response for Knife Party, trimmed to the bits we read.
KNIFE_PARTY_PAGE = {
    "release-count": 6,
    "releases": [
        _release("100% No Modern Talking", "2011-12-12", "rel-100", "rg-100", "EP"),
        _release("Rage Valley", "2012-05-28", "rel-rage", "rg-rage", "EP"),
        _release("Haunted House", "2013-05-06", "rel-haunted", "rg-haunted", "EP"),
        _release("Abandon Ship", "2014-11-24", "rel-abandon", "rg-abandon", "Album"),
        _release("Trigger Warning", "2015-11-13", "rel-trigger", "rg-trigger", "EP"),
        _release("Knife Party Live at Lost Lands 2022", "2022-09-25",
                 "rel-live", "rg-live", "Album", ["Live", "DJ-mix"]),
    ],
}


def test_we_ask_musicbrainz_for_eps_as_well_as_albums(monkeypatch):
    """The one-word bug. `type=album` alone hides most electronic discographies."""
    metadata = _import_metadata_or_skip()
    calls = _patch_httpx(monkeypatch, metadata, [_FakeResp(200, KNIFE_PARTY_PAGE)])

    metadata.fetch_artist_albums(KNIFE_PARTY_MBID)

    assert calls, "no request was made at all"
    assert calls[0]["params"]["type"] == "album|ep"
    assert calls[0]["params"]["artist"] == KNIFE_PARTY_MBID


def test_singles_are_still_left_out(monkeypatch):
    """Deliberate: a prolific artist's list should not become a hundred one-track
    entries you have to scroll past to reach the records."""
    metadata = _import_metadata_or_skip()
    calls = _patch_httpx(monkeypatch, metadata, [_FakeResp(200, KNIFE_PARTY_PAGE)])

    metadata.fetch_artist_albums(KNIFE_PARTY_MBID)

    assert "single" not in calls[0]["params"]["type"]


def test_the_ep_that_started_all_this_comes_back(monkeypatch):
    metadata = _import_metadata_or_skip()
    _patch_httpx(monkeypatch, metadata, [_FakeResp(200, KNIFE_PARTY_PAGE)])

    albums = metadata.fetch_artist_albums(KNIFE_PARTY_MBID)
    titles = [a["title"] for a in albums]

    assert "Haunted House" in titles, f"got {titles}"
    haunted = next(a for a in albums if a["title"] == "Haunted House")
    assert haunted["year"] == "2013"
    assert haunted["release_mbid"] == "rel-haunted"
    assert haunted["release_group_mbid"] == "rg-haunted"


def test_an_ep_heavy_artist_gets_a_discography_rather_than_a_single_record(monkeypatch):
    """Before the fix this list was exactly one entry long."""
    metadata = _import_metadata_or_skip()
    _patch_httpx(monkeypatch, metadata, [_FakeResp(200, KNIFE_PARTY_PAGE)])

    titles = [a["title"] for a in metadata.fetch_artist_albums(KNIFE_PARTY_MBID)]

    assert titles == [
        "100% No Modern Talking",
        "Rage Valley",
        "Haunted House",
        "Abandon Ship",
        "Trigger Warning",
    ], "EPs and albums should interleave in release order"


def test_each_entry_says_whether_it_is_an_album_or_an_ep(monkeypatch):
    """The list now holds two kinds of thing, so it has to be able to say which."""
    metadata = _import_metadata_or_skip()
    _patch_httpx(monkeypatch, metadata, [_FakeResp(200, KNIFE_PARTY_PAGE)])

    by_title = {a["title"]: a["primary_type"] for a in metadata.fetch_artist_albums(KNIFE_PARTY_MBID)}

    assert by_title["Haunted House"] == "EP"
    assert by_title["Abandon Ship"] == "Album"


def test_live_and_dj_mix_records_are_still_shown_the_door(monkeypatch):
    """Widening to EPs must not quietly widen to everything else as well."""
    metadata = _import_metadata_or_skip()
    _patch_httpx(monkeypatch, metadata, [_FakeResp(200, KNIFE_PARTY_PAGE)])

    titles = [a["title"] for a in metadata.fetch_artist_albums(KNIFE_PARTY_MBID)]

    assert "Knife Party Live at Lost Lands 2022" not in titles


def test_a_remix_ep_is_excluded_the_same_as_a_remix_album(monkeypatch):
    """The secondary-type filter has to apply to EPs too, not just albums."""
    metadata = _import_metadata_or_skip()
    _patch_httpx(monkeypatch, metadata, [_FakeResp(200, {
        "release-count": 2,
        "releases": [
            _release("Rage Valley", "2012-05-28", "rel-rage", "rg-rage", "EP"),
            _release("Rage Valley (Remixes)", "2012-08-01", "rel-rmx", "rg-rmx", "EP", ["Remix"]),
        ],
    })])

    titles = [a["title"] for a in metadata.fetch_artist_albums(KNIFE_PARTY_MBID)]

    assert titles == ["Rage Valley"]


def test_one_entry_per_release_group_even_when_the_ep_was_pressed_twice(monkeypatch):
    """Six pressings of the same EP is still one EP."""
    metadata = _import_metadata_or_skip()
    _patch_httpx(monkeypatch, metadata, [_FakeResp(200, {
        "release-count": 3,
        "releases": [
            _release("Haunted House", "2013-05-06", "rel-a", "rg-haunted", "EP"),
            _release("Haunted House", "2013-05-07", "rel-b", "rg-haunted", "EP"),
            _release("Haunted House", "2014-01-01", "rel-c", "rg-haunted", "EP"),
        ],
    })])

    albums = metadata.fetch_artist_albums(KNIFE_PARTY_MBID)

    assert len(albums) == 1
    assert albums[0]["release_mbid"] == "rel-a", "the earliest equivalent release should win"


def test_complete_official_multi_disc_release_beats_earlier_fragment(monkeypatch):
    metadata = _import_metadata_or_skip()
    _patch_httpx(monkeypatch, metadata, [_FakeResp(200, {
        "release-count": 3,
        "releases": [
            _release(
                "The Complete Thing (disc 1)", "1996-01-01", "rel-fragment",
                "rg-complete", status="Official", media=[{"track-count": 10}],
                group_title="The Complete Thing",
            ),
            _release(
                "The Complete Thing", "1997-01-01", "rel-complete",
                "rg-complete", status="Official",
                media=[{"track-count": 10}, {"track-count": 11}],
                group_title="The Complete Thing",
            ),
            _release(
                "The Complete Thing", "1995-01-01", "rel-bootleg",
                "rg-complete", status="Bootleg",
                media=[{"track-count": 12}, {"track-count": 12}],
                group_title="The Complete Thing",
            ),
        ],
    })])

    albums = metadata.fetch_artist_albums(KNIFE_PARTY_MBID)

    assert albums == [{
        "title": "The Complete Thing",
        "year": "1997",
        "release_mbid": "rel-complete",
        "release_group_mbid": "rg-complete",
        "primary_type": "Album",
    }]
