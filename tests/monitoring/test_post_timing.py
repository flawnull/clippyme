"""Post timing ("asap" vs prime time) and the daily post cap."""
import random
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from clippyme.core.errors import ValidationError
from clippyme.monitoring.live_monitor import (
    ASAP_LEAD_SECONDS,
    LiveMonitor,
    SharedGapScheduler,
    validate_monitor_config,
    validate_monitor_partial_update,
)

TZ = ZoneInfo("Europe/Berlin")


def _at(day, hour, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)


DAY = date(2026, 11, 19)  # GTA 6 launch day, a Thursday


def _sched(**kw):
    return SharedGapScheduler(min_gap_seconds=kw.pop("gap", 900), rng=random.Random(1), **kw)


# -- asap ---------------------------------------------------------------------

def test_asap_posts_a_couple_of_minutes_after_now():
    now = _at(DAY, 14, 7)
    slot = _sched(asap=True).find_slot(DAY, [], now=now)
    assert now + timedelta(seconds=ASAP_LEAD_SECONDS) <= slot <= now + timedelta(minutes=4)


def test_asap_waits_out_the_min_gap_after_the_last_post():
    now = _at(DAY, 14, 0)
    sched = _sched(asap=True, gap=2700)
    first = sched.find_slot(DAY, [], now=now)
    second = sched.find_slot(DAY, [], now=now)
    assert (second - first).total_seconds() >= 2700
    assert second - first < timedelta(minutes=50)  # not pushed to a prime-time window


def test_asap_respects_zernio_posts_already_scheduled():
    now = _at(DAY, 14, 0)
    existing = [_at(DAY, 14, 10)]
    slot = _sched(asap=True, gap=1800).find_slot(DAY, existing, now=now)
    assert slot >= _at(DAY, 14, 40)


def test_asap_skips_quiet_hours():
    now = _at(DAY, 3, 30)
    assert _sched(asap=True).find_slot(DAY, [], now=now) == _at(DAY, 8, 0)


def test_asap_late_evening_stays_tonight():
    now = _at(DAY, 22, 30)
    slot = _sched(asap=True).find_slot(DAY, [], now=now)
    assert slot.date() == DAY and slot.hour == 22


# -- daily cap ------------------------------------------------------------------

def test_daily_cap_moves_the_next_post_to_tomorrow_morning():
    now = _at(DAY, 12, 0)
    sched = _sched(asap=True, daily_cap=2)
    a = sched.find_slot(DAY, [], now=now)
    b = sched.find_slot(DAY, [], now=now)
    c = sched.find_slot(DAY, [], now=now)
    assert a.date() == b.date() == DAY
    assert c == _at(DAY + timedelta(days=1), 8, 0)


def test_daily_cap_counts_zernio_posts_and_other_monitors():
    now = _at(DAY, 12, 0)
    shared = [_at(DAY, 9, 0)]  # another monitor already posted today
    sched = _sched(asap=True, daily_cap=2, picked_slots=shared)
    slot = sched.find_slot(DAY, [_at(DAY, 10, 0)], now=now)  # plus one in Zernio
    assert slot.date() == DAY + timedelta(days=1)


def test_daily_cap_also_applies_in_prime_time_mode():
    now = _at(DAY, 8, 0)
    sched = _sched(daily_cap=1)
    first = sched.find_slot(DAY, [], now=now)
    second = sched.find_slot(DAY, [], now=now)
    assert first.date() == DAY
    assert second.date() == DAY + timedelta(days=1)


def test_no_cap_by_default():
    now = _at(DAY, 9, 0)
    sched = _sched(asap=True, gap=60)
    slots = [sched.find_slot(DAY, [], now=now) for _ in range(10)]
    assert all(s.date() == DAY for s in slots)


# -- config -----------------------------------------------------------------------

def _cfg(**kw):
    return validate_monitor_config({
        "platform": "kick", "channel": "n3on",
        "platforms": [{"platform": "tiktok", "accountId": "a"}], **kw})


def test_config_defaults_keep_the_old_behaviour():
    cfg = _cfg()
    assert cfg["post_timing"] == "prime_time"
    assert cfg["max_posts_per_day"] == 0


def test_config_validates_and_updates_at_runtime():
    with pytest.raises(ValidationError):
        _cfg(post_timing="whenever")
    assert _cfg(max_posts_per_day=500)["max_posts_per_day"] == 100
    updated = validate_monitor_partial_update({"post_timing": "asap", "max_posts_per_day": 6}, _cfg())
    assert (updated["post_timing"], updated["max_posts_per_day"]) == ("asap", 6)


def test_runtime_config_reaches_the_scheduler(tmp_path):
    """min_gap_seconds used to be read only when the monitor started."""
    mon = LiveMonitor(id="kick:n3on", jobs={}, job_queue=None, output_dir=str(tmp_path))
    mon.cfg = _cfg(min_gap_seconds=900)
    mon._sync_scheduler()
    assert (mon._scheduler.min_gap_seconds, mon._scheduler.asap) == (900, False)
    mon.cfg = validate_monitor_partial_update(
        {"min_gap_seconds": 2700, "post_timing": "asap", "max_posts_per_day": 5}, mon.cfg)
    mon._sync_scheduler()
    assert mon._scheduler.min_gap_seconds == 2700
    assert mon._scheduler.asap is True
    assert mon._scheduler.daily_cap == 5
    assert mon._scheduler.picked_slots is mon._picked_slots  # still the shared list
