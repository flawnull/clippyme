"""Clips from live-chat spikes when Gemini is out of quota (pure, host-tested).

The live monitor writes the spike windows it found while recording a segment to
``chat_spikes.json`` in the job directory (``monitoring/chat_signal.py``). When
every Gemini model is rate-limited, the pipeline would otherwise publish
nothing for that segment. Instead it builds clips around the strongest spikes:
viewers already voted on those moments. Titles and hooks are generic (no AI),
and each clip says in ``viral_reason`` where it came from.

Output matches ``get_viral_clips``: ``{"shorts": [...]}``, so the clips go
through the same edge snapping, cutting and rendering as Gemini's.
"""
from __future__ import annotations

import json
import os

SPIKES_FILENAME = "chat_spikes.json"

# Clip length bounds, inside the clip schema's 10-75 s cushion.
MIN_CLIP_SECONDS = 15.0
MAX_CLIP_SECONDS = 60.0
# A spike scored at the ratio threshold maps to this, so it passes the
# monitor's default "auto" floor (70); stronger spikes score higher.
BASE_SCORE = 72
MAX_SCORE = 90

_COPY = {
    "laugh": ("Chat could not stop laughing at this 😂", "Wait for it 😂"),
    "shock": ("Chat could not believe this 😳", "Nobody saw this coming"),
    "hype": ("Chat went crazy for this 🔥", "This moment went off 🔥"),
    None: ("Chat exploded at this moment", "Watch what happens"),
}


def write_spikes(job_dir: str, spikes) -> None:
    """Persist spike windows for a job: ``[{start, end, ratio, reaction}]``."""
    data = [{"start": s.start, "end": s.end, "ratio": s.ratio, "reaction": s.reaction}
            for s in spikes]
    tmp = os.path.join(job_dir, SPIKES_FILENAME + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(tmp, os.path.join(job_dir, SPIKES_FILENAME))


def read_spikes(job_dir: str) -> list[dict]:
    """Spike windows saved for this job, or [] when none or unreadable."""
    try:
        with open(os.path.join(job_dir, SPIKES_FILENAME), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    return [s for s in data if isinstance(s, dict)] if isinstance(data, list) else []


def _window(start: float, end: float, duration: float) -> tuple[float, float]:
    """Stretch a spike window to MIN..MAX seconds, kept inside the video."""
    start, end = max(0.0, float(start)), float(end)
    if duration > 0:
        end = min(end, duration)
    length = end - start
    if length < MIN_CLIP_SECONDS:
        pad = (MIN_CLIP_SECONDS - length) / 2
        start, end = start - pad, end + pad
    elif length > MAX_CLIP_SECONDS:
        end = start + MAX_CLIP_SECONDS
    if start < 0:
        start, end = 0.0, end - start
    if duration > 0 and end > duration:
        start, end = max(0.0, start - (end - duration)), duration
    return round(start, 2), round(end, 2)


def _text_between(transcript: dict, start: float, end: float) -> str:
    parts = []
    for seg in (transcript or {}).get("segments") or []:
        try:
            if float(seg.get("end", 0)) > start and float(seg.get("start", 0)) < end:
                parts.append(str(seg.get("text") or "").strip())
        except (TypeError, ValueError):
            continue
    return " ".join(p for p in parts if p)


def _score(ratio) -> int:
    try:
        r = float(ratio)
    except (TypeError, ValueError):
        return BASE_SCORE
    return int(max(BASE_SCORE, min(MAX_SCORE, BASE_SCORE + (r - 2.5) * 3)))


def build_chat_spike_clips(spikes: list[dict], transcript: dict, duration: float,
                           *, max_clips: int = 5, creator: str | None = None) -> dict | None:
    """``{"shorts": [...]}`` from spike windows, strongest first, or None.

    ``max_clips`` 0 means no cap. Overlapping windows keep the stronger one.
    """
    ranked = sorted((s for s in spikes if "start" in s and "end" in s),
                    key=lambda s: float(s.get("ratio") or 0), reverse=True)
    shorts, taken = [], []
    for spike in ranked:
        start, end = _window(spike["start"], spike["end"], duration)
        if end - start < 10 or any(start < e and end > s for s, e in taken):
            continue
        taken.append((start, end))
        reaction = spike.get("reaction") if spike.get("reaction") in _COPY else None
        title, hook = _COPY[reaction]
        if creator:
            title = f"{creator}: {title}"
        text = _text_between(transcript, start, end)
        shorts.append({
            "start": start,
            "end": end,
            "video_title_for_youtube_short": title[:100],
            "tiktok_caption": (text[:150] or title),
            "viral_score": _score(spike.get("ratio")),
            "viral_reason": (f"Chat spike fallback: chat hit {spike.get('ratio')}x its normal "
                             "pace here. Gemini quota was used up, so no AI scoring."),
            "hook": hook,
            "viral_hook_text": hook,
        })
        if max_clips and len(shorts) >= max_clips:
            break
    if not shorts:
        return None
    return {"shorts": sorted(shorts, key=lambda c: c["start"])}
