"""Clips that fail to post: readable reason, current accounts, retry, discard."""
import os

from clippyme.integrations.social_publisher import ZernioError
from clippyme.monitoring.live_monitor import (
    LiveMonitor,
    current_platform_targets,
    publish_error_text,
    validate_monitor_config,
)

BODY = ('{"error":"Invalid accountId format","type":"invalid_request_error",'
        '"code":"invalid_field_value","param":"platforms.0.accountId"}')


def test_error_text_carries_zernios_message():
    """Reported: the card said only 'Zernio POST /posts → HTTP 400'."""
    exc = ZernioError("Zernio POST /posts → HTTP 400", status_code=400, body=BODY)
    assert publish_error_text(exc) == "Zernio POST /posts → HTTP 400: Invalid accountId format"
    assert publish_error_text(ValueError("clip missing")) == "clip missing"


def test_targets_follow_the_accounts_saved_in_settings():
    targets = [{"platform": "tiktok", "accountId": "old-tt"},
               {"platform": "instagram", "accountId": "old-ig"}]
    swapped = current_platform_targets(targets, {"tiktok": "new-tt", "instagram": "", "youtube": "yt"})
    assert swapped == [{"platform": "tiktok", "accountId": "new-tt"}]  # instagram disconnected
    assert current_platform_targets(targets, {}) == targets            # nothing saved: unchanged


def _monitor(tmp_path):
    mon = LiveMonitor(id="twitch:lacy", jobs={}, job_queue=None, output_dir=str(tmp_path / "out"))
    mon.cfg = validate_monitor_config({"platform": "twitch", "channel": "lacy", "timezone": "UTC",
                                       "platforms": [{"platform": "tiktok", "accountId": "a"}]},
                                      default_timezone="UTC")
    mon.publishing_enabled = False
    os.makedirs(mon._clip_dir, exist_ok=True)
    return mon


def _failed(mon, tmp_path):
    job = tmp_path / "out" / "j1"
    job.mkdir(parents=True, exist_ok=True)
    (job / "clip_0.mp4").write_bytes(b"v")
    composed = os.path.join(mon._clip_dir, "Prison.mp4")
    open(composed, "wb").write(b"v")
    entry = {"job_id": "j1", "composed_path": composed, "publication_id": "11111111-aaaa",
             "request_id": "r1", "attempts": 1, "state": "failed", "failed_at": 1.0,
             "last_error": "Zernio POST /posts → HTTP 400: Invalid accountId format",
             "clip": {"original_index": 0, "title": "Prison", "video_url": "/videos/j1/clip_0.mp4"}}
    mon._failed_publish.append(entry)
    return entry, job / "clip_0.mp4", composed


def test_failed_clip_is_listed_with_its_reason_and_can_be_retried(tmp_path):
    mon = _monitor(tmp_path)
    entry, _, _ = _failed(mon, tmp_path)
    listed = mon.status()["failed_clips"][0]
    assert listed["title"] == "Prison" and "Invalid accountId" in listed["error"]
    assert listed["preview_url"].endswith("/Prison.mp4")
    mon.retry_failed("11111111-aaaa")
    assert mon._failed_publish == [] and mon._pending_publish == [entry]
    assert entry["state"] == "queued" and entry["attempts"] == 0 and entry["approved"] is True
    assert entry["request_id"] != "r1"  # a fresh idempotency key after a certain rejection


def test_failed_clip_discard_deletes_its_files(tmp_path):
    mon = _monitor(tmp_path)
    _, raw, composed = _failed(mon, tmp_path)
    mon.discard_failed("11111111-aaaa")
    assert mon._failed_publish == [] and not raw.exists() and not os.path.exists(composed)
