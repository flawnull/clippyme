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
    monkeypatch.setattr(lm, "DEFAULT_DELETE_AFTER_PUBLISH", True)


@pytest.fixture(autouse=True)
def sent_notifications(monkeypatch):
    """Never send a real phone notification from a test; record them instead."""
    from clippyme.integrations import notifier
    sent = []

    def fake_send(title, message, *, tags=(), priority=3, topic=None):
        sent.append({"title": title, "message": message, "tags": tags, "priority": priority})
        return True

    monkeypatch.setattr(notifier, "send", fake_send)
    return sent
