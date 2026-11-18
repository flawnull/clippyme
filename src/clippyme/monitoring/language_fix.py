"""Repair queued monitor clips made before the job language was enforced.

Reported: clips in the publish queue had Spanish or Italian subtitles, titles
and captions from English streams. New jobs are locked to the monitor's
language (``pipeline.language``). Clips already queued are fixed once, when
the monitor starts:

- subtitles: when the job's transcript was not locked to the language, the
  clip's source slice is transcribed again in that language and its words
  replace the old ones for the clip's time range;
- text: title, hook and descriptions are rewritten in the language by Gemini.

The clip is marked ``subtitle_language`` so it is never repaired twice. Every
step is best effort: a clip that cannot be repaired keeps its old text.

Runs in a worker thread under the clip lock; the metadata write goes through
``update_job_metadata`` (lock order: clip lock, then the metadata lock).
"""
from __future__ import annotations

import json
import logging
import os
import re

logger = logging.getLogger("clippyme")

TEXT_FIELDS = (
    "title",
    "viral_hook_text",
    "video_title_for_youtube_short",
    "video_description_for_tiktok",
    "video_description_for_instagram",
)


def needs_retranscription(transcript: dict, language: str) -> bool:
    """True when the job's words were not locked to ``language``."""
    if transcript.get("forced_language") == language:
        return False
    return str(transcript.get("language") or "").split("-")[0].lower() != language.split("-")[0].lower()


def splice_words(transcript: dict, start: float, end: float, segments: list, offset: float) -> dict:
    """Replace the words in ``[start, end]`` with ``segments`` (times relative
    to the clip, shifted by ``offset``). Returns the updated transcript."""
    kept = []
    for seg in transcript.get("segments") or []:
        words = seg.get("words") or []
        outside = [w for w in words
                   if not (start <= float(w.get("start", 0)) < end)]
        if words and not outside:
            continue  # segment fully inside the clip: replaced
        if len(outside) != len(words):
            seg = dict(seg, words=outside,
                       text=" ".join(str(w.get("word", "")).strip() for w in outside))
        elif not words and start <= float(seg.get("start", 0)) < end:
            continue
        kept.append(seg)
    for seg in segments or []:
        words = [dict(w, start=float(w["start"]) + offset, end=float(w["end"]) + offset)
                 for w in seg.get("words") or [] if "start" in w and "end" in w]
        if not words:
            continue
        kept.append(dict(seg, words=words,
                         start=float(seg.get("start", words[0]["start"] - offset)) + offset,
                         end=float(seg.get("end", words[-1]["end"] - offset)) + offset))
    kept.sort(key=lambda s: float(s.get("start", 0)))
    return dict(transcript, segments=kept)


def build_translation_prompt(fields: dict, language_name: str) -> str:
    return (
        f"Rewrite each value of this JSON object in {language_name}. They are the "
        "title, overlay hook and captions of a short stream clip. Keep the meaning, "
        "tone, emojis, hashtags and names. If a value is already in "
        f"{language_name}, return it unchanged. Answer with the JSON object only, "
        "same keys.\n\n" + json.dumps(fields, ensure_ascii=False)
    )


def parse_translation(text: str, fields: dict) -> dict:
    """Translated values for the requested keys; {} when unusable."""
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v.strip() for k, v in data.items()
            if k in fields and isinstance(v, str) and v.strip()}


def gemini_translate(fields: dict, language_name: str) -> dict:
    from clippyme.storage.config_store import load_persistent_config

    cfg = load_persistent_config()
    api_key = cfg.get("GEMINI_API_KEY")
    if not api_key or not fields:
        return {}
    from google import genai

    client = genai.Client(api_key=api_key)
    resp = client.models.generate_content(
        model=cfg.get("GEMINI_MODEL") or "gemini-3.5-flash",
        contents=build_translation_prompt(fields, language_name))
    return parse_translation(getattr(resp, "text", "") or "", fields)


def deepgram_transcribe(path: str, language: str) -> dict | None:
    from clippyme.storage.config_store import load_persistent_config

    key = load_persistent_config().get("DEEPGRAM_API_KEY")
    if not key:
        return None
    from clippyme.pipeline.transcription.deepgram_transcribe import (
        transcribe_with_deepgram,
    )

    os.environ.setdefault("DEEPGRAM_API_KEY", key)
    return transcribe_with_deepgram(path, language=language)


def fix_clip(job_id: str, clip_index: int, output_dir: str, language: str, *,
             transcribe=deepgram_transcribe, translate=gemini_translate) -> dict | None:
    """Repair one clip. Returns the updated text fields (possibly empty), or
    None when the clip was already in ``language`` or could not be read."""
    from clippyme.clips.clip_resolve import resolve_clip
    from clippyme.core.errors import NotFoundError
    from clippyme.jobs.job_artifacts import update_job_metadata
    from clippyme.pipeline.language import language_name

    try:
        resolved = resolve_clip(job_id, clip_index, output_dir, require_file=True)
    except (NotFoundError, OSError, ValueError):
        logger.warning("language fix: clip %s/%s not found", job_id, clip_index)
        return None
    short = resolved.clip_info
    if short.get("subtitle_language") == language:
        return None
    name = language_name(language)

    new_segments = None
    transcript = resolved.metadata.get("transcript") or {}
    redo = needs_retranscription(transcript, language)
    if redo:
        source = os.path.join(resolved.job_dir, f"source_{resolved.clip_filename}")
        try:
            result = transcribe(source if os.path.isfile(source) else resolved.clip_path, language)
            new_segments = (result or {}).get("segments")
        except Exception:
            logger.warning("language fix: transcription failed for %s/%s",
                           job_id, clip_index, exc_info=True)

    fields = {k: short[k] for k in TEXT_FIELDS if isinstance(short.get(k), str) and short[k].strip()}
    try:
        translated = translate(fields, name) or {}
    except Exception:
        logger.warning("language fix: rewrite failed for %s/%s", job_id, clip_index, exc_info=True)
        translated = {}

    def mutate(data: dict) -> bool:
        shorts = data.get("shorts") or []
        if clip_index >= len(shorts):
            return False
        target = shorts[clip_index]
        if new_segments:
            start, end = float(target.get("start", 0)), float(target.get("end", 0))
            data["transcript"] = splice_words(data.get("transcript") or {}, start, end,
                                              new_segments, start)
        target.update(translated)
        if new_segments or not redo:
            # Left unmarked when the subtitles still need redoing (no
            # Deepgram key, a failed call): the next start tries again.
            target["subtitle_language"] = language
        return True

    update_job_metadata(job_id, output_dir, mutate)
    logger.info("language fix: %s/%s → %s (subtitles %s, %d text fields)", job_id, clip_index,
                language, "redone" if new_segments else "kept", len(translated))
    return translated
