"""Settings read/write tests. All writes are restore-after, leaving the service clean."""

import os
import queue
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
from settings import SETTINGS_SCHEMA, get_setting_bool


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
    "monochrome_browser_fallback_enabled",
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


def test_fresh_install_keeps_source_format_by_default():
    assert SETTINGS_SCHEMA["default_convert_to_flac"]["default"] is False


def test_fresh_install_enables_monochrome_browser_fallback():
    assert SETTINGS_SCHEMA["monochrome_browser_fallback_enabled"]["default"] is True


def test_existing_conversion_preference_survives_db_migrations(tmp_path, monkeypatch):
    """Changing the fresh-install default must not rewrite an existing choice."""
    old_db = tmp_path / "pre-upgrade.db"
    with sqlite3.connect(old_db) as conn:
        conn.execute("""
            CREATE TABLE settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.executemany(
            "INSERT INTO settings (key, value) VALUES (?, ?)",
            [
                ("db_version", "0"),
                ("default_convert_to_flac", "true"),
            ],
        )

    # Isolate init_db's connection pool so this temporary database cannot leak
    # into the rest of the suite.
    migration_pool = queue.LifoQueue(maxsize=db._DB_POOL_SIZE)
    monkeypatch.setattr(db, "_db_pool", migration_pool)
    monkeypatch.setattr(db, "DB_PATH", str(old_db))
    monkeypatch.delenv("DEFAULT_CONVERT_TO_FLAC", raising=False)
    migration_pool.put(db.get_db())

    try:
        db.init_db()
        assert get_setting_bool("default_convert_to_flac", False) is True
    finally:
        while not migration_pool.empty():
            migration_pool.get_nowait().close()


def test_settings_monochrome_enabled_by_default(api, base_url):
    settings = api.get(f"{base_url}/api/settings", timeout=10).json()["settings"]
    assert settings["source_monochrome_enabled"] is True


def test_settings_monochrome_urls_non_empty(api, base_url):
    """Monochrome URL settings must have non-empty defaults (migration guard)."""
    settings = api.get(f"{base_url}/api/settings", timeout=10).json()["settings"]
    assert settings.get("monochrome_hifi_api_url"), "monochrome_hifi_api_url is blank; migration may have failed"
    assert settings.get("monochrome_qobuz_proxy_url"), "monochrome_qobuz_proxy_url is blank; migration may have failed"


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


def test_email_test_endpoint_reports_invalid_form_values(api, base_url):
    """The SMTP tester must explain bad input without attempting delivery."""
    r = api.post(
        f"{base_url}/api/settings/test/email",
        json={
            "smtp_host": "",
            "smtp_port": 587,
            "smtp_user": "",
            "smtp_pass": "",
            "smtp_from": "",
            "smtp_to": "tester@example.com",
            "smtp_tls": True,
        },
        timeout=10,
    )

    assert r.status_code == 200
    assert r.json() == {"success": False, "message": "SMTP host is required"}


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


# ---------------------------------------------------------------------------
# Schema / API-model parity
#
# Settings are written through the SettingsUpdate Pydantic model, which drops
# any field it does not declare. Adding a key to SETTINGS_SCHEMA and wiring up
# the UI is therefore not enough: without a matching model field the PUT
# silently succeeds and changes nothing, which is a genuinely annoying way to
# spend an afternoon. Ask me how I know.
# ---------------------------------------------------------------------------

# Schema keys deliberately not writable through /api/settings. Anything else
# missing from SettingsUpdate is a wiring bug, not a design decision.
_NON_WRITABLE_SETTINGS = {
    "spotify_cookies_expired",   # Internal state, set by the cookie check
    "acoustid_api_key",          # Written via its own dedicated flow
    "max_concurrent_downloads",  # Not exposed in the Settings UI
    "webhook_url",
    "youtube_bot_backoff_min",
    "youtube_bot_backoff_max",
}


def test_every_settings_schema_key_is_writable_or_knowingly_excluded():
    from models import SettingsUpdate
    model_fields = set(SettingsUpdate.model_fields.keys())
    missing = set(SETTINGS_SCHEMA) - model_fields - _NON_WRITABLE_SETTINGS
    assert not missing, (
        "These settings exist in SETTINGS_SCHEMA but have no SettingsUpdate "
        f"field, so writing them silently does nothing: {sorted(missing)}"
    )


def test_settings_update_model_has_no_phantom_fields():
    """A model field with no schema key can never be persisted."""
    from models import SettingsUpdate
    phantom = set(SettingsUpdate.model_fields.keys()) - set(SETTINGS_SCHEMA)
    assert not phantom, f"SettingsUpdate fields with no schema entry: {sorted(phantom)}"


def test_replaygain_settings_are_present_and_default_off():
    from models import SettingsUpdate
    for key in ("enable_replaygain", "replaygain_replace_existing"):
        assert key in SETTINGS_SCHEMA, f"{key} missing from SETTINGS_SCHEMA"
        assert SETTINGS_SCHEMA[key]["default"] is False, f"{key} must be opt-in"
        assert key in SettingsUpdate.model_fields, f"{key} not writable via the API"
