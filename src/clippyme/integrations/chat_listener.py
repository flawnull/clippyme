"""Read-only live chat listeners for Twitch and Kick.

Both connect anonymously over a websocket and only read: no login, no token,
nothing is ever sent to the chat. ``ChatRecorder`` runs one listener next to a
segment capture and returns ``(offset, weight, reaction)`` events for
``monitoring.chat_signal``.

Failure is never fatal. A missing ``websockets`` package, a refused
connection or a changed wire format leaves the recorder with fewer (or no)
events, and the segment is clipped from the transcript alone, as before.

Endpoints:
- Twitch: IRC over websocket at ``wss://irc-ws.chat.twitch.tv``, anonymous
  ``justinfan`` login, which Twitch documents for read-only clients.
- Kick: its public Pusher app, channel ``chatrooms.<id>.v2``. Kick rotates
  the app key (the old one died in October 2026), so the current key and
  cluster are asked from Kick's realtime endpoint and cached for an hour,
  with a known-good fallback. ``CLIPPYME_KICK_PUSHER_URL`` overrides both.
  Kick documents none of this.

A quiet chat is not a dead connection: after a silent minute the listener
pings the server and only reconnects if that gets no answer either.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import time
from collections.abc import Callable

from clippyme.monitoring.chat_signal import (
    parse_kick_event,
    parse_twitch_privmsg,
    score_message,
)

logger = logging.getLogger("clippyme")

TWITCH_IRC_URL = "wss://irc-ws.chat.twitch.tv:443"
KICK_REALTIME_URL = "https://web.kick.com/api/v1/realtime/connection"
# Used when the realtime endpoint can't be asked. Verified 2026-11-15.
FALLBACK_KICK_PUSHER = ("34bf7a0ff419a2a775b9", "us2")
_KICK_CACHE_SECONDS = 3600
_KEY_RE = re.compile(r"[0-9a-f]{16,40}")
_CLUSTER_RE = re.compile(r"[a-z0-9]{2,12}")
_kick_cache: dict = {}
# Bound memory on a very busy chat: a 60-minute segment at 100 msg/s fits.
MAX_EVENTS = 400_000
_RECV_TIMEOUT = 60.0
_MAX_BACKOFF = 30.0


def pusher_url(app_key: str, cluster: str) -> str:
    return (f"wss://ws-{cluster}.pusher.com/app/{app_key}"
            "?protocol=7&client=js&version=8.4.0&flash=false")


def parse_kick_realtime(payload) -> tuple[str, str] | None:
    """``(app_key, cluster)`` of the Pusher entry in Kick's realtime answer
    (``data.connections[].credentials``), or None. Both are format-checked,
    since they end up in a websocket URL."""
    data = payload.get("data") if isinstance(payload, dict) else None
    for conn in (data or {}).get("connections") or []:
        if not isinstance(conn, dict) or conn.get("provider") != "pusher":
            continue
        creds = conn.get("credentials") or {}
        key, cluster = str(creds.get("app_key") or ""), str(creds.get("cluster") or "")
        if _KEY_RE.fullmatch(key) and _CLUSTER_RE.fullmatch(cluster):
            return key, cluster
    return None


def _ask_kick_realtime() -> tuple[str, str] | None:
    import uuid

    import requests
    body = {"client": {"id": str(uuid.uuid4()), "type": "web"},
            "capabilities": {"accepted_providers": [{"provider": "pusher"}]}}
    resp = requests.post(KICK_REALTIME_URL, json=body, timeout=10, headers={
        "Accept": "application/json", "Origin": "https://kick.com", "Referer": "https://kick.com/"})
    resp.raise_for_status()
    return parse_kick_realtime(resp.json())


def kick_pusher_url(*, refresh: bool = False) -> str:
    """Websocket URL for Kick chat: env override, else Kick's current key
    (cached an hour), else the fallback key. Blocking; call in a thread."""
    override = os.environ.get("CLIPPYME_KICK_PUSHER_URL")
    if override:
        return override
    now = time.time()
    if not refresh and _kick_cache.get("until", 0) > now:
        return _kick_cache["url"]
    try:
        found = _ask_kick_realtime()
    except Exception as exc:  # network, Cloudflare, shape change
        logger.info("Chat signal: Kick realtime lookup failed (%s), using fallback key",
                    type(exc).__name__)
        found = None
    key, cluster = found or FALLBACK_KICK_PUSHER
    _kick_cache.update(url=pusher_url(key, cluster), until=now + _KICK_CACHE_SECONDS)
    return _kick_cache["url"]


async def _recv(ws, ping: str):
    """Next frame. After a silent _RECV_TIMEOUT, ping once; a second silence
    raises TimeoutError, which the caller treats as a dead connection."""
    try:
        return await asyncio.wait_for(ws.recv(), timeout=_RECV_TIMEOUT)
    except TimeoutError:
        await ws.send(ping)
        return await asyncio.wait_for(ws.recv(), timeout=_RECV_TIMEOUT)


async def _twitch_session(ws, channel: str, on_text: Callable[[str], None],
                          stop: asyncio.Event) -> None:
    nick = f"justinfan{random.randint(10000, 99999)}"
    await ws.send("PASS SCHMOOPIIE")
    await ws.send(f"NICK {nick}")
    await ws.send(f"JOIN #{channel.lower()}")
    while not stop.is_set():
        frame = await _recv(ws, "PING :tmi.twitch.tv")
        for line in str(frame).split("\r\n"):
            if not line:
                continue
            if line.startswith("PING"):
                await ws.send("PONG" + line[4:])
                continue
            text = parse_twitch_privmsg(line)
            if text is not None:
                on_text(text)


async def _kick_session(ws, chatroom_id: int, on_text: Callable[[str], None],
                        stop: asyncio.Event) -> None:
    await ws.send(json.dumps({
        "event": "pusher:subscribe",
        "data": {"auth": "", "channel": f"chatrooms.{chatroom_id}.v2"},
    }))
    while not stop.is_set():
        frame = await _recv(ws, '{"event":"pusher:ping","data":{}}')
        if '"pusher:ping"' in str(frame):
            await ws.send('{"event":"pusher:pong","data":{}}')
            continue
        text = parse_kick_event(frame)
        if text is not None:
            on_text(text)


async def listen(platform: str, channel: str, on_text: Callable[[str], None],
                 stop: asyncio.Event, *, chatroom_id: int | None = None) -> None:
    """Feed every chat message to ``on_text`` until ``stop`` is set.

    Reconnects with backoff. Returns quietly when chat is unavailable for this
    platform, the channel has no chatroom id (Kick), or ``websockets`` is
    missing. Never raises, except ``CancelledError``.
    """
    if platform == "kick" and not chatroom_id:
        logger.info("Chat signal: no Kick chatroom id for %s, chat not recorded", channel)
        return
    if platform not in ("kick", "twitch"):
        return
    try:
        import websockets
    except ImportError:
        logger.warning("Chat signal: 'websockets' not installed, chat not recorded")
        return

    backoff = 2.0
    refresh = False
    while not stop.is_set():
        try:
            if platform == "twitch":
                url = TWITCH_IRC_URL
            else:
                url = await asyncio.to_thread(kick_pusher_url, refresh=refresh)
                refresh = False
            async with websockets.connect(url, open_timeout=15, close_timeout=5,
                                          max_size=2 ** 20) as ws:
                backoff = 2.0
                if platform == "twitch":
                    await _twitch_session(ws, channel, on_text, stop)
                else:
                    await _kick_session(ws, int(chatroom_id), on_text, stop)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # network, protocol, format drift: never fatal
            if stop.is_set():
                return
            # Kick refusing the key (pusher:error 4001) means it rotated:
            # ask the realtime endpoint again on the next try.
            refresh = platform == "kick"
            logger.info("Chat signal (%s/%s): %s: %s, reconnecting in %.0fs",
                        platform, channel, type(exc).__name__, str(exc)[:160], backoff)
            try:
                await asyncio.wait_for(stop.wait(), timeout=backoff)
            except TimeoutError:
                pass
            backoff = min(_MAX_BACKOFF, backoff * 2)


class ChatRecorder:
    """Records scored chat messages for the duration of one segment capture."""

    def __init__(self, platform: str, channel: str, *, chatroom_id: int | None = None):
        self.platform = platform
        self.channel = channel
        self.chatroom_id = chatroom_id
        self.events: list[tuple[float, float, str | None]] = []
        self._t0 = 0.0
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    def _on_text(self, text: str) -> None:
        if len(self.events) >= MAX_EVENTS:
            return
        weight, kind = score_message(text)
        self.events.append((time.monotonic() - self._t0, weight, kind))

    def start(self) -> None:
        self._t0 = time.monotonic()
        self._task = asyncio.create_task(listen(
            self.platform, self.channel, self._on_text, self._stop,
            chatroom_id=self.chatroom_id))

    def cancel(self) -> None:
        """Stop at once without waiting (the capture itself was cancelled)."""
        self._stop.set()
        if self._task is not None:
            self._task.cancel()

    async def stop(self) -> list[tuple[float, float, str | None]]:
        """Stop listening and return the recorded events."""
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except asyncio.CancelledError:
                raise
            except Exception:  # timeout or a listener bug: drop the task
                self._task.cancel()
        return self.events
