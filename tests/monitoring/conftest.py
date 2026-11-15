"""Shared fixtures for the live-monitor tests."""
import pytest


@pytest.fixture
def instant_publish_defaults(monkeypatch):
    """Run with the original publish timing: no review window, no daily cap,
    prime-time scheduling. The restart/reliability suites test recovery, not
    timing, and expect a queued clip to be due at once. The new defaults are
    covered in test_post_timing.py and test_review_window.py."""
    from clippyme.monitoring import live_monitor as lm
    monkeypatch.setattr(lm, "DEFAULT_REVIEW_MINUTES", 0)
    monkeypatch.setattr(lm, "DEFAULT_MAX_POSTS_PER_DAY", 0)
    monkeypatch.setattr(lm, "DEFAULT_POST_TIMING", "prime_time")
