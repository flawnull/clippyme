"""Review window: a queued clip waits review_minutes, then publishes itself."""
import asyncio
import os

from clippyme.monitoring import live_monitor as lm
from clippyme.monitoring.live_monitor import (
    LiveMonitor,
    validate_monitor_config,
    validate_monitor_partial_update,
)


class _Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def _monitor(tmp_path, monkeypatch, review=20, publishing=True):
    clock = _Clock()
    monkeypatch.setattr(lm.time, "time", clock)
    mon = LiveMonitor(id="twitch:kaicenat", jobs={}, job_queue=None, output_dir=str(tmp_path / "out"))
    mon.cfg = validate_monitor_config({
        "platform": "twitch", "channel": "kaicenat", "timezone": "UTC", "review_minutes": review,
        "platforms": [{"platform": "tiktok", "accountId": "a"}]}, default_timezone="UTC")
    mon.publishing_enabled = publishing
    os.makedirs(mon._clip_dir, exist_ok=True)
    return mon, clock


def _entry(job_id, idx):
    return {"job_id": job_id, "clip": {"original_index": idx, "title": f"clip {idx}",
                                       "video_url": f"/videos/{job_id}/clip_{idx}.mp4"}}


def _enqueue(mon, entries):
    """Queue without starting a background drain (no loop running here)."""
    published = mon.publishing_enabled
    mon.publishing_enabled = False
    mon._enqueue_publications(entries)
    mon.publishing_enabled = published


def test_clip_is_not_due_until_its_review_window_ends(tmp_path, monkeypatch):
    mon, clock = _monitor(tmp_path, monkeypatch, review=20)
    _enqueue(mon, [_entry("j1", 0)])
    entry = mon._pending_publish[0]
    assert entry["review_until"] == clock.t + 20 * 60
    assert mon._next_due_publication() is None
    assert mon._seconds_until_next_retry() == 20 * 60
    clock.t += 20 * 60
    assert mon._next_due_publication() is entry


def test_status_shows_when_each_clip_posts(tmp_path, monkeypatch):
    mon, clock = _monitor(tmp_path, monkeypatch, review=15)
    _enqueue(mon, [_entry("j1", 0)])
    assert mon.status()["pending_clips"][0]["review_until"] == clock.t + 15 * 60


def test_no_review_window_by_default(tmp_path, monkeypatch):
    mon, _ = _monitor(tmp_path, monkeypatch, review=0)
    _enqueue(mon, [_entry("j1", 0)])
    assert "review_until" not in mon._pending_publish[0]
    assert mon._next_due_publication() is mon._pending_publish[0]


def test_requeue_keeps_the_original_review_clock(tmp_path, monkeypatch):
    mon, clock = _monitor(tmp_path, monkeypatch, review=20)
    entry = _entry("j1", 0)
    _enqueue(mon, [entry])
    deadline = entry["review_until"]
    clock.t += 600
    _enqueue(mon, [entry])  # e.g. a restore hands the same entry back
    assert entry["review_until"] == deadline
    assert len(mon._pending_publish) == 1


def test_retry_backoff_and_review_take_the_later_deadline(tmp_path, monkeypatch):
    mon, clock = _monitor(tmp_path, monkeypatch, review=20)
    _enqueue(mon, [_entry("j1", 0)])
    entry = mon._pending_publish[0]
    entry["next_retry_at"] = clock.t + 60
    assert mon._due_at(entry) == entry["review_until"]
    entry["next_retry_at"] = entry["review_until"] + 300
    assert mon._due_at(entry) == entry["next_retry_at"]


def test_drain_publishes_after_review_unless_discarded(tmp_path, monkeypatch):
    mon, clock = _monitor(tmp_path, monkeypatch, review=20)
    keep, drop = _entry("j1", 0), _entry("j1", 1)
    _enqueue(mon, [keep, drop])
    published = []

    async def fake_publish_one(entry):
        published.append(entry["clip"]["title"])
        mon._dequeue(entry)

    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)
        if drop in mon._pending_publish:  # user discards during the review
            mon.discard_publication(drop["publication_id"])
        clock.t += seconds

    monkeypatch.setattr(mon, "_publish_one", fake_publish_one)
    monkeypatch.setattr(mon, "_interruptible_sleep", fake_sleep)
    monkeypatch.setattr(lm.asyncio, "sleep", lambda _s: asyncio.sleep(0))

    async def run():
        mon._stop = asyncio.Event()
        await mon._drain_pending()

    asyncio.run(run())
    assert slept and slept[0] == 20 * 60  # waited out the review window
    assert published == ["clip 0"]
    assert mon._pending_publish == []


def test_review_minutes_config():
    cfg = validate_monitor_config({
        "platform": "kick", "channel": "n3on",
        "platforms": [{"platform": "tiktok", "accountId": "a"}]})
    assert cfg["review_minutes"] == 0
    assert validate_monitor_partial_update({"review_minutes": 20}, cfg)["review_minutes"] == 20
    assert validate_monitor_partial_update({"review_minutes": 99999}, cfg)["review_minutes"] == 1440
