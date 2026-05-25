import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_monochrome_search_retries_with_punctuation_normalised(monkeypatch):
    import monochrome

    calls = []

    class FakeResponse:
        def __init__(self, items):
            self._items = items

        def raise_for_status(self):
            pass

        def json(self):
            return {"data": {"items": self._items}}

    def fake_get(url, params, headers, timeout, follow_redirects=False):
        calls.append(params["s"])
        if params["s"] == "Artist1 Artist2 trackName":
            return FakeResponse([
                {
                    "id": "123",
                    "title": "trackName",
                    "artist": {"name": "Artist1"},
                    "duration": 180,
                    "isrc": "GBABC1234567",
                    "mediaMetadata": {"tags": ["LOSSLESS"]},
                    "album": {"title": "Album", "cover": ""},
                }
            ])
        return FakeResponse([])

    monkeypatch.setattr(monochrome, "_hifi_api_url", lambda: "https://api.example.test")
    monkeypatch.setattr(monochrome.httpx, "get", fake_get)

    results = monochrome.search_monochrome("Artist1, Artist2 - trackName", limit=5)

    assert calls == ["Artist1, Artist2 - trackName", "Artist1 Artist2 trackName"]
    assert results
    assert results[0]["source"] == "monochrome"


def test_monochrome_search_searches_normalised_variant_even_when_exact_query_returns_items(monkeypatch):
    import monochrome

    calls = []

    class FakeResponse:
        def __init__(self, items):
            self._items = items

        def raise_for_status(self):
            pass

        def json(self):
            return {"data": {"items": self._items}}

    def fake_get(url, params, headers, timeout, follow_redirects=False):
        calls.append(params["s"])
        if params["s"] == "Artist Featured Track":
            return FakeResponse([
                {
                    "id": "456",
                    "title": "Track",
                    "artist": {"name": "Artist"},
                    "duration": 219,
                    "isrc": "USGF19942501",
                    "mediaMetadata": {"tags": ["HIRES_LOSSLESS"]},
                    "album": {"title": "Album", "cover": ""},
                }
            ])
        return FakeResponse([
            {
                "id": "999",
                "title": "Irrelevant Raw Hit",
                "artist": {"name": "Other"},
                "duration": 180,
                "isrc": "USGF19949999",
                "mediaMetadata": {"tags": ["LOSSLESS"]},
                "album": {"title": "Other Album", "cover": ""},
            }
        ])

    monkeypatch.setattr(monochrome, "_hifi_api_url", lambda: "https://api.example.test")
    monkeypatch.setattr(monochrome.httpx, "get", fake_get)

    results = monochrome.search_monochrome("Artist, Featured - Track", limit=5)

    assert calls == ["Artist, Featured - Track", "Artist Featured Track"]
    assert results
    assert results[0]["title"] == "Track"


def test_monochrome_search_continues_to_normalised_variant_after_exact_query_error(monkeypatch):
    import monochrome

    calls = []

    class FakeResponse:
        def __init__(self, items):
            self._items = items

        def raise_for_status(self):
            pass

        def json(self):
            return {"data": {"items": self._items}}

    class BrokenResponse:
        def raise_for_status(self):
            raise RuntimeError("503 Service Unavailable")

    def fake_get(url, params, headers, timeout, follow_redirects=False):
        calls.append(params["s"])
        if params["s"] == "ILLENIUM Emma Grace Brave Soul":
            return FakeResponse([
                {
                    "id": "mono-1",
                    "title": "Brave Soul",
                    "artist": {"name": "ILLENIUM & Emma Grace"},
                    "duration": 217,
                    "isrc": "USAT22100001",
                    "mediaMetadata": {"tags": ["LOSSLESS"]},
                    "album": {"title": "Fallen Embers", "cover": ""},
                }
            ])
        return BrokenResponse()

    monkeypatch.setattr(monochrome, "_hifi_api_url", lambda: "https://api.example.test")
    monkeypatch.setattr(monochrome.httpx, "get", fake_get)

    results = monochrome.search_monochrome("ILLENIUM, Emma Grace - Brave Soul", limit=5)

    assert calls == ["ILLENIUM, Emma Grace - Brave Soul", "ILLENIUM Emma Grace Brave Soul"]
    assert results
    assert results[0]["source"] == "monochrome"
    assert results[0]["title"] == "Brave Soul"


