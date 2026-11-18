"""Per-job output language, shared by transcription and the Gemini prompt.

``main.py --language`` (the live monitor passes ``en`` by default) sets
``CLIPPYME_LANGUAGE``. When it names a language, every transcriber is locked
to it and Gemini writes titles, hooks and captions in it, whatever language
the audio drifts into. ``multi`` / ``auto`` / unset keep auto-detection.
"""
from __future__ import annotations

import os

_NAMES = {
    "en": "English", "it": "Italian", "es": "Spanish", "fr": "French",
    "de": "German", "pt": "Portuguese", "nl": "Dutch", "hi": "Hindi",
    "ja": "Japanese", "ru": "Russian", "pl": "Polish", "tr": "Turkish",
    "sv": "Swedish", "da": "Danish", "nb": "Norwegian", "fi": "Finnish",
    "cs": "Czech", "uk": "Ukrainian", "el": "Greek", "ko": "Korean",
    "zh": "Chinese",
}


def job_language() -> str | None:
    """The forced language code for this job, or None for auto-detect."""
    lang = (os.getenv("CLIPPYME_LANGUAGE") or "").strip()
    if not lang or lang.lower() in ("multi", "auto"):
        return None
    return lang


def language_name(code: str) -> str:
    return _NAMES.get(code.split("-")[0].lower(), code)
