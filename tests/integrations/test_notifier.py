"""ntfy phone notifications: payload, topic rules, and failure handling."""
import sys
import types

from clippyme.integrations import notifier


def _fake_requests(monkeypatch, status=200, exc=None):
    calls = []

    def post(url, json=None, timeout=None, **_kw):
        calls.append({"url": url, "json": json})
        if exc:
            raise exc
        return types.SimpleNamespace(status_code=status)

    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(post=post))
    return calls


def test_send_posts_json_so_unicode_titles_survive(monkeypatch):
    calls = _fake_requests(monkeypatch)
    assert notifier.send("3 new clips · kaicenat 🔥", "• Big moment (91)", tags=("movie_camera",),
                         priority=4, topic="clippyme-abcdef123456")
    body = calls[0]["json"]
    assert calls[0]["url"] == "https://ntfy.sh"
    assert body["topic"] == "clippyme-abcdef123456"
    assert body["title"] == "3 new clips · kaicenat 🔥"
    assert body["tags"] == ["movie_camera"] and body["priority"] == 4


def test_no_topic_or_bad_topic_sends_nothing(monkeypatch):
    calls = _fake_requests(monkeypatch)
    monkeypatch.setattr(notifier, "configured_topic", lambda: None)
    assert notifier.send("t", "m") is False
    assert notifier.send("t", "m", topic="short") is False
    assert notifier.send("t", "m", topic="has spaces in it") is False
    assert notifier.send("t", "m", topic="../../etc/passwd") is False
    assert calls == []


def test_network_error_and_rejection_return_false(monkeypatch):
    _fake_requests(monkeypatch, exc=ConnectionError("down"))
    assert notifier.send("t", "m", topic="clippyme-abcdef123456") is False
    _fake_requests(monkeypatch, status=429)
    assert notifier.send("t", "m", topic="clippyme-abcdef123456") is False


def test_new_clips_message():
    clips = [{"title": f"Moment {i}", "viral_score": 90 - i} for i in range(7)]
    title, body = notifier.new_clips_message("kaicenat", clips, review_minutes=15,
                                             publishing_enabled=True)
    assert title == "7 new clips · kaicenat"
    assert "• Moment 0 (90)" in body and "• +2 more" in body
    assert body.endswith("Posting in 15 min unless you discard them.")
    _, paused = notifier.new_clips_message("n3on", clips[:1], review_minutes=15,
                                           publishing_enabled=False)
    assert "Auto-publish is off" in paused
    title, now = notifier.new_clips_message("n3on", clips[:1], review_minutes=0,
                                            publishing_enabled=True)
    assert title == "1 new clip · n3on" and now.endswith("Posting now.")
