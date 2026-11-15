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
- Kick: its public Pusher app (``CLIPPYME_KICK_PUSHER_URL``), channel
  ``chatrooms.<id>.v2``. Kick does not document this; the URL is overridable
  because the app key can change.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from collections.abc import Callable

from clippyme.monitoring.chat_signal import (
    parse_kick_event,
    parse_twitch_privmsg,
    score_message,
)

logger = logging.getLogger("clippyme")

TWITCH_IRC_URL = "wss://irc-ws.chat.twitch.tv:443"
DEFAULT_KICK_PUSHER_URL = (
    "wss://ws-us2.pusher.com/app/32cbd69e4b950bf97679"
    "?protocol=7&client=js&version=8.4.0&flash=false"
)
# Bound memory on a very busy chat: a 60-minute segment at 100 msg/s fits.
MAX_EVENTS = 400_000
_RECV_TIMEOUT = 60.0
_MAX_BACKOFF = 30.0


def _kick_pusher_url() -> str:
    return os.environ.get("CLIPPYME_KICK_PUSHER_URL") or DEFAULT_KICK_PUSHER_URL


async def _twitch_session(ws, channel: str, on_text: Callable[[str], None],
                          stop: asyncio.Event) -> None:
    nick = f"justinfan{random.randint(10000, 99999)}"
    await ws.send("PASS SCHMOOPIIE")
    await ws.send(f"NICK {nick}")
    await ws.send(f"JOIN #{channel.lower()}")
    while not stop.is_set():
        frame = await asyncio.wait_for(ws.recv(), timeout=_RECV_TIMEOUT)
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
    import json
    await ws.send(json.dumps({
        "event": "pusher:subscribe",
        "data": {"auth": "", "channel": f"chatrooms.{chatroom_id}.v2"},
    }))
    while not stop.is_set():
        frame = await asyncio.wait_for(ws.recv(), timeout=_RECV_TIMEOUT)
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

    url = TWITCH_IRC_URL if platform == "twitch" else _kick_pusher_url()
    backoff = 2.0
    while not stop.is_set():
        try:
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
            logger.info("Chat signal (%s/%s): %s, reconnecting in %.0fs",
                        platform, channel, exc, backoff)
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
