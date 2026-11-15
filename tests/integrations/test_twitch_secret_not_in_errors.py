"""The Twitch client secret must never appear in an error message.

It used to be sent as a query parameter, and requests repeats the full URL in
its exceptions, so a failed token request showed the secret in the monitor's
last_error on the dashboard.
"""
import sys
import types

import pytest

from clippyme.integrations.twitch_client import TwitchClient

SECRET = "s3cr3t-" + "x" * 20


def _fake_requests(monkeypatch, behaviour):
    calls = []

    def post(url, params=None, data=None, timeout=None, **_kw):
        calls.append({"url": url, "params": params, "data": data})
        return behaviour(url, params, data)

    fake = types.SimpleNamespace(post=post)
    monkeypatch.setitem(sys.modules, "requests", fake)
    return calls


def test_secret_goes_in_the_body_not_the_url(monkeypatch):
    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"access_token": "tok", "expires_in": 3600}

    calls = _fake_requests(monkeypatch, lambda url, params, data: _Resp())
    TwitchClient("cid", SECRET)._get_token()
    assert calls[0]["data"]["client_secret"] == SECRET
    assert not calls[0]["params"]
    assert SECRET not in calls[0]["url"]


def test_connection_error_message_has_no_secret(monkeypatch):
    def fail(url, params, data):
        # requests-style: the message repeats the URL incl. its query string.
        query = "&".join(f"{k}={v}" for k, v in (params or {}).items())
        raise ConnectionError(f"Max retries exceeded with url: {url}?{query}")

    _fake_requests(monkeypatch, fail)
    with pytest.raises(ConnectionError) as exc:
        TwitchClient("cid", SECRET)._get_token()
    assert SECRET not in str(exc.value)
