"""A forced job language (live monitor default "en") reaches every
transcriber and the Gemini prompt. Reported: clips came out with Spanish or
Italian subtitles and titles from English streams."""
from clippyme.pipeline.analysis.gemini_request import build_viral_prompt
from clippyme.pipeline.language import job_language, language_name
from clippyme.pipeline.transcription import deepgram_transcribe, elevenlabs_transcribe

TRANSCRIPT = {"text": "hola chat", "segments": [{"words": [
    {"word": "hola", "start": 0.0, "end": 0.4}, {"word": "chat", "start": 0.4, "end": 0.8}]}]}


def test_job_language(monkeypatch):
    for value in ("", "multi", "auto", "MULTI"):
        monkeypatch.setenv("CLIPPYME_LANGUAGE", value)
        assert job_language() is None
    monkeypatch.setenv("CLIPPYME_LANGUAGE", "en")
    assert job_language() == "en"
    assert language_name("en") == "English" and language_name("en-US") == "English"


def test_prompt_forces_english_when_job_language_is_set(monkeypatch):
    monkeypatch.setenv("CLIPPYME_LANGUAGE", "en")
    prompt, _ = build_viral_prompt(TRANSCRIPT, 60)
    assert "MUST be written in English" in prompt
    assert "SAME LANGUAGE as the transcript" not in prompt
    assert "Same language as" not in prompt


def test_prompt_follows_transcript_without_a_job_language(monkeypatch):
    monkeypatch.delenv("CLIPPYME_LANGUAGE", raising=False)
    prompt, _ = build_viral_prompt(TRANSCRIPT, 60)
    assert "SAME LANGUAGE as the transcript" in prompt
    assert "{lang_phrase" not in prompt and "{language_rule" not in prompt


def _capture(monkeypatch, module, fn_name):
    seen = {}

    class Stop(Exception):
        pass

    real = getattr(module, fn_name)

    def spy(model, language):
        seen["language"] = language
        raise Stop
    monkeypatch.setattr(module, fn_name, spy)
    monkeypatch.setattr(module, "_check_file", lambda _p: 1024)
    return seen, Stop, real


def test_deepgram_uses_the_job_language(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "k" * 8)
    monkeypatch.setenv("CLIPPYME_LANGUAGE", "en")
    monkeypatch.delenv("DEEPGRAM_LANGUAGE", raising=False)
    seen, stop, _ = _capture(monkeypatch, deepgram_transcribe, "_build_params")
    try:
        deepgram_transcribe.transcribe_with_deepgram("x.mp4")
    except stop:
        pass
    assert seen["language"] == "en"


def test_elevenlabs_uses_the_job_language(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k" * 8)
    monkeypatch.setenv("CLIPPYME_LANGUAGE", "en")
    seen, stop, _ = _capture(monkeypatch, elevenlabs_transcribe, "_build_form")
    try:
        elevenlabs_transcribe.transcribe_with_elevenlabs("x.mp4")
    except stop:
        pass
    assert seen["language"] == "en"
