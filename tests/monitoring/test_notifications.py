"""The monitor sends one phone notification per batch of new clips, and one
when a clip fails to post for good."""
import asyncio

from clippyme.monitoring.live_monitor import LiveMonitor, validate_monitor_config


def _monitor(tmp_path):
    mon = LiveMonitor(id="twitch:kaicenat", jobs={}, job_queue=None, output_dir=str(tmp_path))
    mon.cfg = validate_monitor_config({
        "platform": "twitch", "channel": "kaicenat", "timezone": "UTC",
        "platforms": [{"platform": "tiktok", "accountId": "a"}]}, default_timezone="UTC")
    mon.publishing_enabled = False  # no drain task; only the notification
    return mon


def _entry(i):
    return {"job_id": "j1", "clip": {"original_index": i, "title": f"Clip {i}", "viral_score": 80 + i,
                                     "video_url": f"/videos/j1/clip_{i}.mp4"}}


def test_one_notification_per_batch_of_new_clips(tmp_path, sent_notifications):
    mon = _monitor(tmp_path)

    async def run():
        mon._enqueue_publications([_entry(0), _entry(1)])
        await asyncio.gather(*list(mon._publish_tasks))

    asyncio.run(run())
    assert len(sent_notifications) == 1
    note = sent_notifications[0]
    assert note["title"] == "2 new clips · kaicenat"
    assert "• Clip 1 (81)" in note["message"]


def test_requeued_clips_do_not_notify_again(tmp_path, sent_notifications):
    mon = _monitor(tmp_path)
    entry = _entry(0)

    async def run():
        mon._enqueue_publications([entry])
        mon._enqueue_publications([entry])  # restart hand-off re-queues the same entry
        await asyncio.gather(*list(mon._publish_tasks))

    asyncio.run(run())
    assert len(sent_notifications) == 1


def test_permanent_publish_failure_notifies(tmp_path, sent_notifications):
    mon = _monitor(tmp_path)
    entry = _entry(3)

    async def run():
        mon._enqueue_publications([entry])
        entry["attempts"] = 99  # past the retry budget
        await mon._record_publish_failure(entry, ValueError("account disconnected"), "/clip.mp4")
        await asyncio.gather(*list(mon._publish_tasks))

    asyncio.run(run())
    failure = sent_notifications[-1]
    assert failure["title"] == "Clip failed to post · kaicenat"
    assert "account disconnected" in failure["message"] and failure["priority"] == 4


def test_no_running_loop_is_a_silent_no_op(tmp_path, sent_notifications):
    mon = _monitor(tmp_path)
    mon._enqueue_publications([_entry(0)])  # called outside any event loop
    assert sent_notifications == []
