"""
Regression tests for slskd search result scoring.

Reported by Syrius-consulting: slskd runs the search, slskd's own logs show it
completing with hundreds of files, and not one result ever reaches the UI. The
log line that gives it away:

    slskd search error: cannot access local variable 'adjusted_score'
    where it is not associated with a value

v3.1.0 renamed the running score from `adjusted_score` to `relevance_score` but
only at the point where it is first assigned and where it is read back out; the
five `+=` lines that stack the source-trust, lossless, hi-res, free-slot and
fast-uploader bonuses kept the old name. Python is perfectly happy with that
until the line actually runs, at which point it is an UnboundLocalError.

The blanket `except Exception` around the whole search then swallows it, so the
symptom is not a crash but an eerily empty result list: the very first candidate
that survives the quality and confidence filters takes the entire loop down with
it, before a single result has been appended.

That last detail is why this can hide in plain sight. A search where nothing
clears SLSKD_MATCH_CONFIDENCE_FLOOR never reaches the broken lines and looks
exactly like an honest "no matches".
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import slskd


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class _FakeClient:
    """Just enough slskd API to walk search_slskd from end to end."""

    def __init__(self, responses):
        self._responses = responses

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def post(self, url, **_kwargs):
        return _FakeResponse({"id": "search-1"})

    def get(self, url, **_kwargs):
        if url.endswith("/responses"):
            return _FakeResponse(self._responses)
        return _FakeResponse({"fileCount": 1, "responseCount": 1, "isComplete": True})

    def delete(self, url, **_kwargs):
        return _FakeResponse({})


def _flac_hit(filename="Fleetwood Mac/Rumours/02 Dreams.flac"):
    """One good, unlocked, lossless file from a user with a slot free.

    Deliberately a strong match: it has to clear the quality and confidence
    filters, because those are exactly what used to shield the bug from view.
    """
    return {
        "username": "generous_stranger",
        "hasFreeUploadSlot": True,
        "uploadSpeed": 2_000_000,
        "files": [{
            "filename": filename,
            "size": 40_000_000,
            "length": 257,
            "isLocked": False,
            "bitRate": 1000,
            "bitDepth": 24,
            "sampleRate": 96000,
            "extension": "flac",
        }],
    }


@pytest.fixture
def slskd_ready(monkeypatch):
    """Point slskd at a fake server and stop the poll loop napping."""
    monkeypatch.setattr(slskd, "get_slskd_token", lambda: "token")
    monkeypatch.setattr(slskd, "get_setting", lambda *a, **k: "http://slskd.test")
    monkeypatch.setattr(slskd.time, "sleep", lambda _s: None)
    monkeypatch.setattr(slskd, "SLSKD_REQUIRE_FREE_SLOT", False)

    def _install(responses):
        monkeypatch.setattr(slskd.httpx, "Client", lambda **_kw: _FakeClient(responses))

    return _install


def test_a_good_match_actually_reaches_the_caller(slskd_ready, capsys):
    """The reported bug: hundreds of files in, nothing out."""
    slskd_ready([_flac_hit()])

    results = slskd.search_slskd("Fleetwood Mac - Dreams")

    assert results, (
        "slskd found a strong lossless match and returned nothing. "
        f"stderr: {capsys.readouterr().out}"
    )
    assert results[0]["source"] == "soulseek"
    assert results[0]["slskd_username"] == "generous_stranger"


def test_scoring_does_not_blow_up_on_an_unbound_name(slskd_ready, capsys):
    """Fail loudly on the specific fault rather than just on the empty list."""
    slskd_ready([_flac_hit()])

    slskd.search_slskd("Fleetwood Mac - Dreams")

    printed = capsys.readouterr().out
    assert "adjusted_score" not in printed, f"the old name is still referenced: {printed}"
    assert "slskd search error" not in printed, f"search raised: {printed}"


def test_the_quality_bonuses_actually_land_on_the_score(slskd_ready):
    """They were being added to a name nobody ever read, so they counted for nothing."""
    slskd_ready([_flac_hit()])

    results = slskd.search_slskd("Fleetwood Mac - Dreams")

    breakdown = results[0]["score_breakdown"]
    assert any("soulseek_trust" in note for note in breakdown)
    assert any("lossless" in note for note in breakdown)
    assert any("hires" in note for note in breakdown)
    # A bonus that appears in the breakdown but not in the number is just a lie
    # with extra steps, so check the total actually reflects them.
    claimed = slskd.SLSKD_SOURCE_TRUST_BONUS + slskd.SLSKD_LOSSLESS_BONUS + slskd.SLSKD_HIRES_BONUS
    assert results[0]["relevance_score"] > claimed


def test_a_lossy_hit_scores_below_an_otherwise_equal_lossless_one(slskd_ready):
    """The bonuses have to order results, not just decorate them."""
    lossy = _flac_hit("Fleetwood Mac/Rumours/02 Dreams.mp3")
    lossy["username"] = "mp3_merchant"
    lossy["files"][0].update(extension="mp3", bitRate=320, bitDepth=0, size=8_000_000)

    slskd_ready([_flac_hit(), lossy])
    results = slskd.search_slskd("Fleetwood Mac - Dreams")

    by_user = {r["slskd_username"]: r["relevance_score"] for r in results}
    assert by_user["generous_stranger"] > by_user["mp3_merchant"]


def test_an_empty_response_list_is_still_just_empty(slskd_ready, capsys):
    """The quiet case must stay quiet; no results is not an error."""
    slskd_ready([])

    assert slskd.search_slskd("Nobody - Nothing") == []
    assert "slskd search error" not in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Artist attribution
#
# Second report from Syrius-consulting: a Soulseek download is tagged with the
# *username of the person sharing it* rather than the artist, and then filed
# under that username in the library. The screenshot said it all:
#
#   2jqll9htuy62asp1wu - Ignorance
#   soulseek://2jqll9htuy62asp1wu/@@kvkwm\Music\Paramore\Brand New Eyes\2. Ignorance.flac
#
# Paramore is right there in the path. slskd works it out correctly; the loss
# happens later, where the API projects results for the browser and hardcodes
# artist to None, leaving the frontend to fall back to `channel`, which for
# Soulseek is the username. Longstanding, not a v4 regression: it was simply
# unreachable while slskd search itself was broken.
# ---------------------------------------------------------------------------

def test_the_artist_is_read_from_the_path_not_the_uploader():
    """The reported path, verbatim."""
    artist, title = slskd.extract_track_info_from_path(
        r"@@kvkwm\Music\Paramore\Brand New Eyes\2. Ignorance.flac"
    )

    assert artist == "Paramore"
    assert title == "Ignorance"


def test_search_results_carry_the_real_artist_separately_from_the_username(slskd_ready):
    """slskd keeps them apart; whoever consumes this must not conflate them."""
    hit = _flac_hit(r"@@kvkwm\Music\Paramore\Brand New Eyes\2. Ignorance.flac")
    hit["username"] = "2jqll9htuy62asp1wu"
    slskd_ready([hit])

    results = slskd.search_slskd("Paramore - Ignorance")

    assert results, "no results to check"
    assert results[0]["artist"] == "Paramore"
    assert results[0]["channel"] == "2jqll9htuy62asp1wu"
    assert results[0]["artist"] != results[0]["channel"]


def test_the_uploader_name_is_never_accepted_as_the_artist(monkeypatch):
    """Guard for retried jobs and any other caller that gets this wrong.

    Passing the peer's username in as the artist must not survive; the path
    knows better and should win.
    """
    import downloads

    seen = {}
    monkeypatch.setattr(downloads, "_get_job_album_context", lambda _j: {})
    monkeypatch.setattr(downloads, "_get_album_track_tag_context", lambda _j: (None, None))
    monkeypatch.setattr(downloads, "ensure_album_cover_files", lambda *a, **k: None)
    monkeypatch.setattr(downloads, "get_album_art_context", lambda *a, **k: (None, None))

    def _capture(job_id, **fields):
        seen.update(fields)
        # Stop the download dead once the artist has been settled; everything
        # after this point is network and filesystem, and not what we are here for.
        if "artist" in fields:
            raise RuntimeError("stop here")

    monkeypatch.setattr(downloads, "_update_job", _capture)

    downloads.process_slskd_download(
        job_id="job1",
        username="2jqll9htuy62asp1wu",
        filename=r"@@kvkwm\Music\Paramore\Brand New Eyes\2. Ignorance.flac",
        artist="2jqll9htuy62asp1wu",
        title="Ignorance",
    )

    assert seen.get("artist") == "Paramore", f"artist ended up as {seen.get('artist')!r}"
    assert seen.get("uploader") == "2jqll9htuy62asp1wu", "the peer is still recorded, just not as the artist"
