"""Repair clips made before the job language was enforced.

Reported: clips in the publish queue and in History had Spanish or Italian
subtitles, titles and captions from English streams. New jobs are locked to
their language (``pipeline.language``). Older clips are fixed once: queued
ones when their monitor starts (and re-rendered), every other History clip
by a background sweep after startup (``sweep_history``):

- subtitles: when the job's transcript was not locked to the language, the
  clip's source slice is transcribed again in that language and its words
  replace the old ones for the clip's time range;
- text: title, hook and descriptions are rewritten in the language by Gemini.

Subtitles are burned in at render time from the job's transcript, so fixing
the metadata fixes every later render, download and publish; a stale rendered
copy is deleted. A clip is marked ``subtitle_language`` only once both its
subtitles and text are done, so a failed step (no Deepgram key, Gemini out of
quota) is retried at the next start. Gemini gets one call per job.

Runs in a worker thread under the clip locks; the metadata write goes through
``update_job_metadata`` (lock order: clip lock, then the metadata lock).
"""
from __future__ import annotations

import contextlib
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


def build_translation_prompt(clips: dict, language_name: str) -> str:
    return (
        f"Rewrite every text value of this JSON object in {language_name}. Each key "
        "is one short stream clip; its values are the title, overlay hook and "
        "captions. Keep the meaning, tone, emojis, hashtags and names. If a value "
        f"is already in {language_name}, return it unchanged. Answer with the JSON "
        "object only, same keys.\n\n" + json.dumps(clips, ensure_ascii=False)
    )


def parse_translation(text: str, clips: dict) -> dict:
    """``{clip key: {field: text}}`` for the requested clips and fields; a
    clip missing from the answer is left out."""
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for key, fields in clips.items():
        got = data.get(key)
        if isinstance(got, dict):
            out[key] = {k: v.strip() for k, v in got.items()
                        if k in fields and isinstance(v, str) and v.strip()}
    return out


def gemini_translate(clips: dict, language_name: str) -> dict:
    from clippyme.storage.config_store import load_persistent_config

    cfg = load_persistent_config()
    api_key = cfg.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("Gemini API key not configured")
    from google import genai

    client = genai.Client(api_key=api_key)
    resp = client.models.generate_content(
        model=cfg.get("GEMINI_MODEL") or "gemini-3.5-flash",
        contents=build_translation_prompt(clips, language_name))
    return parse_translation(getattr(resp, "text", "") or "", clips)


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


def _text_fields(short: dict) -> dict:
    return {k: short[k] for k in TEXT_FIELDS if isinstance(short.get(k), str) and short[k].strip()}


def clips_to_fix(metadata: dict, language: str) -> list[int]:
    """Indices of clips still on disk and not yet in ``language``. A job whose
    transcript was locked to another language on purpose is left alone."""
    forced = (metadata.get("transcript") or {}).get("forced_language")
    if forced and forced != language:
        return []
    return [i for i, s in enumerate(metadata.get("shorts") or [])
            if isinstance(s, dict) and not s.get("deleted_after_publish")
            and s.get("subtitle_language") != language]


def fix_job(job_id: str, output_dir: str, language: str, indices=None, *,
            transcribe=deepgram_transcribe, translate=gemini_translate) -> dict:
    """Repair a job's clips (all that need it, or ``indices``). Returns
    ``{index: text fields}`` for every requested clip that is in
    ``language`` afterwards, including clips that already were."""
    from clippyme.clips.clip_resolve import clip_filename_for, composed_clip_basename
    from clippyme.jobs.job_artifacts import load_job_metadata, update_job_metadata
    from clippyme.pipeline.language import language_name

    try:
        metadata_path, metadata = load_job_metadata(job_id, output_dir)
    except (OSError, ValueError):
        logger.warning("language fix: no metadata for %s", job_id)
        return {}
    job_dir = os.path.dirname(metadata_path)
    shorts = metadata.get("shorts") or []
    wanted = [i for i in (indices if indices is not None else range(len(shorts)))
              if 0 <= i < len(shorts) and isinstance(shorts[i], dict)]
    done = {i: _text_fields(shorts[i]) for i in wanted
            if shorts[i].get("subtitle_language") == language}
    todo = [i for i in wanted if i not in done and i in clips_to_fix(metadata, language)]
    if not todo:
        return done

    redo = needs_retranscription(metadata.get("transcript") or {}, language)
    segments: dict[int, list] = {}
    if redo:
        for i in todo:
            name = clip_filename_for(metadata_path, shorts[i], i)
            source = os.path.join(job_dir, f"source_{name}")
            path = source if os.path.isfile(source) else os.path.join(job_dir, name)
            if not os.path.isfile(path):
                continue
            try:
                result = transcribe(path, language)
            except Exception:
                logger.warning("language fix: transcription failed for %s/%s",
                               job_id, i, exc_info=True)
                continue
            if result and result.get("segments") is not None:
                segments[i] = result["segments"]

    asked = {str(i): _text_fields(shorts[i]) for i in todo if _text_fields(shorts[i])}
    translated: dict = {}
    if asked:
        try:
            translated = translate(asked, language_name(language)) or {}
        except Exception:
            logger.warning("language fix: rewrite failed for %s", job_id, exc_info=True)

    old_composed = {i: composed_clip_basename(shorts[i], i) for i in todo}
    finished: list[int] = []

    def mutate(data: dict) -> bool:
        current = data.get("shorts") or []
        for i in todo:
            if i >= len(current) or not isinstance(current[i], dict):
                continue
            target = current[i]
            if i in segments:
                start, end = float(target.get("start", 0)), float(target.get("end", 0))
                data["transcript"] = splice_words(data.get("transcript") or {}, start, end,
                                                  segments[i], start)
            target.update(translated.get(str(i)) or {})
            subs_ok = not redo or i in segments
            text_ok = str(i) not in asked or str(i) in translated
            if subs_ok and text_ok:
                target["subtitle_language"] = language
                finished.append(i)
        return bool(segments or translated)

    update_job_metadata(job_id, output_dir, mutate)
    for i in todo:
        if i in segments or str(i) in translated:
            with contextlib.suppress(OSError):
                os.remove(os.path.join(job_dir, old_composed[i]))
    _, after = load_job_metadata(job_id, output_dir)
    for i in finished:
        done[i] = _text_fields(after["shorts"][i])
    logger.info("language fix: %s → %s: %d/%d clips done (subtitles redone for %d)",
                job_id, language, len(finished), len(todo), len(segments))
    return done


def fix_clip(job_id: str, clip_index: int, output_dir: str, language: str, **kw) -> dict | None:
    """One clip: its text fields once it is in ``language``, else None."""
    return fix_job(job_id, output_dir, language, [clip_index], **kw).get(clip_index)


def history_jobs(output_dir: str) -> list[str]:
    """Job ids with metadata, newest first."""
    from clippyme.jobs.history_service import is_valid_job_id

    jobs = []
    try:
        names = os.listdir(output_dir)
    except OSError:
        return []
    for name in names:
        path = os.path.join(output_dir, name)
        if is_valid_job_id(name) and os.path.isdir(path):
            jobs.append((os.path.getmtime(path), name))
    return [name for _, name in sorted(jobs, reverse=True)]
