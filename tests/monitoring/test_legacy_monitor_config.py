"""Monitors saved by an older version must stay editable after an update.

snapshot() writes every _SNAPSHOT_CONFIG_FIELDS key, so a field added later is
persisted as None for a monitor started before it existed. Reported: Settings
→ Apply failed with "chat_signal must be a boolean".
"""
from clippyme.monitoring.live_monitor import (
    _SNAPSHOT_CONFIG_FIELDS,
    validate_monitor_config,
    validate_monitor_partial_update,
)

ADDED_LATER = ("chat_signal", "post_timing", "max_posts_per_day", "review_minutes",
               "smart_cut", "delete_after_publish", "letterbox_zoom")


def _legacy_cfg():
    cfg = validate_monitor_config({
        "platform": "kick", "channel": "n3on",
        "platforms": [{"platform": "tiktok", "accountId": "a"}]})
    # What the persisted snapshot of a pre-update monitor looks like.
    persisted = {k: cfg.get(k) for k in _SNAPSHOT_CONFIG_FIELDS}
    for key in ADDED_LATER:
        persisted[key] = None
    return persisted


def test_settings_update_on_a_monitor_saved_before_new_fields_existed():
    updated = validate_monitor_partial_update({"review_minutes": 15}, _legacy_cfg())
    assert updated["review_minutes"] == 15
    assert updated["chat_signal"] is True
    assert updated["delete_after_publish"] is True
    assert updated["smart_cut"] is False
    assert updated["post_timing"] == "prime_time"
    assert updated["max_posts_per_day"] == 0


def test_restored_legacy_config_validates_to_defaults():
    cfg = validate_monitor_config(_legacy_cfg())
    assert cfg["chat_signal"] is True and cfg["review_minutes"] == 0
