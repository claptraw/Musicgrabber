"""
Test fixtures for MusicGrabber integration tests.

Talks to a running MusicGrabber instance. Configure via env vars:
  MG_BASE_URL    - default: http://localhost:38274 (the local docker container built from this repo)
  MG_USERNAME    - only needed in multi-user mode
  MG_PASSWORD    - only needed in multi-user mode
"""

import os
import pytest
import requests


BASE_URL = os.environ.get("MG_BASE_URL", "http://localhost:38274").rstrip("/")
MG_USERNAME = os.environ.get("MG_USERNAME", "")
MG_PASSWORD = os.environ.get("MG_PASSWORD", "")


@pytest.fixture(scope="session")
def base_url():
    return BASE_URL


@pytest.fixture(scope="session")
def api(base_url):
    """Requests session. Logs in if multi-user mode requires it."""
    s = requests.Session()
    s.headers["Content-Type"] = "application/json"

    cfg = s.get(f"{base_url}/api/config", timeout=10).json()
    if cfg.get("users_exist") and MG_USERNAME:
        resp = s.post(
            f"{base_url}/api/auth/login",
            json={"username": MG_USERNAME, "password": MG_PASSWORD},
            timeout=10,
        )
        resp.raise_for_status()
        token = resp.json()["token"]
        s.headers["Authorization"] = f"Bearer {token}"

    return s


@pytest.fixture(scope="session")
def config(api, base_url):
    return api.get(f"{base_url}/api/config", timeout=10).json()


@pytest.fixture(scope="session")
def is_single_user(config):
    return not config.get("auth_required", False)
