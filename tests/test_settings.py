"""Settings read/write tests. All writes are restore-after, leaving the service clean."""


EXPECTED_KEYS = [
    "music_dir",
    "enable_musicbrainz",
    "enable_lyrics",
    "default_convert_to_flac",
    "audio_format",
    "min_audio_bitrate",
    "singles_subdir",
    "playlists_subdir",
    "albums_subdir",
    "organise_by_artist",
    "include_track_number_in_filename",
    "skip_dupes",
    "source_offline_fallback",
    "source_health_checks_enabled",
    "source_health_check_interval_minutes",
    "source_health_cooldown_minutes",
    "notify_on",
]


def test_settings_get(api, base_url):
    r = api.get(f"{base_url}/api/settings", timeout=10)
    assert r.status_code == 200
    assert "settings" in r.json()


def test_settings_all_expected_keys_present(api, base_url):
    settings = api.get(f"{base_url}/api/settings", timeout=10).json()["settings"]
    for key in EXPECTED_KEYS:
        assert key in settings, f"settings missing key: {key}"


def test_settings_audio_format_is_valid(api, base_url):
    settings = api.get(f"{base_url}/api/settings", timeout=10).json()["settings"]
    assert settings["audio_format"] in ("flac", "alac", "opus", "mp3")


def test_settings_monochrome_enabled_by_default(api, base_url):
    settings = api.get(f"{base_url}/api/settings", timeout=10).json()["settings"]
    assert settings["source_monochrome_enabled"] is True


def test_settings_monochrome_urls_non_empty(api, base_url):
    """Monochrome URL settings must have non-empty defaults (migration guard)."""
    settings = api.get(f"{base_url}/api/settings", timeout=10).json()["settings"]
    assert settings.get("monochrome_hifi_api_url"), "monochrome_hifi_api_url is blank — migration may have failed"
    assert settings.get("monochrome_qobuz_proxy_url"), "monochrome_qobuz_proxy_url is blank — migration may have failed"


def test_settings_write_and_restore(api, base_url):
    """Toggle organise_by_artist, verify it persists, then restore."""
    original = api.get(f"{base_url}/api/settings", timeout=10).json()["settings"]
    original_val = original["organise_by_artist"]
    flipped = not original_val

    r = api.put(f"{base_url}/api/settings", json={"organise_by_artist": flipped}, timeout=10)
    assert r.status_code == 200

    updated = api.get(f"{base_url}/api/settings", timeout=10).json()["settings"]
    assert updated["organise_by_artist"] == flipped, "setting didn't persist after PUT"

    # Restore
    api.put(f"{base_url}/api/settings", json={"organise_by_artist": original_val}, timeout=10)
    restored = api.get(f"{base_url}/api/settings", timeout=10).json()["settings"]
    assert restored["organise_by_artist"] == original_val


def test_settings_put_unknown_key_ignored(api, base_url):
    """Unknown fields in PUT body should not cause a 500."""
    r = api.put(f"{base_url}/api/settings", json={"not_a_real_setting_xyz": True}, timeout=10)
    assert r.status_code == 200


def test_settings_mp3_bitrate_write_and_restore(api, base_url):
    """mp3_bitrate should persist (this regressed in v2.8.2)."""
    settings = api.get(f"{base_url}/api/settings", timeout=10).json()["settings"]
    original = settings.get("mp3_bitrate", "v2")

    target = "320k" if original != "320k" else "v0"
    api.put(f"{base_url}/api/settings", json={"mp3_bitrate": target}, timeout=10)
    updated = api.get(f"{base_url}/api/settings", timeout=10).json()["settings"]
    assert updated["mp3_bitrate"] == target, "mp3_bitrate didn't persist"

    api.put(f"{base_url}/api/settings", json={"mp3_bitrate": original}, timeout=10)


def test_settings_opus_bitrate_write_and_restore(api, base_url):
    """opus_bitrate should persist."""
    settings = api.get(f"{base_url}/api/settings", timeout=10).json()["settings"]
    original = settings.get("opus_bitrate", "320k")

    target = "256k" if original != "256k" else "192k"
    api.put(f"{base_url}/api/settings", json={"opus_bitrate": target}, timeout=10)
    updated = api.get(f"{base_url}/api/settings", timeout=10).json()["settings"]
    assert updated["opus_bitrate"] == target, "opus_bitrate didn't persist"

    api.put(f"{base_url}/api/settings", json={"opus_bitrate": original}, timeout=10)
