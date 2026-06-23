"""
Unit tests for monochrome.resolve_by_isrc.

Fast, no-network tests: _qobuz_isrc_lookup is mocked so nothing hits the
Qobuz proxies. We verify the happy path (hires vs lossless), the caller-hint
field fallback, the validation short-circuit on a malformed ISRC, a clean
miss, and the deliberately-conservative transport-failure behaviour.
"""

import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _import_monochrome_or_skip():
    try:
        import monochrome
    except ModuleNotFoundError as exc:
        pytest.skip(f"monochrome module dependencies unavailable: {exc.name}")
    return monochrome


# A well-formed ISRC: Radiohead - Creep, reliably indexed on Qobuz.
_VALID_ISRC = "GBAYE9200070"


def _qobuz_item(hires=False, title="Creep", artist="Radiohead",
                track_id=33933680):
    """A minimal Qobuz catalogue item shaped like _qobuz_isrc_lookup output."""
    item = {
        "id": track_id,
        "isrc": _VALID_ISRC,
        "hires": hires,
        "duration": 238,
        "album": {"image": {"large": "https://cover.test/large.jpg"}},
    }
    if title is not None:
        item["title"] = title
    if artist is not None:
        item["performer"] = {"name": artist}
    return item


# ---------------------------------------------------------------------------
# 1. Valid ISRC, hires match -> HI_RES_LOSSLESS result dict
# ---------------------------------------------------------------------------

def test_hires_match_returns_hires_result():
    monochrome = _import_monochrome_or_skip()
    matches = [_qobuz_item(hires=True)]

    with patch.object(monochrome, "_qobuz_isrc_lookup",
                      return_value=(matches, False)) as mock_lookup:
        result = monochrome.resolve_by_isrc(_VALID_ISRC, "Radiohead", "Creep")

    assert result is not None
    assert result["quality"] == "HI_RES_LOSSLESS"
    assert result["source"] == "monochrome"
    assert result["source_url"].startswith("monochrome://")
    assert _VALID_ISRC in result["source_url"]
    assert "via=isrc-direct" in result["score_breakdown"]
    # Direct identity-pinned pick: flat 1000 plus the hires bonus.
    assert result["quality_score"] == 1000 + monochrome._QUALITY_BONUS["HI_RES_LOSSLESS"]
    mock_lookup.assert_called_once()


# ---------------------------------------------------------------------------
# 2. Valid ISRC, no hires flag -> LOSSLESS
# ---------------------------------------------------------------------------

def test_non_hires_match_returns_lossless():
    monochrome = _import_monochrome_or_skip()
    matches = [_qobuz_item(hires=False)]

    with patch.object(monochrome, "_qobuz_isrc_lookup",
                      return_value=(matches, False)):
        result = monochrome.resolve_by_isrc(_VALID_ISRC, "Radiohead", "Creep")

    assert result is not None
    assert result["quality"] == "LOSSLESS"
    assert result["quality_score"] == 1000 + monochrome._QUALITY_BONUS["LOSSLESS"]


# ---------------------------------------------------------------------------
# 3. Field fallback: missing title/artist -> use the caller's hints
# ---------------------------------------------------------------------------

def test_missing_catalogue_fields_fall_back_to_caller_hints():
    monochrome = _import_monochrome_or_skip()
    # Item carries neither a title nor a performer name.
    matches = [_qobuz_item(hires=True, title=None, artist=None)]

    with patch.object(monochrome, "_qobuz_isrc_lookup",
                      return_value=(matches, False)):
        result = monochrome.resolve_by_isrc(
            _VALID_ISRC, artist="Caller Artist", title="Caller Title")

    assert result is not None
    # title maps to "title", artist maps to "channel" in the result dict.
    assert result["title"] == "Caller Title"
    assert result["channel"] == "Caller Artist"


# ---------------------------------------------------------------------------
# 4. Malformed ISRC -> None, and the lookup is never attempted
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_isrc", ["NOPE", "QT&JC2622262"])
def test_malformed_isrc_short_circuits_before_lookup(bad_isrc):
    monochrome = _import_monochrome_or_skip()

    with patch.object(monochrome, "_qobuz_isrc_lookup") as mock_lookup:
        result = monochrome.resolve_by_isrc(bad_isrc, "Some Artist", "Some Title")

    assert result is None
    # Validation should short-circuit; the proxies must not be touched.
    mock_lookup.assert_not_called()


# ---------------------------------------------------------------------------
# 5. Clean miss: Qobuz answered, has nothing -> None
# ---------------------------------------------------------------------------

def test_clean_miss_returns_none():
    monochrome = _import_monochrome_or_skip()

    with patch.object(monochrome, "_qobuz_isrc_lookup",
                      return_value=([], False)):
        result = monochrome.resolve_by_isrc(_VALID_ISRC, "Radiohead", "Creep")

    assert result is None


# ---------------------------------------------------------------------------
# 6. Transport failure: every proxy fell over -> None (conservative)
# ---------------------------------------------------------------------------

def test_transport_failure_returns_none():
    monochrome = _import_monochrome_or_skip()

    with patch.object(monochrome, "_qobuz_isrc_lookup",
                      return_value=([], True)):
        result = monochrome.resolve_by_isrc(_VALID_ISRC, "Radiohead", "Creep")

    assert result is None
