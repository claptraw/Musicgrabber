"""Per-playlist native FLAC quality policies.

Profiles describe the bytes MusicGrabber asks Monochrome/Qobuz for.  They are
deliberately separate from output conversion: converting a lossy file to FLAC
does not make it satisfy a native-lossless request, and source masters are not
silently resampled just to resemble a selected profile.
"""

from __future__ import annotations

from pathlib import Path

from audio_probe import probe_file


QUALITY_CD_16_44 = "cd_16_44"
QUALITY_HIRES = "hires"
QUALITY_BEST = "best"

QUALITY_PROFILES = {
    QUALITY_CD_16_44,
    QUALITY_HIRES,
    QUALITY_BEST,
}

_LOSSLESS_CODECS = {"flac", "alac", "wav", "wave", "pcm", "ape", "wavpack", "tak"}


def normalise_quality_profile(value: str | None) -> str:
    """Return a supported stored value, preserving old playlists as ``best``."""
    profile = (value or QUALITY_BEST).strip().lower()
    return profile if profile in QUALITY_PROFILES else QUALITY_BEST


def requested_monochrome_quality(profile: str | None) -> str | None:
    """Map a playlist profile to Monochrome's public quality vocabulary.

    ``None`` means the resolver should use an explicit native-lossless ladder
    for ``best``.
    """
    profile = normalise_quality_profile(profile)
    if profile == QUALITY_CD_16_44:
        return "LOSSLESS"
    if profile == QUALITY_HIRES:
        return "HI_RES_LOSSLESS"
    return None


def requested_qobuz_formats(profile: str | None, allow_fallback: bool) -> list[tuple[str, int]] | None:
    """Return the ordered Qobuz format requests for a profile.

    Qobuz format 6 is CD FLAC and format 27 is the highest Hi-Res tier exposed
    by this resolver. ``best`` tries both in descending order.
    """
    profile = normalise_quality_profile(profile)
    if profile == QUALITY_CD_16_44:
        return [("CD_16_44", 6)]
    if profile == QUALITY_HIRES:
        formats = [("HI_RES", 27)]
        if allow_fallback:
            formats.append(("CD_16_44", 6))
        return formats
    return [("BEST_HI_RES", 27), ("BEST_CD", 6)]


def inspect_native_quality(path: Path) -> dict | None:
    """Return observed source properties without changing the downloaded file."""
    return probe_file(path)


def validate_native_quality(
    path: Path,
    profile: str | None,
    allow_fallback: bool = True,
) -> tuple[bool, str, dict | None]:
    """Check that delivered bytes honestly satisfy the requested FLAC profile."""
    profile = normalise_quality_profile(profile)
    info = inspect_native_quality(path)
    if not info:
        return False, "downloaded audio could not be inspected", None

    codec = (info.get("codec") or "").lower()
    bits = int(info.get("bits_per_sample") or 0)
    rate = int(info.get("sample_rate_hz") or 0)
    actual = f"{bits or '?'}-bit/{rate or '?'} Hz {codec or 'unknown'}"

    if codec not in _LOSSLESS_CODECS:
        return False, f"requested native FLAC but received {actual}", info

    if profile == QUALITY_BEST:
        return True, f"best available native lossless: {actual}", info

    if not bits or not rate:
        return False, f"cannot verify requested profile from {actual}", info

    is_cd = bits == 16 and rate == 44_100
    if profile == QUALITY_CD_16_44:
        if is_cd:
            return True, f"CD profile satisfied: {actual}", info
        return False, f"CD profile requires 16-bit/44100 Hz, received {actual}", info

    # Providers expose different Hi-Res ladders. Qobuz can distinguish its
    # format-6 and format-27 tiers; Monochrome/Tidal expose one generic Hi-Res
    # request. The playlist contract is therefore native 24-bit, at whatever
    # sample rate that source actually supplies (44.1 through 192 kHz today).
    is_hires = bits >= 24 and rate >= 44_100
    if is_hires:
        return True, f"native 24-bit Hi-Res profile satisfied: {actual}", info
    if allow_fallback and is_cd:
        return True, f"CD fallback accepted: {actual}", info
    return False, f"native 24-bit Hi-Res profile not available; received {actual}", info
