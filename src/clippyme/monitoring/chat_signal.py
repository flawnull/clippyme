"""Live-chat spike detection for the live monitor (pure, host-testable).

While a live segment is captured, the monitor records chat messages with their
offset from the start of the capture. Bursts of chat (and of reaction emotes)
mark moments viewers reacted to, which the transcript alone can miss: a
gameplay moment where chat explodes but nobody on stream says much. This module
turns those messages into a short hint appended to the segment's Gemini
instructions. It never chooses clips itself; Gemini still needs a
self-contained moment in the transcript.

Nothing here touches the network. ``integrations/chat_listener.py`` does the
I/O and feeds :func:`score_message` results into :func:`find_spikes`.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from statistics import median

# Width of one counting bin, in seconds.
BIN_SECONDS = 10
# A bin is a spike when its weight is at least this many times the segment's
# typical (median) bin weight...
SPIKE_RATIO = 2.5
# ...and carries at least this much absolute weight, so a near-empty chat does
# not turn three messages into a "spike".
MIN_SPIKE_WEIGHT = 8.0
# Floor for the baseline, for chats so quiet the median bin is ~0.
MIN_BASELINE = 1.0
# Below this many messages in the whole segment the chat says nothing useful.
MIN_SEGMENT_MESSAGES = 30
# Chat reacts after the moment it reacts to. The hint window starts this many
# seconds before the spike so the build-up is included.
LEAD_SECONDS = 20
MAX_HINTS = 5

# Tokens that mark a reaction rather than conversation. Kick sends emotes as
# "[emote:123:KEKW]", which still contains the emote name.
_REACTIONS = {
    "laugh": {"kekw", "lul", "lulw", "omegalul", "lmao", "lmfao", "xd", "xdd",
              "icant", "dead", "💀", "😂", "🤣", "haha", "hahaha", "kek"},
    "shock": {"wtf", "omg", "holy", "monkas", "noway", "😳", "😱", "aware", "?"},
    "hype": {"pog", "poggers", "pogchamp", "w", "ww", "www", "letsgo",
             "goat", "clip", "clipit", "🔥", "insane"},
}
_TOKEN_RE = re.compile(r"[A-Za-z]+|\?+|[\U0001F300-\U0001FAFF]")
_EMOTE_RE = re.compile(r"\[emote:\d+:([^\]]+)\]")


@dataclass(frozen=True)
class Spike:
    start: float  # seconds from segment start, lead included
    end: float
    ratio: float  # peak bin weight / baseline
    reaction: str | None  # dominant reaction kind, or None


def classify_message(text: str) -> str | None:
    """Return the reaction kind a message expresses ("laugh"/"shock"/"hype")."""
    if not text:
        return None
    text = _EMOTE_RE.sub(lambda m: f" {m.group(1)} ", text)
    # A run of question marks ("????") is one shock token.
    tokens = ["?" if t.startswith("?") else t.lower() for t in _TOKEN_RE.findall(text)]
    best, best_hits = None, 0
    for kind, vocab in _REACTIONS.items():
        hits = sum(1 for t in tokens if t in vocab)
        if hits > best_hits:
            best, best_hits = kind, hits
    return best


def score_message(text: str) -> tuple[float, str | None]:
    """Weight of one chat message and its reaction kind.

    A plain message counts 1; a reaction message counts 2, because a wall of
    KEKW is a stronger signal than the same number of ordinary messages.
    """
    kind = classify_message(text)
    return (2.0 if kind else 1.0), kind


def find_spikes(events: Iterable[tuple[float, float, str | None]],
                duration: float, *, max_hints: int = MAX_HINTS) -> list[Spike]:
    """Find chat spikes in one segment.

    ``events`` are ``(offset_seconds, weight, reaction_kind)`` tuples. Returns
    up to ``max_hints`` spikes, strongest first by peak ratio, then ordered by
    time. Empty when the chat was too quiet to judge.
    """
    events = [e for e in events if 0 <= e[0] <= duration]
    if duration <= 0 or len(events) < MIN_SEGMENT_MESSAGES:
        return []
    n = int(duration // BIN_SECONDS) + 1
    weights = [0.0] * n
    kinds: list[dict[str, float]] = [{} for _ in range(n)]
    for t, w, kind in events:
        i = int(t // BIN_SECONDS)
        weights[i] += w
        if kind:
            kinds[i][kind] = kinds[i].get(kind, 0.0) + w
    baseline = max(MIN_BASELINE, median(weights))
    hot = [w >= SPIKE_RATIO * baseline and w >= MIN_SPIKE_WEIGHT for w in weights]

    spikes: list[Spike] = []
    i = 0
    while i < n:
        if not hot[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and hot[j + 1]:
            j += 1
        peak = max(weights[i:j + 1])
        totals: dict[str, float] = {}
        for k in range(i, j + 1):
            for kind, w in kinds[k].items():
                totals[kind] = totals.get(kind, 0.0) + w
        reaction = max(totals, key=totals.get) if totals else None
        spikes.append(Spike(
            start=max(0.0, i * BIN_SECONDS - LEAD_SECONDS),
            end=min(duration, (j + 1) * BIN_SECONDS),
            ratio=round(peak / baseline, 1),
            reaction=reaction,
        ))
        i = j + 1
    spikes.sort(key=lambda s: s.ratio, reverse=True)
    return sorted(spikes[:max_hints], key=lambda s: s.start)


def _mmss(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60:02d}:{s % 60:02d}" if s < 3600 else f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


_HINT_HEADER = (
    "LIVE CHAT SIGNAL (automatic, from the stream's chat): viewers reacted "
    "strongly at these times in this video. Something clip-worthy probably "
    "happened in or just before each window. Prefer clips that contain these "
    "moments when the transcript supports a self-contained clip; ignore a "
    "window if nothing worth clipping is there."
)


def format_hint(spikes: list[Spike]) -> str:
    """Render spikes as an instruction block for Gemini, or "" for none."""
    if not spikes:
        return ""
    lines = [_HINT_HEADER]
    for s in spikes:
        what = f", mostly {s.reaction}" if s.reaction else ""
        lines.append(f"- {_mmss(s.start)}-{_mmss(s.end)} (chat {s.ratio}x normal{what})")
    return "\n".join(lines)


def merge_instructions(user: str | None, hint: str, limit: int) -> str | None:
    """Append ``hint`` to the user's instructions within ``limit`` characters.

    The user's own instructions always survive whole. Spike lines are dropped
    from the end until the result fits; if not even one fits, the hint is left
    out.
    """
    user = (user or "").strip()
    if not hint:
        return user or None
    lines = hint.split("\n")
    while len(lines) > 1:
        block = "\n".join(lines)
        merged = f"{user}\n\n{block}" if user else block
        if len(merged) <= limit:
            return merged
        lines.pop()
    return user or None


def build_chat_hint(events, duration: float) -> str:
    """Events for one segment → hint text ("" when chat gave no signal)."""
    return format_hint(find_spikes(events, duration))


# -- wire-format parsers (pure; the listener calls these) ---------------------

def parse_twitch_privmsg(line: str) -> str | None:
    """Message text from one Twitch IRC line, or None if it is not a PRIVMSG."""
    marker = " PRIVMSG #"
    idx = line.find(marker)
    if idx < 0:
        return None
    rest = line[idx + len(marker):]
    sep = rest.find(" :")
    if sep < 0:
        return None
    return rest[sep + 2:].rstrip("\r\n")


def parse_kick_event(raw: str) -> str | None:
    """Message text from one Kick (Pusher) websocket frame, or None."""
    try:
        frame = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(frame, dict) or not str(frame.get("event", "")).endswith("ChatMessageEvent"):
        return None
    data = frame.get("data")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            return None
    if not isinstance(data, dict):
        return None
    content = data.get("content")
    return content if isinstance(content, str) else None


def kick_chatroom_id(channel: dict | None) -> int | None:
    """Chatroom id from Kick channel JSON (``chatroom.id``), or None."""
    if not isinstance(channel, dict):
        return None
    room = channel.get("chatroom")
    raw = room.get("id") if isinstance(room, dict) else None
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None
