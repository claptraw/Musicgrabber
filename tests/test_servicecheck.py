"""Unit tests for source health cooldown and lazy re-check behaviour."""

import itertools
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import servicecheck


def setup_function():
    servicecheck._HEALTH.clear()


def teardown_function():
    servicecheck._HEALTH.clear()


def test_failed_check_parks_source_until_cooldown(monkeypatch):
    monkeypatch.setattr(servicecheck, "_checks_enabled", lambda: True)
    monkeypatch.setattr(servicecheck, "_check_interval", lambda: 600)
    monkeypatch.setattr(servicecheck, "_cooldown", lambda: 600)
    monkeypatch.setitem(servicecheck._CHECKS, "unit", lambda: (False, "down"))
    monkeypatch.setattr(servicecheck.time, "time", lambda: 1000)

    entry = servicecheck.check_source("unit", force=True)

    assert entry["healthy"] is False
    assert entry["reason"] == "down"
    assert entry["disabled_until"] == 1600
    assert servicecheck.is_source_available("unit") is False


def test_source_rechecks_after_cooldown_even_inside_interval(monkeypatch):
    outcomes = itertools.chain([(False, "down")], itertools.repeat((True, "")))
    clock = {"now": 1000}

    monkeypatch.setattr(servicecheck, "_checks_enabled", lambda: True)
    monkeypatch.setattr(servicecheck, "_check_interval", lambda: 600)
    monkeypatch.setattr(servicecheck, "_cooldown", lambda: 60)
    monkeypatch.setitem(servicecheck._CHECKS, "unit", lambda: next(outcomes))
    monkeypatch.setattr(servicecheck.time, "time", lambda: clock["now"])

    first = servicecheck.check_source("unit", force=True)
    assert first["healthy"] is False
    assert servicecheck.is_source_available("unit") is False

    clock["now"] = 1061
    second = servicecheck.check_source("unit")

    assert second["healthy"] is True
    assert servicecheck.is_source_available("unit") is True


def test_health_snapshot_does_not_deadlock(monkeypatch):
    monkeypatch.setattr(servicecheck, "_checks_enabled", lambda: True)
    monkeypatch.setattr(servicecheck, "_check_interval", lambda: 600)
    monkeypatch.setattr(servicecheck, "_cooldown", lambda: 600)
    monkeypatch.setitem(servicecheck._CHECKS, "unit", lambda: (True, ""))
    servicecheck.check_source("unit", force=True)

    snap = servicecheck.health_snapshot()

    assert any(item["id"] == "unit" and item["available"] is True for item in snap)
