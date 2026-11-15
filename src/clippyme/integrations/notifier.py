"""Phone notifications through ntfy (https://ntfy.sh).

The owner installs the ntfy app, subscribes to a topic, and saves the same
topic in Settings (``NTFY_TOPIC``). Anyone who knows a topic can read it, so the
dashboard generates a long random one and the API treats it like a secret.

Sending is best-effort: a missing topic, a network error or a non-2xx answer
returns False and logs; it never raises into the monitor.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger("clippyme")

NTFY_URL = "https://ntfy.sh"
TOPIC_RE = re.compile(r"[A-Za-z0-9_-]{8,64}")
_TIMEOUT = 10


def valid_topic(topic: str | None) -> bool:
    return bool(topic) and bool(TOPIC_RE.fullmatch(topic))


def configured_topic() -> str | None:
    from clippyme.storage.config_store import load_persistent_config
    topic = (load_persistent_config() or {}).get("NTFY_TOPIC") or ""
    return topic if valid_topic(topic) else None


def send(title: str, message: str, *, tags: tuple[str, ...] = (), priority: int = 3,
         topic: str | None = None) -> bool:
    """Publish one notification. Returns True when ntfy accepted it."""
    topic = topic or configured_topic()
    if not valid_topic(topic):
        return False
    import requests
    body = {"topic": topic, "title": title[:250], "message": message[:3500],
            "priority": max(1, min(5, int(priority)))}
    if tags:
        body["tags"] = list(tags)
    try:
        resp = requests.post(NTFY_URL, json=body, timeout=_TIMEOUT)
    except Exception as exc:  # network: never fatal
        logger.warning("ntfy notification failed: %s", type(exc).__name__)
        return False
    if not 200 <= getattr(resp, "status_code", 0) < 300:
        logger.warning("ntfy notification rejected: HTTP %s", getattr(resp, "status_code", "?"))
        return False
    return True


def new_clips_message(channel: str, clips: list[dict], *, review_minutes: int,
                      publishing_enabled: bool) -> tuple[str, str]:
    """Title and body for "a segment produced clips"."""
    n = len(clips)
    title = f"{n} new clip{'s' if n != 1 else ''} · {channel}"
    lines = []
    for clip in clips[:5]:
        score = clip.get("viral_score")
        name = (clip.get("title") or "Untitled clip").strip()
        lines.append(f"• {name}" + (f" ({score})" if isinstance(score, (int, float)) else ""))
    if n > 5:
        lines.append(f"• +{n - 5} more")
    if not publishing_enabled:
        lines.append("Auto-publish is off: open the dashboard to review.")
    elif review_minutes > 0:
        lines.append(f"Posting in {review_minutes} min unless you discard them.")
    else:
        lines.append("Posting now.")
    return title, "\n".join(lines)
