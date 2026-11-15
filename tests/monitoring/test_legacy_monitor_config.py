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
    assert updated["post_timing"] == "asap"
    assert updated["max_posts_per_day"] == 6


def test_restored_legacy_config_validates_to_defaults():
    cfg = validate_monitor_config(_legacy_cfg())
    assert cfg["chat_signal"] is True and cfg["review_minutes"] == 15


def test_auto_resume_revalidates_a_legacy_snapshot(tmp_path, monkeypatch):
    """A monitor saved before chat_signal existed resumed with chat_signal
    None, which LiveMonitor reads as off: chat was silently not recorded."""
    import asyncio
    import json

    from clippyme.monitoring import live_monitor as lm
    from clippyme.monitoring.live_monitor import LiveMonitorRegistry
    from clippyme.storage import config_store

    cfg = _legacy_cfg()
    cfg.update({"loop": True, "timezone": "UTC", "min_gap_seconds": 2700})
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"monitors": {"kick:n3on": {
        "platform": "kick", "mode": "live", "channel": "n3on",
        "config": cfg, "resume_on_start": True}}}), encoding="utf-8")
    monkeypatch.setattr(config_store, "load_persistent_config", lambda: {"GEMINI_API_KEY": "k"})
    monkeypatch.setattr(config_store, "load_zernio_config", lambda: {"timezone": "UTC", "api_key": "z"})
    monkeypatch.setattr(lm.LiveMonitor, "_make_strategy", lambda self, c, pc: object())
    reg = LiveMonitorRegistry(jobs={}, job_queue=None, output_dir=str(tmp_path),
                              state_path=str(state))

    async def scenario():
        result = await reg.auto_resume()
        assert result["resumed"] == ["kick:n3on"]
        live = reg._monitors["kick:n3on"].cfg
        assert live["chat_signal"] is True
        assert live["review_minutes"] == 15
        assert live["post_timing"] == "asap"
        assert live["min_gap_seconds"] == 2700  # values the user set are kept
        await reg.shutdown()

    asyncio.run(scenario())
