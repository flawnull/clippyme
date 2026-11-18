"""A capture that produces no video must say why, and alert when it keeps
failing. Reported: Twitch monitors sat on "waiting" for an hour while
streamlink crashed on every attempt with its output sent to /dev/null."""
import asyncio
import os

from clippyme.monitoring import live_monitor as lm
from clippyme.monitoring.live_monitor import LiveMonitor, TwitchStrategy, validate_monitor_config


class _ScriptStrategy:
    def __init__(self, script):
        self.script = script

    def capture_args(self, seg_path, seconds, url):
        return ["sh", "-c", self.script.replace("OUT", seg_path)]


def _monitor(tmp_path, script):
    mon = LiveMonitor(id="twitch:lacy", jobs={}, job_queue=None, output_dir=str(tmp_path / "out"),
                      upload_dir=str(tmp_path / "up"))
    mon.cfg = validate_monitor_config({
        "platform": "twitch", "channel": "lacy", "timezone": "UTC",
        "platforms": [{"platform": "tiktok", "accountId": "a"}]}, default_timezone="UTC")
    mon._strategy = _ScriptStrategy(script)
    return mon


FAIL = ("echo '[warnings] --hls-duration has been deprecated' >&2; "
        "echo \"error: Unable to open URL: https://gql.twitch.tv/gql (no attribute 'sub')\" >&2; exit 1")


def test_failed_capture_logs_the_reason_and_shows_it_on_the_card(tmp_path, sent_notifications, caplog):
    mon = _monitor(tmp_path, FAIL)

    async def run():
        return await mon._capture_segment(None)

    assert asyncio.run(run()) is None
    assert "Unable to open URL" in mon.last_error and mon.last_error.startswith("recording failed (1x)")
    assert "deprecated" not in mon.last_error
    assert any("capture produced no video" in r.message for r in caplog.records)
    assert not [f for f in os.listdir(tmp_path / "up") if f.endswith(".log")]  # no stray logs


def test_repeated_capture_failures_alert_once_then_recover(tmp_path, sent_notifications):
    mon = _monitor(tmp_path, FAIL)

    async def run():
        for _ in range(lm.CAPTURE_ALERT_AFTER + 1):
            await mon._capture_segment(None)
        await asyncio.gather(*list(mon._publish_tasks))
        mon._strategy = _ScriptStrategy("printf video > OUT")
        return await mon._capture_segment(None)

    seg = asyncio.run(run())
    assert [n["title"] for n in sent_notifications] == ["Can't record lacy"]  # rate-limited
    assert seg and os.path.getsize(seg) > 0
    assert mon._capture_failures == 0 and mon.last_error is None


def test_twitch_capture_uses_current_streamlink_options():
    args = TwitchStrategy("lacy", client=None).capture_args("/tmp/x.mp4", 1800, None) \
        if lm.shutil.which("streamlink") else None
    if args is None:  # streamlink not installed on this host
        return
    assert "--stream-segmented-duration" in args and "00:30:00" in args
    assert "--twitch-disable-ads" not in args and "--hls-duration" not in args
