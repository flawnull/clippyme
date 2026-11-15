"""Health alerts: problems that silently stop clipping reach the owner's
phone, at most once per kind per cooldown."""
import asyncio
from collections import namedtuple

from clippyme.monitoring import live_monitor as lm
from clippyme.monitoring.live_monitor import LiveMonitor, validate_monitor_config


def _monitor(tmp_path):
    mon = LiveMonitor(id="twitch:kaicenat", jobs={}, job_queue=None, output_dir=str(tmp_path))
    mon.cfg = validate_monitor_config({
        "platform": "twitch", "channel": "kaicenat", "timezone": "UTC",
        "platforms": [{"platform": "tiktok", "accountId": "a"}]}, default_timezone="UTC")
    mon.platform = "twitch"
    return mon


def _drain(mon):
    return asyncio.gather(*list(mon._publish_tasks))


def test_alerts_are_rate_limited_per_kind(tmp_path, sent_notifications, monkeypatch):
    mon = _monitor(tmp_path)
    clock = [1000.0]
    monkeypatch.setattr(lm.time, "time", lambda: clock[0])

    async def run():
        mon._alert("gemini", "a", "m")
        mon._alert("gemini", "b", "m")    # same kind, inside cooldown: dropped
        mon._alert("disk", "c", "m")      # other kind: sent
        clock[0] += lm.ALERT_COOLDOWN_SECONDS
        mon._alert("gemini", "d", "m")    # cooldown over: sent
        await _drain(mon)

    asyncio.run(run())
    assert [n["title"] for n in sent_notifications] == ["a", "c", "d"]
    assert all(n["priority"] == 4 for n in sent_notifications)


def test_stream_detection_alerts_only_after_repeated_failures(tmp_path, sent_notifications):
    mon = _monitor(tmp_path)

    class Broken:
        def get_live_state(self):
            raise ConnectionError("twitch said no")

    mon._strategy = Broken()

    async def run():
        for _ in range(lm.PROVIDER_ALERT_AFTER - 1):
            await mon._live_state()
        await _drain(mon)
        assert sent_notifications == []  # a short blip stays quiet
        await mon._live_state()
        await _drain(mon)

    asyncio.run(run())
    assert len(sent_notifications) == 1
    assert sent_notifications[0]["title"] == "Can't check kaicenat on twitch"
    assert "twitch said no" in sent_notifications[0]["message"]


def test_low_disk_alerts(tmp_path, sent_notifications, monkeypatch):
    mon = _monitor(tmp_path)
    usage = namedtuple("usage", "total used free")
    gb = 1024 ** 3
    monkeypatch.setattr(lm.shutil, "disk_usage", lambda _p: usage(100 * gb, 90 * gb, 40 * gb))

    async def run():
        mon._check_disk()  # plenty of space
        monkeypatch.setattr(lm.shutil, "disk_usage", lambda _p: usage(100 * gb, 97 * gb, 3 * gb))
        mon._check_disk()
        await _drain(mon)

    asyncio.run(run())
    assert [n["title"] for n in sent_notifications] == ["Disk almost full"]
    assert "3.0 GB left" in sent_notifications[0]["message"]


def _job_outcome(mon, tmp_path, job):
    mon._jobs["j1"] = job
    seg = tmp_path / "seg.mp4"
    seg.write_bytes(b"x")

    async def run():
        mon._stop = asyncio.Event()
        await mon._await_and_publish("j1", str(seg))
        await _drain(mon)

    asyncio.run(run())


def test_failed_segment_job_alerts_with_its_last_log_line(tmp_path, sent_notifications):
    mon = _monitor(tmp_path)
    _job_outcome(mon, tmp_path, {"status": "failed", "logs": ["started", "ffmpeg: no space left"]})
    assert sent_notifications[0]["title"] == "Segment failed · kaicenat"
    assert "ffmpeg: no space left" in sent_notifications[0]["message"]


def test_gemini_quota_exhaustion_alerts(tmp_path, sent_notifications):
    mon = _monitor(tmp_path)
    _job_outcome(mon, tmp_path, {"status": "completed", "result": {"gemini_exhausted": True, "clips": []}})
    assert [n["title"] for n in sent_notifications] == ["Gemini quota used up"]


def test_a_good_segment_sends_no_alert(tmp_path, sent_notifications):
    mon = _monitor(tmp_path)
    _job_outcome(mon, tmp_path, {"status": "completed", "result": {"clips": []}})
    assert sent_notifications == []
