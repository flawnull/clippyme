"""POST /api/live-monitor/{monitor_id}/config — runtime config updates.

TestClient is used WITHOUT its context manager so the FastAPI lifespan never
starts (matches tests/api/test_reframe_aspect_api.py). We inject a fake
monitor directly into the app's live_monitor registry rather than driving a
real asyncio monitor task.
"""
from fastapi.testclient import TestClient

from clippyme.api import app as app_module

ORIGIN = {"Origin": "http://localhost:5175"}


class _FakeMonitor:
    id = "kick:foo"

    def update_config(self, partial):
        self.received = partial
        return {"min_gap_seconds": partial.get("min_gap_seconds", 900)}

    def snapshot(self):
        return {"platform": "kick", "channel": "foo"}


def teardown_function():
    app_module.live_monitor._monitors.pop("kick:foo", None)


def test_update_config_success():
    fake = _FakeMonitor()
    app_module.live_monitor._monitors["kick:foo"] = fake
    client = TestClient(app_module.app, headers=ORIGIN)

    r = client.post("/api/live-monitor/kick:foo/config", json={"min_gap_seconds": 120})

    assert r.status_code == 200, r.text
    assert r.json() == {"monitor": {"min_gap_seconds": 120}}
    assert fake.received == {"min_gap_seconds": 120}


def test_update_config_unknown_monitor_404():
    client = TestClient(app_module.app, headers=ORIGIN)
    r = client.post("/api/live-monitor/kick:nope/config", json={"min_gap_seconds": 120})
    assert r.status_code == 404, r.text


def test_update_config_untrusted_origin_rejected():
    fake = _FakeMonitor()
    app_module.live_monitor._monitors["kick:foo"] = fake
    client = TestClient(app_module.app, headers={"Origin": "https://evil.example"})

    r = client.post("/api/live-monitor/kick:foo/config", json={"min_gap_seconds": 120})

    assert r.status_code in (403, 400)


def test_update_config_malformed_json_400():
    fake = _FakeMonitor()
    app_module.live_monitor._monitors["kick:foo"] = fake
    client = TestClient(app_module.app, headers=ORIGIN)

    r = client.post(
        "/api/live-monitor/kick:foo/config",
        content=b"{not json",
        headers={**ORIGIN, "Content-Type": "application/json"},
    )

    assert r.status_code == 400, r.text


def test_set_publishing_malformed_json_400():
    fake = _FakeMonitor()
    app_module.live_monitor._monitors["kick:foo"] = fake
    client = TestClient(app_module.app, headers=ORIGIN)

    r = client.post(
        "/api/live-monitor/kick:foo/publishing",
        content=b"{not json",
        headers={**ORIGIN, "Content-Type": "application/json"},
    )

    assert r.status_code == 400, r.text



def test_stop_malformed_json_400():
    fake = _FakeMonitor()
    app_module.live_monitor._monitors["kick:foo"] = fake
    client = TestClient(app_module.app, headers=ORIGIN)

    r = client.post(
        "/api/live-monitor/stop",
        content=b"{not json",
        headers={**ORIGIN, "Content-Type": "application/json"},
    )

    assert r.status_code == 400, r.text
    assert app_module.live_monitor._monitors["kick:foo"] is fake


class _QueueMonitor(_FakeMonitor):
    def approve_publication(self, publication_id):
        from clippyme.core.errors import NotFoundError
        if publication_id != "22222222-2222-2222-2222-222222222222":
            raise NotFoundError("no queued clip")
        return {"approved": publication_id, "pending_publish": 1}

    def discard_publication(self, publication_id):
        from clippyme.core.errors import ConflictError, NotFoundError
        if publication_id == "11111111-1111-1111-1111-111111111111":
            raise ConflictError("clip is being published right now")
        if publication_id != "22222222-2222-2222-2222-222222222222":
            raise NotFoundError("no queued clip")
        return {"discarded": publication_id, "pending_publish": 0}


def test_discard_pending_clip_routes():
    app_module.live_monitor._monitors["kick:foo"] = _QueueMonitor()
    client = TestClient(app_module.app, headers=ORIGIN)
    base = "/api/live-monitor/kick:foo/pending"

    ok = client.post(f"{base}/22222222-2222-2222-2222-222222222222/discard")
    assert ok.status_code == 200, ok.text
    assert ok.json()["pending_publish"] == 0
    assert client.post(f"{base}/11111111-1111-1111-1111-111111111111/discard").status_code == 409
    assert client.post(f"{base}/33333333-3333-3333-3333-333333333333/discard").status_code == 404
    assert client.post(f"{base}/not..valid/discard").status_code in (400, 404)
    assert client.post(
        "/api/live-monitor/kick:nope/pending/22222222-2222-2222-2222-222222222222/discard"
    ).status_code == 404


def test_discard_pending_clip_untrusted_origin_rejected():
    app_module.live_monitor._monitors["kick:foo"] = _QueueMonitor()
    client = TestClient(app_module.app, headers={"Origin": "https://evil.example"})
    r = client.post("/api/live-monitor/kick:foo/pending/22222222-2222-2222-2222-222222222222/discard")
    assert r.status_code in (403, 400)


def test_post_now_route(monkeypatch):
    # The live-monitor routes share one 10/min bucket; earlier tests here use it.
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "0")
    app_module.live_monitor._monitors["kick:foo"] = _QueueMonitor()
    client = TestClient(app_module.app, headers=ORIGIN)
    base = "/api/live-monitor/kick:foo/pending"
    ok = client.post(f"{base}/22222222-2222-2222-2222-222222222222/publish")
    assert ok.status_code == 200 and ok.json()["approved"].startswith("2222")
    assert client.post(f"{base}/33333333-3333-3333-3333-333333333333/publish").status_code == 404
    evil = TestClient(app_module.app, headers={"Origin": "https://evil.example"})
    assert evil.post(f"{base}/22222222-2222-2222-2222-222222222222/publish").status_code in (400, 403)
