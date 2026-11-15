"""Discarding a queued clip from a live monitor's publish queue."""
import asyncio
import os

import pytest

from clippyme.core.errors import ConflictError, NotFoundError
from clippyme.monitoring import live_monitor as lm
from clippyme.monitoring.live_monitor import LiveMonitor, validate_monitor_config


def _monitor(tmp_path, publishing=False):
    mon = LiveMonitor(id="kick:n3on", jobs={}, job_queue=None, output_dir=str(tmp_path / "out"))
    mon.cfg = validate_monitor_config({
        "platform": "kick", "channel": "n3on", "timezone": "UTC",
        "platforms": [{"platform": "tiktok", "accountId": "a"}]}, default_timezone="UTC")
    mon.publishing_enabled = publishing
    os.makedirs(mon._clip_dir, exist_ok=True)
    return mon


def _entry(mon, job_id, idx, title):
    composed = os.path.join(mon._clip_dir, f"{title}.mp4")
    with open(composed, "wb") as fh:
        fh.write(b"clip")
    return {"job_id": job_id, "composed_path": composed,
            "clip": {"original_index": idx, "title": title, "viral_score": 80 + idx,
                     "video_url": f"/videos/{job_id}/clip_{idx}.mp4"}}


def test_status_lists_queued_clips_without_paths(tmp_path):
    mon = _monitor(tmp_path)
    mon._enqueue_publications([_entry(mon, "j1", 0, "a"), _entry(mon, "j1", 1, "b")])
    status = mon.status()
    assert status["pending_publish"] == 2
    first = status["pending_clips"][0]
    assert first["title"] == "a" and first["viral_score"] == 80
    assert first["video_url"] == "/videos/j1/clip_0.mp4"
    assert first["publication_id"] and first["state"] == "queued"
    assert "composed_path" not in first


def test_discard_removes_the_clip_and_its_composed_copy_only(tmp_path):
    mon = _monitor(tmp_path)
    keep, drop = _entry(mon, "j1", 0, "keep"), _entry(mon, "j1", 1, "drop")
    mon._enqueue_publications([keep, drop])
    result = mon.discard_publication(drop["publication_id"])
    assert result == {"discarded": drop["publication_id"], "pending_publish": 1}
    assert [e["clip"]["title"] for e in mon._pending_publish] == ["keep"]
    assert not os.path.exists(drop["composed_path"])
    assert os.path.exists(keep["composed_path"])
    # The removal is persisted: a restart does not bring the clip back.
    assert [e["clip"]["title"] for e in mon.snapshot()["pending_publish"]] == ["keep"]
    # Manual publish of the discarded clip is no longer blocked by the monitor.
    assert all(e["job_id"] != "j1" or e["clip"]["original_index"] != 1
               for e in mon._pending_publish)


def test_discard_refuses_a_clip_being_published(tmp_path):
    mon = _monitor(tmp_path)
    entry = _entry(mon, "j1", 0, "busy")
    mon._enqueue_publications([entry])
    entry["state"] = "in_flight"
    with pytest.raises(ConflictError):
        mon.discard_publication(entry["publication_id"])
    assert len(mon._pending_publish) == 1


def test_discard_unknown_id_is_not_found(tmp_path):
    mon = _monitor(tmp_path)
    with pytest.raises(NotFoundError):
        mon.discard_publication("00000000-0000-0000-0000-000000000000")


def test_restored_legacy_entries_get_an_id_so_they_can_be_discarded(tmp_path):
    mon = _monitor(tmp_path)
    legacy = {"job_id": "j1", "clip": {"original_index": 0, "title": "old"}}
    mon.restore({"pending_publish": [legacy], "config": mon.cfg})
    pid = mon.status()["pending_clips"][0]["publication_id"]
    assert pid
    mon.discard_publication(pid)
    assert mon._pending_publish == []


def test_clip_discarded_during_the_drain_spacing_sleep_is_not_published(tmp_path, monkeypatch):
    """The drain picks the next entry, sleeps PUBLISH_SPACING_SECONDS, then
    publishes. A discard during that sleep must win."""
    mon = _monitor(tmp_path)
    first, second = _entry(mon, "j1", 0, "first"), _entry(mon, "j1", 1, "second")
    mon._enqueue_publications([first, second])
    published = []

    async def fake_publish_one(entry):
        published.append(entry["clip"]["title"])
        mon._dequeue(entry)

    real_sleep = asyncio.sleep

    async def sleep_then_discard(_seconds):
        if second in mon._pending_publish:
            mon.discard_publication(second["publication_id"])
        await real_sleep(0)

    monkeypatch.setattr(mon, "_publish_one", fake_publish_one)
    monkeypatch.setattr(lm.asyncio, "sleep", sleep_then_discard)
    mon.publishing_enabled = True

    async def run():
        mon._stop = asyncio.Event()
        await mon._drain_pending()

    asyncio.run(run())
    assert published == ["first"]
    assert mon._pending_publish == []
