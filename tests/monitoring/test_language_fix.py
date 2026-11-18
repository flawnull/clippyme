"""Queued clips made before the language was enforced are redone in English
once. Reported: Spanish/Italian subtitles and captions on English streams."""
import asyncio
import json
import os

from clippyme.monitoring.language_fix import (
    fix_clip,
    needs_retranscription,
    parse_translation,
    splice_words,
)
from clippyme.monitoring.live_monitor import LiveMonitor, validate_monitor_config


def _w(word, start, end):
    return {"word": word, "start": start, "end": end}


def test_needs_retranscription():
    assert needs_retranscription({"language": "es"}, "en")
    assert needs_retranscription({"language": "unknown"}, "en")
    assert not needs_retranscription({"language": "en-US"}, "en")
    assert not needs_retranscription({"language": "es", "forced_language": "en"}, "en")


def test_splice_replaces_only_the_clip_range():
    transcript = {"language": "es", "segments": [
        {"start": 0, "end": 2, "words": [_w("antes", 0, 1), _w("hola", 1.5, 2)]},
        {"start": 10, "end": 12, "words": [_w("despues", 10, 12)]}]}
    new = [{"start": 0.1, "end": 0.6, "words": [_w("hello", 0.1, 0.6)]}]
    out = splice_words(transcript, 1.0, 5.0, new, 1.0)
    words = [w["word"] for s in out["segments"] for w in s["words"]]
    assert words == ["antes", "hello", "despues"]
    hello = out["segments"][1]["words"][0]
    assert (hello["start"], hello["end"]) == (1.1, 1.6)
    assert transcript["segments"][0]["words"][1]["word"] == "hola"  # input untouched


def test_parse_translation_keeps_only_requested_string_keys():
    fields = {"title": "Hola", "viral_hook_text": "Mira esto"}
    text = 'Sure:\n```json\n{"title": "Hello", "viral_hook_text": " Watch this ", "x": "y", "title2": 3}\n```'
    assert parse_translation(text, fields) == {"title": "Hello", "viral_hook_text": "Watch this"}
    assert parse_translation("no json", fields) == {}


def _job(tmp_path, transcript_language="es"):
    out = tmp_path / "out"
    job = out / "j1"
    job.mkdir(parents=True)
    (job / "clip_1.mp4").write_bytes(b"v")
    (job / "source_clip_1.mp4").write_bytes(b"v")
    meta = {"transcript": {"language": transcript_language, "segments": [
                {"start": 4, "end": 6, "words": [_w("hola", 4, 5), _w("chat", 5, 6)]}]},
            "shorts": [{"start": 4.0, "end": 6.0, "title": "Hola chat",
                        "viral_hook_text": "Mira esto", "clip_filename": "clip_1.mp4"}]}
    (job / "j1_metadata.json").write_text(json.dumps(meta))
    return str(out), job / "j1_metadata.json"


def test_fix_clip_redoes_subtitles_and_text_once(tmp_path):
    out, meta_path = _job(tmp_path)
    calls = []

    def transcribe(path, language):
        calls.append((os.path.basename(path), language))
        return {"segments": [{"start": 0, "end": 1, "words": [_w("hello", 0, 0.5), _w("chat", 0.5, 1)]}]}

    def translate(fields, name):
        assert name == "English"
        return {"title": "Hello chat", "viral_hook_text": "Watch this"}

    changed = fix_clip("j1", 0, out, "en", transcribe=transcribe, translate=translate)
    assert changed == {"title": "Hello chat", "viral_hook_text": "Watch this"}
    assert calls == [("source_clip_1.mp4", "en")]
    meta = json.loads(meta_path.read_text())
    words = [w["word"] for s in meta["transcript"]["segments"] for w in s["words"]]
    assert words == ["hello", "chat"]
    assert meta["transcript"]["segments"][0]["words"][0]["start"] == 4.0
    assert meta["shorts"][0]["title"] == "Hello chat"
    assert meta["shorts"][0]["subtitle_language"] == "en"
    assert fix_clip("j1", 0, out, "en", transcribe=transcribe, translate=translate) is None


def test_fix_clip_keeps_english_subtitles(tmp_path):
    out, meta_path = _job(tmp_path, transcript_language="en")

    def transcribe(*_a):
        raise AssertionError("an English transcript is not redone")

    assert fix_clip("j1", 0, out, "en", transcribe=transcribe, translate=lambda f, n: {}) == {}
    assert json.loads(meta_path.read_text())["shorts"][0]["subtitle_language"] == "en"


def test_failed_transcription_is_retried_next_start(tmp_path):
    out, meta_path = _job(tmp_path)
    fix_clip("j1", 0, out, "en", transcribe=lambda *_a: None, translate=lambda f, n: {"title": "Hi"})
    short = json.loads(meta_path.read_text())["shorts"][0]
    assert short["title"] == "Hi" and "subtitle_language" not in short


def test_monitor_redoes_queued_clips_and_rerenders(tmp_path, monkeypatch):
    mon = LiveMonitor(id="kick:n3on", jobs={}, job_queue=None, output_dir=str(tmp_path / "out"))
    mon.cfg = validate_monitor_config({
        "platform": "kick", "channel": "n3on", "timezone": "UTC", "review_minutes": 0,
        "platforms": [{"platform": "tiktok", "accountId": "a"}]}, default_timezone="UTC")
    mon.publishing_enabled = False
    os.makedirs(mon._clip_dir, exist_ok=True)
    old = os.path.join(mon._clip_dir, "old.mp4")
    open(old, "wb").close()
    todo = {"job_id": "j1", "composed_path": old, "clip": {"original_index": 0, "title": "Hola"}}
    done = {"job_id": "j1", "clip": {"original_index": 1, "title": "Hi", "subtitle_language": "en"}}
    busy = {"job_id": "j1", "clip": {"original_index": 2, "title": "Ciao"}}
    mon._enqueue_publications([todo, done, busy])
    busy["state"] = "in_flight"
    fixed_calls = []

    def fake_fix(job_id, idx, output_dir, language):
        fixed_calls.append((idx, language))
        return {"title": "Hello"}

    async def fake_compose(job_id, clip, base_path=None):
        return "/new.mp4"

    monkeypatch.setattr(mon, "_compose_for_publish", fake_compose)
    assert asyncio.run(mon._fix_queue_language(fix=fake_fix)) == 1
    assert fixed_calls == [(0, "en")]
    assert todo["clip"]["title"] == "Hello" and todo["clip"]["subtitle_language"] == "en"
    assert todo["composed_path"] == "/new.mp4" and not os.path.exists(old)
    assert busy["clip"]["title"] == "Ciao"