def test_monochrome_search_falls_back_to_next_hifi_api_endpoint(monkeypatch):
    import monochrome

    calls = []
    monochrome._hifi_api_url_cache = None

    class FakeResponse:
        def __init__(self, items):
            self._items = items

        def raise_for_status(self):
            pass

        def json(self):
            return {"data": {"items": self._items}}

    class BrokenResponse:
        def raise_for_status(self):
            raise RuntimeError("503 Service Unavailable")

    def fake_get(url, params, headers, timeout, follow_redirects=False):
        calls.append((url, params["s"]))
        if url.startswith("https://dead.example.test"):
            return BrokenResponse()
        return FakeResponse([
            {
                "id": "mono-2",
                "title": "Brave Soul",
                "artist": {"name": "ILLENIUM & Emma Grace"},
                "duration": 277,
                "isrc": "ZZOPM2106210",
                "mediaMetadata": {"tags": ["LOSSLESS"]},
                "album": {"title": "Fallen Embers", "cover": ""},
            }
        ])

    monkeypatch.setattr(
        monochrome,
        "_hifi_api_url",
        lambda: "https://dead.example.test,https://working.example.test",
    )
    monkeypatch.setattr(monochrome.httpx, "get", fake_get)

    results = monochrome.search_monochrome("ILLENIUM, Emma Grace - Brave Soul", limit=5)

    assert calls[0][0] == "https://dead.example.test/search"
    assert any(url == "https://working.example.test/search" for url, _query in calls)
    assert results
    assert results[0]["source"] == "monochrome"
    assert monochrome._hifi_api_url_cache == "https://working.example.test"


def test_monochrome_playlist_fetch_handles_top_level_playlist_payload(monkeypatch):
    import monochrome

    class FakeResponse:
        def json(self):
            return {
                "version": "2.3",
                "playlist": {
                    "uuid": "0dfc3b10-fbdb-4419-bf54-11b90051fa6c",
                    "title": "Chill Pop",
                    "numberOfTracks": 2,
                },
                "items": [
                    {
                        "item": {
                            "title": "Carrie Bradshaw",
                            "artist": {"name": "Kylie Cantrall"},
                        }
                    },
                    {
                        "item": {
                            "title": "Somebody New",
                            "artist": {"name": "Morgan St. Jean"},
                        }
                    },
                ],
            }

    def fake_hifi_api_get(path, params, timeout):
        assert path == "/playlist/"
        assert params["id"] == "0dfc3b10-fbdb-4419-bf54-11b90051fa6c"
        return FakeResponse()

    monkeypatch.setattr(monochrome, "_hifi_api_get", fake_hifi_api_get)

    tracks, name = monochrome.fetch_tidal_playlist_tracks("0dfc3b10-fbdb-4419-bf54-11b90051fa6c")

    assert name == "Chill Pop"
    assert tracks == [
        ("Kylie Cantrall", "Carrie Bradshaw"),
        ("Morgan St. Jean", "Somebody New"),
    ]


def test_monochrome_playlist_urls_detect_as_monochrome():
    from watched_playlists import detect_playlist_platform

    platform, playlist_id = detect_playlist_platform(
        "https://monochrome.tf/playlist/0dfc3b10-fbdb-4419-bf54-11b90051fa6c"
    )

    assert platform == "monochrome"
    assert playlist_id == "0dfc3b10-fbdb-4419-bf54-11b90051fa6c"
