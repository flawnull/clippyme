"""NTFY_TOPIC is saved like a secret, validated, and testable from Settings."""
from fastapi.testclient import TestClient

from clippyme.api import app as app_module
from clippyme.integrations import notifier
from clippyme.storage import config_store

ORIGIN = {"Origin": "http://localhost:5175"}
TOPIC = "clippyme-abcdef123456"


def _client(tmp_path, monkeypatch):
    monkeypatch.setattr(config_store, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(tmp_path / "config.json"))
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    return TestClient(app_module.app, headers=ORIGIN)


def test_topic_is_saved_masked_and_validated(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    assert client.post("/api/config", json={"keys": {"NTFY_TOPIC": "bad topic!"}}).status_code == 422
    assert client.post("/api/config", json={"keys": {"NTFY_TOPIC": TOPIC}}).status_code == 200
    shown = client.get("/api/config").json()["NTFY_TOPIC"]
    assert shown != TOPIC and shown.startswith("clip")  # masked, never returned verbatim
    assert notifier.configured_topic() == TOPIC
    assert client.post("/api/config", json={"keys": {"NTFY_TOPIC": ""}}).status_code == 200
    assert notifier.configured_topic() is None


def test_send_test_notification(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    assert client.post("/api/notify/test").status_code == 400  # no topic yet
    client.post("/api/config", json={"keys": {"NTFY_TOPIC": TOPIC}})
    sent = []
    monkeypatch.setattr(notifier, "send", lambda title, message, **kw: sent.append(title) or True)
    r = client.post("/api/notify/test")
    assert r.status_code == 200 and r.json() == {"sent": True}
    assert sent == ["ClippyMe is connected"]


def test_untrusted_origin_cannot_send(tmp_path, monkeypatch):
    _client(tmp_path, monkeypatch).post("/api/config", json={"keys": {"NTFY_TOPIC": TOPIC}})
    sent = []
    monkeypatch.setattr(notifier, "send", lambda *a, **kw: sent.append(a) or True)
    evil = TestClient(app_module.app, headers={"Origin": "https://evil.example"})
    assert evil.post("/api/notify/test").status_code in (400, 403)
    assert sent == []
