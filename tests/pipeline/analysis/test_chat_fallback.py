"""Chat-spike clips when Gemini is out of quota."""
import json

from clippyme.monitoring.chat_signal import Spike
from clippyme.pipeline import orchestrator
from clippyme.pipeline.analysis import chat_fallback as cf

TRANSCRIPT = {"segments": [
    {"start": 100.0, "end": 110.0, "text": "no way he just did that"},
    {"start": 600.0, "end": 612.0, "text": "bro what"},
]}


def test_spikes_round_trip_through_the_job_dir(tmp_path):
    cf.write_spikes(str(tmp_path), [Spike(80, 130, 4.2, "laugh")])
    assert cf.read_spikes(str(tmp_path)) == [
        {"start": 80, "end": 130, "ratio": 4.2, "reaction": "laugh"}]
    assert cf.read_spikes(str(tmp_path / "missing")) == []
    (tmp_path / cf.SPIKES_FILENAME).write_text("not json")
    assert cf.read_spikes(str(tmp_path)) == []


def test_clips_are_ranked_bounded_and_shaped_like_gemini_output():
    spikes = [{"start": 590, "end": 600, "ratio": 3.0, "reaction": "shock"},
              {"start": 80, "end": 200, "ratio": 6.0, "reaction": "laugh"},
              {"start": 1000, "end": 1004, "ratio": 2.6, "reaction": None}]
    out = cf.build_chat_spike_clips(spikes, TRANSCRIPT, 1800, max_clips=2, creator="kaicenat")
    shorts = out["shorts"]
    assert [c["start"] for c in shorts] == [80.0, 587.5]  # top 2 by ratio, in time order
    laugh, shock = shorts
    assert laugh["end"] - laugh["start"] == cf.MAX_CLIP_SECONDS          # long spike capped
    assert shock["end"] - shock["start"] == cf.MIN_CLIP_SECONDS          # short spike padded
    assert laugh["video_title_for_youtube_short"].startswith("kaicenat: Chat could not stop laughing")
    assert laugh["viral_hook_text"] == laugh["hook"]
    assert laugh["tiktok_caption"] == "no way he just did that"
    assert laugh["viral_score"] > shock["viral_score"] >= 72
    assert "Gemini quota was used up" in laugh["viral_reason"]


def test_windows_stay_inside_the_video_and_overlaps_keep_the_stronger():
    spikes = [{"start": 1795, "end": 1800, "ratio": 5.0, "reaction": "hype"},
              {"start": 1790, "end": 1799, "ratio": 3.0, "reaction": "hype"}]
    shorts = cf.build_chat_spike_clips(spikes, {}, 1800)["shorts"]
    assert len(shorts) == 1
    assert shorts[0]["end"] == 1800 and shorts[0]["end"] - shorts[0]["start"] >= 15


def test_no_spikes_no_clips():
    assert cf.build_chat_spike_clips([], TRANSCRIPT, 1800) is None


def test_orchestrator_fallback_uses_spikes_and_respects_the_score_floor(tmp_path, monkeypatch):
    cf.write_spikes(str(tmp_path), [Spike(80, 130, 4.2, "laugh"), Spike(590, 600, 2.5, None)])
    monkeypatch.setenv("CLIPPYME_MAX_CLIPS", "5")
    monkeypatch.setenv("CLIPPYME_MIN_VIRAL_SCORE", "75")
    out = orchestrator._chat_spike_fallback(str(tmp_path), TRANSCRIPT, 1800)
    assert out["clip_source"] == "chat_fallback"
    assert [c["start"] for c in out["shorts"]] == [80.0]  # the weak spike scores 72 < 75
    monkeypatch.setenv("CLIPPYME_MIN_VIRAL_SCORE", "95")
    assert orchestrator._chat_spike_fallback(str(tmp_path), TRANSCRIPT, 1800) is None
    assert orchestrator._chat_spike_fallback(str(tmp_path / "none"), TRANSCRIPT, 1800) is None


class _ExhaustedLegacy:
    """Every Gemini model rate-limited, like get_viral_clips reports it."""

    def get_viral_clips(self, transcript, duration, instructions=None):
        self.get_viral_clips.__func__._last_gemini_exhausted = True
        return None

    def build_texttiling_fallback(self, transcript, title):
        raise AssertionError("monitor jobs never use TextTiling")


def _analyze(tmp_path, monkeypatch, legacy, monitor=True):
    import types
    from clippyme.jobs.runtime_state import RuntimeState
    monkeypatch.setenv("CLIPPYME_SILENCE_SNAP", "0")
    monkeypatch.delenv("CLIPPYME_MAX_CLIPS", raising=False)
    monkeypatch.delenv("CLIPPYME_MIN_VIRAL_SCORE", raising=False)
    args = types.SimpleNamespace(skip_analysis=False, url=None, instructions=None,
                                 monitor=monitor, aspect="9:16", reframe_mode="disabled")
    plan, _ = orchestrator._load_or_analyze(args, "in.mp4", "seg", str(tmp_path), 1800.0,
                                            TRANSCRIPT, RuntimeState(str(tmp_path)), legacy)
    return plan


def test_exhausted_monitor_job_clips_chat_spikes(tmp_path, monkeypatch):
    cf.write_spikes(str(tmp_path), [Spike(80, 130, 4.2, "laugh")])
    plan = _analyze(tmp_path, monkeypatch, _ExhaustedLegacy())
    assert plan["gemini_exhausted"] is True
    assert plan["clip_source"] == "chat_fallback"
    assert len(plan["shorts"]) == 1 and plan["shorts"][0]["viral_hook_text"]


def test_exhausted_monitor_job_without_spikes_is_still_skipped(tmp_path, monkeypatch):
    plan = _analyze(tmp_path, monkeypatch, _ExhaustedLegacy())
    assert plan["shorts"] == [] and plan["gemini_exhausted"] is True


def test_spike_file_is_ignored_when_gemini_worked(tmp_path, monkeypatch):
    class _Working:
        def get_viral_clips(self, transcript, duration, instructions=None):
            self.get_viral_clips.__func__._last_gemini_exhausted = False
            return {"shorts": [{"start": 300.0, "end": 330.0, "title": "AI pick"}]}

    cf.write_spikes(str(tmp_path), [Spike(80, 130, 4.2, "laugh")])
    plan = _analyze(tmp_path, monkeypatch, _Working())
    assert [c["title"] for c in plan["shorts"]] == ["AI pick"]
    assert "clip_source" not in plan
