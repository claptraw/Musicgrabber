"""
Job queue tests. Queues a real download and cleans up.

The 'live_download' tests are marked slow - they wait for a download to complete.
Run with: pytest -m "not slow" to skip them.
"""

import time
import pytest


JOB_KEYS = [
    "id", "video_id", "title", "artist", "status",
    "error", "download_type", "source", "convert_to_flac",
]

# A very short, well-known track on YouTube - Aphex Twin's "Avril 14th" excerpt (official)
# Using a known public video; if YouTube breaks, the job will fail but not 500
_YT_VIDEO_ID = "dX3k_QDnzHE"  # "Bohemian Rhapsody" official - widely available, reliable
_SHORT_YT_ID = "jNQXAC9IVRw"  # "Me at the zoo" - first YouTube video, 18 seconds, lightweight


def test_jobs_list(api, base_url):
    r = api.get(f"{base_url}/api/jobs", timeout=10)
    assert r.status_code == 200
    assert "jobs" in r.json()
    assert isinstance(r.json()["jobs"], list)


def test_jobs_list_limit(api, base_url):
    r = api.get(f"{base_url}/api/jobs?limit=3", timeout=10)
    assert r.status_code == 200
    jobs = r.json()["jobs"]
    assert len(jobs) <= 3


def test_jobs_list_shape(api, base_url):
    r = api.get(f"{base_url}/api/jobs?limit=5", timeout=10)
    jobs = r.json()["jobs"]
    for job in jobs:
        for key in JOB_KEYS:
            assert key in job, f"job missing key: {key}"


def test_queue_download_and_cleanup(api, base_url):
    """Queue a download job, verify it's created, then clean it up immediately."""
    payload = {
        "video_id": _SHORT_YT_ID,
        "title": "Me at the zoo",
        "artist": "jawed",
        "source": "youtube",
        "convert_to_flac": True,
    }
    r = api.post(f"{base_url}/api/download", json=payload, timeout=15)
    assert r.status_code == 200
    job = r.json()
    assert "job_id" in job or "id" in job, f"no job id in response: {job}"

    job_id = job.get("job_id") or job.get("id")

    # Verify the job appears in the queue
    r2 = api.get(f"{base_url}/api/jobs/{job_id}", timeout=10)
    assert r2.status_code == 200
    d = r2.json()
    assert d.get("status") in ("queued", "downloading", "completed", "failed", "completed_with_errors")

    # Clean up so we don't leave test jobs in the queue
    api.delete(f"{base_url}/api/jobs/cleanup", timeout=10)


def test_queue_download_duplicate_fields(api, base_url):
    """Response shape should include job_id."""
    payload = {
        "video_id": _SHORT_YT_ID,
        "title": "Test track",
        "artist": "Test artist",
        "source": "youtube",
        "convert_to_flac": True,
    }
    r = api.post(f"{base_url}/api/download", json=payload, timeout=15)
    assert r.status_code == 200
    d = r.json()
    assert "job_id" in d or "id" in d, "POST /api/download response has no job identifier"
    api.delete(f"{base_url}/api/jobs/cleanup", timeout=10)


@pytest.mark.slow
def test_live_youtube_download_completes(api, base_url):
    """Actually wait for a short YouTube download to finish. Slow - network dependent."""
    payload = {
        "video_id": _SHORT_YT_ID,
        "title": "Me at the zoo",
        "artist": "jawed",
        "source": "youtube",
        "convert_to_flac": False,  # skip FLAC conversion to speed up
    }
    r = api.post(f"{base_url}/api/download", json=payload, timeout=15)
    assert r.status_code == 200
    job_id = r.json().get("job_id") or r.json().get("id")

    # Poll for up to 90 seconds
    for _ in range(18):
        time.sleep(5)
        status_r = api.get(f"{base_url}/api/jobs/{job_id}", timeout=10)
        status = status_r.json().get("status")
        if status in ("completed", "failed", "completed_with_errors"):
            break

    final = api.get(f"{base_url}/api/jobs/{job_id}", timeout=10).json()
    assert final["status"] in ("completed", "completed_with_errors"), (
        f"download failed: {final.get('error')}"
    )

    # Tidy up the file and job
    api.delete(f"{base_url}/api/jobs/{job_id}/file", timeout=10)
    api.delete(f"{base_url}/api/jobs/cleanup", timeout=10)
